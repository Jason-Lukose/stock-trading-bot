# Architecture

## Objective

Build a safe, testable, paper-trading-only research bot for Alpaca. The source PDF guide ("How to Build a Trading Bot with Claude That makes you $3k/month") is treated as an unverified educational starting point. No claim in it — strategy quality, parameters, or profit figures — is trusted until reproduced by our own backtester.

The bot supports: historical data ingestion, strategy signal generation, backtesting, risk management, paper trading, logging, and reporting.

**Live trading is not part of the MVP and no live code path exists.** The paper API URL is the only URL the execution client can use. Startup asserts the base URL contains `paper-api` and aborts otherwise.

## Core Modules

```text
bot/
  config.py           # env loading + HARD-CODED risk ceilings (env may only tighten)
  main.py             # entry point, startup checks, main loop
  portfolio.py        # local position/order state
  state.py            # persistence + broker reconciliation on startup
  killswitch.py       # HALT-file check; blocks all order submission
  logging_setup.py    # structured JSON-lines logging, UTC, run IDs
  calendar.py         # market hours, holidays, half-days; equity vs crypto sessions

  data/
    market_data.py    # historical + live bars, staleness detection, validation

  strategies/
    base.py           # strategy interface: bars in -> signal out (pure, no I/O)
    mean_reversion.py     # SPY, QQQ (15-min)
    momentum_breakout.py  # BTC/USD (1-hr)
    trend_following.py    # GLD, USO (4-hr)

  risk/
    risk_manager.py   # ALL orders pass through here; no bypass path exists

  backtesting/
    backtest.py       # event-driven simulator; built and tested BEFORE execution

  execution/
    alpaca_client.py  # alpaca-py SDK (NOT deprecated alpaca-trade-api),
                      # paper URL hardcoded, idempotent client_order_ids

  reporting/
    daily_report.py   # morning/evening summaries from logs
```

## Data Flow (one direction, no shortcuts)

```
market_data -> strategy.signal() -> risk_manager.check() -> alpaca_client.submit()
                                          |                        |
                                       (reject)                 (fill/reject)
                                          v                        v
                                  structured log            portfolio/state update
```

Every order must traverse this path. Strategies never talk to the execution client. The risk manager is the single choke point, which makes risk rules testable in isolation.

## Key Design Decisions

1. **SDK:** `alpaca-py`. The PDF recommends `alpaca-trade-api`, which is deprecated/archived. Do not use it.
2. **Reproducibility:** all dependencies pinned to exact versions with a lockfile. Backtest results must be identical across machines.
3. **Idempotency:** every order carries a deterministic `client_order_id` derived from (strategy, symbol, signal timestamp). A timeout is NOT a failure — reconcile against the broker before retrying.
4. **State recovery:** on startup, `state.py` queries Alpaca for open positions/orders and reconciles against local state before any trading logic runs. Mismatches halt the bot and log loudly.
5. **Kill switch:** presence of a `HALT` file in the repo root blocks all order submission, checked before every submit.
6. **Strategies are pure functions:** bars in, signal out, no side effects. This makes them trivially backtestable and unit-testable.
7. **Clocks:** all timestamps UTC. Equities trade per the market calendar; crypto is 24/7. `calendar.py` is the single source of truth for "is this instrument tradeable right now."

## Build Order (enforced)

1. Config + logging + tests scaffold
2. Data layer + indicators + tests
3. **Backtester + tests** (gate: must pass look-ahead and cost-model tests)
4. Strategies + backtests run and reviewed
5. Risk manager + tests
6. Execution client (paper) + mock tests
7. Paper trading launch per docs/PaperTradingPlan.md

Execution code (steps 5–6) may not begin until the backtester (step 3) is complete and its tests pass. This ordering is a constraint, not a suggestion.
