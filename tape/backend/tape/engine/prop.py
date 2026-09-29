"""Reguły prop firm: dzienny limit straty i maksymalny drawdown (statyczny / trailing).

Liczone na saldzie po zamkniętych transakcjach. Ograniczenie: firmy zwykle liczą też equity
z otwartymi pozycjami — naruszenie w trakcie transakcji, które „wróciło” przed zamknięciem,
nie jest tu widoczne. UI mówi to wprost.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from .positions import Position


@dataclass(frozen=True)
class PropRules:
    initial_balance: Decimal
    daily_loss_pct: Decimal = Decimal(5)           # % salda początkowego
    max_drawdown_pct: Decimal = Decimal(10)
    drawdown_type: str = "static"                  # static | trailing (od szczytu salda, stop na starcie)
    profit_target_pct: Optional[Decimal] = Decimal(10)
    day_tz: str = "Europe/Prague"                  # reset dnia (wiele firm liczy dzień w czasie CE(S)T)
    name: str = ""


PRESETS: Dict[str, PropRules] = {
    "static_10": PropRules(Decimal(100000), Decimal(5), Decimal(10), "static", Decimal(10), name="Statyczny 10% · dzienny 5%"),
    "trailing_6": PropRules(Decimal(100000), Decimal(4), Decimal(6), "trailing", Decimal(8), name="Trailing 6% · dzienny 4%"),
    "trailing_5_3": PropRules(Decimal(100000), Decimal(3), Decimal(5), "trailing", Decimal(8), name="Trailing 5% · dzienny 3%"),
}


@dataclass
class DayRow:
    day: date
    start_balance: Decimal
    end_balance: Decimal
    pnl: Decimal
    daily_floor: Decimal
    overall_floor: Decimal
    breach: Optional[str] = None


@dataclass
class PropReport:
    rules: PropRules
    status: str                                    # active | breached | passed
    balance: Decimal
    breach: Optional[str] = None
    breach_day: Optional[date] = None
    passed_day: Optional[date] = None
    daily_headroom: Optional[Decimal] = None       # ile jeszcze możesz stracić dziś (na ostatni dzień w danych)
    overall_headroom: Optional[Decimal] = None
    days: List[DayRow] = field(default_factory=list)


def evaluate(positions: Sequence[Position], rules: PropRules) -> PropReport:
    tz = ZoneInfo(rules.day_tz)
    closed = sorted((p for p in positions if p.closed_at is not None), key=lambda p: p.closed_at)
    by_day: Dict[date, List[Position]] = {}
    for p in closed:
        by_day.setdefault(p.closed_at.astimezone(tz).date(), []).append(p)

    start = rules.initial_balance
    daily_amount = start * rules.daily_loss_pct / 100
    dd_amount = start * rules.max_drawdown_pct / 100
    target = start * (1 + rules.profit_target_pct / 100) if rules.profit_target_pct else None

    def overall_floor_at(peak: Decimal) -> Decimal:
        # trailing: próg rośnie ze szczytem salda, ale (typowo) zatrzymuje się na saldzie początkowym
        return start - dd_amount if rules.drawdown_type == "static" else min(peak - dd_amount, start)

    balance = start
    peak = start
    report = PropReport(rules=rules, status="active", balance=start)
    for day in sorted(by_day):
        day_start = balance
        daily_floor = day_start - daily_amount
        breach = None
        for p in sorted(by_day[day], key=lambda x: x.closed_at):
            balance += p.net_pnl
            if breach is None and balance <= daily_floor:
                breach = "daily"
            if breach is None and balance <= overall_floor_at(peak):
                breach = "max_drawdown"
            peak = max(peak, balance)
        overall_floor = overall_floor_at(peak)
        report.days.append(DayRow(day, day_start, balance, balance - day_start, daily_floor, overall_floor, breach))
        if breach and report.status == "active":
            report.status, report.breach, report.breach_day = "breached", breach, day
        if report.status == "active" and target is not None and balance >= target:
            report.status, report.passed_day = "passed", day

    report.balance = balance
    last_floor = report.days[-1].daily_floor if report.days else start - daily_amount
    report.daily_headroom = max(Decimal(0), balance - last_floor)
    report.overall_headroom = max(Decimal(0), balance - overall_floor_at(peak))
    return report


def simulate(positions: Sequence[Position], initial_balance: Decimal,
             presets: Optional[Dict[str, PropRules]] = None) -> Dict[str, PropReport]:
    """Ta sama historia na różnych typach kont: „na którym koncie przeżyłbyś ten okres?”."""
    presets = presets or PRESETS
    return {k: evaluate(positions, replace(r, initial_balance=initial_balance)) for k, r in presets.items()}


def report_to_dict(r: PropReport) -> Dict[str, object]:
    f = lambda d: float(d) if d is not None else None  # noqa: E731
    return {
        "rules": {"name": r.rules.name, "initial_balance": f(r.rules.initial_balance),
                  "daily_loss_pct": f(r.rules.daily_loss_pct), "max_drawdown_pct": f(r.rules.max_drawdown_pct),
                  "drawdown_type": r.rules.drawdown_type, "profit_target_pct": f(r.rules.profit_target_pct),
                  "day_tz": r.rules.day_tz},
        "status": r.status, "balance": f(r.balance), "breach": r.breach,
        "breach_day": r.breach_day.isoformat() if r.breach_day else None,
        "passed_day": r.passed_day.isoformat() if r.passed_day else None,
        "daily_headroom": f(r.daily_headroom), "overall_headroom": f(r.overall_headroom),
        "days": [{"day": d.day.isoformat(), "end_balance": f(d.end_balance), "pnl": f(d.pnl),
                  "daily_floor": f(d.daily_floor), "overall_floor": f(d.overall_floor), "breach": d.breach}
                 for d in r.days],
    }
