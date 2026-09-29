import numpy as np
import pandas as pd
import pytest

from research import intraday_lab as lab


def bars(start_utc, rows):
    idx = pd.date_range(start_utc, periods=len(rows), freq="15min", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    return lab.with_sessions(df)


def test_stop_entry_then_stop_loss():
    b = bars("2020-01-06 10:00", [(100, 100.5, 99.5, 100), (100, 101.2, 100, 101), (101, 101.1, 98.9, 99)])
    o = lab.Order(1, b.index[0], b.index[-1] + pd.Timedelta("15min"), 101.0, 99.0, 105.0)
    t = lab.simulate(b, o)
    assert t.entry == 101.0 and t.entry_time == b.index[1] and t.reason == "sl" and t.gross == pytest.approx(-2.0)


def test_gap_through_level_fills_at_open_and_time_exit():
    b = bars("2020-01-06 10:00", [(100, 100.4, 99.8, 100.2), (102, 102.5, 101.5, 102.2), (102.2, 102.6, 102, 102.4)])
    o = lab.Order(1, b.index[0], b.index[-1] + pd.Timedelta("15min"), 101.0, 99.0, None)
    t = lab.simulate(b, o)
    assert t.entry == 102 and t.reason == "time" and t.exit == 102.4


def test_same_bar_stop_and_target_counts_stop():
    b = bars("2020-01-06 10:00", [(100, 100, 100, 100), (100, 103, 97, 100)])
    o = lab.Order(1, b.index[0], b.index[-1] + pd.Timedelta("15min"), None, None, None, stop_dist=2, target_dist=2)
    assert lab.simulate(b, o).reason == "sl"


def test_oco_first_fill_wins():
    b = bars("2020-01-06 10:00", [(100, 100.2, 98.5, 99), (99, 101.5, 99, 101)])
    end = b.index[-1] + pd.Timedelta("15min")
    t = lab.run_day(b, [lab.Order(1, b.index[0], end, 101, 99, None), lab.Order(-1, b.index[0], end, 99, 101, None)])
    assert t.direction == -1


def test_session_windows_follow_local_time_across_dst():
    # 2020-03-10: USA już na czasie letnim, UK jeszcze nie → 08:00 LDN = 04:00 NY (a nie 03:00)
    day = bars("2020-03-09 21:00", [(1, 1, 1, 1)] * 96)                      # 17:00 NY → pełny dzień
    assert set(day.day) == {pd.Timestamp("2020-03-10")}
    assert lab._at(day, "ldn_min", 8 * 60) == pd.Timestamp("2020-03-10 08:00", tz="UTC")
    assert lab._at(day, "ny_min", 8 * 60 + 30) == pd.Timestamp("2020-03-10 12:30", tz="UTC")
    asia = lab._window(day, "ny_min", 19 * 60, 2 * 60)
    assert asia.index[0] == pd.Timestamp("2020-03-09 23:00", tz="UTC") and asia.index[-1] == pd.Timestamp("2020-03-10 05:45", tz="UTC")


def test_random_walk_has_no_edge():
    rng = np.random.default_rng(3)
    n = 96 * 400
    px = 1500 + np.cumsum(rng.normal(0, 0.6, n))
    o = np.r_[px[0], px[:-1]]
    hi = np.maximum(o, px) + rng.uniform(0, 0.3, n)
    lo = np.minimum(o, px) - rng.uniform(0, 0.3, n)
    idx = pd.date_range("2018-01-01 22:00", periods=n, freq="15min", tz="UTC")
    df = lab.with_sessions(pd.DataFrame({"open": o, "high": hi, "low": lo, "close": px}, index=idx))
    s = lab.stats(lab.backtest(df, lab.london_orb, {"range_min": 60, "stop": "range", "tp_r": None, "buffer": 0.0}, cost=0.0))
    assert s["n"] > 200 and abs(s["t"]) < 2.5


def test_daily_context_uses_only_past():
    idx = pd.date_range("2020-01-01 22:00", periods=96 * 30, freq="15min", tz="UTC")
    px = np.linspace(1500, 1600, len(idx))
    df = lab.with_sessions(pd.DataFrame({"open": px, "high": px + 1, "low": px - 1, "close": px}, index=idx))
    ctx = lab.daily_context(df)
    assert ctx.trend.iloc[:20].isna().all()                    # SMA20 znana dopiero po 20 dniach, przesunięta o dzień
    assert (ctx.trend.dropna() == 1).all()                     # rosnący rynek → trend wzrostowy
