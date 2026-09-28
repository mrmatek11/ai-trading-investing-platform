from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from tape.engine import stats
from tape.engine.positions import build_positions
from tape.importers.base import Fill

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def fill(i, minutes, side, qty, price, **kw):
    return Fill(external_id=str(i), ts=T0 + timedelta(minutes=minutes), symbol=kw.pop("symbol", "XAUUSD"),
                side=side, qty=D(str(qty)), price=D(str(price)), contract_size=D(100), **kw)


def test_scale_in_partial_close_fifo():
    ps = build_positions([
        fill(1, 0, "buy", "0.1", 2600, stop_loss=D(2590)),
        fill(2, 10, "buy", "0.1", 2610),
        fill(3, 20, "sell", "0.1", 2620, fee=D("-1")),
        fill(4, 30, "sell", "0.1", 2605, fee=D("-1")),
    ])
    assert len(ps) == 1
    p = ps[0]
    # FIFO: (2620-2600)*0.1*100 + (2605-2610)*0.1*100 = 200 - 50
    assert p.computed_pnl == D("150.0")
    assert p.net_pnl == D("148.0")        # brak PnL brokera → liczony z cen
    assert p.qty == D("0.2")
    assert p.avg_entry == D("2605")
    # 1R = (2605 - 2590) * 0.2 * 100 = 300
    assert p.r_multiple == D("148") / D("300")


def test_reversal_splits_positions():
    ps = build_positions([
        fill(1, 0, "buy", "0.1", 2600),
        fill(2, 10, "sell", "0.3", 2610),   # zamyka 0.1 long i otwiera 0.2 short
        fill(3, 20, "buy", "0.2", 2605),
    ])
    assert [(p.direction, p.qty) for p in ps] == [(1, D("0.1")), (-1, D("0.2"))]
    assert ps[0].computed_pnl == D("100.0")
    assert ps[1].computed_pnl == D("100.0")
    assert ps[1].closed_at == T0 + timedelta(minutes=20)
    assert "2" in ps[1].fill_ids


def test_broker_pnl_preferred_when_complete():
    ps = build_positions([
        fill(1, 0, "buy", "0.1", 2600),
        fill(2, 10, "sell", "0.1", 2610, broker_pnl=D("99.50")),
    ])
    assert ps[0].net_pnl == D("99.50")


def test_open_position_is_not_in_stats():
    ps = build_positions([fill(1, 0, "buy", "0.1", 2600)])
    assert ps[0].is_open
    assert stats.summarize(ps).trades == 0


def test_after_loss_segment_detected():
    fills = []
    i = 0
    for day in range(30):
        base = day * 24 * 60
        # stratna pozycja, potem „revenge” 10 min później — też stratna
        fills += [fill(i, base, "buy", "0.1", 2600), fill(i + 1, base + 30, "sell", "0.1", 2595)]
        fills += [fill(i + 2, base + 40, "buy", "0.1", 2600), fill(i + 3, base + 60, "sell", "0.1", 2592 - day % 3)]
        # spokojna, zyskowna pozycja po południu
        fills += [fill(i + 4, base + 400, "buy", "0.1", 2600), fill(i + 5, base + 460, "sell", "0.1", 2610 + day % 4)]
        i += 6
    ps = build_positions(fills)
    summary = stats.summarize(ps)
    assert summary.trades == 90
    seg = {(s.group, s.key): s for s in stats.segments(ps)}
    revenge = seg[("after_loss", "do 30 min po stracie")]
    assert revenge.trades == 30 and revenge.avg_pnl < 0 and revenge.significant


def test_drawdown_and_profit_factor():
    ps = build_positions([
        fill(1, 0, "buy", "0.1", 2600), fill(2, 1, "sell", "0.1", 2610),    # +100
        fill(3, 2, "buy", "0.1", 2600), fill(4, 3, "sell", "0.1", 2580),    # -200
        fill(5, 4, "buy", "0.1", 2600), fill(6, 5, "sell", "0.1", 2605),    # +50
    ])
    s = stats.summarize(ps)
    assert s.net_pnl == -50 and s.max_drawdown == -200 and s.profit_factor == 0.75
