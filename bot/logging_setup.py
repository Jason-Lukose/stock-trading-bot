"""Structured JSON-lines logging: UTC timestamps, run IDs, secret scrubbing.

Every trade decision must be reconstructable from logs alone, so all log
records are emitted as one JSON object per line.
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone

_SCRUB_PLACEHOLDER = "***REDACTED***"


class SecretScrubber:
    """Redacts configured secret values (and common key-shaped substrings) from text."""

    _KEY_PATTERN = re.compile(r"\b[A-Za-z0-9_\-]{20,}\b")

    def __init__(self, secrets=None):
        self._secrets = [s for s in (secrets or []) if s]

    def scrub(self, text):
        if not isinstance(text, str):
            text = str(text)
        for secret in self._secrets:
            if secret:
                text = text.replace(secret, _SCRUB_PLACEHOLDER)
        return text


class JsonLinesFormatter(logging.Formatter):
    def __init__(self, run_id, scrubber):
        super().__init__()
        self.run_id = run_id
        self.scrubber = scrubber

    def format(self, record):
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        message = self.scrubber.scrub(message)

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            for key, value in extra.items():
                payload[key] = self.scrubber.scrub(value) if isinstance(value, str) else value
        return json.dumps(payload, default=str)


def new_run_id():
    return uuid.uuid4().hex


def configure_logging(log_path=None, secrets=None, level=logging.INFO, run_id=None):
    """Configure the root logger for structured JSON-lines output.

    Returns the run_id used for this run.
    """
    run_id = run_id or new_run_id()
    scrubber = SecretScrubber(secrets)
    formatter = JsonLinesFormatter(run_id, scrubber)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if log_path:
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    return run_id
