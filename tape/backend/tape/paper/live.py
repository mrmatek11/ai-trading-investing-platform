"""Podgląd dnia w trakcie: pozycja przebiegu paper na świecach M15 zapisanych do tej chwili.

Tylko do odczytu i tylko orientacyjnie („provisional”): nic tu nie zapisuje dni ani transakcji. Oficjalny wynik
dnia liczy `store.process_run` po zamknięciu świecy z 16:00 Londynu — na pełnym oknie i z kontrolą dziur w danych,
której podgląd nie robi (w trakcie dnia okno 08:00–16:00 z definicji jest niepełne).

Statusy: waiting_for_range (zakres 08:00–09:00 jeszcze trwa) · orders_working (zlecenia stop czekają, do 12:00)
· in_position · stopped_out (SL trafiony) · flat_awaiting_close (bez pozycji albo zamknięta o 16:00 — czekamy
na rozliczenie).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Optional

from sqlalchemy.orm import Session

from . import engine, store


def _iso(ts: Optional[datetime]) -> Optional[str]:
    return ts.isoformat() if ts else None


def preview(session: Session, run: store.PaperRun, now: Optional[datetime] = None) -> Optional[Dict[str, object]]:
    """Stan dzisiejszego dnia aktywnego (po NR7), jeśli jeszcze trwa; inaczej None."""
    v = store.VERSIONS.get(run.version)
    # niezgodny kod nie dostaje podglądu — status „mismatch” ustawia tylko process_run, tu niczego nie zmieniamy
    if run.status != "active" or v is None or not store.version_ok(v) or run.fingerprint != v.fingerprint:
        return None
    now = now or datetime.now(timezone.utc)
    # tylko świece już zamknięte — bez zaglądania w przyszłość, nawet gdy baza ma nowsze
    bars = [b for b in store.load_bars(session, v.asset, now - timedelta(days=21)) if b.ts + engine.BAR <= now]
    days = engine.group_days(bars)
    if not days:
        return None
    flags = engine.nr7_flags(days)
    day, sess = days[-1]
    if day != store.trading_day(now) or not flags[day] or day < date.fromisoformat(run.first_day):
        return None                                    # dzień nieaktywny albo sprzed startu przebiegu
    close = datetime(day.year, day.month, day.day, 16, tzinfo=engine.LONDON)
    if now >= close + engine.BAR:
        return None                                    # świeca z 16:00 zamknięta — dzień rozlicza process_run
    orders = engine.planned_orders(day, sess, v.params)
    last, eq = bars[-1], store.equity(session, run)
    as_of = last.ts + engine.BAR                       # godziny statusu wg danych, nie zegara — worker bywa spóźniony
    out: Dict[str, object] = {
        "provisional": True, "day": day.isoformat(), "as_of": _iso(as_of), "mark": last.close,
        "orders": [{"direction": o.direction, "entry": round(o.entry, 2), "stop": round(o.stop, 2)} for o in orders],
        "valid_until": _iso(orders[0].cancel_if_no_fill) if orders else None,
        "flat_by": _iso(orders[0].deadline) if orders else None,
        "direction": None, "entry": None, "entry_time": None, "stop": None, "exit": None, "exit_time": None,
        "reason": None, "gross": None, "net": None, "lots": None, "pnl_usd": None, "equity": float(eq),
    }
    if not orders:
        return {**out, "status": "waiting_for_range"}
    t = engine.run_day(sess, orders)                  # OCO jak w rozliczeniu: pierwsze wypełnienie, remis → long
    if t is None:
        working = orders[0].cancel_if_no_fill is None or as_of < orders[0].cancel_if_no_fill
        return {**out, "status": "orders_working" if working else "flat_awaiting_close"}
    closed = t.reason == "sl" or as_of >= orders[0].deadline
    status = "stopped_out" if t.reason == "sl" else "flat_awaiting_close" if closed else "in_position"
    lots = store._lots(eq, run.risk_pct, t.entry, t.stop, run)
    net = t.gross - v.cost_per_oz
    pnl = (Decimal(str(net)) * lots * run.contract_oz).quantize(Decimal("0.01")) if lots > 0 else Decimal(0)
    return {**out, "status": status, "direction": t.direction, "entry": t.entry, "entry_time": _iso(t.entry_time),
            "stop": t.stop, "exit": t.exit if closed else None,
            "exit_time": _iso(t.exit_time) if closed else None, "reason": t.reason if closed else None,
            "gross": t.gross, "net": net, "lots": float(lots), "pnl_usd": float(pnl)}
