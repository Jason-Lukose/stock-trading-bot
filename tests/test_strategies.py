import math
from datetime import datetime, timedelta, timezone

from bot import indicators
from bot.backtesting.backtest import TargetPosition
from bot.data.market_data import Bar
from bot.strategies import mean_reversion as mr
from bot.strategies import momentum_breakout as mb
from bot.strategies import trend_following as tf

START = datetime(2025, 1, 2, tzinfo=timezone.utc)


def _bar(i, o, h, l, c, v):
    return Bar(timestamp=START + timedelta(minutes=15 * i), open=o, high=h, low=l, close=c, volume=v)


def _flat_bars(prices, volumes=None):
    """Degenerate bars where high == low == close == price, for tests that
    only exercise close/volume logic (momentum_breakout, trend_following)."""
    volumes = volumes or [1000.0] * len(prices)
    return [_bar(i, p, p, p, p, v) for i, (p, v) in enumerate(zip(prices, volumes))]


# ---------------------------------------------------------------------------
# Mean reversion (SPY k=1.5, QQQ k=1.8)
# ---------------------------------------------------------------------------

def test_mean_reversion_warmup_then_long_entry_and_exit():
    """20 bars at 100 (SMA=100, sigma=0, so no breach possible), then a sharp
    drop to 80, then a recovery to 100.

    Hand computation for the drop bar (index 20): the trailing 20-bar window
    is 19 values of 100 (indices 1-19) + 1 value of 80 (index 20).
      mean = (19*100 + 80) / 20 = 99.0
      variance = (19*(100-99)^2 + (80-99)^2) / 20 = (19*1 + 361) / 20 = 19.0
      std = sqrt(19) = 4.3589
      lower (k=1.5) = 99.0 - 1.5*4.3589 = 92.46
      80 < 92.46 -> LONG.
    For the recovery bar (index 21): window is 19 values of 100 (indices
    2-20 minus the one at 20... ) -> same composition (19x100 + 1x80),
    since only the position of the 80 shifted, not the composition ->
    mean is again 99.0. price=100 >= 99.0 -> exit to FLAT.
    """
    prices = [100.0] * 20 + [80.0, 100.0]
    bars = _flat_bars(prices)

    states = mr.target_position_series(bars, mr.K_SPY)

    assert all(s == TargetPosition.FLAT for s in states[:20]), states[:20]
    assert states[20] == TargetPosition.LONG
    assert states[21] == TargetPosition.FLAT


def test_mean_reversion_short_entry_and_exit():
    """Mirror of the long case: a sharp spike up instead of down."""
    prices = [100.0] * 20 + [120.0, 100.0]
    bars = _flat_bars(prices)

    states = mr.target_position_series(bars, mr.K_SPY)

    assert all(s == TargetPosition.FLAT for s in states[:20])
    assert states[20] == TargetPosition.SHORT
    assert states[21] == TargetPosition.FLAT


def test_mean_reversion_never_signals_during_warmup():
    """Even a huge one-bar move within the warm-up window must not produce
    a signal — indicators are None until period-1, and the strategy must
    treat None as FLAT, never as a tradeable value."""
    prices = [100.0] * 10 + [10.0] + [100.0] * 9  # spike inside first 20 bars
    bars = _flat_bars(prices)

    states = mr.target_position_series(bars, mr.K_SPY)
    assert all(s == TargetPosition.FLAT for s in states[:20])


def _independent_mean_reversion_reference(bars, k):
    """Independent re-implementation of StrategySpec.md's mean-reversion
    rule, written separately from bot/strategies/mean_reversion.py, driven
    by the same (independently unit-tested) sma/rolling_std primitives. Used
    to cross-check the module across a longer, less hand-tracked series.
    """
    closes = [b.close for b in bars]
    sma_values = indicators.sma(closes, mr.SMA_PERIOD)
    std_values = indicators.rolling_std(closes, mr.STD_PERIOD)

    expected = []
    holding = 0  # 0 flat, 1 long, -1 short
    for i, price in enumerate(closes):
        m, s = sma_values[i], std_values[i]
        if m is None or s is None:
            holding = 0
        else:
            band = k * s
            if holding == 0:
                if price < m - band:
                    holding = 1
                elif price > m + band:
                    holding = -1
            elif holding == 1 and price >= m:
                holding = 0
            elif holding == -1 and price <= m:
                holding = 0
        expected.append({0: TargetPosition.FLAT, 1: TargetPosition.LONG, -1: TargetPosition.SHORT}[holding])
    return expected


def test_mean_reversion_matches_independent_reference_and_k_differs_by_symbol():
    # A hand-crafted, non-monotonic price path long enough to exercise
    # several entries/exits for both k values.
    pattern = [100, 104, 96, 108, 92, 111, 89, 101, 99, 103, 97, 105, 95, 100]
    prices = [100.0] * 20 + [float(p) for p in pattern] * 3
    bars = _flat_bars(prices)

    spy_states = mr.target_position_series(bars, mr.K_SPY)
    qqq_states = mr.target_position_series(bars, mr.K_QQQ)

    assert spy_states == _independent_mean_reversion_reference(bars, mr.K_SPY)
    assert qqq_states == _independent_mean_reversion_reference(bars, mr.K_QQQ)

    # The wider QQQ band (k=1.8) must be strictly less trigger-happy than
    # SPY's (k=1.5) on identical data -- prove k is actually load-bearing.
    assert spy_states != qqq_states
    spy_active = sum(1 for s in spy_states if s is not TargetPosition.FLAT)
    qqq_active = sum(1 for s in qqq_states if s is not TargetPosition.FLAT)
    assert spy_active > qqq_active


def test_spy_and_qqq_strategy_functions_use_the_documented_k():
    prices = [100.0] * 20 + [80.0, 100.0]
    bars = _flat_bars(prices)
    assert mr.spy_strategy(bars) == mr.target_position_series(bars, mr.K_SPY)[-1]
    assert mr.qqq_strategy(bars) == mr.target_position_series(bars, mr.K_QQQ)[-1]


# ---------------------------------------------------------------------------
# Momentum breakout (BTC/USD, no shorting -- degrades to exit)
# ---------------------------------------------------------------------------

def test_momentum_breakout_warmup_then_long_entry_and_exit_to_flat():
    """21 flat baseline bars (price=100, volume=1000) fully warm up the
    20-period rolling high/low/avg-volume. Then:

    Bar 21: high=102, low=99, close=101, volume=2000.
      rolling_high[20] (prior-bar 20-period high, indices 1-20) = 100.
      avg_volume[21] = mean(volumes[2:22]) = (19*1000 + 2000)/20 = 1050.
      101 > 100 and 2000 >= 1.5*1050=1575 -> breakout up confirmed -> LONG.

    Bar 22: high=99, low=94, close=95, volume=2000.
      rolling_low[21] (indices 2-21) = min(19x100, 99) = 99.
      avg_volume[22] = mean(volumes[3:23]) = (18*1000 + 2000 + 2000)/20 = 1100.
      95 < 99 and 2000 >= 1.5*1100=1650 -> breakout down confirmed ->
      degrades to FLAT (no short capability on Alpaca spot BTC), not SHORT.
    """
    bars = _flat_bars([100.0] * 21) + [
        _bar(21, 100.0, 102.0, 99.0, 101.0, 2000.0),
        _bar(22, 101.0, 99.0, 94.0, 95.0, 2000.0),
    ]

    states = mb.target_position_series(bars)

    assert all(s == TargetPosition.FLAT for s in states[:21]), states[:21]
    assert states[21] == TargetPosition.LONG
    assert states[22] == TargetPosition.FLAT
    assert TargetPosition.SHORT not in states  # never emitted, by design


def test_momentum_breakout_requires_volume_confirmation():
    """Same price breakout as above, but volume=1200 fails the 1.5x-average
    gate (avg_volume[21] = (19*1000+1200)/20 = 1010; 1.5*1010 = 1515 > 1200),
    so no entry should fire despite the price condition being met.
    """
    bars = _flat_bars([100.0] * 21) + [_bar(21, 100.0, 102.0, 99.0, 101.0, 1200.0)]

    states = mb.target_position_series(bars)
    assert states[21] == TargetPosition.FLAT


def test_momentum_breakout_never_signals_during_warmup():
    bars = _flat_bars([100.0] * 10 + [200.0] + [100.0] * 10, volumes=[5000.0] * 21)
    states = mb.target_position_series(bars)
    assert all(s == TargetPosition.FLAT for s in states[:20])


def test_btc_strategy_function_matches_series():
    bars = _flat_bars([100.0] * 21) + [_bar(21, 100.0, 102.0, 99.0, 101.0, 2000.0)]
    assert mb.btc_strategy(bars) == mb.target_position_series(bars)[-1]


# ---------------------------------------------------------------------------
# Trend following (GLD, USO: 50/200 EMA cross)
# ---------------------------------------------------------------------------

def _independent_ema_cross_reference(bars):
    closes = [b.close for b in bars]
    fast = indicators.ema(closes, tf.FAST_PERIOD)
    slow = indicators.ema(closes, tf.SLOW_PERIOD)
    expected = []
    for f, s in zip(fast, slow):
        if f is None or s is None:
            expected.append(TargetPosition.FLAT)
        elif f > s:
            expected.append(TargetPosition.LONG)
        elif f < s:
            expected.append(TargetPosition.SHORT)
        else:
            expected.append(TargetPosition.FLAT)
    return expected


def test_trend_following_no_signal_before_200_ema_warmup_on_rising_prices():
    """A monotonic ramp from bar 0 makes the 50 EMA diverge from the (still
    None) 200 EMA well before bar 199 -- the strategy must stay FLAT purely
    because slow EMA is None, not because of any price-based judgment call.
    """
    prices = [100.0 + i for i in range(260)]
    bars = _flat_bars(prices)

    states = tf.target_position_series(bars)
    reference = _independent_ema_cross_reference(bars)

    assert states == reference
    assert all(s == TargetPosition.FLAT for s in states[:199])
    # A sustained uptrend must produce LONG once both EMAs are populated.
    assert all(s == TargetPosition.LONG for s in states[199:260])


def test_trend_following_downtrend_produces_short():
    prices = [400.0 - i for i in range(260)]
    bars = _flat_bars(prices)

    states = tf.target_position_series(bars)
    reference = _independent_ema_cross_reference(bars)

    assert states == reference
    assert all(s == TargetPosition.FLAT for s in states[:199])
    assert all(s == TargetPosition.SHORT for s in states[199:260])


def test_trend_following_flat_on_constant_price_tie():
    prices = [100.0] * 260
    bars = _flat_bars(prices)
    states = tf.target_position_series(bars)
    assert all(s == TargetPosition.FLAT for s in states)


def test_gld_and_uso_strategy_functions_match_series():
    prices = [100.0 + i for i in range(260)]
    bars = _flat_bars(prices)
    expected = tf.target_position_series(bars)[-1]
    assert tf.gld_strategy(bars) == expected
    assert tf.uso_strategy(bars) == expected
