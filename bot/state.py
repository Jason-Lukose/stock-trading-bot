"""Local state persistence and startup reconciliation against the broker.

Architecture.md decision #4: on startup, query the broker for open
positions/orders and reconcile against local state BEFORE any trading logic
runs. Mismatches halt the bot and log loudly — never silently prefer one
side. Reconciliation itself is agnostic to how `broker_positions` was
obtained (an injected dict here); the actual Alpaca query is Phase 6's job
(bot/execution/alpaca_client.py), so this module and its tests never touch
the real API.

Also home to the file-based manual-re-arm flag for the drawdown circuit
breaker (RiskRules.md Rule 5) — the bot never clears this itself.
"""
import json
import logging
import os
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

DEFAULT_TRIPPED_FILENAME = "TRIPPED"


def _repo_root():
    return os.path.dirname(os.path.abspath(__file__)).rsplit(os.sep, 1)[0]


class TrippedFlag:
    """File-based manual-re-arm flag (RiskRules.md Rule 5: "require manual
    re-arm — deleting a TRIPPED state flag after human review. The bot
    never resumes on its own."). Deliberately exposes no clear()/rearm()
    method — the only way to un-trip is a human deleting the file.
    """

    def __init__(self, path=None):
        self.path = path or os.path.join(_repo_root(), DEFAULT_TRIPPED_FILENAME)

    def is_tripped(self):
        return os.path.exists(self.path)

    def trip(self, reason=""):
        if self.is_tripped():
            return
        with open(self.path, "w") as f:
            f.write((reason or "drawdown circuit breaker tripped") + "\n")
        logger.error(
            "RISK_HALT_DRAWDOWN: %s (wrote %s; manual re-arm required — delete the file after human review)",
            reason, self.path,
        )


class ReconciliationError(RuntimeError):
    """Raised when local and broker-reported state disagree. Never silently
    resolved in either direction — the caller must halt and investigate."""


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    direction: int  # 1 long, -1 short


def reconcile(local_positions, broker_positions):
    """Compare local_positions (dict symbol -> Position) against
    broker_positions (dict symbol -> Position, as reported by the broker).

    Returns broker_positions (the broker is authoritative) if every symbol
    matches exactly. Raises ReconciliationError, loudly, on ANY mismatch —
    including a symbol present on only one side.
    """
    mismatches = []
    for symbol in sorted(set(local_positions) | set(broker_positions)):
        local = local_positions.get(symbol)
        broker = broker_positions.get(symbol)
        if local != broker:
            mismatches.append((symbol, local, broker))
    if mismatches:
        logger.error("RECONCILIATION_MISMATCH: %s", mismatches)
        raise ReconciliationError(f"local vs broker position mismatch: {mismatches}")
    return broker_positions


def save_local_state(positions, path):
    """Persist local positions (dict symbol -> Position) as JSON."""
    payload = {symbol: asdict(pos) for symbol, pos in positions.items()}
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def load_local_state(path):
    """Load previously persisted positions. Returns {} if the file doesn't exist yet."""
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        payload = json.load(f)
    return {symbol: Position(**fields) for symbol, fields in payload.items()}
