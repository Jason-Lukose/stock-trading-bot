"""Strategy 1 — Mean Reversion (SPY, QQQ). See docs/StrategySpec.md.

Hypothesis `[UNVERIFIED — PDF CLAIM]`: indices overextend intraday and
revert to the mean. Nothing here is believed until reproduced by the
backtester with costs and out-of-sample validation (StrategySpec.md's trust
policy — every parameter below is a hypothesis to test, not a fact).

Phase 4 scope note: NO STOP LOGIC. StrategySpec.md's "hard stop such that
loss = 1% of equity (via ATR sizing)" is the risk manager's job (Phase 5,
not yet built) per Architecture.md/RiskRules.md — see bot/strategies/base.py.
This module implements only entry (band breach) and exit (price returns to
the SMA); reported backtest numbers are stop-free.
"""
from bot.backtesting.backtest import TargetPosition
from bot.indicators import rolling_std, sma
from bot.strategies.base import closes

SMA_PERIOD = 20
STD_PERIOD = 20
K_SPY = 1.5   # [UNVERIFIED — PDF CLAIM] — PDF gives no evidence for this value
K_QQQ = 1.8   # [UNVERIFIED — PDF CLAIM] — PDF gives no evidence for this value


def target_position_series(window, k):
    """Forward-simulate the full target-position sequence for `window`
    (bars[0:i+1] for whatever `i` the caller passed). Recomputed from
    scratch on every call — no state is retained between calls (see
    bot/strategies/base.py's purity note).

    Entry long: price < SMA - k*sigma. Entry short: price > SMA + k*sigma.
    Exit (either side): price returns to the SMA. FLAT during warm-up
    (indicators.sma/rolling_std return None until their window is full).
    """
    closes_ = closes(window)
    sma_values = sma(closes_, SMA_PERIOD)
    std_values = rolling_std(closes_, STD_PERIOD)

    states = []
    state = TargetPosition.FLAT
    for i in range(len(window)):
        sma_i = sma_values[i]
        std_i = std_values[i]
        if sma_i is None or std_i is None:
            state = TargetPosition.FLAT
            states.append(state)
            continue

        price = closes_[i]
        upper = sma_i + k * std_i
        lower = sma_i - k * std_i

        if state is TargetPosition.FLAT:
            if price < lower:
                state = TargetPosition.LONG
            elif price > upper:
                state = TargetPosition.SHORT
        elif state is TargetPosition.LONG:
            if price >= sma_i:
                state = TargetPosition.FLAT
        elif state is TargetPosition.SHORT:
            if price <= sma_i:
                state = TargetPosition.FLAT

        states.append(state)
    return states


def make_strategy(k):
    """Return a strategy_fn(window) -> TargetPosition closed over k."""

    def strategy_fn(window):
        return target_position_series(window, k)[-1]

    return strategy_fn


spy_strategy = make_strategy(K_SPY)
qqq_strategy = make_strategy(K_QQQ)
