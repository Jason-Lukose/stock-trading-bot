# Build Plan

The operational sequence for building this bot. Each phase has a **deliverable**, a **gate** that must pass before the next phase starts, and the **model** that does the work. Gates are hard stops, not suggestions — a phase is not "done" until its gate is green and committed.

Read this alongside `Architecture.md` (module design), `RiskRules.md`, and `Backtesting.md` (the specs each phase implements). If a phase's implementation disagrees with a doc, stop and flag it — do not silently pick one.

## Model Assignment (summary)

- **Sonnet (Claude Code):** ~80% of the build — scaffolding, data, indicators, strategies, tests, wiring.
- **Opus:** dedicated review passes on the two highest-risk modules — the backtester and the risk/execution layer.
- **Fable (project chat):** three judgment checkpoints only — (1) interpreting backtest results against the evidence gate, (2) the paper-trading go/no-go, (3) any decision to change a strategy parameter.

## Phase 0 — Docs & Repo Hygiene ✅ (this phase)

**Deliverable:** All seven docs in `/docs` filled, `CLAUDE.md` at repo root, `requirements.txt` migrated to `alpaca-py` with pinned versions.
**Gate:** Docs committed. `requirements.txt` contains no `alpaca-trade-api`. `.env` is gitignored and no keys are in git history (run a secret scan once).
**Model:** Fable (done) → you commit.

## Phase 1 — Config, Logging, Test Scaffold

**Deliverable:** `config.py` (hard-coded ceilings, env may only tighten — per RiskRules Rule 1), `logging_setup.py` (structured JSON-lines, UTC, run IDs, key scrubbing), `pytest` scaffold + CI that runs it.
**Gate:** `test_env_cannot_loosen_ceilings`, `test_env_can_tighten`, and a log-scrubbing test all pass. `pytest` green in CI.
**Model:** Sonnet.

## Phase 2 — Data Layer & Indicators

**Deliverable:** `data/market_data.py` (historical + live bars, staleness detection, ingest validation), indicator functions (SMA, rolling σ, EMA, ATR), `calendar.py` (equity vs crypto sessions, holidays, half-days, 4-hr equity bar construction).
**Gate:** Indicators tested against hand-computed known values (`test_indicators.py`). Bar-construction test passes. Data validation rejects gaps/zeros/dupes.
**Model:** Sonnet.

## Phase 3 — Backtester ⚠️ (highest correctness risk)

**Deliverable:** `backtesting/backtest.py` — event-driven, bar-by-bar, fills at N+1 open, costs applied every trade, IS/OOS split support.
**Gate:** ALL of `test_no_lookahead_shift`, `test_fill_at_next_bar`, `test_costs_applied_every_trade`, `test_warmup_no_early_signals`, `test_known_input_known_output`, `test_reproducibility_same_seed`, `test_4hr_bar_construction` pass — **AND an Opus review pass** specifically hunting look-ahead bias, off-by-one bar errors, and cost-model mistakes.
**Model:** Sonnet builds → **Opus reviews**. Execution code (Phase 5+) may not begin until this gate is green.

## Phase 4 — Strategies + Backtest Runs

**Deliverable:** `strategies/base.py` + the three strategy modules (pure functions, bars in → signal out). Backtests run per Backtesting.md protocol (2yr data, IS/OOS, costs). Report artifacts committed to `/reports`.
**Gate:** Each strategy either passes the Minimum Evidence Gate (Backtesting.md) or is documented as failed/deferred. **Fable checkpoint:** interpret results — are they overfit? enough trades? proceed to paper?
**Model:** Sonnet builds & runs → **Fable interprets results**. (Expect GLD/USO 50/200-EMA to fail the ≥100-trade gate — that is the process working, not a bug.)

## Phase 5 — Risk Manager

**Deliverable:** `risk/risk_manager.py` — the single order choke point. Every rule in RiskRules.md enforced, `killswitch.py`, `state.py` (persistence + startup reconciliation).
**Gate:** Every named test in RiskRules.md passes, including `test_no_execution_bypass` (import-graph proof there's one order path). Reconciliation tested.
**Model:** Sonnet builds → **Opus reviews** (failure modes, race conditions).

## Phase 6 — Execution Client (paper)

**Deliverable:** `execution/alpaca_client.py` — `alpaca-py`, paper URL hardcoded, startup `paper-api` assertion, deterministic `client_order_id`, timeout-safe reconciliation.
**Gate:** `test_startup_rejects_non_paper_url` passes. All execution tests run against a **mock Alpaca layer** — never the real API in unit tests. End-to-end synthetic test: data → signal → risk check → mock order → state update.
**Model:** Sonnet builds → **Opus reviews** (shares the review pass with Phase 5).

## Phase 7 — Reporting

**Deliverable:** `reporting/daily_report.py` — morning/evening summaries generated from logs (Telegram/Slack, outbound-only).
**Gate:** Report reconstructs a day's activity from logs alone (no orphan data).
**Model:** Sonnet.

## Phase 8 — Paper Trading Launch

**Deliverable:** Run the phased paper plan (Shadow → Single instrument → Full portfolio) per PaperTradingPlan.md.
**Gate:** All Phase A–C entry criteria met. **Fable checkpoint:** the go/no-go review before first paper execution.
**Model:** Sonnet for any fixes → **Fable for go/no-go**. Weekly divergence reviews rotate Opus/Fable.

## What Is Explicitly NOT in This Plan

Live trading. There is no live phase in the MVP. Reaching it would require a separate, formally-approved plan gated behind the PDT precondition (RiskRules Rule 10) and a full go/no-go — none of which is authorized by completing the above.
