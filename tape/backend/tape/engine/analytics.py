"""Metryki analityka dla dashboardu: oczekiwana wartość, payoff, serie, obsunięcia, rozkład, rytm dnia.

Wszystko z zamkniętych transakcji, w walucie konta. Sharpe/Sortino liczone na dziennym wyniku w dniach
z transakcjami (bez kapitału nie ma stopy zwrotu) — to miara stabilności wyniku, nie zwrotu z kapitału.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Sequence

from .positions import Position

R_EDGES = [-3.0, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0]


def _closed(positions: Sequence[Position]) -> List[Position]:
    return sorted((p for p in positions if p.closed_at is not None), key=lambda p: p.closed_at)


def _streaks(pnls: Sequence[float]) -> Dict[str, int]:
    best_w = best_l = cur_w = cur_l = 0
    for x in pnls:
        if x > 0:
            cur_w, cur_l = cur_w + 1, 0
        elif x < 0:
            cur_w, cur_l = 0, cur_l + 1
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
    current = cur_w if cur_w else -cur_l
    return {"max_wins": best_w, "max_losses": best_l, "current": current}


def _drawdown(done: Sequence[Position]) -> Dict[str, object]:
    """Max obsunięcie i najdłuższy okres „pod wodą” (od szczytu do powrotu na szczyt), w dniach kalendarzowych."""
    equity = peak = max_dd = 0.0
    peak_day: Optional[date] = done[0].closed_at.date() if done else None
    in_dd, longest = False, 0
    for p in done:
        equity += float(p.net_pnl)
        d = p.closed_at.date()
        if equity < peak:
            in_dd = True
            max_dd = min(max_dd, equity - peak)
        else:
            if in_dd:
                longest = max(longest, (d - peak_day).days)
            in_dd, peak, peak_day = False, equity, d
    underwater = (done[-1].closed_at.date() - peak_day).days if in_dd else 0
    return {"max": round(max_dd, 2), "current": round(equity - peak, 2),
            "longest_days": max(longest, underwater), "underwater_days": underwater}


def _ratio(xs: Sequence[float], downside: bool = False) -> Optional[float]:
    if len(xs) < 10:
        return None
    m = sum(xs) / len(xs)
    if downside:
        dev = math.sqrt(sum(min(0.0, x) ** 2 for x in xs) / len(xs))
    else:
        dev = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return round(m / dev * math.sqrt(252), 2) if dev > 0 else None


def analytics(positions: Sequence[Position], rolling: int = 20) -> Dict[str, object]:
    done = _closed(positions)
    pnls = [float(p.net_pnl) for p in done]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    n = len(pnls)
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    rs = [float(p.r_multiple) for p in done if p.r_multiple is not None]

    daily: Dict[date, List[float]] = defaultdict(list)
    for p in done:
        daily[p.closed_at.date()].append(float(p.net_pnl))
    day_pnl = [sum(v) for _, v in sorted(daily.items())]

    by_hour = [{"hour": h, "trades": 0, "pnl": 0.0} for h in range(24)]
    by_wd = [{"weekday": d, "trades": 0, "pnl": 0.0} for d in range(7)]
    for p in done:
        b = by_hour[p.opened_at.hour]
        b["trades"] += 1
        b["pnl"] += float(p.net_pnl)
        w = by_wd[p.opened_at.weekday()]
        w["trades"] += 1
        w["pnl"] += float(p.net_pnl)

    hist = None
    if len(rs) >= 10:
        edges = R_EDGES
        counts = [0] * (len(edges) + 1)
        for r in rs:
            i = next((k for k, e in enumerate(edges) if r < e), len(edges))
            counts[i] += 1
        labels = [f"< {edges[0]:g}"] + [f"{a:g}…{b:g}" for a, b in zip(edges, edges[1:])] + [f"≥ {edges[-1]:g}"]
        mids = [edges[0] - 0.5] + [(a + b) / 2 for a, b in zip(edges, edges[1:])] + [edges[-1] + 0.5]
        hist = [{"label": lb, "mid": m, "count": c} for lb, m, c in zip(labels, mids, counts)]

    roll = []
    for i in range(rolling, n + 1):
        w = pnls[i - rolling:i]
        roll.append({"t": done[i - 1].closed_at.isoformat(), "expectancy": round(sum(w) / rolling, 2)})

    return {
        "trades": n,
        "expectancy": round(sum(pnls) / n, 2) if n else None,
        "avg_win": round(avg_win, 2) if avg_win is not None else None,
        "avg_loss": round(avg_loss, 2) if avg_loss is not None else None,
        "payoff": round(avg_win / -avg_loss, 2) if avg_win and avg_loss else None,
        "breakeven_win_rate": round(-avg_loss / (avg_win - avg_loss), 3) if avg_win and avg_loss else None,
        "largest_win": round(max(pnls), 2) if pnls else None,
        "largest_loss": round(min(pnls), 2) if pnls else None,
        "streaks": _streaks(pnls),
        "drawdown": _drawdown(done),
        "trading_days": len(day_pnl),
        "green_days": sum(1 for x in day_pnl if x > 0),
        "best_day": round(max(day_pnl), 2) if day_pnl else None,
        "worst_day": round(min(day_pnl), 2) if day_pnl else None,
        "sharpe_daily": _ratio(day_pnl),
        "sortino_daily": _ratio(day_pnl, downside=True),
        "daily": [{"day": d.isoformat(), "pnl": round(sum(v), 2), "trades": len(v)} for d, v in sorted(daily.items())],
        "by_hour": [{**b, "pnl": round(b["pnl"], 2)} for b in by_hour],
        "by_weekday": [{**w, "pnl": round(w["pnl"], 2)} for w in by_wd],
        "r_hist": hist,
        "rolling": {"window": rolling, "points": roll},
    }
