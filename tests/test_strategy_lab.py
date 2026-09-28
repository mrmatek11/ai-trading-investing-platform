"""Testy laboratorium strategii (research/strategy_lab.py)."""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def hourly(days=12, seed=0):
    rng = np.random.default_rng(seed)
    n = days * 24
    close = 1800 + np.cumsum(rng.normal(0, 2, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 1
    low = np.minimum(open_, close) - 1
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(dict(open=open_, high=high, low=low, close=close, volume=1.0), index=idx)


class TestLondonBreakout(unittest.TestCase):

    def test_one_signal_per_day_in_window(self):
        from research import strategy_lab as lab
        df = hourly()
        # wymuś wybicie w górę o 08:00 i 09:00 dnia 11 — tylko pierwsze się liczy
        day = df.index.normalize() == pd.Timestamp("2024-01-11", tz="UTC")
        asia_hi = df.loc[day & (df.index.hour < 7), "high"].max()
        asia_lo = df.loc[day & (df.index.hour < 7), "low"].min()
        df.loc[pd.Timestamp("2024-01-11 07:00", tz="UTC"), "close"] = (asia_hi + asia_lo) / 2
        for h in (8, 9):
            ts = pd.Timestamp(f"2024-01-11 {h:02d}:00", tz="UTC")
            df.loc[ts, ["close", "high"]] = asia_hi + 50
        sigs = [s for s in lab.london_breakout(df) if df.index[s.bar].date() == pd.Timestamp("2024-01-11").date()]
        self.assertEqual(len(sigs), 1)
        self.assertEqual(df.index[sigs[0].bar].hour, 8)
        self.assertEqual(sigs[0].direction, 1)
        hours = {df.index[s.bar].hour for s in lab.london_breakout(df)}
        self.assertTrue(hours <= set(range(7, 12)))

    def test_daily_data_gives_no_signals(self):
        from research import strategy_lab as lab
        df = hourly().resample("1D").agg({"open": "first", "high": "max", "low": "min",
                                          "close": "last", "volume": "sum"})
        self.assertEqual(lab.london_breakout(df), [])


class TestBaselineAndThreshold(unittest.TestCase):

    def test_random_baseline_is_deterministic(self):
        from research import strategy_lab as lab
        df = hourly(days=30)
        a, b = lab.random_baseline(df, 50, seed=1), lab.random_baseline(df, 50, seed=1)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 50)

    def test_bonferroni_threshold(self):
        from research import strategy_lab as lab
        self.assertAlmostEqual(lab.bonferroni_t(1), 1.645, places=2)
        self.assertGreater(lab.bonferroni_t(18), 2.7)


if __name__ == "__main__":
    unittest.main()
