"""Automatyczna synchronizacja kont brokerskich.

- IBKR: Flex Web Service (token + query id, zaszyfrowane w bazie) — cyklicznie przez worker albo przyciskiem.
- MT5: EA „Tape Sync” wysyła transakcje na /api/ingest/mt5 z tokenem połączenia (w bazie tylko jego hash).

Fill-e z synchronizacji trafiają do tych samych źródeł co import plików („ibkr”, „mt5”), więc ręczny import
tego samego okresu nie tworzy duplikatów.

Worker:  python -m tape.sync --every 3600
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, UtcDateTime, store_cash_flows, store_fills
from .importers import ibkr
from .importers.base import CashFlow, Fill
from .importers.instruments import default_contract_size, normalize_symbol
from .secretbox import SecretBox, SecretError

log = logging.getLogger("tape.sync")

KINDS = ("ibkr_flex", "mt5_push")
MANUAL_COOLDOWN = timedelta(minutes=2)      # IBKR ogranicza liczbę zapytań Flex
TOKEN_PREFIX = "tps_"


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(80))
    tz: Mapped[str] = mapped_column(String(64), default="")
    secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)          # SecretBox, tylko IBKR
    token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)  # tylko MT5
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=lambda: datetime.now(timezone.utc))
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(16), default="never")      # never | ok | error
    last_new: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(500), default="")


def context(conn: Connection) -> str:
    return f"{conn.account}|{conn.id}|{conn.kind}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_push_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def to_dict(conn: Connection) -> dict:
    return {"id": conn.id, "kind": conn.kind, "label": conn.label, "tz": conn.tz,
            "created_at": conn.created_at.isoformat(),
            "last_sync_at": conn.last_sync_at.isoformat() if conn.last_sync_at else None,
            "last_status": conn.last_status, "last_new": conn.last_new, "last_error": conn.last_error}


def create_ibkr(session: Session, box: SecretBox, account: str, label: str, token: str, query_id: str,
                tz: str = "") -> Connection:
    conn = Connection(id=str(uuid.uuid4()), account=account, kind="ibkr_flex", label=label, tz=tz)
    conn.secret = box.encrypt(json.dumps({"token": token, "query_id": query_id}).encode(), context(conn))
    session.add(conn)
    return conn


def create_mt5(session: Session, account: str, label: str) -> tuple[Connection, str]:
    token = new_push_token()
    conn = Connection(id=str(uuid.uuid4()), account=account, kind="mt5_push", label=label,
                      token_hash=hash_token(token))
    session.add(conn)
    return conn, token


def _finish(conn: Connection, now: datetime, new: int = 0, error: str = "") -> None:
    conn.last_sync_at = now
    conn.last_status = "error" if error else "ok"
    conn.last_new = new
    conn.last_error = error[:500]


Fetcher = Callable[[str, str], bytes]


def sync_ibkr(session: Session, conn: Connection, box: Optional[SecretBox], fetch: Optional[Fetcher] = None,
              now: Optional[datetime] = None) -> dict:
    """Pobierz raport Flex i zapisz nowe wykonania. Błędy zapisujemy w statusie połączenia, nie rzucamy."""
    now = now or datetime.now(timezone.utc)
    fetch = fetch or (lambda token, q: ibkr.fetch_statement(token, q))
    try:
        if box is None:
            raise SecretError("serwer nie ma skonfigurowanego TAPE_SECRET_KEYS")
        creds = json.loads(box.decrypt(conn.secret or "", context(conn)))
        data = fetch(creds["token"], creds["query_id"])
        result = ibkr.parse(data, "flex.xml", tz=conn.tz or "America/New_York")
        if not result.fills and result.errors:
            raise ValueError(result.errors[0])
        new, dup = store_fills(session, conn.account, "ibkr", result.fills)
        store_cash_flows(session, conn.account, "ibkr", result.cash_flows)
        _finish(conn, now, new, "; ".join(result.errors[:3]))
        if result.errors:
            conn.last_status = "partial"
        return {"new": new, "duplicates": dup, "errors": result.errors[:50]}
    except Exception as exc:  # sieć, XML, klucze — worker nie może paść na jednym koncie
        # nigdy nie logujemy treści sekretu — tylko typ i komunikat
        msg = f"Brak połączenia z IBKR — spróbujemy ponownie ({exc})" if isinstance(exc, OSError) else f"{exc}"
        _finish(conn, now, error=msg)
        return {"new": 0, "duplicates": 0, "errors": [conn.last_error]}


# ---- MT5 (EA „Tape Sync”) ----

class Mt5Deal(BaseModel):
    ticket: int = Field(ge=0)
    time: int = Field(ge=0, description="czas serwera MT5, sekundy od epoki")
    symbol: str = Field("", max_length=64)
    type: str = Field(max_length=16)             # buy | sell | balance | credit | …
    entry: str = Field("", max_length=8)         # in | out | inout | out_by
    volume: Decimal = Field(ge=0)
    price: Decimal = Field(ge=0)
    commission: Decimal = Decimal(0)
    fee: Decimal = Decimal(0)
    swap: Decimal = Decimal(0)
    profit: Decimal = Decimal(0)
    sl: Optional[Decimal] = Field(None, ge=0)
    contract_size: Optional[Decimal] = Field(None, ge=0)          # 0 = EA nie zna symbolu → domyślna


class Mt5Payload(BaseModel):
    gmt_offset: int = Field(0, ge=-14 * 3600, le=14 * 3600, description="czas serwera − UTC, w sekundach")
    deals: List[Mt5Deal] = Field(default_factory=list, max_length=5000)


def mt5_fills(payload: Mt5Payload) -> tuple[List[Fill], List[str], List[CashFlow]]:
    fills: List[Fill] = []
    errors: List[str] = []
    flows: List[CashFlow] = []
    for d in payload.deals:
        kind = d.type.strip().lower()
        if kind == "balance" and d.profit:
            flows.append(CashFlow(str(d.ticket), datetime.fromtimestamp(d.time - payload.gmt_offset, tz=timezone.utc),
                                  d.profit, note="MT5 balance"))
            continue
        if kind not in ("buy", "sell"):
            continue                              # wpłaty, wypłaty, korekty — nie są transakcjami
        if not d.symbol or d.volume <= 0 or d.price <= 0:
            errors.append(f"deal {d.ticket}: brak symbolu, wolumenu lub ceny")
            continue
        symbol = normalize_symbol(d.symbol)
        closing = d.entry.strip().lower() in ("out", "inout", "out_by")
        fills.append(Fill(
            external_id=str(d.ticket),
            ts=datetime.fromtimestamp(d.time - payload.gmt_offset, tz=timezone.utc),
            symbol=symbol, side=kind, qty=d.volume, price=d.price,
            contract_size=d.contract_size or default_contract_size(symbol),
            fee=d.commission + d.fee + d.swap,
            broker_pnl=d.profit if closing else None,
            stop_loss=d.sl if d.sl else None,
        ))
    return fills, errors, flows


def connection_for_token(session: Session, token: str) -> Optional[Connection]:
    if not token.startswith(TOKEN_PREFIX):
        return None
    return session.scalars(select(Connection).where(Connection.token_hash == hash_token(token),
                                                    Connection.kind == "mt5_push")).first()


def ingest_mt5(session: Session, conn: Connection, payload: Mt5Payload, now: Optional[datetime] = None) -> dict:
    fills, errors, flows = mt5_fills(payload)
    new, dup = store_fills(session, conn.account, "mt5", fills)
    store_cash_flows(session, conn.account, "mt5", flows)
    _finish(conn, now or datetime.now(timezone.utc), new, "; ".join(errors[:3]))
    if errors:
        conn.last_status = "partial"
    return {"new": new, "duplicates": dup, "errors": errors[:50]}


# ---- worker ----

def run_due(session: Session, box: Optional[SecretBox], every: timedelta, fetch: Optional[Fetcher] = None,
            now: Optional[datetime] = None) -> List[dict]:
    """Zsynchronizuj połączenia IBKR, których ostatnia synchronizacja jest starsza niż `every`."""
    now = now or datetime.now(timezone.utc)
    out = []
    for conn in session.scalars(select(Connection).where(Connection.kind == "ibkr_flex")):
        if conn.last_sync_at and now - conn.last_sync_at < every:
            continue
        rep = sync_ibkr(session, conn, box, fetch, now)
        session.commit()
        out.append({"id": conn.id, **{k: rep[k] for k in ("new", "duplicates")}, "status": conn.last_status})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Synchronizacja kont Tape (IBKR Flex)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=3600, help="Jak często synchronizować konto, w sekundach")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from .db import make_sessionmaker

    Session_ = make_sessionmaker()
    box = SecretBox.from_env()
    if box is None:
        log.warning("brak TAPE_SECRET_KEYS — połączenia IBKR nie zostaną odszyfrowane")
    while True:
        with Session_() as s:
            log.info("sync: %s", run_due(s, box, timedelta(seconds=args.every)))
        if args.once:
            break
        time.sleep(min(args.every, 300))


if __name__ == "__main__":
    main()
