# CLAUDE.md — Trading Bot Project

## What this project is
A paper-trading research bot for Alpaca. Educational/research only. The source PDF guide is treated as an UNVERIFIED starting point — its strategies and profit claims are assumed false until reproduced in our own backtester with realistic costs.

## Non-negotiable safety rules (never modify without explicit human sign-off)
1. **Paper trading only.** The paper API URL is hardcoded. `alpaca_client.py` must assert the base URL contains `paper-api` at startup and refuse to run otherwise. Never add a live-trading code path.
2. **Risk ceilings live in code, not config.** Environment variables may TIGHTEN risk limits, never loosen them past the hardcoded ceilings in `config.py`. Never remove or raise a ceiling.
3. **API keys via environment only.** Never hardcode keys, never log them, scrub them from exception traces. `.env` is gitignored — keep it that way.
4. **No order submission without passing the risk manager.** Every order flows: signal → risk check → order. No bypass paths, ever, including in tests against the real paper API.
5. **Kill switch:** if a file named `HALT` exists in the repo root, no orders may be submitted. Check before every submission.

## Architecture rules
- SDK: `alpaca-py` (NOT the deprecated `alpaca-trade-api`).
- All dependencies pinned to exact versions. Update pins deliberately, never casually.
- Every order gets a deterministic `client_order_id` so retries are idempotent. A request timeout does NOT mean the order failed — reconcile before retrying.
- On startup, reconcile local state against Alpaca's reported positions/orders before doing anything else.
- All timestamps UTC. Structured JSON-lines logging. Every trade decision must be reconstructable from logs alone (signal inputs → signal → risk outcome → order → fill).
- Backtester must be complete and tested BEFORE any paper execution code runs.

## Backtesting rules
- No look-ahead: signals use only data available at decision time. There is a test that verifies this by data shifting — keep it passing.
- Model transaction costs (spread + slippage) even though Alpaca is commission-free.
- Strategy parameters are tuned in-sample only. Out-of-sample data is never used for tuning. Log every parameter set tried.
- A strategy cannot be enabled for paper trading without a backtest report meeting the minimums in docs/Backtesting.md.

## Testing rules
- New logic requires tests in the same PR/commit. Run `pytest` before declaring any task done.
- Execution client is tested against a mock Alpaca layer — never hit the real API in unit tests.
- Risk manager tests must include: limits clamp to ceilings, daily-loss halt fires, drawdown halt requires manual re-arm, oversized orders rejected.

## Workflow conventions
- Read the relevant doc in /docs before implementing its module; the docs are the spec.
- Small commits, descriptive messages, one module per session where practical.
- If a doc and the code disagree, stop and flag it — don't silently pick one.
- If the task requires weakening any safety rule above, stop and ask Jason.
- **Log significant decisions to docs/DecisionLog.md.** At the end of any session that makes a real engineering or process decision (architecture choice, rejected alternative, a bug found in review, a gate call, a parameter change), append a numbered entry (next `D-###`) recording the decision, what was rejected, and the reasoning. The *why* and the rejected alternative are the point — they're what make the decision defensible later. Do not silently make a decision that belongs in the log.

## Owner
Jason Lukose. Goal: a safe, reproducible research platform — profitability claims come only from our own walk-forward evidence.
