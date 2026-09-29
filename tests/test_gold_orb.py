import numpy as np
import pandas as pd

from research import intraday_lab as lab
from strategy import gold_orb


def synthetic(days=60, seed=5):
    rng = np.random.default_rng(seed)
    n = 96 * days
    idx = pd.date_range("2021-03-01 22:00", periods=n, freq="15min", tz="UTC")
    vol = np.repeat(rng.uniform(0.3, 1.5, days), 96)[:n]          # różna zmienność dni → są dni NR7
    px = 1800 + np.cumsum(rng.normal(0, 1, n) * vol)
    o = np.r_[px[0], px[:-1]]
    return gold_orb.prepare(pd.DataFrame({"open": o, "high": np.maximum(o, px) + 0.2, "low": np.minimum(o, px) - 0.2,
                                          "close": px}, index=idx))


def test_plan_active_only_after_nr7_and_levels_consistent():
    df = synthetic()
    ctx = lab.daily_context(df)
    nr7_day = ctx[ctx.nr7].index[-1]
    other = ctx[~ctx.nr7 & ctx.trend.notna()].index[-1]
    assert gold_orb.plan_for_day(df, other).active is False
    p = gold_orb.plan_for_day(df, nr7_day, balance=100000, risk_pct=0.5)
    assert p.active and p.range_low < p.range_high
    width = p.range_high - p.range_low
    assert abs(p.buy_stop - (p.range_high + 0.1 * width)) < 0.02 and abs(p.sell_stop - (p.range_low - 0.1 * width)) < 0.02
    assert p.long_sl == p.range_low and p.short_sl == p.range_high
    assert 0 < p.risk_usd <= 500 and isinstance(p.lots, float)                  # ryzyko ≤ 0,5% salda, zaokrąglone w dół
    assert gold_orb.discord_embed(p)["fields"][1]["value"].startswith(str(p.buy_stop))


def test_paper_uses_exactly_the_backtest_engine():
    df = synthetic()
    days = sorted(df.day.unique())[-40:]
    a = gold_orb.paper(df, 40)
    b = lab.backtest(df, lab.london_orb_nr7, gold_orb.PARAMS, gold_orb.COST_USD, set(days))
    pd.testing.assert_frame_equal(a, b)
    assert "transakcji" in gold_orb.paper_summary(a) or "Brak" in gold_orb.paper_summary(a)
