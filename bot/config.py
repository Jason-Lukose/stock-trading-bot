"""Configuration and risk ceilings.

Ceilings are hard-coded constants, never overridable by environment. Env vars
may only tighten a limit toward zero risk; any env value looser than its
ceiling is clamped to the ceiling and a warning is logged.
"""
import logging
import os

logger = logging.getLogger(__name__)

# --- Hard ceilings (never change without explicit human sign-off) ---
RISK_PER_TRADE_CEILING = 0.02       # 2.0% of equity
PORTFOLIO_DRAWDOWN_CEILING = 0.15   # 15%
DAILY_LOSS_CEILING = 0.05           # 5%
MAX_CONCURRENT_POSITIONS_CEILING = 5
MAX_NOTIONAL_PER_SYMBOL_CEILING = 0.25  # 25% of equity

# --- Defaults used when no env override is given ---
_DEFAULTS = {
    "RISK_PER_TRADE": 0.01,
    "PORTFOLIO_DRAWDOWN_HALT": 0.10,
    "DAILY_LOSS_HALT": 0.03,
    "MAX_CONCURRENT_POSITIONS": 5,
    "MAX_NOTIONAL_PER_SYMBOL": 0.20,
}

_CEILINGS = {
    "RISK_PER_TRADE": RISK_PER_TRADE_CEILING,
    "PORTFOLIO_DRAWDOWN_HALT": PORTFOLIO_DRAWDOWN_CEILING,
    "DAILY_LOSS_HALT": DAILY_LOSS_CEILING,
    "MAX_CONCURRENT_POSITIONS": MAX_CONCURRENT_POSITIONS_CEILING,
    "MAX_NOTIONAL_PER_SYMBOL": MAX_NOTIONAL_PER_SYMBOL_CEILING,
}


def _resolve_limit(name, env_getter=os.environ.get):
    """Resolve one limit: env value if present and <= ceiling, else clamp to ceiling."""
    ceiling = _CEILINGS[name]
    raw = env_getter(name)
    if raw is None:
        return _DEFAULTS[name]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid env value for %s=%r; using default %s", name, raw, _DEFAULTS[name])
        return _DEFAULTS[name]
    if value < 0:
        logger.warning("Env value for %s (%s) is negative; using default %s", name, value, _DEFAULTS[name])
        return _DEFAULTS[name]
    if value > ceiling:
        logger.warning(
            "Env value for %s (%s) exceeds ceiling (%s); clamping to ceiling",
            name, value, ceiling,
        )
        return ceiling
    return value


class RiskLimits:
    """Resolved, effective risk limits for this run (env-tightened, ceiling-clamped)."""

    def __init__(self, env_getter=os.environ.get):
        self.risk_per_trade = _resolve_limit("RISK_PER_TRADE", env_getter)
        self.portfolio_drawdown_halt = _resolve_limit("PORTFOLIO_DRAWDOWN_HALT", env_getter)
        self.daily_loss_halt = _resolve_limit("DAILY_LOSS_HALT", env_getter)
        self.max_concurrent_positions = _resolve_limit("MAX_CONCURRENT_POSITIONS", env_getter)
        self.max_notional_per_symbol = _resolve_limit("MAX_NOTIONAL_PER_SYMBOL", env_getter)


def get_risk_limits(env_getter=os.environ.get):
    return RiskLimits(env_getter)


# --- Alpaca API credentials (env only, never hardcoded/logged) ---
ALPACA_API_KEY_ENV = "ALPACA_API_KEY"
ALPACA_SECRET_KEY_ENV = "ALPACA_SECRET_KEY"
ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"


def get_alpaca_credentials(env_getter=os.environ.get):
    api_key = env_getter(ALPACA_API_KEY_ENV)
    secret_key = env_getter(ALPACA_SECRET_KEY_ENV)
    if not api_key or not secret_key:
        raise RuntimeError(
            f"Missing Alpaca credentials: set {ALPACA_API_KEY_ENV} and {ALPACA_SECRET_KEY_ENV} in the environment"
        )
    return api_key, secret_key
