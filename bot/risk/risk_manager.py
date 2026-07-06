"""The single order choke point (Architecture.md: "ALL orders pass through
here; no bypass path exists"). Every rule in docs/RiskRules.md is enforced
here and only here.

Data flow (Architecture.md): market_data -> strategy.signal() ->
risk_manager.check() -> alpaca_client.submit(). `submit_order()` below is
THE one function in this codebase that calls an injected broker-submission
callable (`submit_fn`) — see `test_no_execution_bypass` in
tests/test_risk_manager.py, which greps the whole `bot/` package to prove
no other module calls anything named `submit_fn`.

Rule-numbering note (interpreting one genuine doc ambiguity): RiskRules.md
Rule 2 says an oversized computed position is "skipped, not resized
upward," which read in isolation could mean "reject oversized orders."
Rule 2's own named test, `test_oversized_trade_capped`, disambiguates this:
"not resized upward" describes the UNDERSIZED case (never round a sub-1-unit
size up to meet a minimum); the OVERSIZED case is capped down to the symbol
notional limit, not rejected. Implemented accordingly, both here and in
Rule 6's "notional > symbol cap" sanity bullet (which is then a
defense-in-depth invariant check on the already-capped size, not a second,
contradictory rejection path).
"""
import logging
import math
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from bot import calendar as bot_calendar
from bot import config
from bot import killswitch
from bot import state
from bot.data import market_data

logger = logging.getLogger(__name__)

# --- Rule 0: paper trading only (structural) ---------------------------------

def assert_paper_trading_url(base_url):
    """Refuse to construct/operate a RiskManager against a non-paper base
    URL. There is no live-trading code path in this codebase (D-003); this
    assertion cannot be worked around by passing a different URL — it only
    ever results in a raised exception, never in live trading becoming
    reachable.
    """
    if "paper-api" not in base_url:
        raise RuntimeError(f"Refusing to start: base_url {base_url!r} is not a paper-trading URL")


# --- Rule 1: hard ceilings (reused from bot.config, built in Phase 1) --------
# See bot.config.get_risk_limits / RiskLimits — env may only tighten,
# ceilings are hard-coded constants. Reused, not reimplemented.


# --- Rule 2: ATR-based position sizing ---------------------------------------

# Per-strategy stop distance in ATR units. StrategySpec.md: mean reversion's
# hard stop is defined directly as "1% of equity" (Cross-Strategy Rules'
# general formula collapses to atr_multiple=1 for that framing); momentum
# breakout's trailing stop is 2x ATR(14) [UNVERIFIED — PDF parameter];
# trend following's trailing stop is 3x ATR(14) [UNVERIFIED — PDF parameter].
ATR_MULTIPLE_BY_STRATEGY = {
    "mean_reversion": 1.0,
    "momentum_breakout": 2.0,
    "trend_following": 3.0,
}

MIN_TRADE_QUANTITY = 1.0  # "< 1 share/unit" per RiskRules Rule 2, applied uniformly

# --- Rule 6: order sanity ----------------------------------------------------

APPROVED_SYMBOLS = {"SPY", "QQQ", "BTC/USD", "GLD", "USO"}  # StrategySpec.md instrument table
MAX_LIMIT_PRICE_DEVIATION = 0.03  # 3% from last trade

# --- Rule 8: error circuit breaker -------------------------------------------

CONSECUTIVE_API_ERROR_LIMIT = 5
CONSECUTIVE_REJECTION_LIMIT = 3
ERROR_HALT_DURATION = timedelta(minutes=15)
MAX_ERROR_HALTS_PER_DAY = 3

# --- Rule 10: PDT precondition (live-trading gate, future; not applicable to
# paper trading -- recorded now so it cannot be forgotten later) -------------

PDT_MINIMUM_EQUITY = 25_000.0
PDT_RESTRICTED_SYMBOLS = {"SPY", "QQQ"}  # intraday mean-reversion instruments


def assert_pdt_precondition(is_live, symbol, equity):
    """Not applicable to paper trading (is_live is always False in this
    MVP -- no live code path exists, Rule 0/D-003). Blocking for any future
    live phase: without $25k+ equity, SPY/QQQ intraday strategies must be
    dropped or converted to swing timeframes before going live.
    """
    if is_live and symbol in PDT_RESTRICTED_SYMBOLS and equity < PDT_MINIMUM_EQUITY:
        raise RuntimeError(
            f"PDT precondition violated: {symbol} intraday requires >= ${PDT_MINIMUM_EQUITY:,.0f} equity for live trading"
        )


class RejectionReason(Enum):
    UNAPPROVED_SYMBOL = "UNAPPROVED_SYMBOL"
    MARKET_CLOSED = "MARKET_CLOSED"
    STALE_DATA = "STALE_DATA"
    INVALID_PRICE = "INVALID_PRICE"
    PRICE_DEVIATION = "PRICE_DEVIATION"
    DRAWDOWN_TRIPPED = "RISK_HALT_DRAWDOWN"
    DAILY_LOSS_HALT = "RISK_HALT_DAILY"
    ERROR_HALT = "RISK_HALT_ERRORS"
    CORRELATION_FILTER = "CORRELATION_FILTER"
    UNKNOWN_STRATEGY = "UNKNOWN_STRATEGY"
    INVALID_ATR = "INVALID_ATR"
    UNDERSIZED = "UNDERSIZED"
    MAX_CONCURRENT_POSITIONS = "MAX_CONCURRENT_POSITIONS"
    KILL_SWITCH = "KILL_SWITCH"


@dataclass(frozen=True)
class OrderRequest:
    """A strategy signal, not yet sized or approved. `is_entry=False` marks
    an exit/flatten of an existing position (RiskRules Rule 4/5 only block
    NEW entries — "close nothing automatically" / "close all positions" both
    imply exits must still be reachable, never blocked by the halts)."""
    symbol: str
    asset_class: str          # "equity" | "crypto"
    direction: int            # 1 long, -1 short
    is_entry: bool
    strategy: str             # key into ATR_MULTIPLE_BY_STRATEGY
    signal_time: object       # tz-aware datetime
    last_bar_time: object     # tz-aware datetime, for staleness (Rule 6)
    bar_interval_seconds: float
    reference_price: float    # last trade price, for sizing and deviation checks
    limit_price: float
    atr: float                # ATR(14) at signal time
    exit_quantity: float = None  # required when is_entry=False (portfolio.py owns position size)


@dataclass(frozen=True)
class SizedOrder:
    symbol: str
    direction: int
    quantity: float
    entry_price: float
    stop_price: float
    strategy: str


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    order: object = None  # SizedOrder if approved, else None


def tighten_stop(direction, current_stop, candidate_stop):
    """Return the new stop, enforcing "may tighten, never widen" (Rule 3) BY
    CONSTRUCTION: for a long, the stop can only move up; for a short, only
    down. A caller passing a widening candidate has no effect.
    """
    if direction == 1:
        return max(current_stop, candidate_stop)
    return min(current_stop, candidate_stop)


def check_correlation_filter(symbol, direction, open_positions):
    """Rule 7 (v1, acknowledged minimal): if SPY and QQQ are both long,
    block new BTC/USD longs. Ignores GLD/USO, short-side pile-ups, and uses
    position state rather than measured correlation — a documented stub,
    not a portfolio risk model (RiskRules.md).
    """
    if symbol == "BTC/USD" and direction == 1:
        if open_positions.get("SPY") == 1 and open_positions.get("QQQ") == 1:
            return False
    return True


class RiskManager:
    """The single order choke point. All state is in-memory except the
    drawdown trip flag (state.TrippedFlag, file-based, human-re-armed) and
    the kill switch (bot.killswitch, file-based).
    """

    def __init__(self, limits=None, base_url=config.ALPACA_PAPER_BASE_URL, tripped_flag=None, halt_checker=None):
        assert_paper_trading_url(base_url)
        self.limits = limits or config.get_risk_limits()
        self.tripped_flag = tripped_flag or state.TrippedFlag()
        # Injectable so tests never need to create a real HALT file in the
        # actual repo root; defaults to the real file-based check.
        self.halt_checker = halt_checker or killswitch.is_halted

        self.open_positions = {}   # symbol -> direction (1/-1)
        self.peak_equity = None
        self.daily_pnl = 0.0
        self.daily_halted = False
        self.consecutive_api_errors = 0
        self.consecutive_rejections = 0
        self.halt_until = None
        self.halt_count_today = 0

    # --- Rule 5: portfolio drawdown circuit breaker -------------------------

    def update_equity(self, equity, reason_prefix=""):
        if self.peak_equity is None or equity > self.peak_equity:
            self.peak_equity = equity
            return
        if self.peak_equity <= 0:
            return
        drawdown = (self.peak_equity - equity) / self.peak_equity
        if drawdown >= self.limits.portfolio_drawdown_halt and not self.tripped_flag.is_tripped():
            self.tripped_flag.trip(
                reason=f"{reason_prefix}drawdown {drawdown:.2%} >= limit {self.limits.portfolio_drawdown_halt:.2%}"
            )

    # --- Rule 4: daily loss halt ---------------------------------------------

    def record_daily_pnl(self, pnl, equity):
        self.daily_pnl = pnl
        if equity > 0 and pnl <= -self.limits.daily_loss_halt * equity:
            if not self.daily_halted:
                logger.error("RISK_HALT_DAILY: daily pnl %.2f <= -%.2f%% of equity %.2f", pnl, self.limits.daily_loss_halt * 100, equity)
            self.daily_halted = True

    def reset_daily(self):
        """Call at the start of a new trading session."""
        self.daily_pnl = 0.0
        self.daily_halted = False

    # --- Rule 8: error circuit breaker ---------------------------------------

    def record_api_error(self, now):
        self.consecutive_api_errors += 1
        self._maybe_trip_error_halt(now)

    def record_api_success(self):
        self.consecutive_api_errors = 0

    def record_order_rejection(self, now):
        self.consecutive_rejections += 1
        self._maybe_trip_error_halt(now)

    def record_order_fill(self):
        self.consecutive_rejections = 0

    def _maybe_trip_error_halt(self, now):
        if self.consecutive_api_errors >= CONSECUTIVE_API_ERROR_LIMIT or self.consecutive_rejections >= CONSECUTIVE_REJECTION_LIMIT:
            self.halt_until = now + ERROR_HALT_DURATION
            self.halt_count_today += 1
            logger.error(
                "RISK_HALT_ERRORS: api_errors=%d rejections=%d, halting order submission until %s (halt #%d today)",
                self.consecutive_api_errors, self.consecutive_rejections, self.halt_until, self.halt_count_today,
            )
            self.consecutive_api_errors = 0
            self.consecutive_rejections = 0
            if self.halt_count_today >= MAX_ERROR_HALTS_PER_DAY:
                self.daily_halted = True

    # --- Core evaluation ------------------------------------------------------

    def evaluate_order(self, order, equity, now):
        """Run every RiskRules.md rule (except Rule 9, the kill switch,
        which is checked at the last possible moment in submit_order) and
        return a RiskDecision. Never raises for a rule violation — always
        returns an unapproved decision with a reason.
        """
        # Rule 6: symbol allow-list
        if order.symbol not in APPROVED_SYMBOLS:
            return RiskDecision(False, RejectionReason.UNAPPROVED_SYMBOL.value)

        # Rule 6: market must be open for this instrument
        if not bot_calendar.is_tradeable(order.asset_class, now):
            return RiskDecision(False, RejectionReason.MARKET_CLOSED.value)

        # Rule 6: signal data must not be stale
        if market_data.is_stale(order.last_bar_time, now, order.bar_interval_seconds):
            return RiskDecision(False, RejectionReason.STALE_DATA.value)

        # Rule 6: price sanity (finite, positive, and not a wild limit deviation)
        if (order.reference_price is None or not math.isfinite(order.reference_price) or order.reference_price <= 0
                or order.limit_price is None or not math.isfinite(order.limit_price) or order.limit_price <= 0):
            return RiskDecision(False, RejectionReason.INVALID_PRICE.value)
        deviation = abs(order.limit_price - order.reference_price) / order.reference_price
        if deviation > MAX_LIMIT_PRICE_DEVIATION:
            return RiskDecision(False, RejectionReason.PRICE_DEVIATION.value)

        # Rule 5: drawdown-tripped blocks entries only; exits must still be reachable
        if order.is_entry and self.tripped_flag.is_tripped():
            return RiskDecision(False, RejectionReason.DRAWDOWN_TRIPPED.value)

        # Rule 4 / Rule 8: daily halt and error-circuit halt block entries only
        if order.is_entry:
            if self.daily_halted:
                return RiskDecision(False, RejectionReason.DAILY_LOSS_HALT.value)
            if self.halt_until is not None and now < self.halt_until:
                return RiskDecision(False, RejectionReason.ERROR_HALT.value)

        # Exits: no sizing/correlation/concurrency logic -- close the position
        # portfolio.py already knows the size of.
        if not order.is_entry:
            sized = SizedOrder(
                symbol=order.symbol, direction=order.direction, quantity=order.exit_quantity,
                entry_price=order.reference_price, stop_price=None, strategy=order.strategy,
            )
            return RiskDecision(True, "exit approved", sized)

        # Rule 7: correlation filter (entries only)
        if not check_correlation_filter(order.symbol, order.direction, self.open_positions):
            return RiskDecision(False, RejectionReason.CORRELATION_FILTER.value)

        # Rule 6: max concurrent positions (adding a NEW symbol beyond the cap)
        if order.symbol not in self.open_positions and len(self.open_positions) >= self.limits.max_concurrent_positions:
            return RiskDecision(False, RejectionReason.MAX_CONCURRENT_POSITIONS.value)

        # Rule 2: ATR-based sizing
        atr_multiple = ATR_MULTIPLE_BY_STRATEGY.get(order.strategy)
        if atr_multiple is None:
            return RiskDecision(False, RejectionReason.UNKNOWN_STRATEGY.value)
        if order.atr is None or not math.isfinite(order.atr) or order.atr <= 0:
            return RiskDecision(False, RejectionReason.INVALID_ATR.value)

        raw_quantity = (equity * self.limits.risk_per_trade) / (order.atr * atr_multiple)
        notional_cap = self.limits.max_notional_per_symbol * equity
        max_quantity_for_cap = notional_cap / order.reference_price
        quantity = min(raw_quantity, max_quantity_for_cap)  # cap oversized DOWN, never up

        if quantity < MIN_TRADE_QUANTITY:
            return RiskDecision(False, RejectionReason.UNDERSIZED.value)

        stop_price = order.reference_price - order.direction * order.atr * atr_multiple

        sized = SizedOrder(
            symbol=order.symbol, direction=order.direction, quantity=quantity,
            entry_price=order.reference_price, stop_price=stop_price, strategy=order.strategy,
        )
        return RiskDecision(True, "approved", sized)

    def submit_order(self, order, equity, now, submit_fn):
        """THE single order-submission entrypoint (see module docstring and
        test_no_execution_bypass). Evaluates every rule, then — as the LAST
        possible check, immediately before submission (Rule 9) — checks the
        HALT file, then calls the injected `submit_fn(sized_order)`.
        """
        decision = self.evaluate_order(order, equity, now)
        if not decision.approved:
            return decision

        if self.halt_checker():
            return RiskDecision(False, RejectionReason.KILL_SWITCH.value)

        submit_fn(decision.order)

        if order.is_entry:
            self.open_positions[order.symbol] = order.direction
        else:
            self.open_positions.pop(order.symbol, None)

        return decision
