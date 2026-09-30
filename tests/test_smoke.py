"""
Smoke tests — szybkie testy potwierdzające że krytyczne fixy działają.

Uruchom:
    python3 -m unittest tests.test_smoke -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPricePrecision(unittest.TestCase):
    """Fix #4: SL/TP dla niskocenowych monet nie giną na zaokrąglaniu."""

    def test_precision_by_magnitude(self):
        from strategy.signal_detector import price_precision
        self.assertEqual(price_precision(67000), 2)   # BTC
        self.assertEqual(price_precision(612), 4)     # BNB
        self.assertEqual(price_precision(0.6), 5)     # XRP
        self.assertEqual(price_precision(0.08), 5)    # DOGE
        self.assertEqual(price_precision(0.000012), 8)  # SHIB

    def test_round_price_doge(self):
        from strategy.signal_detector import round_price
        # DOGE @ 0.08 — SL musi zachować przynajmniej 5 cyfr precyzji
        self.assertEqual(round_price(0.082345678, 0.08), 0.08235)

    def test_round_price_btc(self):
        from strategy.signal_detector import round_price
        self.assertEqual(round_price(67432.567, 67000), 67432.57)


class TestCooldownStrength(unittest.TestCase):
    """Fix #3: silniejszy sygnał nadpisuje cooldown słabszego."""

    def test_strength_hierarchy(self):
        from bot import StochSignalBot

        class FakeSig:
            def __init__(s, src):
                s.extra_data = {"source": src}

        self.assertEqual(StochSignalBot._signal_strength(FakeSig("CONFLUENCE")), 3)
        self.assertEqual(StochSignalBot._signal_strength(FakeSig("STOCH STRICT+NWO")), 3)
        self.assertEqual(StochSignalBot._signal_strength(FakeSig("STOCH+NWO")), 2)
        self.assertEqual(StochSignalBot._signal_strength(FakeSig("STOCH-ONLY")), 1)


class TestIntraBarSLTP(unittest.TestCase):
    """Fix #6: SL/TP wykrywane intra-bar (po high/low), nie tylko close."""

    def _tracker(self, **kw):
        from tracking.position_tracker import PositionTracker
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return PositionTracker(db_path=tmp.name, **kw)

    def test_long_sl_intrabar(self):
        pt = self._tracker(apply_slippage=False)
        pt.open_position("X", "LONG", 100.0, sl=95.0, tp=110.0, size=1.0)
        # Close=97 wyglada bezpiecznie, ale low baru = 94 trafil SL
        closed = pt.check_closes({"X": 97.0}, {"X": {"high": 98.0, "low": 94.0, "close": 97.0}})
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].close_reason, "SL")
        self.assertEqual(closed[0].close_price, 95.0)

    def test_long_tp_intrabar(self):
        pt = self._tracker(apply_slippage=False)
        pt.open_position("X", "LONG", 100.0, sl=95.0, tp=110.0, size=1.0)
        closed = pt.check_closes({"X": 105.0}, {"X": {"high": 112.0, "low": 104.0, "close": 105.0}})
        self.assertEqual(closed[0].close_reason, "TP")
        self.assertEqual(closed[0].close_price, 110.0)

    def test_short_sl_intrabar(self):
        pt = self._tracker(apply_slippage=False)
        pt.open_position("S", "SHORT", 100.0, sl=105.0, tp=90.0, size=1.0)
        closed = pt.check_closes({"S": 102.0}, {"S": {"high": 106.0, "low": 101.0, "close": 102.0}})
        self.assertEqual(closed[0].close_reason, "SL")
        self.assertEqual(closed[0].close_price, 105.0)

    def test_no_bars_falls_back_to_close(self):
        """Backward-compat: brak current_bars => check tylko po close."""
        pt = self._tracker(apply_slippage=False)
        pt.open_position("X", "LONG", 100.0, sl=95.0, tp=110.0, size=1.0)
        # bez current_bars i close=97 nie powinno zamknąć
        closed = pt.check_closes({"X": 97.0})
        self.assertEqual(len(closed), 0)


class TestSlippageToggle(unittest.TestCase):
    """Fix #7: slippage opcjonalny."""

    def _tracker(self, **kw):
        from tracking.position_tracker import PositionTracker
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return PositionTracker(db_path=tmp.name, **kw)

    def test_slippage_off(self):
        pt = self._tracker(apply_slippage=False)
        pos = pt.open_position("X", "LONG", 100.0, sl=95.0, tp=110.0, size=1.0)
        self.assertEqual(pos.entry_price, 100.0)

    def test_slippage_on(self):
        pt = self._tracker(apply_slippage=True, slippage_pct=0.001)
        pos = pt.open_position("X", "LONG", 100.0, sl=95.0, tp=110.0, size=1.0)
        self.assertAlmostEqual(pos.entry_price, 100.1, places=3)


class TestClosedBarMode(unittest.TestCase):
    """Fix #2: anti-repaint — closed-bar vs live-bar daje różne rezultaty."""

    def test_closed_bar_index(self):
        from strategy.signal_detector import SignalDetector
        det = SignalDetector(use_closed_bar=True)
        self.assertTrue(det.use_closed_bar)
        det2 = SignalDetector(use_closed_bar=False)
        self.assertFalse(det2.use_closed_bar)


if __name__ == "__main__":
    unittest.main()
