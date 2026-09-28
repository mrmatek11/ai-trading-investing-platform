"""
Testy poprawek strategii (determinizm NWO, hierarchia tierów, STOCH-ONLY)
oraz symulacji backtestu i autoryzacji API.

Uruchom:
    python3 -m unittest tests.test_strategy_backtest -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def synth(n=400, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(dict(open=open_, high=high, low=low, close=close,
                             volume=rng.uniform(100, 1000, n)), index=idx)


def bars(rows):
    """DataFrame z listy (open, high, low, close)."""
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1.0)


class TestNWODeterminism(unittest.TestCase):
    """Ten sam DataFrame → ten sam oscylator, niezależnie od historii wywołań."""

    def test_repeated_compute_is_identical(self):
        from strategy.neural_weight_oscillator import NeuralWeightOscillator
        df = synth()
        nwo = NeuralWeightOscillator()
        first = nwo.compute(df)["osc"]
        nwo.compute(synth(seed=2))           # inne dane pomiędzy
        again = nwo.compute(df)["osc"]
        fresh = NeuralWeightOscillator().compute(df)["osc"]
        pd.testing.assert_series_equal(first, again)
        pd.testing.assert_series_equal(first, fresh)


class FakeNWO:
    """Podmienia wynik NWO, żeby sterować triggerami strategii."""

    def __init__(self, n, **flags):
        f = pd.Series(False, index=range(n))
        self.result = {
            "osc": pd.Series(50.0, index=range(n)),
            "stoch_k": pd.Series(15.0, index=range(n)),
            "stoch_d": pd.Series(12.0, index=range(n)),
            "cvd": pd.Series(0.0, index=range(n)),
            "histogram": pd.Series(flags.pop("histogram", 1.0), index=range(n)),
        }
        for name in ("stoch_bull", "stoch_bear", "stoch_bull_relaxed", "stoch_bear_relaxed",
                     "stoch_bull_zone", "stoch_bear_zone"):
            self.result[name] = pd.Series(flags.get(name, False), index=range(n)) | f

    def compute(self, df):
        return self.result


class TestSignalHierarchy(unittest.TestCase):

    def setUp(self):
        import strategy.custom_strategy as cs
        self.cs = cs
        saved = (cs._use_closed_bar, cs._allow_stoch_only, cs.TREND_FILTER_MODE)

        def restore():
            cs._use_closed_bar, cs._allow_stoch_only, cs.TREND_FILTER_MODE = saved
        self.addCleanup(restore)
        cs.TREND_FILTER_MODE = "off"
        self.df = synth(200)

    def _run(self, **flags):
        fake = FakeNWO(len(self.df), **flags)
        with mock.patch.object(self.cs, "get_nwo_instance", return_value=fake):
            return self.cs.strategy_nwo_stoch_cvd(self.df, "BTC/USDT", "1h")

    def test_strict_is_reachable(self):
        # strict (K<20) zawsze spełnia też relaxed (K<30) — wcześniej wygrywał STOCH+NWO
        sigs = self._run(stoch_bull=True, stoch_bull_relaxed=True)
        self.assertEqual([s.strategy_name for s in sigs], ["STOCH STRICT+NWO"])
        self.assertEqual(sigs[0].extra_data["confidence"], "HIGH")

    def test_relaxed_only(self):
        sigs = self._run(stoch_bull_relaxed=True)
        self.assertEqual([s.strategy_name for s in sigs], ["STOCH+NWO"])

    def test_stoch_only_is_opt_in(self):
        self.cs.set_stoch_only_enabled(False)
        self.assertEqual(self._run(stoch_bull_zone=True), [])
        self.cs.set_stoch_only_enabled(True)
        self.assertEqual([s.strategy_name for s in self._run(stoch_bull_zone=True)], ["STOCH-ONLY"])

    def test_config_defaults(self):
        from core.config import BotConfig
        cfg = BotConfig()
        self.assertEqual(cfg.trend_filter_mode, "block")
        self.assertFalse(cfg.allow_stoch_only)
        self.assertGreaterEqual(cfg.candles_per_fetch, 120)   # minimum strategii NWO


class TestSimulation(unittest.TestCase):

    def setUp(self):
        import backtest as bt
        self.bt = bt
        self.p = bt.Params(sl_mult=1.0, tp_mult=2.0, trend_mode="off", tiers=bt.TIER_SETS["all"],
                           fee=0.0, slippage=0.0)

    def sig(self, bar=0, direction=1, close=100.0, atr=1.0, against=False):
        return self.bt.RawSignal(bar=bar, direction=direction, source="CONFLUENCE",
                                 against_trend=against, close=close, atr=atr)

    def test_tp_hit(self):
        df = bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 102.5, 100, 102)])
        (t,) = self.bt.simulate(df, [self.sig()], self.p)
        self.assertEqual((t.reason, t.exit, t.exit_bar), ("TP", 102.0, 2))
        self.assertAlmostEqual(t.r, 2.0)

    def test_sl_wins_when_both_hit_same_bar(self):
        df = bars([(100, 100, 100, 100), (100, 103, 98, 100)])
        (t,) = self.bt.simulate(df, [self.sig()], self.p)
        self.assertEqual((t.reason, t.exit), ("SL", 99.0))

    def test_gap_through_sl_fills_at_open(self):
        df = bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (97, 97.5, 96, 97)])
        (t,) = self.bt.simulate(df, [self.sig()], self.p)
        self.assertEqual((t.reason, t.exit), ("SL", 97.0))
        self.assertAlmostEqual(t.r, -3.0)

    def test_short_and_costs(self):
        p = self.bt.Params(sl_mult=1.0, tp_mult=2.0, trend_mode="off", tiers=self.bt.TIER_SETS["all"],
                           fee=0.001, slippage=0.0)
        df = bars([(100, 100, 100, 100), (100, 100.5, 99, 99.5), (99.5, 99.6, 97.5, 98)])
        (t,) = self.bt.simulate(df, [self.sig(direction=-1)], p)
        self.assertEqual((t.reason, t.exit), ("TP", 98.0))
        self.assertAlmostEqual(t.ret, 0.02 - 0.002)

    def test_trend_block_and_single_position(self):
        df = bars([(100, 100, 100, 100)] + [(100, 100.5, 99.5, 100)] * 5)
        sigs = [self.sig(bar=0), self.sig(bar=2), self.sig(bar=3, against=True)]
        self.assertEqual(len(self.bt.simulate(df, sigs, self.p)), 1)       # w pozycji do końca
        blocked = self.bt.simulate(df, [self.sig(against=True)], self.bt.replace(self.p, trend_mode="block"))
        self.assertEqual(blocked, [])

    def test_load_binance_raw_csv_microseconds(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("1735689600000000,1,2,0.5,1.5,10,0,0,0,0,0,0\n")
            f.write("1735693200000000,1.5,2,1,1.8,12,0,0,0,0,0,0\n")
        self.addCleanup(os.unlink, f.name)
        df = self.bt.load_csv(f.name)
        self.assertEqual(str(df.index[0]), "2025-01-01 00:00:00+00:00")
        self.assertEqual(df["close"].tolist(), [1.5, 1.8])


class TestApiAuth(unittest.TestCase):

    def setUp(self):
        from api.server import FASTAPI_AVAILABLE
        try:
            from fastapi.testclient import TestClient  # noqa: F401
        except Exception:
            self.skipTest("fastapi/httpx nie zainstalowane")
        if not FASTAPI_AVAILABLE:
            self.skipTest("fastapi/uvicorn nie zainstalowane")

    def _client(self, key):
        from fastapi.testclient import TestClient
        from api.server import create_api_app
        env = {"API_KEY": key} if key else {}
        with mock.patch.dict(os.environ, env, clear=False):
            if not key:
                os.environ.pop("API_KEY", None)
            return TestClient(create_api_app(None))

    def test_key_required_when_set(self):
        c = self._client("s3cret")
        self.assertEqual(c.get("/api/health").status_code, 200)
        self.assertEqual(c.get("/api/stats").status_code, 401)
        self.assertEqual(c.get("/api/stats", headers={"X-API-Key": "s3cret"}).status_code, 503)

    def test_config_update_disabled_without_key(self):
        c = self._client("")
        self.assertEqual(c.post("/api/config/update", json={"scan_interval": 1}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
