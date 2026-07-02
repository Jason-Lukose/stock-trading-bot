# Architecture

## Objective

Build a safe, testable paper-trading research bot for Alpaca.

The bot should support:

- Historical data ingestion
- Strategy signal generation
- Backtesting
- Risk management
- Paper trading
- Logging and reporting

Live trading is not part of the MVP.

## Core Modules

```text
bot/
  config.py
  main.py
  portfolio.py

  data/
    market_data.py

  strategies/
    mean_reversion.py
    momentum_breakout.py
    trend_following.py

  risk/
    risk_manager.py

  backtesting/
    backtest.py

  execution/
    alpaca_client.py

  reporting/
    daily_report.py
