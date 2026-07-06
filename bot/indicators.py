"""Pure indicator functions: series/array in, series/array out, no I/O.

Shared by strategies (bot/strategies/*) and the backtester (bot/backtesting/backtest.py)
so both compute signals identically. Every function emits `None` for the
warm-up window instead of a partial-window value — Backtesting.md's
anti-look-ahead rule forbids signals derived from an under-populated window.
"""
import math


def sma(values, period):
    """Simple moving average. First `period - 1` entries are None."""
    if period <= 0:
        raise ValueError("period must be positive")
    n = len(values)
    out = [None] * n
    window_sum = 0.0
    for i, value in enumerate(values):
        window_sum += value
        if i >= period:
            window_sum -= values[i - period]
        if i >= period - 1:
            out[i] = window_sum / period
    return out


def rolling_std(values, period):
    """Rolling population standard deviation (ddof=0), matching common
    technical-analysis convention (e.g. Bollinger Bands). First `period - 1`
    entries are None.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    n = len(values)
    out = [None] * n
    for i in range(n):
        if i >= period - 1:
            window = values[i - period + 1 : i + 1]
            mean = sum(window) / period
            variance = sum((v - mean) ** 2 for v in window) / period
            out[i] = math.sqrt(variance)
    return out


def ema(values, period):
    """Exponential moving average. Seeded with the SMA of the first `period`
    values; the first `period - 1` entries are None (no partial-window seed).
    """
    if period <= 0:
        raise ValueError("period must be positive")
    n = len(values)
    out = [None] * n
    multiplier = 2.0 / (period + 1)
    seed = None
    for i, value in enumerate(values):
        if i < period - 1:
            continue
        if i == period - 1:
            seed = sum(values[: period]) / period
            out[i] = seed
            prev = seed
            continue
        prev = (value - prev) * multiplier + prev
        out[i] = prev
    return out


def rolling_max(values, period):
    """Rolling maximum, inclusive of the current bar (consistent with sma()'s
    and rolling_std()'s window convention: values[i - period + 1 : i + 1]).
    First `period - 1` entries are None.

    Callers implementing a breakout strategy (e.g. Donchian-channel style —
    "close breaks above the N-period high") must compare against the PRIOR
    bar's value (`rolling_max(...)[i - 1]`), not `[i]`: since this window
    includes the current bar's own high, and high >= close always holds for
    a valid bar, `close[i] > rolling_max(highs, period)[i]` can never be true
    — the current bar's own high makes the inclusive-window comparison
    self-defeating. Shifting to `[i - 1]` compares today's close against the
    completed prior N-bar high, which is the standard, non-self-referential
    breakout definition.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    n = len(values)
    out = [None] * n
    for i in range(n):
        if i >= period - 1:
            out[i] = max(values[i - period + 1 : i + 1])
    return out


def rolling_min(values, period):
    """Rolling minimum, inclusive of the current bar. See rolling_max()'s
    docstring for the same shift-by-one caveat when used for breakdown
    detection (low <= close always holds, so compare against `[i - 1]`).
    """
    if period <= 0:
        raise ValueError("period must be positive")
    n = len(values)
    out = [None] * n
    for i in range(n):
        if i >= period - 1:
            out[i] = min(values[i - period + 1 : i + 1])
    return out


def atr(high, low, close, period=14):
    """Average True Range. True range at index 0 is high[0] - low[0] (no
    prior close exists); every later index uses max(high-low,
    abs(high-prev_close), abs(low-prev_close)). ATR is the simple moving
    average of true range over `period`, so the first non-None value lands
    at index `period - 1`, consistent with sma()'s warm-up convention.
    """
    if not (len(high) == len(low) == len(close)):
        raise ValueError("high, low, close must be the same length")
    n = len(high)
    true_range = [0.0] * n
    for i in range(n):
        if i == 0:
            true_range[i] = high[i] - low[i]
        else:
            true_range[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
    return sma(true_range, period)
