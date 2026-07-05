# Decision Log

A running record of every significant decision made on this project, with the reasoning behind it. Each entry is written so the *why* survives — not just what we chose, but what we rejected and the risk it addressed. This log is the source of truth when a past choice is questioned, and it doubles as a record of engineering judgment for review.

Format: **[ID] Date — Decision.** Context / Rejected alternative / Reasoning.

---

## Phase 0 — Project Framing & Documentation

**[D-001] Treat the source PDF as unverified, not authoritative.**
The project is based on a marketing lead-magnet PDF ("How to Build a Trading Bot with Claude That makes you $3k/month") for a paid community. Rejected: taking its strategies, parameters, or profit claims at face value. Reasoning: the title itself makes an income claim the PDF later admits is "not typical," and PDF trading guides near-universally show cherry-picked, cost-free, in-sample, survivorship-biased results. Correct prior: every claim is a hypothesis to test, and each strategy is assumed unprofitable after costs until our own walk-forward evidence says otherwise. Every PDF-sourced parameter is tagged `[UNVERIFIED — PDF CLAIM]` in StrategySpec.md.

**[D-002] Documentation before code.**
Rejected: prompting Claude Code to "build the bot" from the PDF directly (as the PDF instructs). Reasoning: a system with real financial-risk surface needs its safety rules, strategy assumptions, and gates specified *before* implementation, so the build has a spec to conform to and gates that can actually stop it. Seven docs + a build plan were written and reviewed first.

**[D-003] Paper trading only; no live code path exists in the MVP.**
Rejected: the PDF's approach of flipping `.env` from paper to live URL. Reasoning: making live trading one environment-variable typo away is unacceptable. Instead: the paper API URL is hardcoded, startup asserts `paper-api` is in the URL and aborts otherwise, and no live-trading code is written at all. The strongest control against accidental live trading is that the code to do it does not exist. Reaching live would require a code change + key generation + a documented go/no-go — three independent human actions.

**[D-004] Risk ceilings live in code, not config.**
Rejected: the PDF's `MAX_RISK_PER_TRADE` / `MAX_PORTFOLIO_DRAWDOWN` as editable env vars. Reasoning: env-configurable limits can be *loosened* by editing a text file. Instead, `config.py` holds immutable hard ceilings; env vars may only *tighten* toward zero risk. Any env value looser than its ceiling is clamped to the ceiling with a logged warning. This makes risk testable (`test_env_cannot_loosen_ceilings`).

**[D-005] Every risk rule must have a named test.**
Reasoning: "a rule without a test does not exist." RiskRules.md pairs each of its 11 rules with a specific test name in `tests/test_risk_manager.py`, including `test_no_execution_bypass` (an import-graph proof that there is exactly one order-submission path).

## Phase 0 — Senior-Review Findings Baked Into Docs

**[D-006] PDT rule flagged as a blocking live-trading precondition.**
Discovery: the PDF's SPY/QQQ 15-min mean-reversion strategies are intraday day trading, which triggers FINRA's Pattern Day Trader rule — 4+ day trades in 5 business days requires $25,000 minimum equity or the account is restricted. The PDF never mentions this and even suggests starting with "whatever you can afford to lose." Decision: recorded as RiskRules.md Rule 10, a hard precondition for any future live phase. Not applicable to paper trading. Without $25k+, SPY/QQQ intraday strategies must be dropped or converted to swing timeframes.

**[D-007] Reject the PDF's backtesting methodology entirely (the overfitting loop).**
The PDF prescribes: backtest 6 months, and "if any strategy has a negative Sharpe, adjust the parameters." Reasoning: this is the textbook overfitting loop — tuning parameters against the same window used to evaluate them guarantees a good-looking, meaningless result. Replaced with: minimum 2 years of data spanning a real drawdown regime; a 70/30 in-sample/out-of-sample split; parameter selection on IS only; OOS run once per finalized parameter set (changing params after seeing OOS "burns" the OOS); every parameter set ever tried is logged, because trial count is itself an overfitting metric.

**[D-008] Minimum Evidence Gate to unlock paper trading per strategy.**
Reasoning: a strategy shouldn't reach paper trading on vibes. A strategy is enabled only if its backtest shows ALL of: ≥100 IS trades, positive OOS expectancy after costs, OOS max drawdown ≤ 15%, OOS results within a band of IS, and a committed report artifact. Explicitly anticipated: GLD/USO 50/200-EMA on 4-hr bars will likely fail the ≥100-trade gate — that is the process working, not a bug.

**[D-009] Replace "zero screen time" with an actively monitored, phased paper plan.**
Rejected: the PDF's hands-off "two texts a day" framing. Reasoning: paper trading's purpose is to find how the system breaks, which requires active monitoring, not passive income theater. Replaced with three phases: Shadow mode (1 wk, order submission disabled) → Single instrument (2+ wks) → Full portfolio (4+ wks or ≥30 trades). Daily human checklist; weekly divergence tracking against backtest expectations.

**[D-010] Use `alpaca-py`, not the PDF's `alpaca-trade-api`.**
Reasoning: `alpaca-trade-api` is deprecated/archived. An unmaintained SDK is a security and compatibility liability. `requirements.txt` migrated to `alpaca-py` with all dependencies pinned to exact versions for reproducibility.

**[D-011] Add the modules the PDF omitted.**
The PDF's architecture had no state persistence, broker reconciliation, kill switch, market calendar, or structured logging. Added all of them: `state.py` (startup reconciliation against the broker), `killswitch.py` (a `HALT` file blocks all order submission), `calendar.py` (the single source of truth for tradeability), and `logging_setup.py` (structured JSON-lines).

## Model Workflow

**[D-012] Three-model division of labor: Sonnet builds, Opus reviews, Fable/project-chat judges.**
Reasoning: match model to task. Sonnet (Claude Code) does ~80% of the build — scaffolding, data, indicators, strategies, tests. Opus does dedicated adversarial review passes on the two highest-risk modules (backtester, risk/execution). The project chat is reserved for three judgment checkpoints: interpreting backtest results against the evidence gate, the paper-trading go/no-go, and any strategy-parameter change. Rule of thumb: Sonnet when the spec answers the question, Opus when the code answers it, the judgment chat when neither does.

**[D-013] Author and reviewer must be different models.**
When Opus offered to implement the fixes it had just recommended, we declined. Reasoning: the value of the review comes from the reviewer being independent of the author. Fixes go back to Sonnet; Opus then re-reviews. This also keeps the cheaper model on mechanical work and the expensive model on what it's uniquely good at.

**[D-014] Gates are enforced by the build plan, not by memory.**
Reasoning: docs only work if the build actually stops at the gates they define. `BuildPlan.md` gives each phase a hard gate and a named model, so "did the review actually happen?" is written down rather than trusted to memory under deadline pressure. A phase is not done until its gate is green and committed.

## Phase 1 — Config, Logging, Test Scaffold

**[D-015] Phase 1 completed and gated green (14/14 tests).**
`config.py` (ceilings clamp, env-tightens-only, paper URL constant, env-only credentials) and `logging_setup.py` (JSON-lines, UTC ISO timestamps, per-run run_id, secret scrubbing including from tracebacks). Gate tests `test_env_cannot_loosen_ceilings`, `test_env_can_tighten`, and secret-scrubbing all pass.

## Phase 2 — Data Layer & Indicators

**[D-016] Indicators placed in a flat `bot/indicators.py`.**
Reasoning: both `bot/strategies/*` and `bot/backtesting/backtest.py` need them, and a flat module avoids circular dependencies. All indicators (SMA, rolling std [population/ddof=0], EMA [SMA-seeded], ATR [SMA of true range]) emit `None` through the warm-up window rather than a partial value — required by the anti-look-ahead rule and verified by a dedicated no-partial-window test.

**[D-017] Resolved the 4-hour equity bar ambiguity: session-aligned.**
The docs flagged that a "4-hour bar" in a 6.5-hour session is ill-defined. Decision: bars are anchored to the 9:30 open, producing one full 4h window (09:30–13:30) and one short 2.5h trailing window (13:30–16:00), since 6.5h doesn't divide evenly by 4. Half-days yield a single truncated window. Documented in a comment and covered by `test_4hr_bar_construction_session_aligned` plus a half-day case.

**[D-018] Alpaca SDK imports are lazy/injected, never at module scope.**
Reasoning: keeps `market_data.py` (and its unit tests) free of any network access or hard dependency on the package. Alpaca imports are deferred into `build_stock_client`/`build_crypto_client`; the rest of the module takes an injected client. This is what makes the "never hit the real API in unit tests" gate free rather than a later fight. Phase 2 gated green at 45/45.

**[D-019] Install `alpaca-py` before Phase 4, not necessarily Phase 3.**
`alpaca-py` is pinned but not yet installed in the dev environment. Because imports are lazy/injected, this doesn't block the backtester (which runs on historical/fixture data). It becomes blocking when Phase 4 needs real historical bars. Worth installing sooner to confirm the pinned version resolves and the import surface matches expectations.

## Phase 3 — Backtester

**[D-020] Backtester built by Sonnet, then subjected to an adversarial Opus review that distrusts green tests.**
Reasoning: the backtester is the highest-correctness-risk module — a look-ahead bug doesn't crash, it silently inflates every future backtest number, and every Phase 4 go/no-go decision rests on it being honest. The review prompt was written to audit the *tests* themselves, not just the code, because the dangerous bugs are the ones no test anticipated.

**[D-021] Opus review verdict: CHANGES REQUIRED — one result-invalidating P0 caught despite 45 green tests.**
The audit confirmed the look-ahead architecture, cost direction, warm-up handling, and drawdown math were correct, but found failures at trade-accounting boundaries the tests didn't exercise:
- **P0:** a position open at the final bar was dropped from all trade metrics and its exit cost never charged, so equity-curve metrics and trade metrics were computed on different trade sets. Critical because low-trade strategies (GLD/USO, 1–3 trades) could lose a large fraction of their trades from exactly the metrics the evidence gate reads, while `total_return` was inflated by an uncosted mark.
- **P1:** Sharpe was never annualized, making per-bar Sharpes across 15-min/1-hr/4-hr strategies non-comparable — corrupting the portfolio comparison the docs require.
- **P1:** the terminal position's exit cost was uncharged (cost side of the P0).
- **P2s:** the look-ahead test only checked decisions (not trades/equity), so it would pass a fill-leak; drawdown is close-to-close (understates intra-bar); no gap-awareness across overnight/irregular bars.
Decision: gate stays red; fixes go to Sonnet, then Opus re-reviews. This is the build-then-adversarially-review discipline working exactly as designed — green tests alone would have shipped a corrupted evidence pipeline.

---

## Standing Conventions

- **CLAUDE.md at the root of every project**, kept current for Claude Code sessions.
- **Commit is a checkpoint, not a blessing.** Work-in-progress is pushed before review so the reviewer audits exactly what's in the repo; review fixes land as their own follow-up commits, which makes the build-and-fix discipline visible in git history.
- **Stop and flag, never silently resolve**, when code would contradict a doc.
