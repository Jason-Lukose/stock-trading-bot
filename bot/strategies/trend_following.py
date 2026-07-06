"""Strategy 3 — Trend Following (GLD, USO). See docs/StrategySpec.md.

Hypothesis `[UNVERIFIED — PDF CLAIM]`: commodities move in cleaner long
waves. Nothing here is believed until reproduced by the backtester with
costs and out-of-sample validation.

Documented assumption (a) from StrategySpec.md: 50/200 EMA on 4-hr bars
needs ~200x4 = 800+ hours of warm-up data, so trade count over any
reasonably-sized window will likely be tiny (possibly 1-3 trades per
instrument) — expected, not a bug (see docs/BuildPlan.md Phase 4 gate note).

Phase 4 scope note: NO STOP LOGIC. StrategySpec.md's "trailing stop: 3x
ATR(14) [UNVERIFIED — PDF parameter]" is the risk manager's job (Phase 5,
not yet built) — see bot/strategies/base.py.

Unlike momentum_breakout (BTC/USD, spot-only, no shorting), GLD/USO are
equity ETFs and shorting is assumed available (StrategySpec.md's "Exit /
short: 50 EMA crosses below 200 EMA" is implemented literally as SHORT, not
degraded to FLAT).
"""
from bot.backtesting.backtest import TargetPosition
from bot.indicators import ema
from bot.strategies.base import closes

FAST_PERIOD = 50
SLOW_PERIOD = 200


def target_position_series(window):
    """Target position is a direct (memoryless) function of which EMA is
    currently above the other — no forward-simulation/hysteresis needed,
    unlike mean_reversion/momentum_breakout, since the spec defines both
    the long and the short side explicitly rather than "hold until X".

    LONG when the 50 EMA is above the 200 EMA, SHORT when below, FLAT
    during warm-up or on the (float-arithmetic-improbable) exact tie.
    """
    closes_ = closes(window)
    fast = ema(closes_, FAST_PERIOD)
    slow = ema(closes_, SLOW_PERIOD)

    states = []
    for i in range(len(window)):
        fast_i = fast[i]
        slow_i = slow[i]
        if fast_i is None or slow_i is None:
            states.append(TargetPosition.FLAT)
        elif fast_i > slow_i:
            states.append(TargetPosition.LONG)
        elif fast_i < slow_i:
            states.append(TargetPosition.SHORT)
        else:
            states.append(TargetPosition.FLAT)
    return states


def gld_strategy(window):
    return target_position_series(window)[-1]


def uso_strategy(window):
    return target_position_series(window)[-1]
