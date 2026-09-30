from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from tape.engine.analytics import analytics
from tape.engine.positions import build_positions
from tape.importers.base import Fill


def series(pnls, start=datetime(2026, 1, 5, 9, tzinfo=timezone.utc), step_days=1):
    fills = []
    for i, x in enumerate(pnls):
        t = start + timedelta(days=i * step_days)
        fills += [Fill(f"{i}a", t, "XAUUSD", "buy", D(1), D(2000), D(1)),
                  Fill(f"{i}b", t + timedelta(hours=1), "XAUUSD", "sell", D(1), D(2000) + D(x), D(1))]
    return build_positions(fills)


def test_expectancy_payoff_breakeven():
    a = analytics(series([100, 100, -50, -50, -50]))
    assert a["expectancy"] == 10 and a["avg_win"] == 100 and a["avg_loss"] == -50
    assert a["payoff"] == 2 and a["breakeven_win_rate"] == pytest.approx(1 / 3, abs=1e-3)   # 50 / (100 + 50)
    assert a["largest_win"] == 100 and a["largest_loss"] == -50


def test_streaks_and_drawdown_duration():
    # dni: +100 (szczyt d0), −30, −30 (dołek −60), +80 (nowy szczyt d3 → 3 dni pod wodą), +10, −5 (pod wodą od d4)
    a = analytics(series([100, -30, -30, 80, 10, -5]))
    assert a["streaks"] == {"max_wins": 2, "max_losses": 2, "current": -1}
    dd = a["drawdown"]
    assert dd["max"] == -60 and dd["longest_days"] == 3 and dd["current"] == -5 and dd["underwater_days"] == 1


def test_consecutive_highs_are_not_underwater():
    a = analytics(series([10, 10, 10], step_days=30))
    assert a["drawdown"]["longest_days"] == 0 and a["drawdown"]["max"] == 0


def test_daily_hour_hist_and_rolling():
    ps = series([10] * 12 + [-5] * 12)
    a = analytics(ps, rolling=20)
    assert a["trading_days"] == 24 and a["green_days"] == 12 and a["best_day"] == 10
    assert a["by_hour"][9]["trades"] == 24 and sum(h["trades"] for h in a["by_hour"]) == 24
    assert a["by_weekday"][0]["trades"] > 0
    assert a["r_hist"] is None                                   # brak SL → brak rozkładu R
    assert len(a["rolling"]["points"]) == 5 and a["rolling"]["points"][-1]["expectancy"] == pytest.approx((8 * 10 - 12 * 5) / 20)
    assert a["sharpe_daily"] is not None and a["sortino_daily"] is not None


def test_r_histogram_bins():
    fills = []
    for i, x in enumerate([-10, -10, 20, 5, 15, -10, 30, -10, 10, -10]):     # SL 10 → R = x/10
        t = datetime(2026, 1, 5, 9, tzinfo=timezone.utc) + timedelta(days=i)
        fills += [Fill(f"{i}a", t, "XAUUSD", "buy", D(1), D(2000), D(1), stop_loss=D(1990)),
                  Fill(f"{i}b", t + timedelta(hours=1), "XAUUSD", "sell", D(1), D(2000 + x), D(1))]
    h = analytics(build_positions(fills))["r_hist"]
    counts = {b["label"]: b["count"] for b in h}
    assert counts["-1…-0.5"] == 5 and counts["2…3"] == 1 and counts["≥ 5"] == 0 and sum(counts.values()) == 10
