"""Strategy interface contract.

A strategy is a pure function: `bars_so_far -> TargetPosition`
(Architecture.md: "strategies are pure functions: bars in, signal out").
It matches exactly how bot.backtesting.backtest.run_backtest calls
`strategy_fn`: once per bar, with `bars[: i + 1]` — never later bars — so
look-ahead is impossible by construction, not by convention.

No I/O, no side effects, and — deliberately, for Phase 4 — NO STOP LOGIC.
StrategySpec.md assigns each strategy a stop (mean reversion: 1%-equity hard
stop; momentum breakout: 2x ATR trailing; trend following: 3x ATR trailing),
but Architecture.md and RiskRules.md assign ALL stop/sizing logic to
bot/risk/risk_manager.py (Phase 5, not yet built) as the single order choke
point. Phase 4 strategies therefore emit signal-only entries/exits (e.g.
"price returns to the SMA", "opposite EMA cross") with no stop-loss exit.
This is a deliberate scope decision (see docs/DecisionLog.md), not an
oversight: the Phase 4 backtest numbers are stop-free and will look
different — likely more drawdown, longer losing trades — once Phase 5 adds
real stops. Every strategy module and the backtest report must say so
explicitly, not bury it.

A strategy that needs "hold current position until some condition changes"
(mean reversion's hold-until-SMA-return, momentum breakout's hold-until-
opposite-breakout) is implemented as a forward simulation over the ENTIRE
window argument on every call — never as hidden/cached state in the
strategy's closure. This keeps the function referentially transparent
(same `window` argument always produces the same output, independent of
call history or call order) while still expressing "no memory beyond what's
in the bars" naturally. It costs O(len(window)) per call, same order as
recomputing the underlying indicators (sma/rolling_std/ema/...) on each call
already costs — no new asymptotic overhead.
"""


def closes(bars):
    return [b.close for b in bars]


def highs(bars):
    return [b.high for b in bars]


def lows(bars):
    return [b.low for b in bars]


def volumes(bars):
    return [b.volume for b in bars]
