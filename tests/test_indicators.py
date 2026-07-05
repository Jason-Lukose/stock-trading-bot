import math

from bot import indicators


def test_sma_hand_computed():
    values = [1, 2, 3, 4, 5]
    result = indicators.sma(values, period=3)
    assert result[0] is None
    assert result[1] is None
    assert result[2] == 2.0   # mean(1,2,3)
    assert result[3] == 3.0   # mean(2,3,4)
    assert result[4] == 4.0   # mean(3,4,5)


def test_rolling_std_hand_computed():
    values = [1, 2, 3, 4, 5]
    result = indicators.rolling_std(values, period=3)
    assert result[0] is None
    assert result[1] is None
    expected = math.sqrt(2.0 / 3.0)  # population std of (1,2,3)/(2,3,4)/(3,4,5)
    assert math.isclose(result[2], expected, rel_tol=1e-9)
    assert math.isclose(result[3], expected, rel_tol=1e-9)
    assert math.isclose(result[4], expected, rel_tol=1e-9)


def test_ema_hand_computed():
    values = [1, 2, 3, 4, 5]
    result = indicators.ema(values, period=3)
    assert result[0] is None
    assert result[1] is None
    assert result[2] == 2.0   # seed = mean(1,2,3)
    assert result[3] == 3.0   # (4-2)*0.5+2
    assert result[4] == 4.0   # (5-3)*0.5+3


def test_atr_hand_computed():
    high = [10, 10, 10, 10, 10]
    low = [8, 8, 8, 8, 8]
    close = [9, 9, 9, 9, 9]
    result = indicators.atr(high, low, close, period=3)
    assert result[0] is None
    assert result[1] is None
    assert result[2] == 2.0
    assert result[3] == 2.0
    assert result[4] == 2.0


def test_no_partial_window_values_emitted():
    """Warm-up rule: no signal-eligible indicator value before its window
    is fully populated with real data (Backtesting.md anti-look-ahead rule).
    """
    values = list(range(1, 21))
    period = 20

    sma_result = indicators.sma(values, period)
    std_result = indicators.rolling_std(values, period)
    ema_result = indicators.ema(values, period)
    atr_result = indicators.atr(values, values, values, period)

    for series in (sma_result, std_result, ema_result, atr_result):
        assert all(v is None for v in series[: period - 1]), series
        assert series[period - 1] is not None


def test_sma_rejects_nonpositive_period():
    try:
        indicators.sma([1, 2, 3], 0)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_atr_rejects_mismatched_lengths():
    try:
        indicators.atr([1, 2], [1], [1, 2])
        assert False, "expected ValueError"
    except ValueError:
        pass
