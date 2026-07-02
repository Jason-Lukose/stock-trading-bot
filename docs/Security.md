# Security

## API Keys

- Keys live only in `.env` (gitignored) or the host's environment. Never hardcoded, never committed, never in logs or exception traces.
- Log scrubbing: the logging layer redacts any string matching the configured key values before write. A test feeds a fake key through an exception path and asserts it never appears in output.
- **Paper/live key separation:** only paper keys exist on the machine during the MVP. Live keys are not generated until a live phase is formally approved — the strongest control is that the credential does not exist.
- Rotation: rotate paper keys if a leak is suspected; rotation is free, hesitation is not.
- `.env.example` documents variable names only, never real values.

## No Live Path (defense in depth)

1. Paper URL hardcoded in `alpaca_client.py`
2. Startup assertion on `paper-api` in the URL
3. No live keys present on the machine
4. Live trading requires a code change + key generation + documented go/no-go review — three independent human actions

## Dependencies

- Exact-version pinning + lockfile; changes are deliberate commits.
- `pip-audit` run in the weekly ops review and before any dependency change.
- Use `alpaca-py` (maintained). The PDF's `alpaca-trade-api` is deprecated — unmaintained SDKs are a security liability, not just a compatibility one.
- No packages installed on the PDF's or any tutorial's say-so without a maintenance/reputation check. No "trading bot framework" packages.

## Host Hardening (VPS phase)

- SSH keys only (password auth disabled), non-root user runs the bot, ufw default-deny inbound except SSH, unattended security upgrades enabled, NTP active (clock drift breaks order timing and API auth).
- The bot needs zero inbound ports. Briefing delivery (Telegram/Slack) is outbound-only.

## Data & Code

- `trades.csv` / logs contain no secrets by design, but treat them as private (they reveal strategy behavior). Off-machine backups encrypted.
- Repo may be public/private per preference, but audit before any visibility change: git history must contain no keys (run a secret scanner, e.g., gitleaks, once now and in CI).
- Third-party prompts/code from the PDF or community are reviewed before use — same rule as any untrusted input.

## Incident Response (suspected key leak)

1. Regenerate keys in the Alpaca dashboard immediately (invalidate old).
2. `touch HALT`; verify no unexpected orders in account history.
3. Find the leak vector (git history, logs, screenshots, pastes) and close it.
4. Incident note in `/reports/incidents/`.
