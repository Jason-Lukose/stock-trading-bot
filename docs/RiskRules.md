# Risk Rules

Every rule below is (a) explicit, (b) enforced in `bot/risk/risk_manager.py` as the single choke point for all orders, and (c) covered by a named test in `tests/test_risk_manager.py`. A rule without a test does not exist.

## Rule 0 — Paper Trading Only (structural)

There is no live-trading code path in the MVP. The paper URL is hardcoded; startup asserts `paper-api` is in the base URL and aborts otherwise. `TRADING_MODE` env var cannot enable live trading — the code to do so does not exist.

**Test:** `test_startup_rejects_non_paper_url`

## Rule 1 — Hard Ceilings Live in Code, Not Config

`config.py` defines immutable ceilings. Environment variables may only TIGHTEN limits; any env value looser than the ceiling is clamped to the ceiling and a warning is logged.

| Limit | Ceiling (code) | Default (env) |
|---|---|---|
| Risk per trade | 2.0% of equity | 1.0% |
| Portfolio drawdown halt | 15% | 10% |
| Daily loss halt | 5% | 3% |
| Max concurrent positions | 5 | 5 |
| Max notional per symbol | 25% of equity | 20% |

**Tests:** `test_env_cannot_loosen_ceilings`, `test_env_can_tighten`

## Rule 2 — Per-Trade Risk via ATR Sizing

Position size = (equity × risk_per_trade) / (ATR(14) × ATR_multiple). Stop distance determines size, never the reverse. If computed size < 1 share/unit or > symbol notional cap, the trade is skipped, not resized upward.

**Tests:** `test_atr_sizing_math`, `test_undersized_trade_skipped`, `test_oversized_trade_capped`

## Rule 3 — Hard Stops, Immutable

Every order is paired with a stop at creation. The bot may tighten a trailing stop, never widen it, never cancel it without closing the position.

**Tests:** `test_stop_attached_at_entry`, `test_stop_never_widened`

## Rule 4 — Daily Loss Halt

If realized + unrealized P&L for the session ≤ −daily_loss_limit, close nothing automatically but block ALL new entries until next session. Logged as `RISK_HALT_DAILY`.

**Test:** `test_daily_loss_blocks_new_entries`

## Rule 5 — Portfolio Drawdown Circuit Breaker (manual re-arm)

If equity drops ≥ drawdown limit from its all-time-high watermark: close all positions, halt all trading, and require **manual re-arm** (deleting a `TRIPPED` state flag after human review). The bot never resumes on its own.

Note: the PDF uses 10% here but 15% as its backtest-adjustment threshold, without explanation. Our choice: 10% operational halt (env default), 15% absolute ceiling.

**Tests:** `test_drawdown_closes_and_halts`, `test_halt_requires_manual_rearm`

## Rule 6 — Order Sanity Checks

Reject any order where: notional > symbol cap; limit price deviates > 3% from last trade; quantity ≤ 0 or non-finite; symbol not in the approved instrument list; market is closed for that instrument (per `calendar.py`); or data for the signal is stale (last bar older than 2× bar interval).

**Tests:** one per condition (`test_reject_stale_data`, `test_reject_offmarket_hours`, …)

## Rule 7 — Correlation Filter (v1, acknowledged as minimal)

If SPY and QQQ are both long, block new BTC/USD longs. `[PDF rule]`

**Known limitations, documented deliberately:** ignores GLD/USO correlation, ignores short-side pile-ups, uses position state rather than measured correlation. This is a stub, not a portfolio risk model. Upgrading it is future work; pretending it's sufficient is not allowed.

**Test:** `test_correlation_filter_blocks_btc_long`

## Rule 8 — Error Circuit Breaker

After 5 consecutive API errors or 3 consecutive order rejections, halt order submission for 15 minutes and log `RISK_HALT_ERRORS`. After 3 such halts in one session, stop for the day.

**Test:** `test_error_circuit_breaker`

## Rule 9 — Kill Switch

If a `HALT` file exists in the repo root, no order is submitted, checked immediately before every submission. Creating the file is the documented emergency stop (see OperationsRunbook.md).

**Test:** `test_halt_file_blocks_submission`

## Rule 10 — PDT Precondition (live-trading gate, future)

SPY/QQQ intraday strategies are prohibited in any live account with < $25,000 equity (FINRA Pattern Day Trader rule). This gate is recorded now so it cannot be "forgotten" later. Not applicable to paper trading, blocking for any future live phase.

## No Bypass Principle

There is exactly one function that submits orders, and it is only reachable through the risk manager. Tests must verify no other call path exists (`test_no_execution_bypass` — grep/import-graph based).
