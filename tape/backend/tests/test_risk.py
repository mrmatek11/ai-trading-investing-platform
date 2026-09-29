from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from tape.engine.positions import build_positions
from tape.engine.prop import PropRules, evaluate, simulate
from tape.engine.risk import position_size
from tape.importers.base import Fill


def test_gold_position_size_rounds_down():
    # 10 000 USD, 1% ryzyka, SL 15 USD od wejścia, złoto 100 oz/lot → 100 / 1500 = 0,0666 → 0,06
    r = position_size(D(10000), D(1), D("2684.20"), D("2669.20"))
    assert r.lots == D("0.06")
    assert r.risk_budget == D("100.00") and r.risk_actual == D("90.00")
    assert r.risk_actual <= r.risk_budget
    assert r.value_per_point == D("6.00")


def test_min_lot_and_limits():
    r = position_size(D(500), D(1), D(2684), D(2600))          # 5 USD budżetu, min lot = 84 USD
    assert r.lots == 0 and any("Minimalny lot" in w for w in r.warnings)
    r = position_size(D(100000), D(1), D(2684), D(2683), max_lot=D(5))
    assert r.lots == D(5) and any("maksymalnego" in w for w in r.warnings)
    r = position_size(D(100000), D(2), D(2684), D(2664), daily_range=D(30), daily_loss_limit=D(3000))
    assert r.daily_limit_share > D("0.5") and any("połowę" in w for w in r.warnings)
    with pytest.raises(ValueError):
        position_size(D(1000), D(1), D(2684), D(2684))


T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def trades(pnls_by_day):
    """Każdy dzień: lista PnL w USD; złoto 0,1 lota → 10 USD na 1 USD ruchu ceny."""
    fills, i = [], 0
    for d, pnls in enumerate(pnls_by_day):
        for k, pnl in enumerate(pnls):
            t = T0 + timedelta(days=d, hours=k)
            move = D(str(pnl)) / 10
            fills += [Fill(str(i), t, "XAUUSD", "buy", D("0.1"), D(2600), D(100)),
                      Fill(str(i + 1), t + timedelta(minutes=30), "XAUUSD", "sell", D("0.1"), D(2600) + move, D(100))]
            i += 2
    return build_positions(fills)


def test_daily_limit_breach():
    rules = PropRules(D(10000), D(5), D(10), "static", D(10))
    rep = evaluate(trades([[200], [-300, -250]]), rules)          # dzień 2: −550 > 5% z 10 000
    assert rep.status == "breached" and rep.breach == "daily" and rep.breach_day.day == 2


def test_trailing_vs_static_same_history():
    # +800, potem −900 (saldo 9 900): statyczny próg 9 500 trzyma, trailing — szczyt 10 800 − 500,
    # zatrzymany na saldzie początkowym 10 000 — pęka
    history = trades([[400], [400], [-450], [-450]])
    static = evaluate(history, PropRules(D(10000), D(5), D(5), "static", None))
    trailing = evaluate(history, PropRules(D(10000), D(5), D(5), "trailing", None))
    assert static.status == "active"
    assert trailing.status == "breached" and trailing.breach == "max_drawdown"
    sims = simulate(history, D(10000))
    assert set(sims) == {"static_10", "trailing_6", "trailing_5_3"}


def test_trailing_floor_stops_at_initial_balance():
    rep = evaluate(trades([[1500], [-400]]), PropRules(D(10000), D(5), D(5), "trailing", None))
    assert rep.status == "active"
    assert rep.overall_headroom == D(11100) - D(10000)            # próg zatrzymany na 10 000


def test_passed_target():
    rep = evaluate(trades([[600], [500]]), PropRules(D(10000), D(5), D(10), "static", D(10)))
    assert rep.status == "passed"
