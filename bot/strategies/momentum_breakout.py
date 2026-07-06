"""Strategy 2 — Momentum Breakout (BTC/USD). See docs/StrategySpec.md.

Hypothesis `[UNVERIFIED — PDF CLAIM]`: crypto trends harder than indices;
ride breakouts. Nothing here is believed until reproduced by the backtester
with costs and out-of-sample validation.

Phase 4 scope note: NO STOP LOGIC. StrategySpec.md's "trailing stop: 2x
ATR(14) [UNVERIFIED — PDF parameter]" is the risk manager's job (Phase 5,
not yet built) — see bot/strategies/base.py. This module implements only
entry/exit via the breakout+volume signal.

FLAGGED, not silently implemented: StrategySpec.md's documented assumption
(c) is that "shorting BTC may not be supported on Alpaca spot crypto — if
not, 'go short' changes to 'exit,' which changes the strategy materially."
Alpaca's crypto trading is SPOT ONLY — there is no margin/short capability
for BTC/USD on Alpaca. This module therefore never emits TargetPosition.SHORT:
a downside breakout (close breaks below the 20-period low with volume
confirmation) degrades from "enter short" to "exit to FLAT" exactly as
StrategySpec.md anticipates. This is a material change from the PDF's
literal strategy (which assumes shorting is available) and must be treated
as such when interpreting results — the strategy that actually ran is
"long-only breakout", not "long/short breakout".
"""
from bot.backtesting.backtest import TargetPosition
from bot.indicators import rolling_max, rolling_min, sma
from bot.strategies.base import closes, highs, lows, volumes

PERIOD = 20
VOLUME_MULTIPLE = 1.5   # [UNVERIFIED — PDF CLAIM]
ATR_STOP_MULTIPLE = 2.0  # [UNVERIFIED — PDF parameter] — NOT enforced in Phase 4 (see module docstring)


def target_position_series(window):
    """Forward-simulate the full target-position sequence for `window`.

    Entry long: close breaks above the PRIOR PERIOD-bar high (rolling_max
    shifted by one bar — see indicators.rolling_max's docstring for why the
    comparison must use the prior, not current, window) AND volume >=
    VOLUME_MULTIPLE x the PERIOD-bar average volume.
    Exit long (never enter short — see module docstring): close breaks below
    the PRIOR PERIOD-bar low with the same volume confirmation.
    Otherwise: hold whatever position was already open (no new signal).
    """
    closes_ = closes(window)
    highs_ = highs(window)
    lows_ = lows(window)
    volumes_ = volumes(window)

    rolling_high = rolling_max(highs_, PERIOD)
    rolling_low = rolling_min(lows_, PERIOD)
    avg_volume = sma(volumes_, PERIOD)

    states = []
    state = TargetPosition.FLAT
    for i in range(len(window)):
        # Need the PRIOR bar's completed rolling high/low (index i - 1) and
        # this bar's average-volume value (index i) to be populated.
        if i < 1 or rolling_high[i - 1] is None or rolling_low[i - 1] is None or avg_volume[i] is None:
            states.append(state)
            continue

        volume_confirmed = volumes_[i] >= VOLUME_MULTIPLE * avg_volume[i]
        breakout_up = closes_[i] > rolling_high[i - 1] and volume_confirmed
        breakout_down = closes_[i] < rolling_low[i - 1] and volume_confirmed

        if breakout_up:
            state = TargetPosition.LONG
        elif breakout_down:
            state = TargetPosition.FLAT  # degrade "enter short" -> "exit" (no BTC spot shorting)

        states.append(state)
    return states


def btc_strategy(window):
    return target_position_series(window)[-1]
