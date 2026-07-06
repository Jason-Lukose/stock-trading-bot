"""HALT-file kill switch (RiskRules.md Rule 9).

Presence of a `HALT` file in the repo root blocks ALL order submission.
Checked immediately before every submit — the last possible moment, so a
HALT file created between an order's risk evaluation and its actual
submission still blocks it. Creating the file is the documented emergency
stop (see docs/OperationsRunbook.md).
"""
import os

DEFAULT_HALT_FILENAME = "HALT"


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def halt_file_path(repo_root=None):
    return os.path.join(repo_root or _repo_root(), DEFAULT_HALT_FILENAME)


def is_halted(repo_root=None):
    return os.path.exists(halt_file_path(repo_root))
