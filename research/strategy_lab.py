"""
Laboratorium strategii — kilka klasycznych podejść na tym samym silniku co backtest.py.
═══════════════════════════════════════════════════════════════════════════════════

Każda strategia zwraca listę RawSignal (bar sygnału, kierunek, ATR, flaga „pod trend”).
Transakcje symuluje backtest.simulate(): wejście na open następnej świecy, fees + slippage,
SL przed TP w tej samej świecy, opcjonalny timeout. Parametry wyjścia (SL/TP w ATR, filtr
trendu, timeout) wybiera walk-forward tylko na przeszłości.

Punkt odniesienia: „random” — losowe wejścia w losowym kierunku, tyle samo co w typowej
strategii. Strategia bez przewagi powinna wypaść jak random (≈ −koszty).

Uwaga na wielokrotne testowanie: przy N strategiach × M interwałach część wyników przekroczy
t = 2 przypadkiem. Raport pokazuje próg Bonferroniego dla całej rodziny testów.

Użycie:
  python research/strategy_lab.py --csv data/XAUUSD-h1.csv --symbol XAU/USD --timeframe 1h
  python research/strategy_lab.py --csv data/XAUUSD-d1.csv --timeframe 1d --strategies donchian,rsi2_pullback
"""

from __future__ import annotations

import argparse
import itertools
import math
import os
import sys
from dataclasses import replace
from statistics import NormalDist
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest as bt  # noqa: E402
from strategy.neural_weight_oscillator import calc_atr, calc_rsi, ema  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _context(df: pd.DataFrame):
    """ATR i trend (EMA20 vs EMA100 — ta sama definicja co w strategii bota)."""
    atr = calc_atr(df["high"], df["low"], df["close"], 14).to_numpy()
    e20, e100 = ema(df["close"], 20).to_numpy(), ema(df["close"], 100).to_numpy()
    trend = np.where(np.isnan(e100), 0, np.sign(e20 - e100))
    return atr, trend


def _signals(df: pd.DataFrame, longs: np.ndarray, shorts: np.ndarray, source: str,
             warmup: int = 120) -> List[bt.RawSignal]:
    atr, trend = _context(df)
    close = df["close"].to_numpy()
    out = []
    for i in range(warmup, len(df) - 1):
        for d, flag in ((1, longs[i]), (-1, shorts[i])):
            if flag and atr[i] > 0 and not np.isnan(atr[i]):
                out.append(bt.RawSignal(bar=i, direction=d, source=source,
                                        against_trend=bool(trend[i] == -d),
                                        close=float(close[i]), atr=float(atr[i])))
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGIES
# ═══════════════════════════════════════════════════════════════════════════════

def donchian(df: pd.DataFrame, n: int = 20) -> List[bt.RawSignal]:
    """Trend following: zamknięcie ponad max(high) / pod min(low) z ostatnich n świec."""
    hi = df["high"].rolling(n).max().shift(1)
    lo = df["low"].rolling(n).min().shift(1)
    c = df["close"]
    return _signals(df, (c > hi).to_numpy(), (c < lo).to_numpy(), "donchian")


def rsi2_pullback(df: pd.DataFrame) -> List[bt.RawSignal]:
    """Cofnięcie w trendzie (Connors): trend EMA50/EMA200, RSI(2) skrajnie wyprzedany/wykupiony."""
    c = df["close"]
    e50, e200 = ema(c, 50), ema(c, 200)
    rsi2 = calc_rsi(c, 2)
    up = (e50 > e200) & (c > e200)
    dn = (e50 < e200) & (c < e200)
    return _signals(df, (up & (rsi2 < 10)).to_numpy(), (dn & (rsi2 > 90)).to_numpy(), "rsi2_pullback")


def bollinger_reversion(df: pd.DataFrame) -> List[bt.RawSignal]:
    """Powrót do średniej: zamknięcie poza wstęgą Bollingera (20, 2) + RSI(14) skrajne."""
    c = df["close"]
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    rsi = calc_rsi(c, 14)
    return _signals(df, ((c < mid - 2 * sd) & (rsi < 30)).to_numpy(),
                    ((c > mid + 2 * sd) & (rsi > 70)).to_numpy(), "bollinger_reversion")


TZ_OFFSET_HOURS = 0  # przesunięcie czasu danych względem UTC (np. serwer MT5: +2 / +3)


def london_breakout(df: pd.DataFrame) -> List[bt.RawSignal]:
    """Wybicie zakresu azjatyckiego (00:00–06:59 UTC) między 07:00 a 11:59 UTC; max 1 sygnał dziennie.

    Tylko dla interwałów ≤ 1h. Czas danych przesuwany o TZ_OFFSET_HOURS do UTC.
    """
    idx = df.index - pd.Timedelta(hours=TZ_OFFSET_HOURS)
    if len(idx) < 3 or (idx[1] - idx[0]) > pd.Timedelta(hours=1):
        return []
    hours = idx.hour
    day = idx.normalize()
    asia = hours < 7
    frame = pd.DataFrame({"high": df["high"].where(asia), "low": df["low"].where(asia), "day": day})
    rng_hi = frame.groupby("day")["high"].transform("max")
    rng_lo = frame.groupby("day")["low"].transform("min")
    window = (hours >= 7) & (hours < 12)
    c = df["close"]
    longs = (window & (c > rng_hi)).to_numpy()
    shorts = (window & (c < rng_lo)).to_numpy()
    # tylko pierwsze wybicie w danym dniu
    first = np.zeros(len(df), dtype=bool)
    seen = set()
    days = day.to_numpy()
    for i in np.flatnonzero(longs | shorts):
        if days[i] not in seen:
            seen.add(days[i])
            first[i] = True
    return _signals(df, longs & first, shorts & first, "london_breakout")


def ts_momentum(df: pd.DataFrame, lookback: int = 60, every: int = 20) -> List[bt.RawSignal]:
    """Momentum szeregu czasowego: co `every` świec kierunek = znak zwrotu z `lookback` świec."""
    c = df["close"]
    ret = c / c.shift(lookback) - 1
    at = np.zeros(len(df), dtype=bool)
    at[np.arange(lookback, len(df), every)] = True
    r = ret.to_numpy()
    return _signals(df, at & (r > 0), at & (r < 0), "ts_momentum")


def random_baseline(df: pd.DataFrame, count: int = 400, seed: int = 0) -> List[bt.RawSignal]:
    """Losowe wejścia w losowym kierunku — punkt odniesienia „brak przewagi”."""
    rng = np.random.default_rng(seed)
    bars = np.sort(rng.choice(np.arange(120, len(df) - 1), size=min(count, len(df) - 121), replace=False))
    longs = np.zeros(len(df), dtype=bool)
    shorts = np.zeros(len(df), dtype=bool)
    for b in bars:
        (longs if rng.random() < 0.5 else shorts)[b] = True
    return _signals(df, longs, shorts, "random")


STRATEGIES: Dict[str, Callable[[pd.DataFrame], List[bt.RawSignal]]] = {
    "donchian": donchian,
    "rsi2_pullback": rsi2_pullback,
    "bollinger_reversion": bollinger_reversion,
    "london_breakout": london_breakout,
    "ts_momentum": ts_momentum,
    "random": random_baseline,
}


# ═══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD
# ═══════════════════════════════════════════════════════════════════════════════

def grid(base: bt.Params, source: str, max_bars_opts: Sequence[Optional[int]]) -> List[bt.Params]:
    return [replace(base, sl_mult=sl, tp_mult=tp, trend_mode=tr, max_bars=mb, tiers=frozenset({source}))
            for sl, tp, tr, mb in itertools.product((1.5, 2.0, 3.0), (2.0, 3.0, 4.5, 6.0),
                                                    ("block", "off"), max_bars_opts)]


def walk_forward(df: pd.DataFrame, signals: Sequence[bt.RawSignal], params: List[bt.Params],
                 folds: int = 5, min_trades: int = 15):
    edges = np.linspace(0, len(df), folds + 1).astype(int)
    oos: List[bt.Trade] = []
    chosen = []
    for k in range(1, folds):
        best, best_score = None, -math.inf
        for p in params:
            m = bt.metrics(bt.simulate(df, signals, p, 0, edges[k]))
            if m["trades"] >= min_trades and m["avg_r"] > best_score:
                best, best_score = p, m["avg_r"]
        chosen.append(best)
        if best is not None:
            oos.extend(bt.simulate(df, signals, best, edges[k], edges[k + 1]))
    return oos, chosen


def run(df: pd.DataFrame, names: Sequence[str], timeframe: str, base: bt.Params, folds: int = 5,
        nwo_signals: Optional[List[bt.RawSignal]] = None) -> pd.DataFrame:
    max_bars_opts = (None, {"1d": 20, "4h": 60}.get(timeframe, 120))
    rows = []
    for name in names:
        signals = nwo_signals if name == "nwo_stoch_cvd" else STRATEGIES[name](df)
        source = "nwo" if name == "nwo_stoch_cvd" else name
        if name == "nwo_stoch_cvd":
            signals = [replace(s, source="nwo") for s in signals if s.source != "STOCH-ONLY"]
        default = replace(base, sl_mult=2.0, tp_mult=3.0, trend_mode="off", tiers=frozenset({source}))
        full = bt.metrics(bt.simulate(df, signals, default), len(df))
        oos, chosen = walk_forward(df, signals, grid(base, source, max_bars_opts), folds)
        m = bt.metrics(oos)
        rows.append({
            "strategy": name, "signals": len(signals),
            "full_trades": full["trades"], "full_avg_r": full["avg_r"], "full_t": full["t_stat"],
            "oos_trades": m["trades"], "oos_avg_r": m["avg_r"], "oos_t": m["t_stat"],
            "oos_pf": m["profit_factor"], "oos_return": m["net_return"], "oos_max_dd": m["max_dd"],
            "params": " / ".join(f"{p.sl_mult:g}/{p.tp_mult:g}/{p.trend_mode}/{p.max_bars or '-'}"
                                 if p else "—" for p in chosen),
        })
    return pd.DataFrame(rows)


def bonferroni_t(tests: int, alpha: float = 0.05) -> float:
    """Jednostronny próg t dla rodziny `tests` testów (przybliżenie normalne)."""
    return NormalDist().inv_cdf(1 - alpha / tests)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Porównanie strategii z walk-forwardem")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--symbol", default="XAU/USD")
    ap.add_argument("--timeframe", "-tf", default="1h")
    ap.add_argument("--strategies", default=",".join(STRATEGIES))
    ap.add_argument("--with-nwo", action="store_true", help="Dołącz strategię bota (wolne na H1)")
    ap.add_argument("--fee", type=float, default=0.0001)
    ap.add_argument("--slippage", type=float, default=0.00005)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--family-size", type=int, default=0, help="Łączna liczba testów do progu Bonferroniego")
    ap.add_argument("--out", default=None, help="Zapisz tabelę do CSV")
    ap.add_argument("--tz-offset", type=float, default=0, help="Czas danych minus UTC w godzinach (MT5: 2 lub 3)")
    args = ap.parse_args(argv)

    global TZ_OFFSET_HOURS
    TZ_OFFSET_HOURS = args.tz_offset
    df = bt.load_csv(args.csv)
    names = [s for s in args.strategies.split(",") if s]
    nwo = None
    if args.with_nwo:
        names.append("nwo_stoch_cvd")
        nwo = bt.generate_signals(df, args.symbol, args.timeframe, workers=os.cpu_count() or 1)
    base = bt.Params(fee=args.fee, slippage=args.slippage)
    table = run(df, names, args.timeframe, base, args.folds, nwo)

    bh = df["close"].iloc[-1] / df["close"].iloc[120] - 1
    print(f"\n{args.symbol} {args.timeframe}: {len(df)} świec {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d} "
          f"| buy & hold {bh:+.1%} | fee {args.fee:.3%} + slippage {args.slippage:.3%} na stronę")
    fam = args.family_size or len(names)
    print(f"Próg istotności dla rodziny {fam} testów (Bonferroni, α=5%): t > {bonferroni_t(fam):.2f}\n")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        view = table.drop(columns=["params"]).copy()
        for c in ("full_avg_r", "oos_avg_r", "full_t", "oos_t", "oos_pf"):
            view[c] = view[c].map(lambda v: f"{v:+.2f}")
        for c in ("oos_return", "oos_max_dd"):
            view[c] = view[c].map(lambda v: f"{v:+.1%}")
        print(view.to_string(index=False))
        print("\nParametry wybrane w kolejnych okresach (SL/TP/trend/timeout):")
        for _, r in table.iterrows():
            print(f"  {r['strategy']:<20} {r['params']}")
    if args.out:
        table.assign(symbol=args.symbol, timeframe=args.timeframe).to_csv(args.out, index=False)


if __name__ == "__main__":
    main()
