# Operations Runbook

## Normal Startup

1. Confirm `.env` present, `HALT` file absent, `TRIPPED` flag absent.
2. `python -m bot.main` — startup sequence runs automatically:
   - Assert base URL contains `paper-api` (abort otherwise)
   - Load config; log effective limits (post-ceiling-clamp)
   - Reconcile local state vs Alpaca positions/orders — mismatch ⇒ halt with `STATE_MISMATCH` log; do not trade until resolved
   - Verify data feed freshness per instrument
3. Confirm the startup summary log line shows: mode=paper, instruments, limits, reconciliation=clean.

## Normal Shutdown

`Ctrl+C` / SIGTERM ⇒ graceful: stop signal loop, leave positions and stops in place (stops live server-side at Alpaca), flush logs, persist state. Positions are NOT auto-closed on shutdown — that's a human decision.

## Emergency Stop (kill switch)

`touch HALT` in repo root ⇒ all order submission blocked immediately (checked before every submit). To also flatten: use the Alpaca paper dashboard directly (close all positions there), then investigate. Remove `HALT` only after written cause analysis.

## Circuit Breaker Tripped (`TRIPPED` flag present)

The drawdown halt fired and closed everything. Required before re-arm: read the logs for the trigger window, write a short incident note in `/reports/incidents/`, verify account state on Alpaca dashboard, then delete `TRIPPED`. The bot will not re-arm itself.

## Common Failures

| Symptom | Likely cause | Action |
|---|---|---|
| `STATE_MISMATCH` at startup | Order filled during downtime; manual dashboard action | Compare log vs dashboard; adopt broker state as truth; restart |
| `RISK_HALT_ERRORS` | API outage, rate limiting | Check Alpaca status page; bot auto-resumes after cooldown; 3 halts ⇒ done for the day |
| Stale-data warnings | Feed outage, calendar bug, clock drift | Verify system clock (NTP); check feed; no trading occurs on stale data by design |
| Order rejected | Sanity check or broker rejection | Log shows which rule; broker rejections include reason from API |
| Timeout on submit | Network | Do NOT resubmit manually; bot reconciles by client_order_id — verify in logs |
| Crash loop under process manager | Bug | Stop the process manager; `HALT`; investigate before restarting |

## Logs & Data

- Structured JSON-lines in `logs/`, UTC, one file per day, run ID on every line. Every trade decision reconstructable: signal inputs → signal → risk outcome → order → fill.
- `trades.csv`, `daily_pnl.csv` are the audit trail: append-only, backed up weekly off-machine, retained ≥ 1 year. They are gitignored but never deleted casually.
- `/reports` holds backtest artifacts and incident notes; committed to git.

## Process Management (VPS phase)

systemd unit with `Restart=on-failure`, `RestartSec=60`, and a start limit (e.g., 5 restarts / 10 min then stop) — an unbounded restart loop on a trading bot is dangerous. Restart triggers the full startup reconciliation, which is what makes auto-restart safe.

## Weekly Ops Review (10 min)

Log volume anomalies, error/halt counts, divergence tracking (PaperTradingPlan.md), disk space for logs/data, dependency security advisories (`pip-audit`).
