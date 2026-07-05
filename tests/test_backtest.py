from datetime import date, datetime, timedelta, timezone

import pytest

from bot import calendar as bot_calendar
from bot.backtesting import backtest as bt
from bot.data.market_data import Bar


def _bars(n, start_price=100.0, step=1.0, start=datetime(2025, 1, 2, tzinfo=timezone.utc)):
    bars = []
    for i in range(n):
        price = start_price + i * step
        bars.append(
            Bar(
                timestamp=start + timedelta(minutes=15 * i),
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price + 0.1,
                volume=1000.0,
            )
        )
    return bars


def test_no_lookahead_shift():
    bars = _bars(20)
    cutoff = 10

    def strategy_fn(window):
        return bt.TargetPosition.LONG if window[-1].close > window[0].close else bt.TargetPosition.FLAT

    cost_model = bt.equity_cost_model()
    result_a = bt.run_backtest(bars, strategy_fn, cost_model)

    shifted = list(bars)
    for i in range(cutoff + 1, len(shifted)):
        b = shifted[i]
        shifted[i] = Bar(
            timestamp=b.timestamp,
            open=b.open + 1000.0,
            high=b.high + 1000.0,
            low=b.low + 1000.0,
            close=b.close + 1000.0,
            volume=b.volume,
        )
    result_b = bt.run_backtest(shifted, strategy_fn, cost_model)

    decisions_a = [d for i, d in result_a.decisions if i <= cutoff]
    decisions_b = [d for i, d in result_b.decisions if i <= cutoff]
    assert decisions_a == decisions_b


def test_fill_at_next_bar():
    bars = _bars(5)

    def strategy_fn(window):
        return bt.TargetPosition.LONG if len(window) == 1 else bt.TargetPosition.FLAT

    cost_model = bt.equity_cost_model()
    result = bt.run_backtest(bars, strategy_fn, cost_model)

    assert len(result.trades) == 1
    trade = result.trades[0]
    # Decision at bar 0 (window len 1) -> fill at bar 1's open, not bar 0's close.
    assert trade.entry_time == bars[1].timestamp
    assert trade.entry_price != bars[0].close
    # Decision at bar 1 (window len 2, target FLAT) -> exit fill at bar 2's open.
    assert trade.exit_time == bars[2].timestamp


def test_costs_applied_every_trade():
    bars = _bars(5, start_price=100.0, step=10.0)  # opens: 100, 110, 120, 130, 140

    def strategy_fn(window):
        return bt.TargetPosition.LONG if len(window) == 1 else bt.TargetPosition.FLAT

    cost_model = bt.CostModel(cost_bps=10.0)  # 0.10%
    result = bt.run_backtest(bars, strategy_fn, cost_model, quantity=1.0)

    trade = result.trades[0]
    expected_entry = 110.0 * 1.0010   # buy at bar1 open, cost pushes price up
    expected_exit = 120.0 * 0.9990    # sell at bar2 open, cost pushes price down
    assert trade.entry_price == pytest.approx(expected_entry)
    assert trade.exit_price == pytest.approx(expected_exit)
    assert trade.pnl == pytest.approx(expected_exit - expected_entry)


def test_zero_cost_backtest_is_not_possible():
    with pytest.raises(ValueError):
        bt.CostModel(cost_bps=0)
    with pytest.raises(ValueError):
        bt.CostModel(cost_bps=-1.0)
    with pytest.raises(TypeError):
        bt.CostModel()  # no default -> cannot be constructed cost-free by omission


def test_warmup_no_early_signals():
    bars = _bars(10)
    warmup_bars = 3

    def strategy_fn(window):
        # Strategy always wants LONG regardless of window length; the engine
        # must override this until warmup_bars have elapsed.
        return bt.TargetPosition.LONG

    cost_model = bt.equity_cost_model()
    result = bt.run_backtest(bars, strategy_fn, cost_model, warmup_bars=warmup_bars)

    forced_flat = [d for i, d in result.decisions if i < warmup_bars]
    assert all(d == bt.TargetPosition.FLAT for d in forced_flat)

    first_entry_time = result.trades[0].entry_time if result.trades else None
    if first_entry_time is not None:
        assert first_entry_time >= bars[warmup_bars + 1].timestamp


def test_known_input_known_output():
    bars = _bars(7, start_price=100.0, step=1.0)  # opens: 100..106

    def strategy_fn(window):
        if len(window) in (3, 4):
            return bt.TargetPosition.LONG
        return bt.TargetPosition.FLAT

    cost_model = bt.CostModel(cost_bps=10.0)  # 0.10%
    result = bt.run_backtest(bars, strategy_fn, cost_model, quantity=1.0)

    assert len(result.trades) == 1
    trade = result.trades[0]
    expected_entry = 103.0 * 1.0010  # decision at i=2 (len 3) -> fill at bar3 open
    expected_exit = 105.0 * 0.9990   # decision at i=4 (len 5, FLAT) -> fill at bar5 open
    assert trade.entry_price == pytest.approx(expected_entry)
    assert trade.exit_price == pytest.approx(expected_exit)
    assert trade.pnl == pytest.approx(expected_exit - expected_entry)
    assert result.metrics["total_trades"] == 1
    assert result.metrics["win_rate"] == 1.0


def test_reproducibility_same_seed():
    bars = _bars(30)

    def strategy_fn(window):
        return bt.TargetPosition.LONG if len(window) % 6 < 3 else bt.TargetPosition.FLAT

    cost_model = bt.CostModel(cost_bps=10.0, jitter_bps=5.0)

    result_a = bt.run_backtest(bars, strategy_fn, cost_model, seed=123)
    result_b = bt.run_backtest(bars, strategy_fn, cost_model, seed=123)

    assert [t.pnl for t in result_a.trades] == [t.pnl for t in result_b.trades]
    assert result_a.equity_curve == result_b.equity_curve


def test_jitter_requires_explicit_seed():
    bars = _bars(5)
    cost_model = bt.CostModel(cost_bps=10.0, jitter_bps=5.0)
    with pytest.raises(ValueError):
        bt.run_backtest(bars, lambda w: bt.TargetPosition.FLAT, cost_model)  # no seed


class _FakeIntradayBar:
    def __init__(self, timestamp, o, h, l, c, v):
        self.timestamp = timestamp
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v


def test_4hr_bar_construction():
    """The backtester must consume session-aligned 4-hr bars produced by
    bot.calendar.build_4hr_equity_bars without special-casing.
    """
    day = date(2025, 6, 4)
    start = datetime.combine(day, bot_calendar.EQUITY_OPEN, tzinfo=bot_calendar.NY_TZ)
    intraday = [
        _FakeIntradayBar(start + timedelta(minutes=15 * i), 100 + i, 101 + i, 99 + i, 100.5 + i, 1000)
        for i in range(26)
    ]
    aggregated = bot_calendar.build_4hr_equity_bars(intraday)
    assert len(aggregated) == 2

    four_hr_bars = [
        Bar(timestamp=w["timestamp"], open=w["open"], high=w["high"], low=w["low"], close=w["close"], volume=w["volume"])
        for w in aggregated
    ]

    result = bt.run_backtest(four_hr_bars, lambda window: bt.TargetPosition.FLAT, bt.equity_cost_model())
    assert result.total_bars == 2
    assert len(result.equity_curve) == 2
    assert result.trades == []


def test_split_is_oos_default_70_30():
    bars = _bars(100)
    is_bars, oos_bars, oos_warmup = bt.split_is_oos(bars)
    assert len(is_bars) == 70
    assert len(oos_bars) == 30
    assert oos_warmup == 0
    assert is_bars == bars[:70]
    assert oos_bars == bars[70:]


def test_split_is_oos_with_warmup_context():
    bars = _bars(100)
    is_bars, oos_bars, oos_warmup = bt.split_is_oos(bars, is_fraction=0.7, warmup_context_bars=10)
    assert len(is_bars) == 70
    assert oos_warmup == 10
    assert len(oos_bars) == 40  # 10 context bars + 30 OOS bars
    assert oos_bars[0] == bars[60]
