"""Laboratorium strategii daytradingowych na złocie (M15).

Metodyka (żeby nie oszukać samego siebie):
- czas: dane z MT5 w czasie serwera EET/EEST (przerwa dzienna zawsze o 00:00 serwera) → UTC → sesje
  definiowane w czasie lokalnym Londynu / Nowego Jorku (zmiany czasu w UE i USA w różnych tygodniach);
- wejścia zleceniem stop na poziomie (luka przez poziom = fill po open), stop przed targetem w tej samej świecy,
  zamknięcie najpóźniej o końcu okna — zero pozycji na noc;
- koszty: spread + poślizg w USD/oz na transakcję (domyślnie 0,40 USD ≈ ECN), analiza wrażliwości;
- parametry wybierane WYŁĄCZNIE na okresie in-sample (2012–2016), ocena na out-of-sample (2017–2022);
- punkt odniesienia: losowy kierunek w tych samych godzinach i z tym samym zarządzaniem pozycją;
- próg istotności dla rodziny testów: Bonferroni.

Użycie:
  python -m research.intraday_lab --csv data/XAUUSD-m15.csv
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from datetime import time
from itertools import product
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

SERVER_TZ = "Europe/Athens"          # EET/EEST — typowy czas serwerów MT5 („NY close”)
LONDON, NEW_YORK = "Europe/London", "America/New_York"


# ─── dane ──────────────────────────────────────────────────────────────────────

def load_m15(path: str, price_scale: float = 100.0, server_tz: str = SERVER_TZ) -> pd.DataFrame:
    """CSV (Date, open, high, low, close[, tick_volume]) w czasie serwera → indeks UTC, ceny w USD."""
    df = pd.read_csv(path)
    ts = pd.to_datetime(df.iloc[:, 0])
    local = ts.dt.tz_localize(server_tz, ambiguous="NaT", nonexistent="NaT")
    df.index = local.dt.tz_convert("UTC")
    df = df[df.index.notna()]
    cols = {c.lower(): c for c in df.columns}
    out = pd.DataFrame({k: df[cols[k]].astype(float) / price_scale for k in ("open", "high", "low", "close")},
                       index=df.index)
    return out[~out.index.duplicated()].sort_index()


def with_sessions(df: pd.DataFrame) -> pd.DataFrame:
    """Dodaj czas lokalny Londynu i NY oraz „dzień handlowy” (od 17:00 NY poprzedniego dnia)."""
    out = df.copy()
    ny = out.index.tz_convert(NEW_YORK)
    ldn = out.index.tz_convert(LONDON)
    out["ny_min"] = ny.hour * 60 + ny.minute
    out["ldn_min"] = ldn.hour * 60 + ldn.minute
    day = (ny + pd.Timedelta(hours=7)).normalize().tz_localize(None)     # 17:00 NY = początek nowego dnia
    out["day"] = day
    out["dmin"] = (out["ny_min"] - 17 * 60) % 1440                        # minuta dnia handlowego — rośnie monotonicznie
    return out


# ─── symulacja ─────────────────────────────────────────────────────────────────

@dataclass
class Order:
    """Zlecenie stop (breakout) albo market (entry=None → open następnej świecy po `start`)."""
    direction: int                    # 1 long, −1 short
    start: pd.Timestamp               # od tej świecy zlecenie jest aktywne
    deadline: pd.Timestamp            # wyjście najpóźniej na close ostatniej świecy przed deadline
    entry: Optional[float]            # poziom stop-entry; None = market
    stop: Optional[float]             # poziom SL (absolutny) albo None
    target: Optional[float]
    stop_dist: Optional[float] = None  # SL/TP liczone od faktycznej ceny wejścia (dla wejść market)
    target_dist: Optional[float] = None
    cancel_if_no_fill: Optional[pd.Timestamp] = None


@dataclass
class Trade:
    day: pd.Timestamp
    direction: int
    entry_time: pd.Timestamp
    entry: float
    exit: float
    exit_time: pd.Timestamp
    reason: str
    gross: float                      # USD/oz
    risk: Optional[float]


def simulate(bars: pd.DataFrame, order: Order) -> Optional[Trade]:
    """Jedna transakcja na świecach M15 (bars = świece tego dnia, posortowane)."""
    sub = bars[(bars.index >= order.start) & (bars.index < order.deadline)]
    if sub.empty:
        return None
    o, h, l, c = (sub[k].to_numpy() for k in ("open", "high", "low", "close"))
    idx = sub.index
    i0 = None
    entry = None
    last_fill = len(sub) if order.cancel_if_no_fill is None else int((idx < order.cancel_if_no_fill).sum())
    if order.entry is None:
        i0, entry = 0, o[0]
    else:
        for i in range(last_fill):
            if order.direction == 1 and h[i] >= order.entry:
                i0, entry = i, max(o[i], order.entry)          # luka ponad poziom → fill po open
                break
            if order.direction == -1 and l[i] <= order.entry:
                i0, entry = i, min(o[i], order.entry)
                break
    if i0 is None:
        return None
    stop = order.stop if order.stop is not None else (
        entry - order.direction * order.stop_dist if order.stop_dist else None)
    target = order.target if order.target is not None else (
        entry + order.direction * order.target_dist if order.target_dist else None)
    d = order.direction
    for i in range(i0, len(sub)):
        # w świecy wejścia sprawdzamy tylko część po wejściu — konserwatywnie: SL przed TP
        if stop is not None and ((d == 1 and l[i] <= stop) or (d == -1 and h[i] >= stop)):
            px = stop if i == i0 or (d == 1 and o[i] > stop) or (d == -1 and o[i] < stop) else o[i]
            return Trade(sub.index[0].normalize(), d, idx[i0], entry, px, idx[i], "sl", d * (px - entry),
                         abs(entry - stop))
        if target is not None and ((d == 1 and h[i] >= target) or (d == -1 and l[i] <= target)):
            if i == i0 and order.entry is not None:
                pass                                            # nie wiemy, czy TP padł po wejściu — pomijamy w tej świecy
            else:
                px = target if (d == 1 and o[i] < target) or (d == -1 and o[i] > target) else o[i]
                return Trade(sub.index[0].normalize(), d, idx[i0], entry, px, idx[i], "tp", d * (px - entry),
                             abs(entry - stop) if stop is not None else None)
    return Trade(sub.index[0].normalize(), d, idx[i0], entry, c[-1], idx[-1], "time", d * (c[-1] - entry),
                 abs(entry - stop) if stop is not None else None)


# ─── strategie ─────────────────────────────────────────────────────────────────
# Każda strategia: (dzień: DataFrame świec dnia z kolumnami sesji, params) → lista zleceń.

def _mask(day: pd.DataFrame, col: str, a: int, b: Optional[int] = None):
    """Świece od czasu lokalnego `a` (do `b`) w obrębie dnia handlowego (17:00 NY → 17:00 NY).

    Godziny NY liczymy przez minutę dnia (monotoniczną); godziny Londynu tylko w części „po północy”
    (dmin ≥ 300 = od ~03:00 LDN), żeby wieczór poprzedniego dnia nie udawał poranka."""
    if col == "ny_min":
        lo = (a - 17 * 60) % 1440
        m = day.dmin >= lo
        if b is not None:
            m &= day.dmin < ((b - 17 * 60) % 1440 or 1440)
        return m
    m = (day.dmin >= 300) & (day[col] >= a)
    if b is not None:
        m &= day[col] < b
    return m


def _at(day: pd.DataFrame, col: str, minute: int) -> Optional[pd.Timestamp]:
    m = day.index[_mask(day, col, minute)]
    return m[0] if len(m) else None


def _window(day: pd.DataFrame, col: str, a: int, b: int) -> pd.DataFrame:
    return day[_mask(day, col, a, b)]


def london_orb(day, p) -> List[Order]:
    """Opening range Londynu: zakres pierwszych `range_min` minut od 08:00 LDN, wybicie do 12:00 LDN, wyjście 16:00."""
    rng = _window(day, "ldn_min", 8 * 60, 8 * 60 + p["range_min"])
    start, cancel, end = _at(day, "ldn_min", 8 * 60 + p["range_min"]), _at(day, "ldn_min", 12 * 60), _at(day, "ldn_min", 16 * 60)
    return _orb_orders(rng, start, cancel, end, p)


def asian_breakout(day, p) -> List[Order]:
    """Zakres azjatycki 19:00–02:00 NY (≈ 00:00–07:00 LDN), wybicie 02:00–06:00 NY, wyjście 11:00 NY."""
    rng = _window(day, "ny_min", 19 * 60, 2 * 60)
    start, cancel, end = _at(day, "ny_min", 2 * 60), _at(day, "ny_min", 6 * 60), _at(day, "ny_min", 11 * 60)
    return _orb_orders(rng, start, cancel, end, p)


def ny_orb(day, p) -> List[Order]:
    """Otwarcie COMEX 08:20 NY: zakres pierwszych `range_min` minut, wybicie do 11:00 NY, wyjście 13:30 NY."""
    a = 8 * 60 + 15                                                   # świece M15: 08:15 zawiera 08:20
    rng = _window(day, "ny_min", a, a + p["range_min"])
    start, cancel, end = _at(day, "ny_min", a + p["range_min"]), _at(day, "ny_min", 11 * 60), _at(day, "ny_min", 13 * 60 + 30)
    return _orb_orders(rng, start, cancel, end, p)


def _orb_orders(rng, start, cancel, end, p) -> List[Order]:
    if rng.empty or start is None or end is None or len(rng) < 2:
        return []
    hi, lo = rng.high.max(), rng.low.min()
    width = hi - lo
    if width <= 0 or (p.get("max_width") and width > p["max_width"]):
        return []
    buf = p.get("buffer", 0.0) * width
    orders = []
    for d, lvl, opp in ((1, hi + buf, lo), (-1, lo - buf, hi)):
        stop = opp if p["stop"] == "range" else lvl - d * width * 0.5
        risk = abs(lvl - stop)
        target = lvl + d * p["tp_r"] * risk if p.get("tp_r") else None
        orders.append(Order(d, start, end, lvl, stop, target, cancel_if_no_fill=cancel))
    return orders                                                     # OCO: pierwsze wypełnione wygrywa (patrz run_day)


def london_orb_trend(day, p) -> List[Order]:
    """London ORB tylko w kierunku trendu dziennego (wczorajsze zamknięcie vs SMA20) — filtr ustalony z góry."""
    trend = day.attrs.get("trend")
    if not trend:
        return []
    return [o for o in london_orb(day, p) if o.direction == trend]


def london_orb_nr7(day, p) -> List[Order]:
    """London ORB tylko po dniu o najwęższym zakresie z 7 (NR7) — kompresja zmienności przed wybiciem."""
    return london_orb(day, p) if day.attrs.get("nr7") else []


def london_ny_continuation(day, p) -> List[Order]:
    """Kierunek sesji Londyn (03:00→08:00 NY) — kontynuacja na otwarciu NY do 11:30 NY, jeśli ruch > k·zakres dnia."""
    ldn = _window(day, "ny_min", 3 * 60, 8 * 60)
    start, end = _at(day, "ny_min", 8 * 60 + 30), _at(day, "ny_min", 11 * 60 + 30)
    if ldn.empty or start is None or end is None:
        return []
    move = ldn.close.iloc[-1] - ldn.open.iloc[0]
    rng = ldn.high.max() - ldn.low.min()
    if rng <= 0 or abs(move) < p["min_frac"] * rng:
        return []
    d = 1 if move > 0 else -1
    return [Order(d, start, end, None, None, None, stop_dist=p["stop_frac"] * rng)]


def session_drift(day, p) -> List[Order]:
    """Znany wzorzec złota: wzrosty w godzinach azjatyckich, spadki w godzinach USA. Jedna noga na dzień."""
    if p["leg"] == "asia_long":
        start, end, d = _at(day, "ny_min", 18 * 60), _at(day, "ny_min", 2 * 60), 1
    else:
        start, end, d = _at(day, "ny_min", 8 * 60 + 30), _at(day, "ny_min", 13 * 60 + 30), -1
    if start is None or end is None or end <= start:
        return []
    return [Order(d, start, end, None, None, None)]


def fade_extension(day, p) -> List[Order]:
    """Mean reversion: gdy do 10:00 NY cena odjechała od otwarcia Londynu o > k·ATR(20 dni), gra powrót do 13:30 NY."""
    ldn_open = _at(day, "ldn_min", 8 * 60)
    start, end = _at(day, "ny_min", 10 * 60), _at(day, "ny_min", 13 * 60 + 30)
    atr = day.attrs.get("atr")
    if ldn_open is None or start is None or end is None or not atr:
        return []
    ref = day.loc[ldn_open, "open"]
    last = day[day.index < start]
    if last.empty:
        return []
    dev = last.close.iloc[-1] - ref
    if abs(dev) < p["k"] * atr:
        return []
    d = -1 if dev > 0 else 1
    return [Order(d, start, end, None, None, ref, stop_dist=p["stop_atr"] * atr)]


STRATEGIES: Dict[str, Tuple[Callable, Dict[str, list]]] = {
    "london_orb": (london_orb, {"range_min": [30, 60], "stop": ["range", "mid"], "tp_r": [None, 1.5, 2.0], "buffer": [0.0, 0.1]}),
    "asian_breakout": (asian_breakout, {"stop": ["range", "mid"], "tp_r": [None, 1.0, 2.0], "buffer": [0.0, 0.1]}),
    "ny_orb": (ny_orb, {"range_min": [15, 30, 60], "stop": ["range", "mid"], "tp_r": [None, 1.5, 2.0]}),
    "london_orb_trend": (london_orb_trend, {"range_min": [30, 60], "stop": ["range", "mid"], "tp_r": [None, 2.0]}),
    "london_orb_nr7": (london_orb_nr7, {"range_min": [30, 60], "stop": ["range", "mid"], "tp_r": [None, 2.0]}),
    "london_ny_continuation": (london_ny_continuation, {"min_frac": [0.3, 0.5, 0.7], "stop_frac": [0.5, 1.0]}),
    "session_drift": (session_drift, {"leg": ["asia_long", "us_short"]}),
    "fade_extension": (fade_extension, {"k": [0.5, 0.75, 1.0], "stop_atr": [0.5, 1.0]}),
}


# ─── przebieg ──────────────────────────────────────────────────────────────────

def daily_context(df: pd.DataFrame) -> pd.DataFrame:
    """Kontekst dnia znany PRZED jego startem: trend (wczorajsze zamknięcie vs SMA20) i NR7 wczorajszego dnia."""
    d = df.groupby("day").agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    sma = d.close.rolling(20).mean()
    trend = np.sign(d.close - sma)
    rng = d.high - d.low
    nr7 = rng <= rng.rolling(7).min()
    return pd.DataFrame({"trend": trend.shift(), "nr7": nr7.shift().fillna(False).astype(bool)})


def daily_atr(df: pd.DataFrame, n: int = 20) -> pd.Series:
    d = df.groupby("day").agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    prev = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - prev).abs(), (d.low - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean().shift()                                # znane przed startem dnia


def run_day(day: pd.DataFrame, orders: Sequence[Order], rng: Optional[np.random.Generator] = None) -> Optional[Trade]:
    """OCO: z kilku zleceń wygrywa to, które wypełni się pierwsze. rng → losowy kierunek (punkt odniesienia)."""
    best = None
    for o in orders:
        if rng is not None:
            o = Order(int(rng.choice([-1, 1])), o.start, o.deadline, None, None, None,
                      stop_dist=abs(o.entry - o.stop) if o.entry is not None and o.stop is not None else o.stop_dist,
                      target_dist=(abs(o.target - o.entry) if o.target is not None and o.entry is not None else o.target_dist))
            t = simulate(day, o)
            return t
        t = simulate(day, o)
        if t is not None and (best is None or t.entry_time < best.entry_time):
            best = t
    return best


def backtest(df: pd.DataFrame, fn: Callable, params: dict, cost: float, days: Optional[Iterable] = None,
             random_seed: Optional[int] = None) -> pd.DataFrame:
    atr = daily_atr(df)
    ctx = daily_context(df)
    rng = np.random.default_rng(random_seed) if random_seed is not None else None
    rows = []
    for day, bars in df.groupby("day", sort=True):
        if days is not None and day not in days:
            continue
        bars = bars.copy()
        bars.attrs["atr"] = atr.get(day)
        if day in ctx.index:
            t = ctx.at[day, "trend"]
            bars.attrs["trend"] = int(t) if t == t and t != 0 else 0          # NaN → brak trendu
            bars.attrs["nr7"] = bool(ctx.at[day, "nr7"])
        orders = fn(bars, params)
        if not orders:
            continue
        t = run_day(bars, orders, rng)
        if t is None:
            continue
        rows.append({"day": day, "dir": t.direction, "entry": t.entry, "exit": t.exit, "reason": t.reason,
                     "gross": t.gross, "net": t.gross - cost, "risk": t.risk,
                     "net_bps": (t.gross - cost) / t.entry * 1e4})
    return pd.DataFrame(rows)


def stats(tr: pd.DataFrame) -> Dict[str, float]:
    if tr.empty:
        return {"n": 0, "mean_bps": float("nan"), "t": float("nan"), "win": float("nan"), "pf": float("nan"),
                "sharpe": float("nan"), "net_usd": 0.0}
    x = tr.net_bps.to_numpy()
    sd = x.std(ddof=1) if len(x) > 1 else float("nan")
    wins, losses = tr.net[tr.net > 0].sum(), -tr.net[tr.net < 0].sum()
    per_year = len(x) / max(1e-9, (tr.day.max() - tr.day.min()).days / 365.25) if len(x) > 1 else 0
    return {"n": len(x), "mean_bps": x.mean(), "t": x.mean() / (sd / math.sqrt(len(x))) if sd and sd > 0 else float("nan"),
            "win": (x > 0).mean(), "pf": wins / losses if losses > 0 else float("inf"),
            "sharpe": x.mean() / sd * math.sqrt(per_year) if sd and sd > 0 else float("nan"),
            "net_usd": tr.net.sum()}


def grid(space: Dict[str, list]) -> List[dict]:
    keys = list(space)
    return [dict(zip(keys, v)) for v in product(*(space[k] for k in keys))]


@dataclass
class Result:
    name: str
    params: dict
    is_stats: Dict[str, float]
    oos_stats: Dict[str, float]
    oos_by_year: Dict[int, float] = field(default_factory=dict)
    random_oos: Dict[str, float] = field(default_factory=dict)
    cost_sensitivity: Dict[float, float] = field(default_factory=dict)


def evaluate(df: pd.DataFrame, name: str, split: str = "2017-01-01", cost: float = 0.40, min_trades: int = 100) -> Result:
    fn, space = STRATEGIES[name]
    days = sorted(df.day.unique())
    cut = pd.Timestamp(split)
    is_days = {d for d in days if d < cut}
    oos_days = {d for d in days if d >= cut}
    best, best_score = None, -1e18
    for p in grid(space):
        s = stats(backtest(df, fn, p, cost, is_days))
        score = s["t"] if s["n"] >= min_trades and not math.isnan(s["t"]) else -1e18
        if score > best_score:
            best, best_score = (p, s), score
    params, is_s = best
    oos = backtest(df, fn, params, cost, oos_days)
    res = Result(name, params, is_s, stats(oos))
    if not oos.empty:
        res.oos_by_year = oos.groupby(oos.day.dt.year).net_bps.mean().round(1).to_dict()
    res.random_oos = stats(pd.concat([backtest(df, fn, params, cost, oos_days, random_seed=s) for s in range(5)]))
    for c in (0.2, 0.6, 1.0):
        res.cost_sensitivity[c] = stats(backtest(df, fn, params, c, oos_days))["mean_bps"]
    return res


def bonferroni_t(n_tests: int, alpha: float = 0.05) -> float:
    from statistics import NormalDist

    return NormalDist().inv_cdf(1 - alpha / (2 * n_tests))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--split", default="2017-01-01")
    ap.add_argument("--cost", type=float, default=0.40, help="koszt transakcji w USD/oz (spread + poślizg)")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args(argv)
    df = with_sessions(load_m15(args.csv))
    names = args.only or list(STRATEGIES)
    thr = bonferroni_t(len(STRATEGIES))
    print(f"{len(df):,} świec M15 · {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d} · koszt {args.cost} USD/oz · "
          f"próg Bonferroni t > {thr:.2f}\n")
    for n in names:
        r = evaluate(df, n, args.split, args.cost)
        o, i, rnd = r.oos_stats, r.is_stats, r.random_oos
        print(f"{n:24s} params={r.params}")
        print(f"   IS : n={i['n']:4d} śr={i['mean_bps']:6.2f} bps t={i['t']:5.2f}")
        print(f"   OOS: n={o['n']:4d} śr={o['mean_bps']:6.2f} bps t={o['t']:5.2f} win={o['win']:.0%} PF={o['pf']:.2f} "
              f"Sharpe={o['sharpe']:.2f} wynik={o['net_usd']:.0f} USD/oz")
        print(f"   losowy kierunek OOS: śr={rnd['mean_bps']:6.2f} bps t={rnd['t']:5.2f} · lata: {r.oos_by_year}")
        print(f"   koszt 0.2/0.6/1.0 USD: {', '.join(f'{v:.2f}' for v in r.cost_sensitivity.values())} bps\n")


if __name__ == "__main__":
    main()
