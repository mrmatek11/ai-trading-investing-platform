"""Paper trading: zamrożone wersje strategii, przebiegi użytkowników, świece M15 i rozliczenie dni.

Zasady (docs/BACKTEST_RESULTS.md → karta zamrożenia):
- wersja = parametry + SHA-256 pliku silnika; uruchomionej wersji nie da się zmienić — zmiana kodu
  zatrzymuje przebiegi ze statusem „mismatch”, a nowa wersja zaczyna test od zera;
- tylko do przodu: przebieg handluje wyłącznie dniami po swoim starcie (historia służy tylko do NR7);
- kryterium karty liczymy na wyniku za uncję po stałym koszcie 0,40 USD/oz (porównywalnym z badaniem),
  niezależnie od wielkości pozycji; koszt z rzeczywistego spreadu jest drugą, pomocniczą miarą;
- transakcje trafiają do journala jako osobny rachunek `paper:<id>`, który NIE wchodzi do „wszystkich
  rachunków” (statystyki, przegląd AI, raporty, MCP) — tylko po jawnym wybraniu.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from sqlalchemy import Float, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..db import PAPER_PREFIX, Base, ExactDecimal, UtcDateTime, store_cash_flows, store_fills
from ..importers.base import CashFlow, Fill
from . import engine

ENGINE_FILE = Path(engine.__file__)
MAX_RISK_PCT = 2.0
MAX_ACTIVE_RUNS = 5


def engine_sha256() -> str:
    return hashlib.sha256(ENGINE_FILE.read_bytes()).hexdigest()


@dataclass(frozen=True)
class Version:
    id: str
    name: str
    description: str
    asset: str
    params: Dict[str, object]
    engine_sha256: str                # zamrożony skrót silnika — test pilnuje zgodności z plikiem
    cost_per_oz: float                # koszt z badania — podstawa kryterium
    expected_bps: float               # wynik poza próbą z badania (punkt odniesienia)
    min_trades: int
    t_threshold: float

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps({"engine": self.engine_sha256, "params": self.params, "asset": self.asset,
                                          "cost": self.cost_per_oz}, sort_keys=True).encode()).hexdigest()


VERSIONS: Dict[str, Version] = {v.id: v for v in [
    Version(
        id="london-orb-nr7@1",
        name="London ORB po dniu NR7",
        description=("Zakres 08:00–09:00 Londynu, zlecenia stop 10% szerokości za zakresem (OCO), SL po drugiej "
                     "stronie zakresu, wejście do 12:00, wyjście najpóźniej 16:00 Londynu. Tylko po dniu o najwęższym "
                     "zakresie z 7."),
        asset="XAU",
        params={"range_min": 60, "stop": "range", "tp_r": None, "buffer": 0.1},
        engine_sha256="253446b04edf777e02200a3c073b0946621ceab729ad6ed8dcda26e2974c2cc8",
        cost_per_oz=0.40,
        expected_bps=4.0,
        min_trades=100,
        t_threshold=1.65,
    ),
]}


def version_ok(v: Version) -> bool:
    """Czy plik silnika jest dokładnie tym, który zamrożono dla wersji."""
    return engine_sha256() == v.engine_sha256


# ─── tabele ────────────────────────────────────────────────────────────────────

class BarRow(Base):
    __tablename__ = "bars_m15"

    asset: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime, primary_key=True)      # otwarcie świecy, UTC
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    spread: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(16))


class PaperRun(Base):
    __tablename__ = "paper_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(80))
    balance_start: Mapped[Decimal] = mapped_column(ExactDecimal)
    risk_pct: Mapped[Decimal] = mapped_column(ExactDecimal)
    contract_oz: Mapped[Decimal] = mapped_column(ExactDecimal, default=Decimal(100))
    lot_step: Mapped[Decimal] = mapped_column(ExactDecimal, default=Decimal("0.01"))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    first_day: Mapped[str] = mapped_column(String(10))                     # pierwszy dzień handlowy przebiegu
    status: Mapped[str] = mapped_column(String(16), default="active")      # active | stopped | mismatch
    stopped_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
    last_processed_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)


class PaperDay(Base):
    __tablename__ = "paper_days"
    __table_args__ = (UniqueConstraint("run_id", "day", name="uq_paper_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    day: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(16))     # trade | no_nr7 | no_setup | no_fill | data_gap | size_zero
    note: Mapped[str] = mapped_column(String(300), default="")
    direction: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    entry: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_time: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
    exit_time: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    gross: Mapped[Optional[float]] = mapped_column(Float, nullable=True)            # USD/oz
    net: Mapped[Optional[float]] = mapped_column(Float, nullable=True)              # USD/oz po koszcie z badania
    net_bps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_spread: Mapped[Optional[float]] = mapped_column(Float, nullable=True)       # USD/oz po rzeczywistym spreadzie
    lots: Mapped[Optional[Decimal]] = mapped_column(ExactDecimal, nullable=True)
    pnl_usd: Mapped[Optional[Decimal]] = mapped_column(ExactDecimal, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime, default=lambda: datetime.now(timezone.utc))


def book_of(run: PaperRun) -> str:
    return f"{PAPER_PREFIX}{run.id}"


def is_paper_book(book: Optional[str]) -> bool:
    return bool(book) and book.startswith(PAPER_PREFIX)


# ─── świece ────────────────────────────────────────────────────────────────────

def save_bars(session: Session, asset: str, bars: Sequence[engine.Bar], provider: str) -> int:
    """Dopisz albo popraw świece (ostatnia mogła przyjść niepełna przy wcześniejszym pobraniu)."""
    n = 0
    for b in bars:
        row = session.get(BarRow, (asset, b.ts))
        if row is None:
            session.add(BarRow(asset=asset, ts=b.ts, open=b.open, high=b.high, low=b.low, close=b.close,
                               spread=b.spread, provider=provider))
            n += 1
        else:
            row.open, row.high, row.low, row.close, row.spread, row.provider = (
                b.open, b.high, b.low, b.close, b.spread, provider)
    return n


def load_bars(session: Session, asset: str, since: datetime) -> List[engine.Bar]:
    rows = session.execute(select(BarRow.ts, BarRow.open, BarRow.high, BarRow.low, BarRow.close, BarRow.spread)
                           .where(BarRow.asset == asset, BarRow.ts >= since).order_by(BarRow.ts))
    return [engine.Bar(ts, o, h, lo, c, sp) for ts, o, h, lo, c, sp in rows]


# ─── przebiegi ─────────────────────────────────────────────────────────────────

class PaperError(ValueError):
    pass


def trading_day(ts: datetime) -> date:
    ny = ts.astimezone(engine.NEW_YORK)
    return (ny.replace(tzinfo=None) + timedelta(hours=7)).date()


def create_run(session: Session, account: str, version_id: str, balance: Decimal, risk_pct: Decimal,
               name: str = "", now: Optional[datetime] = None) -> PaperRun:
    v = VERSIONS.get(version_id)
    if v is None:
        raise PaperError("Nieznana wersja strategii")
    if not version_ok(v):
        raise PaperError("Kod silnika różni się od zamrożonej wersji — potrzebna nowa wersja strategii")
    if not (Decimal("0.1") <= risk_pct <= Decimal(str(MAX_RISK_PCT))):
        raise PaperError(f"Ryzyko na transakcję musi być między 0,1% a {MAX_RISK_PCT:g}%")
    if not (Decimal(100) <= balance <= Decimal(10_000_000)):
        raise PaperError("Saldo startowe: od 100 do 10 000 000 USD")
    active = session.scalars(select(PaperRun.id).where(PaperRun.account == account, PaperRun.status == "active")).all()
    if len(active) >= MAX_ACTIVE_RUNS:
        raise PaperError(f"Masz już {MAX_ACTIVE_RUNS} aktywnych przebiegów — zatrzymaj któryś")
    now = now or datetime.now(timezone.utc)
    run = PaperRun(id=str(uuid.uuid4()), account=account, version=v.id, fingerprint=v.fingerprint,
                   name=(name.strip() or v.name)[:80], balance_start=balance, risk_pct=risk_pct,
                   started_at=now, first_day=(trading_day(now) + timedelta(days=1)).isoformat(), status="active")
    session.add(run)
    session.flush()
    store_cash_flows(session, account, "paper", [CashFlow("start", now, balance, note="Saldo startowe paper")],
                     book=book_of(run))
    return run


def _lots(equity: Decimal, risk_pct: Decimal, entry: float, stop: float, run: PaperRun) -> Decimal:
    per_lot = Decimal(str(abs(entry - stop))) * run.contract_oz
    if per_lot <= 0:
        return Decimal(0)
    raw = equity * risk_pct / Decimal(100) / per_lot
    return (raw / run.lot_step).to_integral_value(rounding=ROUND_DOWN) * run.lot_step


def equity(session: Session, run: PaperRun) -> Decimal:
    pnl = session.scalars(select(PaperDay.pnl_usd).where(PaperDay.run_id == run.id, PaperDay.pnl_usd.is_not(None)))
    return run.balance_start + sum(pnl, Decimal(0))


def _spread_cost(t: engine.Trade) -> Optional[float]:
    """Świece po stronie bid: kupno płaci spread przy wejściu, sprzedaż (short) — przy wyjściu."""
    sp = t.entry_bar.spread if t.direction == 1 else t.exit_bar.spread
    return sp


def process_run(session: Session, run: PaperRun, now: Optional[datetime] = None) -> Dict[str, int]:
    """Rozlicz dni, które się skończyły. Idempotentne: dzień zapisany raz zostaje bez zmian."""
    now = now or datetime.now(timezone.utc)
    v = VERSIONS.get(run.version)
    if run.status != "active":
        return {"recorded": 0}
    if v is None or not version_ok(v) or run.fingerprint != v.fingerprint:
        run.status, run.stopped_at = "mismatch", now
        return {"recorded": 0, "mismatch": 1}
    first = date.fromisoformat(run.first_day)
    since = datetime.combine(first - timedelta(days=21), datetime.min.time(), tzinfo=timezone.utc)
    bars = load_bars(session, v.asset, since)
    done = set(session.scalars(select(PaperDay.day).where(PaperDay.run_id == run.id)))
    eq = equity(session, run)
    recorded = 0
    for r in engine.evaluate(bars, v.params, now=now, from_day=first):
        if r.status == "pending" or r.day.isoformat() in done:
            continue
        row = PaperDay(run_id=run.id, day=r.day.isoformat(), status=r.status, note=r.note[:300])
        t = r.trade
        if t is not None:
            lots = _lots(eq, run.risk_pct, t.entry, t.stop, run)
            net = t.gross - v.cost_per_oz
            sp = _spread_cost(t)
            row.direction, row.entry, row.exit, row.stop = t.direction, t.entry, t.exit, t.stop
            row.entry_time, row.exit_time, row.reason = t.entry_time, t.exit_time, t.reason
            row.gross, row.net, row.net_bps = t.gross, net, net / t.entry * 1e4
            row.net_spread = (t.gross - sp) if sp is not None else None
            row.lots = lots
            if lots <= 0:
                # kryterium karty liczymy na uncji — dzień z sygnałem liczy się, nawet gdy saldo nie pozwala na 0,01 lota
                row.note = "pozycja poniżej minimalnego lota przy tym saldzie i ryzyku — tylko w statystyce"
                row.pnl_usd = Decimal(0)
            else:
                oz = lots * run.contract_oz
                row.pnl_usd = (Decimal(str(net)) * oz).quantize(Decimal("0.01"))
                eq += row.pnl_usd
                side_in, side_out = ("buy", "sell") if t.direction == 1 else ("sell", "buy")
                cost = -(Decimal(str(v.cost_per_oz)) * oz)
                store_fills(session, run.account, "paper", [
                    Fill(f"{r.day}:in", t.entry_time, "XAUUSD", side_in, lots, Decimal(str(t.entry)), run.contract_oz,
                         Decimal(0), None, Decimal(str(t.stop))),
                    Fill(f"{r.day}:out", t.exit_time + engine.BAR, "XAUUSD", side_out, lots, Decimal(str(t.exit)),
                         run.contract_oz, cost, None, None),
                ], book=book_of(run))
        session.add(row)
        done.add(row.day)
        recorded += 1
    run.last_processed_at = now
    return {"recorded": recorded}


def progress(session: Session, run: PaperRun) -> Dict[str, object]:
    v = VERSIONS.get(run.version)
    xs = list(session.scalars(select(PaperDay.net_bps).where(PaperDay.run_id == run.id, PaperDay.status == "trade")
                              .order_by(PaperDay.day)))
    n = len(xs)
    mean = sum(xs) / n if n else None
    t = None
    if n > 1:
        sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))
        t = mean / (sd / math.sqrt(n)) if sd > 0 else None
    need_n, need_t = (v.min_trades, v.t_threshold) if v else (100, 1.65)
    passed = n >= need_n and t is not None and t >= need_t and mean is not None and mean > 0
    if run.status == "mismatch":
        verdict = "Kod strategii zmienił się po starcie — ten przebieg nic już nie dowodzi. Zacznij nowy."
    elif passed:
        verdict = "Kryterium karty zamrożenia spełnione."
    elif n < need_n:
        verdict = f"W trakcie: {n} z {need_n} transakcji. Do tego czasu wynik nic nie rozstrzyga."
    else:
        verdict = f"Po {n} transakcjach t = {t:.2f} < {need_t} — przewagi nie potwierdzono."
    return {"trades": n, "need_trades": need_n, "mean_bps": mean, "t_stat": t, "need_t": need_t,
            "expected_bps": v.expected_bps if v else None, "passed": passed, "verdict": verdict}


def run_dict(session: Session, run: PaperRun) -> Dict[str, object]:
    v = VERSIONS.get(run.version)
    return {"id": run.id, "book": book_of(run), "name": run.name, "version": run.version,
            "version_name": v.name if v else run.version, "status": run.status,
            "balance_start": float(run.balance_start), "equity": float(equity(session, run)),
            "risk_pct": float(run.risk_pct), "started_at": run.started_at.isoformat(), "first_day": run.first_day,
            "last_processed_at": run.last_processed_at.isoformat() if run.last_processed_at else None,
            "progress": progress(session, run)}


def day_dict(d: PaperDay) -> Dict[str, object]:
    return {"day": d.day, "status": d.status, "note": d.note, "direction": d.direction, "entry": d.entry,
            "exit": d.exit, "stop": d.stop, "reason": d.reason, "gross": d.gross, "net": d.net, "net_bps": d.net_bps,
            "net_spread": d.net_spread, "lots": float(d.lots) if d.lots is not None else None,
            "pnl_usd": float(d.pnl_usd) if d.pnl_usd is not None else None,
            "entry_time": d.entry_time.isoformat() if d.entry_time else None,
            "exit_time": d.exit_time.isoformat() if d.exit_time else None}


def today_plan(session: Session, run: PaperRun, now: Optional[datetime] = None) -> Optional[Dict[str, object]]:
    """Dzisiejszy dzień handlowy, jeśli jeszcze trwa: aktywny (po NR7)? jakie poziomy?"""
    v = VERSIONS.get(run.version)
    if v is None or run.status != "active":
        return None
    now = now or datetime.now(timezone.utc)
    bars = load_bars(session, v.asset, now - timedelta(days=21))
    res = engine.evaluate(bars, v.params, now=now)
    last = res[-1] if res else None
    if last is None or last.status != "pending":
        return None
    out: Dict[str, object] = {"day": last.day.isoformat(), "active": last.nr7, "orders": []}
    if last.nr7:
        out["orders"] = [{"direction": o.direction, "entry": round(o.entry, 2), "stop": round(o.stop, 2),
                          "valid_until": o.cancel_if_no_fill.isoformat() if o.cancel_if_no_fill else None,
                          "flat_by": o.deadline.isoformat()} for o in last.orders]
    return out
