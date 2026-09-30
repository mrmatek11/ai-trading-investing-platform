"""Statystyki journala liczone deterministycznie w kodzie (AI tylko je interpretuje).

Każdy segment ma porównanie z resztą transakcji (test t Welcha). Segment jest „istotny”, gdy
ma co najmniej MIN_SEGMENT transakcji i |t| ≥ 2 — tylko takie trafiają do wniosków AI.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Sequence

from .positions import Position

MIN_SEGMENT = 10
AFTER_LOSS_WINDOW = timedelta(minutes=30)
WEEKDAYS = ["Pn", "Wt", "Śr", "Cz", "Pt", "Sb", "Nd"]


@dataclass
class Summary:
    trades: int
    wins: int
    win_rate: Optional[float]
    net_pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: Optional[float]
    avg_pnl: Optional[float]
    avg_r: Optional[float]
    r_trades: int
    max_drawdown: float
    t_stat: Optional[float]


@dataclass
class Segment:
    group: str
    key: str
    trades: int
    net_pnl: float
    avg_pnl: float
    win_rate: float
    t_vs_rest: Optional[float]
    significant: bool


def _t_welch(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    return (ma - mb) / se if se > 0 else None


def _t_mean(xs: Sequence[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return m / (sd / math.sqrt(len(xs))) if sd > 0 else None


def closed(positions: Sequence[Position]) -> List[Position]:
    return sorted((p for p in positions if not p.is_open), key=lambda p: p.closed_at)


def summarize(positions: Sequence[Position]) -> Summary:
    done = closed(positions)
    pnls = [float(p.net_pnl) for p in done]
    rs = [float(p.r_multiple) for p in done if p.r_multiple is not None]
    gp = sum(x for x in pnls if x > 0)
    gl = -sum(x for x in pnls if x < 0)
    equity, peak, dd = 0.0, 0.0, 0.0
    for x in pnls:
        equity += x
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    wins = sum(1 for x in pnls if x > 0)
    # t-stat na R, gdy SL jest znany dla większości transakcji; inaczej na PnL
    t = _t_mean(rs) if rs and len(rs) * 2 >= len(pnls) else _t_mean(pnls)
    return Summary(
        trades=len(pnls), wins=wins,
        win_rate=wins / len(pnls) if pnls else None,
        net_pnl=round(sum(pnls), 2), gross_profit=round(gp, 2), gross_loss=round(gl, 2),
        profit_factor=round(gp / gl, 3) if gl > 0 else None,
        avg_pnl=round(sum(pnls) / len(pnls), 2) if pnls else None,
        avg_r=round(sum(rs) / len(rs), 3) if rs else None, r_trades=len(rs),
        max_drawdown=round(dd, 2),
        t_stat=round(t, 2) if t is not None else None,
    )


def equity_curve(positions: Sequence[Position]) -> List[Dict[str, object]]:
    out, equity = [], Decimal(0)
    for p in closed(positions):
        equity += p.net_pnl
        out.append({"t": p.closed_at.isoformat(), "equity": float(equity)})
    return out


def after_loss_flags(positions: Sequence[Position]) -> Dict[int, bool]:
    """Czy pozycja została otwarta w ciągu 30 min po zamknięciu stratnej pozycji (sygnał „revenge”)."""
    done = closed(positions)
    loss_closes = sorted(p.closed_at for p in done if p.net_pnl < 0)
    flags = {}
    for p in done:
        # pierwsza strata zamknięta nie wcześniej niż 30 min przed wejściem — O(log n) zamiast przeglądu wszystkich
        i = bisect_left(loss_closes, p.opened_at - AFTER_LOSS_WINDOW)
        flags[id(p)] = i < len(loss_closes) and loss_closes[i] <= p.opened_at
    return flags


def segments(positions: Sequence[Position], news_times: Optional[Sequence[datetime]] = None,
             news_window: timedelta = timedelta(minutes=30)) -> List[Segment]:
    """Segmenty z testem istotności. `news_times` (posortowane) dodaje grupę „wejście przy ważnych danych”."""
    done = closed(positions)
    after = after_loss_flags(done)
    groupers: Dict[str, Callable[[Position], str]] = {
        "symbol": lambda p: p.symbol,
        "direction": lambda p: "long" if p.direction == 1 else "short",
        "hour_utc": lambda p: f"{p.opened_at.hour:02d}:00",
        "weekday": lambda p: WEEKDAYS[p.opened_at.weekday()],
        "after_loss": lambda p: "do 30 min po stracie" if after[id(p)] else "pozostałe",
    }
    if news_times:
        def at_news(p: Position) -> str:
            i = bisect_left(news_times, p.opened_at - news_window)
            hit = i < len(news_times) and news_times[i] <= p.opened_at + news_window
            return "±30 min od ważnych danych" if hit else "pozostałe"

        groupers["news_window"] = at_news
    out: List[Segment] = []
    vals = [float(p.net_pnl) for p in done]
    for group, key_fn in groupers.items():
        keys = [key_fn(p) for p in done]                  # klucz liczony raz na pozycję, nie raz na koszyk
        buckets: Dict[str, List[float]] = defaultdict(list)
        for k, v in zip(keys, vals):
            buckets[k].append(v)
        if len(buckets) < 2:
            continue
        for key, xs in buckets.items():
            rest = [v for k, v in zip(keys, vals) if k != key]
            t = _t_welch(xs, rest)
            out.append(Segment(
                group=group, key=key, trades=len(xs), net_pnl=round(sum(xs), 2),
                avg_pnl=round(sum(xs) / len(xs), 2), win_rate=sum(1 for x in xs if x > 0) / len(xs),
                t_vs_rest=round(t, 2) if t is not None else None,
                significant=len(xs) >= MIN_SEGMENT and t is not None and abs(t) >= 2,
            ))
    out.sort(key=lambda s: (not s.significant, -abs(s.t_vs_rest or 0)))
    return out


def as_dict(obj) -> Dict[str, object]:
    return asdict(obj)
