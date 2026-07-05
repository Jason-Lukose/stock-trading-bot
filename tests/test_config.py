import pytest

from bot import config


def _env(overrides):
    return lambda key, default=None: overrides.get(key, default)


def test_env_cannot_loosen_ceilings():
    """An env value looser than the hard ceiling is clamped to the ceiling, not honored."""
    overrides = {
        "RISK_PER_TRADE": "0.10",              # ceiling is 0.02
        "PORTFOLIO_DRAWDOWN_HALT": "0.50",     # ceiling is 0.15
        "DAILY_LOSS_HALT": "0.20",             # ceiling is 0.05
        "MAX_CONCURRENT_POSITIONS": "50",      # ceiling is 5
        "MAX_NOTIONAL_PER_SYMBOL": "0.90",     # ceiling is 0.25
    }
    limits = config.get_risk_limits(_env(overrides))

    assert limits.risk_per_trade == config.RISK_PER_TRADE_CEILING
    assert limits.portfolio_drawdown_halt == config.PORTFOLIO_DRAWDOWN_CEILING
    assert limits.daily_loss_halt == config.DAILY_LOSS_CEILING
    assert limits.max_concurrent_positions == config.MAX_CONCURRENT_POSITIONS_CEILING
    assert limits.max_notional_per_symbol == config.MAX_NOTIONAL_PER_SYMBOL_CEILING


def test_env_can_tighten():
    """An env value tighter than the ceiling and default is honored exactly."""
    overrides = {
        "RISK_PER_TRADE": "0.005",
        "PORTFOLIO_DRAWDOWN_HALT": "0.05",
        "DAILY_LOSS_HALT": "0.01",
        "MAX_CONCURRENT_POSITIONS": "2",
        "MAX_NOTIONAL_PER_SYMBOL": "0.10",
    }
    limits = config.get_risk_limits(_env(overrides))

    assert limits.risk_per_trade == 0.005
    assert limits.portfolio_drawdown_halt == 0.05
    assert limits.daily_loss_halt == 0.01
    assert limits.max_concurrent_positions == 2
    assert limits.max_notional_per_symbol == 0.10


def test_defaults_used_when_no_env_set():
    limits = config.get_risk_limits(_env({}))

    assert limits.risk_per_trade == 0.01
    assert limits.portfolio_drawdown_halt == 0.10
    assert limits.daily_loss_halt == 0.03
    assert limits.max_concurrent_positions == 5
    assert limits.max_notional_per_symbol == 0.20


def test_invalid_env_value_falls_back_to_default():
    limits = config.get_risk_limits(_env({"RISK_PER_TRADE": "not-a-number"}))
    assert limits.risk_per_trade == 0.01


def test_negative_env_value_falls_back_to_default():
    limits = config.get_risk_limits(_env({"DAILY_LOSS_HALT": "-0.5"}))
    assert limits.daily_loss_halt == 0.03


def test_get_alpaca_credentials_requires_both_keys():
    with pytest.raises(RuntimeError):
        config.get_alpaca_credentials(_env({"ALPACA_API_KEY": "key-only"}))


def test_get_alpaca_credentials_returns_both():
    overrides = {"ALPACA_API_KEY": "abc", "ALPACA_SECRET_KEY": "xyz"}
    api_key, secret_key = config.get_alpaca_credentials(_env(overrides))
    assert api_key == "abc"
    assert secret_key == "xyz"


def test_paper_base_url_is_paper():
    assert "paper-api" in config.ALPACA_PAPER_BASE_URL
