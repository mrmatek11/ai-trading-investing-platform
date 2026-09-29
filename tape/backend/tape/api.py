"""HTTP API Tape (FastAPI).

Konto pochodzi wyłącznie z uwierzytelnienia: JWT (Clerk/OIDC) gdy skonfigurowano TAPE_AUTH_JWKS_URL,
w przeciwnym razie tryb jednego użytkownika („default”, opcjonalny TAPE_API_TOKEN).
Wyjątek: /api/ingest/mt5 uwierzytelnia token połączenia EA „Tape Sync”.

Uruchomienie: uvicorn tape.api:create_app --factory --reload
"""

from __future__ import annotations

import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import journal
from . import econ_calendar, market, prop_accounts
from . import review as ai_review
from . import sync as broker_sync
from .auth import AuthError, Verifier, admin_subs, verifier_from_env
from .db import (CashFlowRow, ImportRow, books as list_books, load_cash_flows, load_fills, load_fills_by_book,
                 make_sessionmaker, store_cash_flows, store_fills)
from .engine import portfolio, prop, risk, stats
from .engine.positions import build_positions
from .importers import BROKERS, generic, parse_file
from .news import store as news_store
from .news import track_record
from .news.bias import aggregate, event_to_dict
from .news.sample import sample_events
from .secretbox import SecretBox

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
PUBLIC_PATHS = {"/api/health", "/api/ingest/mt5"}


class SizeRequest(BaseModel):
    balance: Decimal = Field(gt=0)
    risk_pct: Decimal = Field(gt=0, le=100)
    entry: Decimal = Field(gt=0)
    stop: Decimal = Field(gt=0)
    contract_size: Decimal = Field(Decimal(100), gt=0)
    lot_step: Decimal = Field(Decimal("0.01"), gt=0)
    min_lot: Decimal = Field(Decimal("0.01"), gt=0)
    max_lot: Decimal | None = Field(None, gt=0)
    daily_range: Decimal | None = Field(None, gt=0)
    daily_loss_limit: Decimal | None = Field(None, gt=0)


class SetupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field("", max_length=2000)
    rules: list[str] = Field(default_factory=list, max_length=20)


class JournalIn(BaseModel):
    setup_id: int | None = None
    checklist: dict[str, bool] = Field(default_factory=dict)
    mistakes: list[str] = Field(default_factory=list, max_length=12)
    notes: str = Field("", max_length=10000)
    initial_stop: Decimal | None = Field(None, gt=0)


class PropRequest(BaseModel):
    initial_balance: Decimal = Field(gt=0)
    daily_loss_pct: Decimal = Field(Decimal(5), gt=0, le=100)
    max_drawdown_pct: Decimal = Field(Decimal(10), gt=0, le=100)
    drawdown_type: str = Field("static", pattern="^(static|trailing)$")
    profit_target_pct: Decimal | None = Field(Decimal(10), gt=0)
    day_tz: str = "Europe/Prague"


class CashFlowIn(BaseModel):
    book: str = Field("", max_length=64)
    ts: datetime
    amount: Decimal = Field(gt=Decimal("-1e12"), lt=Decimal("1e12"))
    currency: str = Field("USD", pattern="^[A-Z]{3}$")
    note: str = Field("", max_length=200)


class PropAccountIn(PropRequest):
    book: str = Field("", max_length=64)
    name: str = Field(min_length=1, max_length=80)


class IbkrConnectionIn(BaseModel):
    label: str = Field("Interactive Brokers", min_length=1, max_length=80)
    token: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9]+$")
    query_id: str = Field(min_length=1, max_length=20, pattern=r"^[0-9]+$")
    tz: str = Field("", max_length=64)


class Mt5ConnectionIn(BaseModel):
    label: str = Field("MetaTrader 5", min_length=1, max_length=80)


def default_ai_client():
    """Klient Claude tylko gdy skonfigurowano klucz — bez niego funkcje AI mają działający fallback."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    import anthropic

    return anthropic.Anthropic()


def create_app(database_url: Optional[str] = None, ai_client=None, verifier: Optional[Verifier] = None,
               secret_box: Optional[SecretBox] = None, flex_fetch=None) -> FastAPI:
    Session = make_sessionmaker(database_url)
    with Session() as s:
        econ_calendar.ensure_seed(s)
    box = secret_box if secret_box is not None else SecretBox.from_env()
    ai = ai_client if ai_client is not None else default_ai_client()
    token = os.getenv("TAPE_API_TOKEN", "")
    verify = verifier if verifier is not None else verifier_from_env()
    admins = set(admin_subs())

    async def auth(request: Request):
        # Konto zawsze z uwierzytelnienia, nigdy z parametrów żądania.
        request.state.account = "default"
        request.state.is_admin = verify is None          # tryb jednego użytkownika: właściciel = admin
        if request.url.path in PUBLIC_PATHS:
            return                                        # health; ingest MT5 ma własny token połączenia
        header = request.headers.get("authorization", "")
        if verify is not None:
            if not header.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="Zaloguj się")
            try:
                sub = verify(header[len("Bearer "):])
            except AuthError as exc:
                raise HTTPException(status_code=401, detail="Sesja wygasła lub token jest niepoprawny") from exc
            request.state.account = sub
            request.state.is_admin = sub in admins
            return
        if token and not hmac.compare_digest(header, f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="Brak lub niepoprawny token")

    def current_account(request: Request) -> str:
        return request.state.account

    app = FastAPI(title="Tape API", version="0.1.0", dependencies=[Depends(auth)])

    def positions_for(account: str, book: Optional[str] = None):
        """Pozycje liczone osobno dla każdego rachunku — long na jednym koncie nie zamyka shorta na drugim."""
        with Session() as s:
            items = []
            for b, fills in load_fills_by_book(s, account, book or None).items():
                for p in build_positions(fills):
                    p.book = b
                    items.append(p)
            items.sort(key=lambda p: p.opened_at)
            journal.apply_manual_stops(items, journal.entries_by_key(s, account))
            return items

    def position_dict(p, entry=None, setups=None):
        return {
            "key": p.key, "book": getattr(p, "book", ""), "symbol": p.symbol, "direction": "long" if p.direction == 1 else "short",
            "opened_at": p.opened_at.isoformat(), "closed_at": p.closed_at.isoformat() if p.closed_at else None,
            "qty": str(p.qty), "avg_entry": str(round(p.avg_entry, 5)),
            "avg_exit": str(round(p.avg_exit, 5)) if p.avg_exit is not None else None,
            "net_pnl": float(round(p.net_pnl, 2)), "fees": float(round(p.fees, 2)),
            "r_multiple": float(round(p.r_multiple, 2)) if p.r_multiple is not None else None,
            "initial_stop": str(p.initial_stop) if p.initial_stop is not None else None,
            "setup": setups[entry.setup_id].name if entry and setups and entry.setup_id in setups else None,
            "mistakes": list(entry.mistakes or []) if entry else [],
            "has_notes": bool(entry and entry.notes),
        }

    def news_times(positions):
        """Czasy ważnych danych USD w zakresie historii — do segmentu „wejście przy danych”."""
        if not positions:
            return []
        start = min(p.opened_at for p in positions) - timedelta(days=1)
        end = max(p.opened_at for p in positions) + timedelta(days=1)
        with Session() as s:
            return [r.ts for r in econ_calendar.relevant(s, start, end)]

    def load_setups(s, account: str):
        return {x.id: x for x in s.scalars(select(journal.Setup).where(journal.Setup.account == account))}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

    @app.post("/api/imports/suggest")
    async def suggest_mapping(file: UploadFile = File(...)):
        """Nagłówki, podgląd i propozycja mapowania kolumn dla nierozpoznanego pliku."""
        from .importers.base import read_rows
        from .importers.suggest import suggest

        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Plik większy niż 10 MB")
        rows = read_rows(data, file.filename or "upload.csv")
        if not rows:
            raise HTTPException(status_code=400, detail="Nie znaleziono wierszy z danymi")
        headers = list(dict.fromkeys(k for r in rows[:50] for k in r))
        try:
            suggestion = suggest(headers, rows, ai)
        except Exception as exc:  # awaria AI → i tak zwracamy heurystykę
            suggestion = suggest(headers, rows, None)
            suggestion["notes"] = f"AI niedostępne ({type(exc).__name__}); propozycja z heurystyki."
        preview = [{h: (None if r.get(h) is None else str(r.get(h))) for h in headers} for r in rows[:8]]
        return {"headers": headers, "preview": preview, "rows": len(rows), "suggestion": suggestion,
                "ai_available": ai is not None}

    @app.post("/api/imports")
    async def import_file(
        file: UploadFile = File(...),
        broker: Optional[str] = Form(None),
        tz: Optional[str] = Form(None),
        mapping: Optional[str] = Form(None),
        book: str = Form("", max_length=64),
        account: str = Depends(current_account),
    ):
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Plik większy niż 10 MB")
        if broker and broker not in BROKERS and broker not in ("generic", "ibkr"):
            raise HTTPException(status_code=400, detail=f"Nieznany broker: {broker}")
        parsed_mapping = None
        if mapping:
            try:
                raw = {k: v for k, v in json.loads(mapping).items() if v is not None}
                parsed_mapping = generic.Mapping(**raw)
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=400, detail=f"Niepoprawne mapowanie: {exc}") from exc
        result = parse_file(data, file.filename or "upload.csv", broker=broker if broker != "generic" else None,
                            tz=tz, mapping=parsed_mapping)
        source = result.detected or (broker or "unknown")
        with Session() as s:
            new, dup = store_fills(s, account, source, result.fills, book.strip()) if result.fills else (0, 0)
            flows_new = store_cash_flows(s, account, source, result.cash_flows, book.strip())
            s.add(ImportRow(account=account, source=source, filename=file.filename or "", new=new,
                            duplicates=dup, errors=result.errors[:200]))
            s.commit()
        return {"detected": result.detected or None, "fills": len(result.fills), "new": new, "cash_flows": flows_new,
                "duplicates": dup, "errors": result.errors[:50], "error_count": len(result.errors)}

    @app.get("/api/positions")
    def positions(account: str = Depends(current_account), limit: int = 200, book: Optional[str] = None):
        items = positions_for(account, book)
        items.sort(key=lambda p: p.closed_at or p.opened_at, reverse=True)
        with Session() as s:
            entries = journal.entries_by_key(s, account)
            setups = load_setups(s, account)
        return [position_dict(p, entries.get(p.key), setups) for p in items[:limit]]

    @app.get("/api/positions/{key}")
    def position_detail(key: str, account: str = Depends(current_account)):
        items = positions_for(account)
        p = next((x for x in items if x.key == key), None)
        if p is None:
            raise HTTPException(status_code=404, detail="Nie ma takiej pozycji")
        with Session() as s:
            entry = journal.entries_by_key(s, account).get(key)
            setups = load_setups(s, account)
            fills = [f for f in load_fills(s, account) if f.external_id in set(p.fill_ids)]
            asset = {"XAUUSD": "XAU", "XAGUSD": "XAG"}.get(p.symbol, p.symbol)
            start = p.opened_at - timedelta(days=2)
            end = (p.closed_at or datetime.now(timezone.utc)) + timedelta(days=1)
            prices = [(t, v) for t, v in news_store.price_series(s, asset) if start <= t <= end][-3000:]
            events = [econ_calendar.to_dict(r) for r in econ_calendar.relevant(
                s, p.opened_at - timedelta(hours=6), (p.closed_at or p.opened_at) + timedelta(hours=6), impact="medium")]
        return {
            "position": position_dict(p, entry, setups),
            "fills": [{"id": f.external_id, "ts": f.ts.isoformat(), "side": f.side, "qty": str(f.qty),
                       "price": str(f.price), "fee": float(f.fee),
                       "broker_pnl": float(f.broker_pnl) if f.broker_pnl is not None else None} for f in fills],
            "journal": None if entry is None else {
                "setup_id": entry.setup_id, "checklist": entry.checklist or {}, "mistakes": entry.mistakes or [],
                "notes": entry.notes, "initial_stop": str(entry.initial_stop) if entry.initial_stop is not None else None,
            },
            "prices": [{"t": t.isoformat(), "p": v} for t, v in prices],
            "events": events,
        }

    @app.put("/api/positions/{key}/journal")
    def save_journal(key: str, body: JournalIn, account: str = Depends(current_account)):
        p = next((x for x in positions_for(account) if x.key == key), None)
        if p is None:
            raise HTTPException(status_code=404, detail="Nie ma takiej pozycji")
        if body.initial_stop is not None and (p.avg_entry - body.initial_stop) * p.direction <= 0:
            side = "poniżej" if p.direction == 1 else "powyżej"
            raise HTTPException(status_code=400, detail=f"Stop loss musi być {side} ceny wejścia {round(p.avg_entry, 2)}")
        with Session() as s:
            if body.setup_id is not None and body.setup_id not in load_setups(s, account):
                raise HTTPException(status_code=400, detail="Nieznany setup")
            entry = journal.entries_by_key(s, account).get(key) or journal.JournalEntry(account=account, position_key=key)
            entry.setup_id = body.setup_id
            entry.checklist = body.checklist
            entry.mistakes = [m.strip()[:80] for m in body.mistakes if m.strip()]
            entry.notes = body.notes
            entry.initial_stop = body.initial_stop
            entry.updated_at = datetime.now(timezone.utc)
            s.add(entry)
            s.commit()
        return {"ok": True}

    @app.get("/api/journal/meta")
    def journal_meta():
        return {"mistakes": journal.MISTAKES}

    @app.get("/api/setups")
    def list_setups(account: str = Depends(current_account), book: Optional[str] = None):
        items = positions_for(account, book)
        with Session() as s:
            setups = load_setups(s, account)
            entries = journal.entries_by_key(s, account)
        by_name = {g.key: g for g in journal.setup_stats(items, entries, setups)}
        return [{"id": x.id, "name": x.name, "description": x.description, "rules": x.rules or [],
                 "stats": stats.as_dict(by_name[x.name]) if x.name in by_name else None}
                for x in sorted(setups.values(), key=lambda v: v.name.lower())]

    @app.post("/api/setups", status_code=201)
    def create_setup(body: SetupIn, account: str = Depends(current_account)):
        with Session() as s:
            if body.name.strip() in {x.name for x in load_setups(s, account).values()}:
                raise HTTPException(status_code=409, detail="Setup o tej nazwie już istnieje")
            row = journal.Setup(account=account, name=body.name.strip(), description=body.description,
                                rules=[r.strip() for r in body.rules if r.strip()])
            s.add(row)
            s.commit()
            return {"id": row.id}

    @app.put("/api/setups/{setup_id}")
    def update_setup(setup_id: int, body: SetupIn, account: str = Depends(current_account)):
        with Session() as s:
            setups = load_setups(s, account)
            row = setups.get(setup_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Nie ma takiego setupu")
            if any(x.name == body.name.strip() and x.id != setup_id for x in setups.values()):
                raise HTTPException(status_code=409, detail="Setup o tej nazwie już istnieje")
            row.name, row.description = body.name.strip(), body.description
            row.rules = [r.strip() for r in body.rules if r.strip()]
            s.commit()
        return {"ok": True}

    @app.delete("/api/setups/{setup_id}")
    def delete_setup(setup_id: int, account: str = Depends(current_account)):
        with Session() as s:
            row = load_setups(s, account).get(setup_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Nie ma takiego setupu")
            for e in s.scalars(select(journal.JournalEntry).where(journal.JournalEntry.setup_id == setup_id)):
                e.setup_id = None      # SQLite bez PRAGMA foreign_keys nie wykona ON DELETE SET NULL
            s.delete(row)
            s.commit()
        return {"ok": True}

    @app.get("/api/stats")
    def get_stats(account: str = Depends(current_account), book: Optional[str] = None):
        items = positions_for(account, book)
        with Session() as s:
            entries = journal.entries_by_key(s, account)
            setups = load_setups(s, account)
        return {
            "setups": [stats.as_dict(g) for g in journal.setup_stats(items, entries, setups)],
            "mistakes": [stats.as_dict(g) for g in journal.mistake_costs(items, entries)],
            "summary": stats.as_dict(stats.summarize(items)),
            "equity": stats.equity_curve(items),
            "segments": [stats.as_dict(x) for x in stats.segments(items, news_times(items))],
        }

    @app.post("/api/tools/position-size")
    def size(req: SizeRequest):
        try:
            r = risk.position_size(req.balance, req.risk_pct, req.entry, req.stop, req.contract_size,
                                   req.lot_step, req.min_lot, req.max_lot, req.daily_range, req.daily_loss_limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        f = lambda d: float(d) if d is not None else None  # noqa: E731
        return {"lots": f(r.lots), "risk_budget": f(r.risk_budget), "risk_actual": f(r.risk_actual),
                "stop_distance": f(r.stop_distance), "value_per_point": f(r.value_per_point),
                "notional": f(r.notional), "min_lot_risk": f(r.min_lot_risk),
                "daily_range_loss": f(r.daily_range_loss), "daily_limit_share": f(r.daily_limit_share),
                "warnings": list(r.warnings)}

    @app.post("/api/prop/evaluate")
    def prop_evaluate(req: PropRequest, account: str = Depends(current_account), book: Optional[str] = None):
        try:
            rules = prop.PropRules(req.initial_balance, req.daily_loss_pct, req.max_drawdown_pct,
                                   req.drawdown_type, req.profit_target_pct, req.day_tz, name="Twoje konto")
        except Exception as exc:  # np. nieznana strefa czasowa
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        items = positions_for(account, book)
        try:
            report = prop.evaluate(items, rules)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sims = prop.simulate(items, req.initial_balance)
        return {"report": prop.report_to_dict(report),
                "simulation": {k: {"name": v.rules.name, "status": v.status, "breach": v.breach,
                                   "breach_day": v.breach_day.isoformat() if v.breach_day else None,
                                   "passed_day": v.passed_day.isoformat() if v.passed_day else None}
                               for k, v in sims.items()},
                "note": "Liczone na saldzie po zamknięciu transakcji; firmy liczą też equity z otwartymi pozycjami."}

    @app.get("/api/calendar")
    def calendar(days: int = 7):
        now = datetime.now(timezone.utc)
        with Session() as s:
            rows = econ_calendar.relevant(s, now - timedelta(hours=12), now + timedelta(days=max(1, min(days, 60))),
                                          impact="medium")
        return {"events": [econ_calendar.to_dict(r) for r in rows],
                "sources": sorted({r.source for r in rows}),
                "note": "Daty FOMC wpisane w kodzie — potwierdź na federalreserve.gov." if all(r.source == "seed" for r in rows) else ""}

    @app.post("/api/calendar")
    async def upload_calendar(request: Request, file: UploadFile = File(...)):
        if not request.state.is_admin:
            raise HTTPException(status_code=403, detail="Tylko administrator może wgrywać kalendarz")
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Plik większy niż 10 MB")
        try:
            events = econ_calendar.parse_csv(data, file.filename or "calendar.csv")
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with Session() as s:
            n = econ_calendar.add_events(s, "csv", events)
            s.commit()
        return {"added": n, "rows": len(events)}

    @app.get("/api/market/quotes")
    def market_quotes():
        with Session() as s:
            return market.quotes(s, datetime.now(timezone.utc))

    # ---- przegląd AI ----

    def review_inputs(account: str, book: Optional[str] = None):
        items = positions_for(account, book)
        with Session() as s:
            facts = ai_review.build_facts(items, journal.entries_by_key(s, account), load_setups(s, account),
                                          news_times(items))
        closed = sum(1 for p in items if not p.is_open)
        return facts, ai_review.facts_hash(facts), closed

    @app.get("/api/review")
    def get_review(account: str = Depends(current_account)):
        facts, h, closed = review_inputs(account)
        with Session() as s:
            row = ai_review.latest(s, account)
        return {"ai_available": ai is not None, "trades": closed, "min_trades": ai_review.MIN_TRADES,
                "review": ai_review.to_dict(row, facts, h) if row else None}

    @app.post("/api/review")
    def create_review(account: str = Depends(current_account)):
        if ai is None:
            raise HTTPException(status_code=503, detail="AI nie jest skonfigurowane (ANTHROPIC_API_KEY)")
        facts, h, closed = review_inputs(account)
        if closed < ai_review.MIN_TRADES:
            raise HTTPException(status_code=400, detail=f"Potrzeba co najmniej {ai_review.MIN_TRADES} zamkniętych transakcji")
        with Session() as s:
            row = ai_review.latest(s, account)
            if row and row.facts_hash == h:
                return ai_review.to_dict(row, facts, h)          # dane bez zmian — nie płacimy drugi raz
            now = datetime.now(timezone.utc)
            if row and now - row.created_at < timedelta(minutes=5):
                raise HTTPException(status_code=429, detail="Nowy przegląd możesz wygenerować za kilka minut")
        try:
            out = ai_review.generate(ai, facts)
        except Exception as exc:  # błąd API/sieci — nie zapisujemy niczego
            raise HTTPException(status_code=502, detail=f"AI chwilowo niedostępne ({type(exc).__name__})") from exc
        if out is None:
            raise HTTPException(status_code=502, detail="AI nie przygotowało przeglądu — spróbuj ponownie później")
        with Session() as s:
            row = ai_review.ReviewRow(account=account, facts_hash=h, model=ai_review.MODEL,
                                      payload={**out, "facts_snapshot": facts})
            s.add(row)
            s.commit()
            return ai_review.to_dict(row, facts, h)

    @app.get("/api/books")
    def get_books(account: str = Depends(current_account)):
        """Rachunki handlowe: połączenia (MT5/IBKR) i nazwy nadane przy imporcie pliku."""
        with Session() as s:
            conns = {c.id: c for c in s.scalars(select(broker_sync.Connection)
                                                .where(broker_sync.Connection.account == account))}
            used = list_books(s, account)
        out = [{"id": c.id, "label": c.label, "kind": c.kind, "has_trades": c.id in used} for c in conns.values()]
        out += [{"id": b, "label": b or "Import z plików", "kind": "import", "has_trades": True}
                for b in used if b not in conns]
        return sorted(out, key=lambda x: (x["kind"] != "import" or x["id"] != "", x["label"].lower()))

    # ---- portfel ----

    def flow_dict(f: CashFlowRow):
        return {"id": f.id, "book": f.book, "ts": f.ts.isoformat(), "amount": float(f.amount), "currency": f.currency,
                "note": f.note, "source": f.source}

    @app.get("/api/portfolio")
    def get_portfolio(account: str = Depends(current_account), book: Optional[str] = None):
        items = positions_for(account, book)
        with Session() as s:
            rows = load_cash_flows(s, account, book or None)
            marks = {a: m for a in ("XAU", "XAG") if (m := market.mark(s, a))}
        flows = [portfolio.Flow(f.ts, f.amount) for f in rows]
        rep = portfolio.report(items, flows, marks)
        f = lambda d: None if d is None else float(round(d, 2))  # noqa: E731
        first_trade = min((p.opened_at for p in items), default=None)
        if not rows:
            note = "Dodaj wpłatę początkową, żeby policzyć stopy zwrotu (bez kapitału liczymy tylko wynik kwotowo)."
        elif first_trade and first_trade < rows[0].ts:
            note = "Pierwsza transakcja jest wcześniejsza niż pierwsza wpłata — dodaj saldo początkowe z wcześniejszą datą."
        else:
            note = ""
        return {
            "note": note,
            "balance": f(rep.balance), "realized": f(rep.realized), "deposits": f(rep.deposits),
            "twr": rep.twr, "ytd": rep.ytd,
            "currencies": sorted({r.currency for r in rows}),
            "exposure": {k: {"ounces": float(e.ounces), "price": e.price, "notional": f(e.notional),
                             "price_ts": e.price_ts.isoformat() if e.price_ts else None}
                         for k, e in rep.exposure.items()},
            "holdings": [{"key": h.key, "symbol": h.symbol, "metal": h.metal,
                          "direction": "long" if h.direction == 1 else "short", "qty": str(h.qty),
                          "avg_price": str(round(h.avg_price, 5)), "ounces": f(h.ounces), "mark": h.mark,
                          "unrealized": f(h.unrealized)} for h in rep.holdings],
            "months": [{"month": m.month, "pnl": f(m.pnl), "flows": f(m.flows), "start_equity": f(m.start_equity),
                        "end_equity": f(m.end_equity), "ret": m.ret} for m in rep.months],
            "cash_flows": [flow_dict(x) for x in rows][-200:],
        }

    @app.post("/api/cashflows", status_code=201)
    def add_cash_flow(body: CashFlowIn, account: str = Depends(current_account)):
        if body.amount == 0:
            raise HTTPException(status_code=422, detail="Kwota nie może być zerowa")
        ts = body.ts if body.ts.tzinfo else body.ts.replace(tzinfo=timezone.utc)
        with Session() as s:
            row = CashFlowRow(account=account, source="manual", book=body.book, external_id=uuid.uuid4().hex, ts=ts,
                              amount=body.amount, currency=body.currency, note=body.note)
            s.add(row)
            s.commit()
            return flow_dict(row)

    @app.delete("/api/cashflows/{flow_id}")
    def delete_cash_flow(flow_id: int, account: str = Depends(current_account)):
        with Session() as s:
            row = s.get(CashFlowRow, flow_id)
            if row is None or row.account != account:
                raise HTTPException(status_code=404, detail="Nie ma takiej operacji")
            if row.source != "manual":
                raise HTTPException(status_code=400, detail="Operacje z brokera wracają przy synchronizacji — "
                                                            "usuń je u źródła")
            s.delete(row)
            s.commit()
        return {"ok": True}

    # ---- połączenia z brokerami (automatyczna synchronizacja) ----

    def own_connection(s, account: str, conn_id: str):
        conn = s.get(broker_sync.Connection, conn_id)
        if conn is None or conn.account != account:
            raise HTTPException(status_code=404, detail="Nie ma takiego połączenia")
        return conn

    @app.get("/api/connections")
    def list_connections(account: str = Depends(current_account)):
        with Session() as s:
            rows = s.scalars(select(broker_sync.Connection).where(broker_sync.Connection.account == account)
                             .order_by(broker_sync.Connection.created_at))
            return {"connections": [broker_sync.to_dict(c) for c in rows], "encryption": box is not None}

    @app.post("/api/connections/ibkr", status_code=201)
    def create_ibkr_connection(body: IbkrConnectionIn, account: str = Depends(current_account)):
        if box is None:
            raise HTTPException(status_code=503, detail="Serwer nie ma klucza szyfrowania (TAPE_SECRET_KEYS) — "
                                                        "nie zapiszemy tokenu brokera jawnym tekstem.")
        if body.tz:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(body.tz)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Nieznana strefa czasowa: {body.tz}") from exc
        with Session() as s:
            conn = broker_sync.create_ibkr(s, box, account, body.label.strip(), body.token, body.query_id, body.tz)
            s.commit()
            return broker_sync.to_dict(conn)

    @app.post("/api/connections/mt5", status_code=201)
    def create_mt5_connection(body: Mt5ConnectionIn, account: str = Depends(current_account)):
        with Session() as s:
            conn, tok = broker_sync.create_mt5(s, account, body.label.strip())
            s.commit()
            # token pokazujemy tylko raz — w bazie jest wyłącznie jego hash
            return {**broker_sync.to_dict(conn), "token": tok}

    @app.post("/api/connections/{conn_id}/sync")
    def sync_connection(conn_id: str, account: str = Depends(current_account)):
        with Session() as s:
            conn = own_connection(s, account, conn_id)
            if conn.kind != "ibkr_flex":
                raise HTTPException(status_code=400, detail="MT5 wysyła dane sam (EA Tape Sync)")
            now = datetime.now(timezone.utc)
            if conn.last_sync_at and now - conn.last_sync_at < broker_sync.MANUAL_COOLDOWN:
                raise HTTPException(status_code=429, detail="IBKR ogranicza liczbę zapytań — spróbuj za 2 minuty")
            rep = broker_sync.sync_ibkr(s, conn, box, flex_fetch, now)
            s.commit()
            return {**rep, "connection": broker_sync.to_dict(conn)}

    @app.delete("/api/connections/{conn_id}")
    def delete_connection(conn_id: str, account: str = Depends(current_account)):
        with Session() as s:
            s.delete(own_connection(s, account, conn_id))   # usuwa też zaszyfrowany token / hash
            s.commit()
        return {"ok": True}

    @app.post("/api/ingest/mt5")
    async def ingest_mt5(request: Request):
        """Endpoint dla EA „Tape Sync” — uwierzytelnienie tokenem połączenia, nie sesją użytkownika."""
        header = request.headers.get("authorization", "")
        tok = header[len("Bearer "):] if header.startswith("Bearer ") else ""
        raw = await request.body()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Za duża paczka — wyślij mniej transakcji naraz")
        with Session() as s:
            conn = broker_sync.connection_for_token(s, tok) if tok else None
            if conn is None:
                raise HTTPException(status_code=401, detail="Niepoprawny token połączenia")
            try:
                payload = broker_sync.Mt5Payload.model_validate_json(raw)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Niepoprawne dane: {str(exc)[:300]}") from exc
            rep = broker_sync.ingest_mt5(s, conn, payload)
            s.commit()
            return rep

    # ---- zapisane konta prop i alerty ----

    def prop_rows(s, account: str):
        return list(s.scalars(select(prop_accounts.PropAccountRow)
                              .where(prop_accounts.PropAccountRow.account == account)
                              .order_by(prop_accounts.PropAccountRow.name)))

    @app.get("/api/prop/accounts")
    def list_prop_accounts(account: str = Depends(current_account)):
        now = datetime.now(timezone.utc)
        with Session() as s:
            rows = prop_rows(s, account)
        out = []
        for r in rows:
            with Session() as s:
                eq = broker_sync.latest_equity(s, account, r.book)
            fresh = eq is not None and now - eq.ts <= timedelta(minutes=15)   # stary odczyt nie udaje bieżącego
            st = prop.today_status(positions_for(account, r.book), r.rules(), now,
                                   floating=(eq.equity - eq.balance) if fresh else None)
            d = prop_accounts.status_dict(r, st)
            d["equity_ts"] = eq.ts.isoformat() if eq else None
            d["equity_fresh"] = fresh
            out.append(d)
        return out

    @app.put("/api/prop/accounts")
    def save_prop_account(body: PropAccountIn, account: str = Depends(current_account)):
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(body.day_tz)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Nieznana strefa czasowa: {body.day_tz}") from exc
        with Session() as s:
            row = next((r for r in prop_rows(s, account) if r.book == body.book), None)
            if row is None:
                row = prop_accounts.PropAccountRow(account=account, book=body.book)
                s.add(row)
            row.name, row.initial_balance = body.name.strip(), body.initial_balance
            row.daily_loss_pct, row.max_drawdown_pct = body.daily_loss_pct, body.max_drawdown_pct
            row.drawdown_type, row.profit_target_pct, row.day_tz = body.drawdown_type, body.profit_target_pct, body.day_tz
            row.updated_at = datetime.now(timezone.utc)
            s.commit()
            return {"id": row.id}

    @app.delete("/api/prop/accounts/{prop_id}")
    def delete_prop_account(prop_id: int, account: str = Depends(current_account)):
        with Session() as s:
            row = s.get(prop_accounts.PropAccountRow, prop_id)
            if row is None or row.account != account:
                raise HTTPException(status_code=404, detail="Nie ma takiego konta prop")
            s.delete(row)
            s.commit()
        return {"ok": True}

    def current_events(now: datetime):
        """Zdarzenia z pipeline'u newsów; dopóki go nie uruchomiono — przykładowe (oznaczone)."""
        with Session() as s:
            stored = news_store.recent_events(s, now)
        return (stored, False) if stored else (sample_events(now), True)

    @app.get("/api/events")
    def events():
        now = datetime.now(timezone.utc)
        evts, _ = current_events(now)
        return [event_to_dict(e, now) for e in evts]

    @app.get("/api/bias")
    def bias():
        now = datetime.now(timezone.utc)
        evts, sample = current_events(now)
        out = {}
        for asset in ("XAU", "XAG"):
            b = aggregate(evts, asset, now)
            out[asset] = {"score": b.score, "label": b.label, "strength": b.strength,
                          "events_used": b.events_used,
                          "drivers": [d.__dict__ for d in b.drivers]}
        with Session() as s:
            snaps = [(x.ts, x.score, x.label) for x in news_store.snapshots(s, "XAU")]
            prices = news_store.price_series(s, "XAU")
        tr = track_record.evaluate(snaps, prices)
        if tr.observations == 0:
            note = ("Trafność pojawi się po okresie działania pipeline'u w trybie shadow."
                    if not snaps else f"Zapisanych ocen: {len(snaps)} — czekamy na ceny po ich horyzoncie.")
        else:
            note = (f"Trafność XAU poza próbą: {tr.hit_rate:.1%} · n = {tr.observations} · "
                    f"t = {tr.t_stat:.1f}" if tr.t_stat is not None else f"n = {tr.observations}")
        out["track_record"] = {
            "available": tr.observations > 0, "observations": tr.observations, "hit_rate": tr.hit_rate,
            "t_stat": tr.t_stat, "baseline_long_return": tr.baseline_long_return,
            "mean_signed_return": tr.mean_signed_return, "labels_allowed": tr.labels_allowed, "note": note,
        }
        out["sample"] = sample
        return out

    @app.post("/api/prices")
    async def upload_prices(request: Request, file: UploadFile = File(...), asset: str = Form("XAU")):
        """CSV z cenami do liczenia trafności: kolumny czasu (timestamp/date/time) i ceny (close/price).

        Ceny są wspólne dla wszystkich użytkowników, więc przy włączonym logowaniu tylko admin."""
        if not request.state.is_admin:
            raise HTTPException(status_code=403, detail="Tylko administrator może wgrywać ceny")
        from .importers.base import norm_header, read_rows, to_utc

        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES * 5:
            raise HTTPException(status_code=413, detail="Plik za duży")
        rows = read_rows(data, file.filename or "prices.csv")
        if not rows:
            raise HTTPException(status_code=400, detail="Pusty plik")
        cols = set(rows[0])
        t_col = next((c for c in ("timestamp", "date", "time", "datetime") if c in cols), None)
        p_col = next((c for c in ("close", "price") if c in cols), None)
        if not t_col or not p_col:
            raise HTTPException(status_code=400, detail=f"Brak kolumn czasu/ceny; są: {sorted(cols)}")
        parsed = []
        for r in rows:
            try:
                parsed.append((to_utc(r[t_col]), float(str(r[p_col]).replace(",", "."))))
            except (ValueError, KeyError, TypeError):
                continue
        with Session() as s:
            n = news_store.add_prices(s, norm_header(asset).upper(), parsed)
            s.commit()
        return {"added": n, "rows": len(rows)}

    return app
