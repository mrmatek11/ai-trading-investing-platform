"""
Backtest strategii NWO + Stoch(7,3,2) + CVD — z fees, slippage i walk-forward.
═══════════════════════════════════════════════════════════════════════════════

Zasady symulacji (celowo pesymistyczne — lepiej zaniżyć niż zawyżyć wynik):
  - Sygnał liczony DOKŁADNIE jak na żywo: ta sama funkcja strategii, to samo okno
    (candles_per_fetch - 1 zamkniętych barów), stan NWO od zera na każdym oknie.
  - Wejście po OPEN następnej świecy (+ slippage), nie po close świecy sygnału.
  - SL/TP liczone od close świecy sygnału ± ATR * mnożnik (tak jak w alercie).
  - Gap przez SL/TP → wyjście po open. SL i TP w tej samej świecy → liczymy SL.
  - Fee od wejścia i wyjścia. Slippage na wejściu, SL i timeout (TP = limit).
  - Jedna pozycja na symbol; sygnały w trakcie pozycji są pomijane.

Walk-forward: historia dzielona na N okresów. Dla każdego okresu k>=1 parametry
(SL/TP, filtr trendu, dozwolone tiery) wybierane są TYLKO na okresach 0..k-1,
a wynik liczony na okresie k. Wynik OOS = sklejone okresy testowe — to jest
liczba, której można (ostrożnie) wierzyć. Wynik na całej historii z najlepszymi
parametrami to overfitting.

Użycie:
  # Dane z CSV (np. z https://data.binance.vision — format surowych klines też działa)
  python backtest.py --csv data/BTCUSDT-1h.csv --symbol BTC/USDT --timeframe 1h

  # Pobranie historii przez ccxt (zapis do data/ jako cache)
  python backtest.py --fetch --symbol BTC/USDT --timeframe 1h --since 2023-01-01

  # Eksport transakcji
  python backtest.py --csv ... --trades-out trades.csv
"""

from __future__ import annotations

import argparse
import itertools
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategy.neural_weight_oscillator import calc_stoch  # noqa: E402

ALL_TIERS = ("CONFLUENCE", "STOCH STRICT+NWO", "STOCH+NWO", "STOCH-ONLY")
TIER_SETS = {
    "all": frozenset(ALL_TIERS),
    "no_stoch_only": frozenset(ALL_TIERS[:3]),
    "high_only": frozenset(ALL_TIERS[:2]),
}


# ═══════════════════════════════════════════════════════════════════════════════
# DATA
# ═══════════════════════════════════════════════════════════════════════════════

def load_csv(path: str) -> pd.DataFrame:
    """Wczytaj OHLCV z CSV.

    Obsługuje:
      - CSV z nagłówkiem: timestamp/open_time/date + open, high, low, close, volume
      - surowe klines z data.binance.vision (bez nagłówka, czas w ms lub µs)
    """
    raw = pd.read_csv(path, header=None)
    first = str(raw.iloc[0, 0])
    if not first.replace(".", "", 1).isdigit():
        raw = pd.read_csv(path)
        cols = {c.lower().strip(): c for c in raw.columns}
        ts_col = next((cols[c] for c in ("timestamp", "open_time", "date", "datetime", "time") if c in cols), None)
        if ts_col is None:
            raise ValueError(f"{path}: brak kolumny czasu (timestamp/open_time/date)")
        df = pd.DataFrame({k: raw[cols[k]] for k in ("open", "high", "low", "close", "volume") if k in cols})
        ts = raw[ts_col]
    else:
        df = raw.iloc[:, 1:6].copy()
        df.columns = ["open", "high", "low", "close", "volume"]
        ts = raw.iloc[:, 0]

    if "volume" not in df:
        df["volume"] = 0.0
    if pd.api.types.is_numeric_dtype(ts):
        # s / ms / µs — data.binance.vision przeszło na µs od 2025
        unit = "us" if ts.iloc[0] > 1e14 else ("ms" if ts.iloc[0] > 1e11 else "s")
        index = pd.to_datetime(ts, unit=unit, utc=True)
    else:
        index = pd.to_datetime(ts, utc=True)
    df.index = pd.DatetimeIndex(index, name="timestamp")
    df = df.astype(float)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


def fetch_history(symbol: str, timeframe: str, since: str, exchange_id: str = "binance",
                  cache_dir: str = "data") -> pd.DataFrame:
    """Pobierz pełną historię przez ccxt (paginacja) i zapisz do CSV."""
    import ccxt

    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    since_ms = exchange.parse8601(pd.Timestamp(since, tz="UTC").isoformat())
    tf_ms = exchange.parse_timeframe(timeframe) * 1000
    rows: List[list] = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        since_ms = batch[-1][0] + tf_ms
        print(f"\r[fetch] {symbol} {timeframe}: {len(rows)} świec do "
              f"{pd.Timestamp(batch[-1][0], unit='ms', tz='UTC'):%Y-%m-%d}", end="", flush=True)
        if len(batch) < 2 or since_ms > exchange.milliseconds():
            break
    print()
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{symbol.replace('/', '')}-{timeframe}.csv")
    df.to_csv(path, index=False)
    print(f"[fetch] zapisano {path}")
    return load_csv(path)


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATION (identyczna z live)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RawSignal:
    bar: int                 # indeks świecy sygnałowej (zamkniętej)
    direction: int           # +1 LONG, -1 SHORT
    source: str
    against_trend: bool
    close: float
    atr: float


def candidate_bars(df: pd.DataFrame) -> np.ndarray:
    """Bary, na których strategia W OGÓLE może dać sygnał.

    Każdy tier wymaga triggera Stochastic (crossover w strefie albo wejście/wyjście
    ze strefy). Stoch(7,3,2) to czyste rolling-window, więc na pełnej historii ma
    identyczne wartości jak na oknie live — drogie NWO liczymy tylko tam.
    """
    k, d = calc_stoch(df["high"], df["low"], df["close"], 7, 3, 2)
    kp, dp = k.shift(1), d.shift(1)
    cross_up = (kp <= dp) & (k > d)
    cross_dn = (kp >= dp) & (k < d)
    trig = (
        (cross_up & (k < 30)) | (cross_dn & (k > 70))
        | ((kp >= 20) & (k < 20)) | ((kp <= 80) & (k > 80))
        | ((kp < 30) & (k >= 30) & cross_up) | ((kp > 70) & (k <= 70) & cross_dn)
    )
    return np.flatnonzero(trig.to_numpy())


def _backtest_mode():
    """Ustaw strategię w tryb backtestu; zwraca funkcję przywracającą poprzedni stan."""
    import strategy.custom_strategy as cs
    saved = (cs._use_closed_bar, cs._allow_stoch_only, cs._use_sentiment, cs.TREND_FILTER_MODE)
    cs.set_closed_bar_mode(False)     # okno kończy się na zamkniętym barze sygnałowym
    cs.set_stoch_only_enabled(True)   # generujemy wszystkie tiery, filtr w symulacji
    cs.set_sentiment_enabled(False)
    cs.TREND_FILTER_MODE = "off"      # flaga against_trend zapisana, filtr w symulacji

    def restore():
        cs._use_closed_bar, cs._allow_stoch_only, cs._use_sentiment, cs.TREND_FILTER_MODE = saved
    return restore


def _signals_for_bars(args) -> List[RawSignal]:
    df, bars, symbol, timeframe, window = args
    import strategy.custom_strategy as cs
    restore = _backtest_mode()
    out = []
    try:
        for t in bars:
            chunk = df.iloc[max(0, t - window + 1): t + 1]
            for sig in cs.strategy_nwo_stoch_cvd(chunk, symbol, timeframe):
                ex = sig.extra_data
                out.append(RawSignal(
                    bar=int(t),
                    direction=1 if sig.signal_type == "LONG" else -1,
                    source=ex["source"],
                    against_trend=bool(ex["against_trend"]),
                    close=float(sig.price),
                    atr=float(ex["atr"]),
                ))
    finally:
        restore()
    return out


def generate_signals(df: pd.DataFrame, symbol: str, timeframe: str,
                     window: int = 499, workers: int = 1) -> List[RawSignal]:
    """Wygeneruj wszystkie sygnały (wszystkie tiery, bez filtra trendu).

    window = liczba ZAMKNIĘTYCH barów widziana przez strategię na żywo
             (candles_per_fetch - 1, bo ostatnia pobrana świeca jest w trakcie).
    """
    bars = [int(b) for b in candidate_bars(df) if b >= 119]
    if not bars:
        return []
    if workers <= 1:
        return _signals_for_bars((df, bars, symbol, timeframe, window))
    chunks = [bars[i::workers] for i in range(workers)]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        parts = ex.map(_signals_for_bars, [(df, c, symbol, timeframe, window) for c in chunks])
        signals = [s for p in parts for s in p]
    return sorted(signals, key=lambda s: (s.bar, -s.direction))


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE SIMULATION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Params:
    sl_mult: float = 3.0
    tp_mult: float = 4.5
    trend_mode: str = "block"              # "block" | "off" ("alert" handluje jak "off")
    tiers: frozenset = TIER_SETS["no_stoch_only"]
    fee: float = 0.001                     # 0.1% / stronę (Binance spot taker)
    slippage: float = 0.0005               # 0.05% na zleceniach market
    max_bars: Optional[int] = None         # timeout pozycji w barach
    risk_per_trade: float = 0.01           # 1% equity ryzyka na SL
    max_leverage: float = 3.0

    def label(self) -> str:
        tiers = next((k for k, v in TIER_SETS.items() if v == self.tiers), "+".join(sorted(self.tiers)))
        return f"SL {self.sl_mult:g}ATR / TP {self.tp_mult:g}ATR | trend={self.trend_mode} | tiers={tiers}"


@dataclass
class Trade:
    entry_bar: int
    exit_bar: int
    direction: int
    source: str
    against_trend: bool
    entry: float
    exit: float
    sl: float
    tp: float
    reason: str              # SL | TP | TIMEOUT | END
    ret: float = 0.0         # zwrot netto na notional (po fees)
    r: float = 0.0           # zwrot w jednostkach ryzyka (R)
    size: float = 0.0        # notional jako ułamek equity


def simulate(df: pd.DataFrame, signals: Sequence[RawSignal], p: Params,
             start: int = 0, end: Optional[int] = None) -> List[Trade]:
    """Symuluj transakcje dla sygnałów z przedziału [start, end)."""
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    lo = df["low"].to_numpy()
    c = df["close"].to_numpy()
    n = len(df)
    end = n if end is None else end

    trades: List[Trade] = []
    busy_until = -1
    for s in signals:
        if s.bar < start or s.bar >= end or s.bar <= busy_until or s.bar + 1 >= n:
            continue
        if s.source not in p.tiers or s.atr <= 0:
            continue
        if p.trend_mode == "block" and s.against_trend:
            continue

        d = s.direction
        sl = s.close - d * s.atr * p.sl_mult
        tp = s.close + d * s.atr * p.tp_mult
        e = s.bar + 1
        entry = o[e] * (1 + d * p.slippage)
        # Wejście już za SL/TP (gap) — alert byłby nieaktualny, pomijamy
        if (entry - sl) * d <= 0 or (tp - entry) * d <= 0:
            continue

        exit_px, reason, j = None, "END", n - 1
        last = n - 1 if p.max_bars is None else min(n - 1, e + p.max_bars - 1)
        for j in range(e, last + 1):
            if j > e:  # gap na otwarciu kolejnej świecy
                if (o[j] - sl) * d <= 0:
                    exit_px, reason = o[j] * (1 - d * p.slippage), "SL"
                    break
                if (o[j] - tp) * d >= 0:
                    exit_px, reason = o[j], "TP"
                    break
            hit_sl = (lo[j] <= sl) if d > 0 else (h[j] >= sl)
            hit_tp = (h[j] >= tp) if d > 0 else (lo[j] <= tp)
            if hit_sl:  # SL ma pierwszeństwo, gdy oba w tej samej świecy
                exit_px, reason = sl * (1 - d * p.slippage), "SL"
                break
            if hit_tp:
                exit_px, reason = tp, "TP"
                break
        if exit_px is None:
            reason = "TIMEOUT" if p.max_bars is not None and j < n - 1 else "END"
            exit_px = c[j] * (1 - d * p.slippage)

        ret = d * (exit_px - entry) / entry - 2 * p.fee
        risk = abs(entry - sl) / entry
        size = min(p.risk_per_trade / risk, p.max_leverage)
        trades.append(Trade(
            entry_bar=e, exit_bar=j, direction=d, source=s.source, against_trend=s.against_trend,
            entry=entry, exit=exit_px, sl=sl, tp=tp, reason=reason,
            ret=ret, r=ret / risk, size=size,
        ))
        busy_until = j
    return trades


# ═══════════════════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════════════════

def metrics(trades: Sequence[Trade], bars: Optional[int] = None) -> Dict[str, float]:
    if not trades:
        return {"trades": 0, "win_rate": float("nan"), "avg_r": float("nan"), "t_stat": float("nan"),
                "profit_factor": float("nan"),
                "net_return": 0.0, "max_dd": 0.0, "avg_bars": float("nan"), "exposure": 0.0}
    rets = np.array([t.ret for t in trades])
    rs = np.array([t.r for t in trades])
    equity = np.cumprod(1 + np.array([t.size for t in trades]) * rets)
    peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    gains, losses = rs[rs > 0].sum(), -rs[rs < 0].sum()
    held = np.array([t.exit_bar - t.entry_bar + 1 for t in trades])
    return {
        "trades": len(trades),
        "win_rate": float((rets > 0).mean()),
        "avg_r": float(rs.mean()),
        # t-stat średniego R: |t| < 2 → wynik nieodróżnialny od szumu
        "t_stat": float(rs.mean() / (rs.std(ddof=1) / math.sqrt(len(rs)))) if len(rs) > 1 and rs.std(ddof=1) > 0 else float("nan"),
        "profit_factor": float(gains / losses) if losses > 0 else float("inf"),
        "net_return": float(equity[-1] - 1),
        "max_dd": float((equity / peak - 1).min()),
        "avg_bars": float(held.mean()),
        "exposure": float(held.sum() / bars) if bars else float("nan"),
    }


def _fmt_row(name: str, m: Dict[str, float]) -> str:
    if m["trades"] == 0:
        return f"  {name:<28} {'0':>6}  — brak transakcji"
    pf = "inf" if math.isinf(m["profit_factor"]) else f"{m['profit_factor']:.2f}"
    return (f"  {name:<28} {m['trades']:>6} {m['win_rate']:>7.1%} {m['avg_r']:>+7.2f}R {m['t_stat']:>+6.1f} {pf:>6} "
            f"{m['net_return']:>+9.1%} {m['max_dd']:>8.1%} {m['avg_bars']:>7.1f}")


HEADER = (f"  {'':<28} {'trades':>6} {'win%':>7} {'avg R':>8} {'t':>6} {'PF':>6} "
          f"{'return':>9} {'maxDD':>8} {'bars':>7}")


# ═══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD
# ═══════════════════════════════════════════════════════════════════════════════

def param_grid(base: Params) -> List[Params]:
    grid = []
    for sl, tp, trend, tiers in itertools.product(
            (2.0, 3.0, 4.0), (3.0, 4.5, 6.0), ("block", "off"), TIER_SETS.values()):
        grid.append(replace(base, sl_mult=sl, tp_mult=tp, trend_mode=trend, tiers=tiers))
    return grid


def _score(m: Dict[str, float], min_trades: int) -> float:
    """Kryterium wyboru parametrów: avg R (niezależne od sizingu), kara za małą próbę."""
    if m["trades"] < min_trades:
        return -math.inf
    return m["avg_r"]


@dataclass
class FoldResult:
    fold: int
    start: pd.Timestamp
    end: pd.Timestamp
    params: Optional[Params]
    trades: List[Trade] = field(default_factory=list)


def walk_forward(df: pd.DataFrame, signals: Sequence[RawSignal], base: Params,
                 folds: int = 5, min_trades: int = 15) -> List[FoldResult]:
    n = len(df)
    edges = np.linspace(0, n, folds + 1).astype(int)
    grid = param_grid(base)
    results = []
    for k in range(1, folds):
        is_start, is_end = 0, edges[k]           # anchored: cała przeszłość
        oos_start, oos_end = edges[k], edges[k + 1]
        best, best_score = None, -math.inf
        for p in grid:
            sc = _score(metrics(simulate(df, signals, p, is_start, is_end)), min_trades)
            if sc > best_score:
                best, best_score = p, sc
        trades = simulate(df, signals, best, oos_start, oos_end) if best else []
        results.append(FoldResult(k, df.index[oos_start], df.index[oos_end - 1], best, trades))
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def run_report(df: pd.DataFrame, symbol: str, timeframe: str, base: Params, window: int,
               folds: int, workers: int, trades_out: Optional[str] = None) -> Dict[str, object]:
    n = len(df)
    print(f"\n{'═' * 96}\n{symbol} {timeframe}: {n} świec, "
          f"{df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d} | fee {base.fee:.2%}/stronę, "
          f"slippage {base.slippage:.2%}\n{'═' * 96}")

    signals = generate_signals(df, symbol, timeframe, window=window, workers=workers)
    print(f"Sygnały (wszystkie tiery, bez filtra trendu): {len(signals)}")

    bh = df["close"].iloc[-1] / df["close"].iloc[window] - 1
    print(f"Buy & hold w tym okresie: {bh:+.1%}\n")

    print("1) Konfiguracja live (domyślna po poprawkach):", base.label())
    print(HEADER)
    live_trades = simulate(df, signals, base)
    print(_fmt_row("RAZEM", metrics(live_trades, n)))
    for d, name in ((1, "LONG"), (-1, "SHORT")):
        print(_fmt_row(f"  {name}", metrics([t for t in live_trades if t.direction == d], n)))

    print("\n2) Jakość tierów (każdy tier osobno, SL/TP jak live, bez filtra trendu):")
    print(HEADER)
    for tier in ALL_TIERS:
        p = replace(base, tiers=frozenset({tier}), trend_mode="off")
        print(_fmt_row(tier, metrics(simulate(df, signals, p), n)))
    p_off = replace(base, tiers=TIER_SETS["all"], trend_mode="off")
    all_off = simulate(df, signals, p_off)
    print(_fmt_row("zgodnie z trendem", metrics([t for t in all_off if not t.against_trend], n)))
    print(_fmt_row("pod trend", metrics([t for t in all_off if t.against_trend], n)))

    print(f"\n3) Walk-forward ({folds} okresów, anchored; parametry wybierane tylko na przeszłości):")
    wf = walk_forward(df, signals, base, folds=folds)
    print(HEADER)
    oos: List[Trade] = []
    for fr in wf:
        name = f"OOS {fr.start:%Y-%m-%d}→{fr.end:%Y-%m-%d}"
        print(_fmt_row(name, metrics(fr.trades)))
        print(f"      wybrane: {fr.params.label() if fr.params else 'brak (za mało transakcji in-sample)'}")
        oos.extend(fr.trades)
    oos_m = metrics(oos)
    print(_fmt_row("OOS RAZEM", oos_m))

    verdict = _verdict(oos_m)
    print(f"\nWERDYKT: {verdict}\n")

    if trades_out:
        rows = [{
            "entry_time": df.index[t.entry_bar], "exit_time": df.index[t.exit_bar],
            "side": "LONG" if t.direction > 0 else "SHORT", "source": t.source,
            "against_trend": t.against_trend, "entry": t.entry, "exit": t.exit, "sl": t.sl, "tp": t.tp,
            "reason": t.reason, "ret": t.ret, "r": t.r,
        } for t in live_trades]
        pd.DataFrame(rows).to_csv(trades_out, index=False)
        print(f"Transakcje (konfiguracja live) zapisane: {trades_out}")

    return {"signals": signals, "live": metrics(live_trades, n), "oos": oos_m, "walk_forward": wf}


def _verdict(m: Dict[str, float]) -> str:
    if m["trades"] < 30:
        return f"ZA MAŁO DANYCH ({m['trades']} transakcji OOS) — weź dłuższą historię lub więcej symboli."
    head = f"{m['avg_r']:+.2f}R/transakcję OOS po kosztach (t={m['t_stat']:+.1f}, PF {m['profit_factor']:.2f})"
    if m["t_stat"] >= 2 and m["profit_factor"] > 1.2:
        return f"Obiecujące: {head}. Potwierdź na innych symbolach i TF, zanim uwierzysz."
    if m["avg_r"] > 0:
        return (f"Nieistotne statystycznie: {head}. Przy |t| < 2 taki wynik daje też losowy rynek "
                "— to nie jest dowód przewagi.")
    return f"BRAK PRZEWAGI: {head}."


def main(argv: Optional[Iterable[str]] = None):
    ap = argparse.ArgumentParser(description="Backtest NWO+Stoch+CVD z fees, slippage i walk-forward")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="Plik CSV z OHLCV")
    src.add_argument("--fetch", action="store_true", help="Pobierz historię przez ccxt")
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--timeframe", "-tf", default="1h")
    ap.add_argument("--since", default="2023-01-01", help="Start historii dla --fetch")
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--candles", type=int, default=500, help="candles_per_fetch bota (okno strategii)")
    ap.add_argument("--fee", type=float, default=0.001, help="Fee na stronę (0.001 = 0.1%%)")
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--sl", type=float, default=3.0, help="SL w ATR")
    ap.add_argument("--tp", type=float, default=4.5, help="TP w ATR")
    ap.add_argument("--trend-filter", choices=["block", "off", "alert"], default="block")
    ap.add_argument("--stoch-only", action="store_true", help="Uwzględnij tier STOCH-ONLY w konfiguracji live")
    ap.add_argument("--max-bars", type=int, default=None, help="Timeout pozycji w barach")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--trades-out", default=None)
    args = ap.parse_args(argv)

    if args.csv:
        df = load_csv(args.csv)
    else:
        df = fetch_history(args.symbol, args.timeframe, args.since, args.exchange)

    base = Params(
        sl_mult=args.sl, tp_mult=args.tp,
        trend_mode="off" if args.trend_filter == "alert" else args.trend_filter,
        tiers=TIER_SETS["all"] if args.stoch_only else TIER_SETS["no_stoch_only"],
        fee=args.fee, slippage=args.slippage, max_bars=args.max_bars,
    )
    run_report(df, args.symbol, args.timeframe, base, window=args.candles - 1,
               folds=args.folds, workers=args.workers, trades_out=args.trades_out)


if __name__ == "__main__":
    main()
