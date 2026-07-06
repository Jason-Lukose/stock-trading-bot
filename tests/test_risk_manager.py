import ast
import os
from datetime import datetime, timedelta, timezone

import pytest

from bot import config
from bot import killswitch
from bot import state
from bot.risk import risk_manager as rm

MARKET_OPEN_TIME = datetime(2025, 6, 4, 14, 0, tzinfo=timezone.utc)   # Wed, 10:00 ET -- regular session
MARKET_CLOSED_TIME = datetime(2025, 6, 4, 2, 0, tzinfo=timezone.utc)  # ~10pm ET prior day -- closed


def _order(**overrides):
    defaults = dict(
        symbol="SPY", asset_class="equity", direction=1, is_entry=True,
        strategy="mean_reversion", signal_time=MARKET_OPEN_TIME,
        last_bar_time=MARKET_OPEN_TIME, bar_interval_seconds=900,
        reference_price=100.0, limit_price=100.0, atr=2.0,
    )
    defaults.update(overrides)
    return rm.OrderRequest(**defaults)


# --- Rule 0: paper trading only -----------------------------------------------

def test_startup_rejects_non_paper_url():
    with pytest.raises(RuntimeError):
        rm.RiskManager(base_url="https://api.alpaca.markets")


def test_startup_accepts_paper_url():
    manager = rm.RiskManager(base_url="https://paper-api.alpaca.markets")
    assert manager is not None


# --- Rule 1: hard ceilings (reused from bot.config) ---------------------------

def test_env_cannot_loosen_ceilings():
    limits = config.get_risk_limits(lambda k, d=None: {"RISK_PER_TRADE": "0.5"}.get(k, d))
    manager = rm.RiskManager(limits=limits)
    assert manager.limits.risk_per_trade == config.RISK_PER_TRADE_CEILING


def test_env_can_tighten():
    limits = config.get_risk_limits(lambda k, d=None: {"RISK_PER_TRADE": "0.005"}.get(k, d))
    manager = rm.RiskManager(limits=limits)
    assert manager.limits.risk_per_trade == 0.005


# --- Rule 2: ATR-based position sizing ----------------------------------------

def test_atr_sizing_math():
    manager = rm.RiskManager()
    equity = 100_000.0
    atr = 20.0  # chosen so raw quantity's notional (50 * $100 = $5,000) stays under the 20% notional cap ($20,000)
    order = _order(atr=atr)

    decision = manager.evaluate_order(order, equity, MARKET_OPEN_TIME)

    assert decision.approved
    expected_qty = (equity * manager.limits.risk_per_trade) / (atr * rm.ATR_MULTIPLE_BY_STRATEGY["mean_reversion"])
    assert decision.order.quantity == pytest.approx(expected_qty)
    assert decision.order.stop_price == pytest.approx(order.reference_price - order.direction * atr * 1.0)


def test_undersized_trade_skipped():
    manager = rm.RiskManager()
    order = _order(atr=100_000.0)  # huge ATR -> tiny raw quantity
    decision = manager.evaluate_order(order, 100_000.0, MARKET_OPEN_TIME)
    assert not decision.approved
    assert decision.reason == rm.RejectionReason.UNDERSIZED.value


def test_oversized_trade_capped():
    manager = rm.RiskManager()
    equity = 100_000.0
    order = _order(atr=0.0001, reference_price=100.0, limit_price=100.0)  # tiny ATR -> huge raw quantity

    decision = manager.evaluate_order(order, equity, MARKET_OPEN_TIME)

    assert decision.approved  # capped, not rejected -- see module docstring's Rule 2/6 reconciliation
    expected_cap_qty = (manager.limits.max_notional_per_symbol * equity) / 100.0
    assert decision.order.quantity == pytest.approx(expected_cap_qty)


# --- Rule 3: hard stops, immutable ---------------------------------------------

def test_stop_attached_at_entry():
    manager = rm.RiskManager()
    order = _order()
    decision = manager.evaluate_order(order, 100_000.0, MARKET_OPEN_TIME)
    assert decision.approved
    assert decision.order.stop_price is not None
    expected_stop = order.reference_price - order.direction * order.atr * rm.ATR_MULTIPLE_BY_STRATEGY["mean_reversion"]
    assert decision.order.stop_price == pytest.approx(expected_stop)


def test_stop_never_widened():
    # Long: stop can only move up. A widening candidate (lower) is ignored.
    assert rm.tighten_stop(1, current_stop=95.0, candidate_stop=90.0) == 95.0
    assert rm.tighten_stop(1, current_stop=95.0, candidate_stop=97.0) == 97.0
    # Short: stop can only move down. A widening candidate (higher) is ignored.
    assert rm.tighten_stop(-1, current_stop=105.0, candidate_stop=110.0) == 105.0
    assert rm.tighten_stop(-1, current_stop=105.0, candidate_stop=102.0) == 102.0


# --- Rule 4: daily loss halt ----------------------------------------------------

def test_daily_loss_blocks_new_entries():
    manager = rm.RiskManager()
    equity = 100_000.0
    manager.record_daily_pnl(-manager.limits.daily_loss_halt * equity - 1, equity)

    entry_decision = manager.evaluate_order(_order(), equity, MARKET_OPEN_TIME)
    assert not entry_decision.approved
    assert entry_decision.reason == rm.RejectionReason.DAILY_LOSS_HALT.value

    # "close nothing automatically... block ALL new entries" -- exits must still pass.
    exit_decision = manager.evaluate_order(_order(is_entry=False, exit_quantity=5.0), equity, MARKET_OPEN_TIME)
    assert exit_decision.approved


def test_reset_daily_clears_halt():
    manager = rm.RiskManager()
    equity = 100_000.0
    manager.record_daily_pnl(-manager.limits.daily_loss_halt * equity - 1, equity)
    assert manager.daily_halted
    manager.reset_daily()
    assert not manager.daily_halted
    assert manager.evaluate_order(_order(), equity, MARKET_OPEN_TIME).approved


# --- Rule 5: portfolio drawdown circuit breaker (manual re-arm) ----------------

def test_drawdown_closes_and_halts(tmp_path):
    flag = state.TrippedFlag(path=str(tmp_path / "TRIPPED"))
    manager = rm.RiskManager(tripped_flag=flag)

    manager.update_equity(100_000.0)
    manager.update_equity(100_000.0 * (1 - manager.limits.portfolio_drawdown_halt) - 1)  # breach

    assert flag.is_tripped()
    entry_decision = manager.evaluate_order(_order(), 50_000.0, MARKET_OPEN_TIME)
    assert not entry_decision.approved
    assert entry_decision.reason == rm.RejectionReason.DRAWDOWN_TRIPPED.value

    # "close all positions" -- exits must still be reachable while tripped.
    exit_decision = manager.evaluate_order(_order(is_entry=False, exit_quantity=3.0), 50_000.0, MARKET_OPEN_TIME)
    assert exit_decision.approved


def test_halt_requires_manual_rearm(tmp_path):
    path = tmp_path / "TRIPPED"
    flag = state.TrippedFlag(path=str(path))
    manager = rm.RiskManager(tripped_flag=flag)

    manager.update_equity(100_000.0)
    manager.update_equity(80_000.0)  # 20% drawdown >= 15% ceiling -> trip
    assert flag.is_tripped()

    manager.update_equity(100_000.0)  # equity fully recovers...
    decision = manager.evaluate_order(_order(), 100_000.0, MARKET_OPEN_TIME)
    assert not decision.approved  # ...the bot never resumes on its own

    path.unlink()  # human re-arm: delete the flag after review
    decision2 = manager.evaluate_order(_order(), 100_000.0, MARKET_OPEN_TIME)
    assert decision2.approved


# --- Rule 6: order sanity checks -----------------------------------------------

def test_reject_stale_data():
    manager = rm.RiskManager()
    order = _order(last_bar_time=MARKET_OPEN_TIME - timedelta(hours=5))
    decision = manager.evaluate_order(order, 100_000.0, MARKET_OPEN_TIME)
    assert not decision.approved
    assert decision.reason == rm.RejectionReason.STALE_DATA.value


def test_reject_offmarket_hours():
    manager = rm.RiskManager()
    decision = manager.evaluate_order(_order(), 100_000.0, MARKET_CLOSED_TIME)
    assert not decision.approved
    assert decision.reason == rm.RejectionReason.MARKET_CLOSED.value


def test_reject_unapproved_symbol():
    manager = rm.RiskManager()
    decision = manager.evaluate_order(_order(symbol="TSLA"), 100_000.0, MARKET_OPEN_TIME)
    assert not decision.approved
    assert decision.reason == rm.RejectionReason.UNAPPROVED_SYMBOL.value


def test_reject_invalid_price():
    manager = rm.RiskManager()
    for bad_price in (float("nan"), -5.0, 0.0):
        decision = manager.evaluate_order(_order(reference_price=bad_price), 100_000.0, MARKET_OPEN_TIME)
        assert not decision.approved
        assert decision.reason == rm.RejectionReason.INVALID_PRICE.value


def test_reject_price_deviation():
    manager = rm.RiskManager()
    order = _order(reference_price=100.0, limit_price=110.0)  # 10% > 3% max deviation
    decision = manager.evaluate_order(order, 100_000.0, MARKET_OPEN_TIME)
    assert not decision.approved
    assert decision.reason == rm.RejectionReason.PRICE_DEVIATION.value


def test_reject_max_concurrent_positions():
    limits = config.get_risk_limits(lambda k, d=None: {"MAX_CONCURRENT_POSITIONS": "2"}.get(k, d))
    manager = rm.RiskManager(limits=limits)
    manager.open_positions = {"SPY": 1, "QQQ": 1}

    new_symbol_decision = manager.evaluate_order(_order(symbol="GLD", strategy="trend_following"), 100_000.0, MARKET_OPEN_TIME)
    assert not new_symbol_decision.approved
    assert new_symbol_decision.reason == rm.RejectionReason.MAX_CONCURRENT_POSITIONS.value

    # Adding to an already-open symbol isn't blocked by the concurrency cap.
    existing_symbol_decision = manager.evaluate_order(_order(symbol="SPY"), 100_000.0, MARKET_OPEN_TIME)
    assert existing_symbol_decision.approved


# --- Rule 7: correlation filter v1 ----------------------------------------------

def test_correlation_filter_blocks_btc_long():
    manager = rm.RiskManager()
    manager.open_positions = {"SPY": 1, "QQQ": 1}
    order = _order(symbol="BTC/USD", asset_class="crypto", direction=1, strategy="momentum_breakout")

    decision = manager.evaluate_order(order, 100_000.0, MARKET_OPEN_TIME)
    assert not decision.approved
    assert decision.reason == rm.RejectionReason.CORRELATION_FILTER.value

    manager2 = rm.RiskManager()
    manager2.open_positions = {"SPY": 1, "QQQ": -1}  # not BOTH long
    decision2 = manager2.evaluate_order(order, 100_000.0, MARKET_OPEN_TIME)
    assert decision2.approved


# --- Rule 8: error circuit breaker ----------------------------------------------

def test_error_circuit_breaker():
    manager = rm.RiskManager()
    now = MARKET_OPEN_TIME
    for _ in range(rm.CONSECUTIVE_API_ERROR_LIMIT):
        manager.record_api_error(now)

    assert manager.halt_until == now + rm.ERROR_HALT_DURATION
    decision = manager.evaluate_order(_order(), 100_000.0, now)
    assert not decision.approved
    assert decision.reason == rm.RejectionReason.ERROR_HALT.value

    later = manager.halt_until + timedelta(seconds=1)
    decision2 = manager.evaluate_order(_order(), 100_000.0, later)
    assert decision2.approved


def test_error_circuit_breaker_via_order_rejections():
    manager = rm.RiskManager()
    now = MARKET_OPEN_TIME
    for _ in range(rm.CONSECUTIVE_REJECTION_LIMIT):
        manager.record_order_rejection(now)
    assert manager.halt_until == now + rm.ERROR_HALT_DURATION


def test_error_circuit_breaker_three_halts_stops_for_day():
    manager = rm.RiskManager()
    now = MARKET_OPEN_TIME
    for _ in range(rm.MAX_ERROR_HALTS_PER_DAY):
        for _ in range(rm.CONSECUTIVE_API_ERROR_LIMIT):
            manager.record_api_error(now)
        now = manager.halt_until + timedelta(seconds=1)

    assert manager.daily_halted
    decision = manager.evaluate_order(_order(last_bar_time=now), 100_000.0, now)
    assert not decision.approved
    assert decision.reason == rm.RejectionReason.DAILY_LOSS_HALT.value


def test_success_and_fill_reset_counters():
    manager = rm.RiskManager()
    now = MARKET_OPEN_TIME
    manager.record_api_error(now)
    manager.record_api_error(now)
    manager.record_api_success()
    assert manager.consecutive_api_errors == 0

    manager.record_order_rejection(now)
    manager.record_order_fill()
    assert manager.consecutive_rejections == 0


# --- Rule 9: kill switch ---------------------------------------------------------

def test_halt_file_blocks_submission():
    submitted = []
    manager = rm.RiskManager(halt_checker=lambda: True)  # simulate HALT file present
    decision = manager.submit_order(_order(), 100_000.0, MARKET_OPEN_TIME, submitted.append)
    assert not decision.approved
    assert decision.reason == rm.RejectionReason.KILL_SWITCH.value
    assert submitted == []


def test_submit_order_calls_submit_fn_when_approved():
    submitted = []
    manager = rm.RiskManager(halt_checker=lambda: False)
    decision = manager.submit_order(_order(), 100_000.0, MARKET_OPEN_TIME, submitted.append)
    assert decision.approved
    assert len(submitted) == 1
    assert manager.open_positions["SPY"] == 1


def test_real_killswitch_file_blocks_submission(tmp_path):
    (tmp_path / killswitch.DEFAULT_HALT_FILENAME).write_text("emergency stop\n")
    manager = rm.RiskManager(halt_checker=lambda: killswitch.is_halted(repo_root=str(tmp_path)))
    decision = manager.submit_order(_order(), 100_000.0, MARKET_OPEN_TIME, lambda o: None)
    assert not decision.approved
    assert decision.reason == rm.RejectionReason.KILL_SWITCH.value


# --- Rule 10: PDT precondition (future live gate) --------------------------------

def test_pdt_precondition_not_applicable_to_paper():
    rm.assert_pdt_precondition(is_live=False, symbol="SPY", equity=1000.0)  # must never raise


def test_pdt_precondition_blocks_future_live_path():
    with pytest.raises(RuntimeError):
        rm.assert_pdt_precondition(is_live=True, symbol="SPY", equity=1000.0)
    rm.assert_pdt_precondition(is_live=True, symbol="SPY", equity=30_000.0)   # enough equity, ok
    rm.assert_pdt_precondition(is_live=True, symbol="GLD", equity=1000.0)     # not PDT-restricted, ok


# --- No Bypass Principle ----------------------------------------------------------

def test_no_execution_bypass():
    """There is exactly one function that submits orders, and the only call
    site of the injected `submit_fn` callable is inside it (grep/import-graph
    based, per RiskRules.md).
    """
    bot_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
    submit_fn_call_sites = []
    submit_order_defs = []

    for root, _, files in os.walk(bot_dir):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            with open(path) as f:
                source = f.read()
            tree = ast.parse(source, filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "submit_fn":
                    submit_fn_call_sites.append((path, node.lineno))
                if isinstance(node, ast.FunctionDef) and node.name == "submit_order":
                    submit_order_defs.append((path, node.lineno))

    assert len(submit_order_defs) == 1, submit_order_defs
    assert submit_order_defs[0][0].endswith(os.path.join("risk", "risk_manager.py"))
    assert len(submit_fn_call_sites) == 1, submit_fn_call_sites
    assert submit_fn_call_sites[0][0] == submit_order_defs[0][0]
