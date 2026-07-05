"""Event-driven, bar-by-bar backtest simulator.

Design for anti-look-ahead by construction (Backtesting.md):
  - A strategy is a pure function `bars_so_far -> TargetPosition`
    (Architecture.md: "strategies are pure functions: bars in, signal out").
    It is called with `bars[: i + 1]` at bar i — it physically cannot see
    bar i+1 or later, so `test_no_lookahead_shift` (mutating everything after
    a cutoff) cannot change any decision at or before that cutoff.
  - The target position decided from bar i's data is executed at bar i+1's
    OPEN, never at bar i's close. Flipping directly from long to short takes
    two bars (exit this fill, re-enter next), since only one fill happens
    per bar.
"""
import math
import random
import statistics
from dataclasses import dataclass, field
from enum import Enum


class TargetPosition(Enum):
    FLAT = 0
    LONG = 1
    SHORT = -1


@dataclass(frozen=True)
class CostModel:
    """Combined slippage+spread cost applied to every fill, in basis points of
    the fill price. Deliberately has NO default and rejects non-positive
    values in __post_init__, so a backtest can never silently run cost-free.

    `jitter_bps` adds a bounded random adverse/favorable jitter per fill
    (uniform in [-jitter_bps, jitter_bps]) to approximate real-world fill
    variance. If set, `run_backtest` requires an explicit `seed` so results
    stay reproducible.
    """
    cost_bps: float
    jitter_bps: float = 0.0

    def __post_init__(self):
        if self.cost_bps <= 0:
            raise ValueError(
                "cost_bps must be > 0 — zero-cost backtests are not allowed. "
                "Alpaca is commission-free, but 'commission-free' != 'cost-free' (Backtesting.md)."
            )
        if self.jitter_bps < 0:
            raise ValueError("jitter_bps must be >= 0")

    def fill_price(self, price, is_buy, rng=None):
        bps = self.cost_bps
        if self.jitter_bps:
            bps += (rng or random).uniform(-self.jitter_bps, self.jitter_bps)
        adjustment = price * (bps / 10000.0)
        return price + adjustment if is_buy else price - adjustment


# PDF's flat 0.05% is a documented floor for equities; crypto slippage is
# typically worse, so 0.12% (midpoint of the 0.10-0.15% documented range) is
# used as the crypto default. Both are starting assumptions pending
# measurement against real fills, not verified facts.
EQUITY_COST_FLOOR_BPS = 5.0
CRYPTO_COST_BPS = 12.0


def equity_cost_model(cost_bps=EQUITY_COST_FLOOR_BPS, jitter_bps=0.0):
    return CostModel(cost_bps=cost_bps, jitter_bps=jitter_bps)


def crypto_cost_model(cost_bps=CRYPTO_COST_BPS, jitter_bps=0.0):
    return CostModel(cost_bps=cost_bps, jitter_bps=jitter_bps)


@dataclass
class Trade:
    direction: int          # 1 long, -1 short
    entry_time: object
    entry_price: float
    exit_time: object
    exit_price: float
    quantity: float

    @property
    def pnl(self):
        return self.direction * (self.exit_price - self.entry_price) * self.quantity

    @property
    def duration(self):
        return self.exit_time - self.entry_time


@dataclass
class _Position:
    direction: int
    entry_time: object
    entry_price: float
    quantity: float


@dataclass
class BacktestResult:
    trades: list
    equity_curve: list          # list of (timestamp, mark_to_market_equity)
    exposure_bars: int
    total_bars: int
    initial_equity: float
    decisions: list = field(default_factory=list)  # list of (index, TargetPosition), for look-ahead testing
    metrics: dict = field(default_factory=dict)


def split_is_oos(bars, is_fraction=0.7, warmup_context_bars=0):
    """Split bars into in-sample / out-of-sample per Backtesting.md's ~70/30
    protocol. If `warmup_context_bars` > 0, that many trailing IS bars are
    prepended to the OOS slice so indicators have real warm-up data at the
    OOS boundary; the caller must pass a matching `warmup_bars` to the OOS
    `run_backtest` call so those context bars stay non-tradeable (they were
    seen during IS parameter selection and must not generate OOS trades).

    Returns (is_bars, oos_bars, oos_warmup_bars).
    """
    if not 0 < is_fraction < 1:
        raise ValueError("is_fraction must be between 0 and 1")
    split_index = int(len(bars) * is_fraction)
    is_bars = bars[:split_index]
    oos_start = max(0, split_index - warmup_context_bars)
    oos_bars = bars[oos_start:]
    oos_warmup_bars = split_index - oos_start
    return is_bars, oos_bars, oos_warmup_bars


def run_backtest(bars, strategy_fn, cost_model, quantity=1.0, initial_equity=100000.0,
                  warmup_bars=0, seed=None):
    """Run one event-driven backtest over `bars` (chronological, no gaps the
    caller hasn't already validated via bot.data.market_data.validate_bars).

    `strategy_fn(window) -> TargetPosition` is called once per bar with
    `bars[: i + 1]` — never later bars. `cost_model` is required (see
    CostModel — cannot be zero-cost).
    """
    if cost_model.jitter_bps and seed is None:
        raise ValueError("cost_model.jitter_bps is set; an explicit seed is required for reproducibility")
    rng = random.Random(seed) if seed is not None else None

    n = len(bars)
    position = None
    pending = None  # ('enter', direction) | ('exit', None)
    trades = []
    equity_curve = []
    decisions = []
    cash = initial_equity
    exposure_bars = 0

    for i in range(n):
        bar = bars[i]

        # 1. Execute any action queued from the previous bar's decision, at THIS bar's open.
        if pending is not None:
            kind, direction = pending
            if kind == "exit" and position is not None:
                is_buy = position.direction == -1  # buy to cover a short, sell to close a long
                exit_price = cost_model.fill_price(bar.open, is_buy, rng)
                trade = Trade(
                    direction=position.direction,
                    entry_time=position.entry_time,
                    entry_price=position.entry_price,
                    exit_time=bar.timestamp,
                    exit_price=exit_price,
                    quantity=position.quantity,
                )
                cash += trade.pnl
                trades.append(trade)
                position = None
            elif kind == "enter" and position is None:
                is_buy = direction == 1
                entry_price = cost_model.fill_price(bar.open, is_buy, rng)
                position = _Position(
                    direction=direction,
                    entry_time=bar.timestamp,
                    entry_price=entry_price,
                    quantity=quantity,
                )
            pending = None

        # 2. Mark to market at this bar's close.
        unrealized = 0.0
        if position is not None:
            exposure_bars += 1
            unrealized = position.direction * (bar.close - position.entry_price) * position.quantity
        equity_curve.append((bar.timestamp, cash + unrealized))

        # 3. Decide using only bars[: i + 1] (no look-ahead), queue for i+1's open.
        if i < warmup_bars:
            target = TargetPosition.FLAT
        else:
            target = strategy_fn(bars[: i + 1])
            if target is None:
                target = TargetPosition.FLAT
        decisions.append((i, target))

        current_direction = position.direction if position is not None else 0
        if target.value != current_direction:
            if current_direction != 0:
                pending = ("exit", None)
            elif target is not TargetPosition.FLAT:
                pending = ("enter", target.value)

    metrics = compute_metrics(trades, equity_curve, initial_equity, n, exposure_bars)

    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        exposure_bars=exposure_bars,
        total_bars=n,
        initial_equity=initial_equity,
        decisions=decisions,
        metrics=metrics,
    )


def compute_metrics(trades, equity_curve, initial_equity, total_bars, exposure_bars, periods_per_year=None):
    """Compute the metrics required by Backtesting.md's "Reported Metrics"
    section. Risk-free rate assumption for Sharpe: 0% (short lookback bars
    make a nonzero risk-free drag negligible and this avoids picking an
    arbitrary rate).
    """
    total_trades = len(trades)
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]

    win_rate = len(wins) / total_trades if total_trades else None
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (math.inf if gross_profit > 0 else None)
    expectancy = statistics.mean([t.pnl for t in trades]) if trades else None

    final_equity = equity_curve[-1][1] if equity_curve else initial_equity
    total_return = (final_equity - initial_equity) / initial_equity

    equity_values = [e for _, e in equity_curve]
    max_drawdown = _max_drawdown(equity_values)

    bar_returns = _bar_returns(equity_values)
    sharpe = _sharpe(bar_returns, periods_per_year)

    exposure_pct = (exposure_bars / total_bars) if total_bars else 0.0

    durations = [t.duration for t in trades]

    return {
        "total_trades": total_trades,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "sharpe_risk_free_assumption": 0.0,
        "total_return": total_return,
        "exposure_pct": exposure_pct,
        "trade_durations": durations,
    }


def _max_drawdown(equity_values):
    if not equity_values:
        return 0.0
    peak = equity_values[0]
    max_dd = 0.0
    for value in equity_values:
        peak = max(peak, value)
        if peak > 0:
            dd = (peak - value) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def _bar_returns(equity_values):
    returns = []
    for prev, curr in zip(equity_values, equity_values[1:]):
        if prev != 0:
            returns.append((curr - prev) / prev)
    return returns


def _sharpe(bar_returns, periods_per_year=None):
    if len(bar_returns) < 2:
        return None
    mean_return = statistics.mean(bar_returns)
    stdev = statistics.pstdev(bar_returns)
    if stdev == 0:
        return None
    sharpe = mean_return / stdev
    if periods_per_year:
        sharpe *= math.sqrt(periods_per_year)
    return sharpe
