"""Portfel: ekspozycja na metale, otwarte pozycje i stopy zwrotu ważone czasem.

Stopa zwrotu miesiąca liczona metodą Modified Dietz na saldzie (wpłaty/wypłaty + zrealizowany wynik),
miesiące łączone łańcuchowo (TWR) — wpłata w połowie miesiąca nie „poprawia” wyniku.
Bez wpłat nie znamy kapitału, więc stóp zwrotu nie liczymy (pokazujemy tylko wynik kwotowo).

Ceny do wyceny pochodzą z zapisanych notowań (/api/prices) — zawsze pokazujemy ich znacznik czasu.
Futures (GC, SI…) wyceniamy spotem tylko przy ekspozycji; niezrealizowany wynik liczymy wyłącznie dla spot/CFD,
bo baza futures–spot (contango) zafałszowałaby wynik.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from .positions import Position

ZERO = Decimal(0)
SPOT = {"XAUUSD": "XAU", "XAGUSD": "XAG"}
_FUT = re.compile(r"^(MGC|GC|SIL|SI)([FGHJKMNQUVXZ]\d{1,2})?$")


def metal_of(symbol: str) -> Optional[str]:
    if symbol in SPOT:
        return SPOT[symbol]
    m = _FUT.match(symbol)
    if not m:
        return None
    return "XAU" if m.group(1) in ("GC", "MGC") else "XAG"


@dataclass(frozen=True)
class Flow:
    ts: datetime
    amount: Decimal          # + wpłata, − wypłata


@dataclass
class Holding:
    key: str
    symbol: str
    metal: Optional[str]
    direction: int
    qty: Decimal
    avg_price: Decimal
    ounces: Optional[Decimal]
    mark: Optional[float]
    unrealized: Optional[Decimal]


@dataclass
class Exposure:
    metal: str
    ounces: Decimal = ZERO
    price: Optional[float] = None
    price_ts: Optional[datetime] = None

    @property
    def notional(self) -> Optional[Decimal]:
        return None if self.price is None else self.ounces * Decimal(str(self.price))


@dataclass
class Month:
    month: str               # "2026-09"
    pnl: Decimal
    flows: Decimal
    start_equity: Decimal
    end_equity: Decimal
    ret: Optional[float]


@dataclass
class Report:
    holdings: List[Holding] = field(default_factory=list)
    exposure: Dict[str, Exposure] = field(default_factory=dict)
    months: List[Month] = field(default_factory=list)
    balance: Optional[Decimal] = None
    realized: Decimal = ZERO
    deposits: Decimal = ZERO
    twr: Optional[float] = None
    ytd: Optional[float] = None


def holdings(positions: Sequence[Position], marks: Dict[str, Tuple[datetime, float]]) -> Tuple[List[Holding], Dict[str, Exposure]]:
    out: List[Holding] = []
    expo = {m: Exposure(m, price=marks[m][1] if m in marks else None, price_ts=marks[m][0] if m in marks else None)
            for m in ("XAU", "XAG")}
    for p in positions:
        if not p.is_open or not p.open_qty:
            continue
        metal = metal_of(p.symbol)
        avg = p.avg_open_price or p.avg_entry
        ounces = p.open_qty * p.contract_size * p.direction if metal else None
        mark = marks[metal][1] if metal in marks else None
        unreal = None
        if p.symbol in SPOT and mark is not None:
            unreal = (Decimal(str(mark)) - avg) * p.open_qty * p.contract_size * p.direction
        if metal:
            expo[metal].ounces += ounces
        out.append(Holding(p.key, p.symbol, metal, p.direction, p.open_qty, avg, ounces, mark, unreal))
    return out, expo


def _month_key(ts: datetime) -> str:
    return f"{ts.year:04d}-{ts.month:02d}"


def monthly_returns(positions: Sequence[Position], flows: Sequence[Flow]) -> List[Month]:
    """Modified Dietz per miesiąc kalendarzowy (UTC) na saldzie: przepływy ważone częścią miesiąca po nich."""
    pnl_by: Dict[str, Decimal] = {}
    for p in positions:
        if p.closed_at is not None:
            k = _month_key(p.closed_at)
            pnl_by[k] = pnl_by.get(k, ZERO) + p.net_pnl
    flows_by: Dict[str, List[Flow]] = {}
    for f in flows:
        flows_by.setdefault(_month_key(f.ts), []).append(f)
    keys = sorted(set(pnl_by) | set(flows_by))
    if not keys:
        return []
    # wszystkie miesiące od pierwszego do ostatniego — pusty miesiąc to 0%, nie dziura
    y, m = map(int, keys[0].split("-"))
    ly, lm = map(int, keys[-1].split("-"))
    months: List[Month] = []
    equity = ZERO
    funded = False            # przed pierwszą wpłatą kapitał jest nieznany — zysk nie jest „kapitałem”
    while (y, m) <= (ly, lm):
        k = f"{y:04d}-{m:02d}"
        days = calendar.monthrange(y, m)[1]
        start = datetime(y, m, 1, tzinfo=timezone.utc)
        month_flows = flows_by.get(k, [])
        f_sum = sum((f.amount for f in month_flows), ZERO)
        weighted = sum((f.amount * Decimal(str(max(0.0, days - (f.ts - start).total_seconds() / 86400) / days))
                        for f in month_flows), ZERO)
        pnl = pnl_by.get(k, ZERO)
        funded = funded or bool(month_flows)
        base = equity + weighted
        ret = float(pnl / base) if funded and base > 0 else None
        months.append(Month(k, pnl, f_sum, equity, equity + f_sum + pnl, ret))
        equity += f_sum + pnl
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return months


def chain(rets: Sequence[Optional[float]]) -> Optional[float]:
    acc, used = 1.0, False
    for r in rets:
        if r is None:
            continue
        acc *= 1 + r
        used = True
    return acc - 1 if used else None


def report(positions: Sequence[Position], flows: Sequence[Flow], marks: Dict[str, Tuple[datetime, float]],
           now: Optional[datetime] = None) -> Report:
    now = now or datetime.now(timezone.utc)
    hs, expo = holdings(positions, marks)
    months = monthly_returns(positions, flows)
    realized = sum((p.net_pnl for p in positions if p.closed_at is not None), ZERO)
    deposits = sum((f.amount for f in flows), ZERO)
    rep = Report(holdings=hs, exposure=expo, months=months, realized=realized, deposits=deposits)
    if flows:
        rep.balance = deposits + realized
        rep.twr = chain([x.ret for x in months])
        rep.ytd = chain([x.ret for x in months if x.month.startswith(f"{now.year:04d}-")])
    return rep
