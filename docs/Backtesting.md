# Backtesting Methodology

## Why the PDF's Methodology Is Rejected

The PDF prescribes: backtest 6 months, and "if any strategy has a negative Sharpe ratio... adjust the parameters." **This is the textbook overfitting loop** — tuning parameters against the same window used to evaluate them guarantees a good-looking, meaningless result. Six months is also far too short (one regime, and for the 4-hr EMA-cross strategy, potentially 1–3 total trades). This document replaces that methodology entirely.

## Principles

1. **Built before execution.** The backtester is completed and tested before any paper-trading code is written (Architecture.md build order).
2. **Event-driven, bar-by-bar.** Signals are computed using only data available at decision time. Decisions on bar N execute at bar N+1's open (or worse), never at bar N's close.
3. **Costs are mandatory.** Slippage + spread modeled per instrument. The PDF's flat 0.05% is a starting floor; crypto gets a higher assumption (0.10–0.15%) pending measurement. "Commission-free" ≠ cost-free.
4. **Adjusted data, documented.** Specify split/dividend adjustment for every dataset. GLD/USO must use adjusted series.
5. **Reproducible.** Same data + same code + same seed = identical results, on any machine. Pinned dependencies enforce this.

## Anti-Look-Ahead Enforcement (tested, not promised)

- `test_no_lookahead_shift`: shifting all future data by one bar must not change any signal already emitted.
- Indicator warm-up: no signals until the longest lookback (e.g., 200 EMA) is fully populated with real data — no partial-window values.
- Bar construction for 4-hr equity bars is defined explicitly (session-aligned) and tested, since "4-hour bars" during a 6.5-hour session is otherwise ambiguous.

## Data Requirements

- **Minimum 2 years** of history per instrument, spanning at least one meaningful drawdown/volatility regime. Six months is insufficient.
- Data validation on ingest: no gaps beyond calendar expectations, no zero/negative prices, no duplicate timestamps, volume sanity.
- Survivorship note: SPY/QQQ/GLD/USO/BTC is a universe chosen as-of-today. Results carry survivorship optimism; documented, not fixable at this scale.

## In-Sample / Out-of-Sample Protocol

- Split: first ~70% of history = in-sample (IS), final ~30% = out-of-sample (OOS).
- All parameter selection happens on IS only.
- OOS is run **once** per finalized parameter set. If parameters are changed after seeing OOS results, the OOS is burned: extend data or wait for new data before re-validating. This is logged in `docs/ParameterLog.md`.
- Preferred upgrade: walk-forward (rolling IS-optimize / OOS-test windows) once the basic protocol works.
- Every parameter set ever evaluated is logged. Trial count is reported alongside results — 200 trials that produced one "winner" is evidence of data mining, not edge.

## Minimum Evidence Gate (to unlock paper trading per strategy)

A strategy may be enabled for paper trading only if its backtest report shows ALL of:

- ≥ 100 trades in-sample (flag: trend-following on 4-hr bars will likely fail this — that is a finding, not an inconvenience; the strategy may need a longer history or be deferred)
- Positive OOS expectancy after costs
- OOS max drawdown ≤ 15%
- OOS results within a reasonable band of IS results (no cliff)
- Report artifact committed to `/reports` (metrics table + equity curve + parameter log reference)

## Reported Metrics

Per instrument and combined portfolio (with correlation filter active): total trades, win rate, average win/loss, profit factor, expectancy per trade, max drawdown, Sharpe (state the risk-free assumption), total return, exposure %, and trade duration distribution. Equity curve chart saved to `/reports`.

## Honest-Reporting Rules

- Never report IS results without OOS results beside them.
- Never report returns without drawdown and trade count.
- A strategy failing the gate is a successful outcome of the process — the default expectation is that some or all of the PDF's strategies fail after costs.

## Tests (tests/test_backtest.py)

`test_no_lookahead_shift`, `test_fill_at_next_bar`, `test_costs_applied_every_trade`, `test_warmup_no_early_signals`, `test_known_input_known_output` (synthetic data with hand-computed expected trades), `test_reproducibility_same_seed`, `test_4hr_bar_construction`.
