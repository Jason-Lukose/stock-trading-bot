import json
import logging

from bot import logging_setup


def _configure_and_capture(caplog_stream, secrets=None, run_id="test-run"):
    logger = logging.getLogger("bot.test_logging_setup")
    formatter = logging_setup.JsonLinesFormatter(run_id, logging_setup.SecretScrubber(secrets))
    handler = logging.StreamHandler(caplog_stream)
    handler.setFormatter(formatter)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def test_log_output_is_valid_json_lines(tmp_path):
    import io

    stream = io.StringIO()
    logger = _configure_and_capture(stream)
    logger.info("hello world")

    line = stream.getvalue().strip()
    payload = json.loads(line)
    assert payload["message"] == "hello world"
    assert payload["run_id"] == "test-run"
    assert payload["level"] == "INFO"
    assert "timestamp" in payload


def test_timestamp_is_utc_iso():
    import io

    stream = io.StringIO()
    logger = _configure_and_capture(stream)
    logger.info("check timestamp")

    payload = json.loads(stream.getvalue().strip())
    # ISO 8601 with UTC offset (+00:00) — datetime.now(timezone.utc).isoformat()
    assert payload["timestamp"].endswith("+00:00")


def test_secret_scrubbed_from_message():
    import io

    secret = "sk-live-abcdefghijklmnopqrstuvwxyz123456"
    stream = io.StringIO()
    logger = _configure_and_capture(stream, secrets=[secret])
    logger.info(f"failed request with key {secret}")

    payload = json.loads(stream.getvalue().strip())
    assert secret not in payload["message"]
    assert "***REDACTED***" in payload["message"]


def test_secret_scrubbed_from_exception_traceback():
    import io

    secret = "sk-live-abcdefghijklmnopqrstuvwxyz123456"
    stream = io.StringIO()
    logger = _configure_and_capture(stream, secrets=[secret])

    try:
        raise ValueError(f"bad key: {secret}")
    except ValueError:
        logger.exception("request failed")

    output = stream.getvalue()
    assert secret not in output
    assert "***REDACTED***" in output


def test_run_id_stable_across_records():
    import io

    stream = io.StringIO()
    logger = _configure_and_capture(stream, run_id="run-abc")
    logger.info("first")
    logger.info("second")

    lines = [json.loads(l) for l in stream.getvalue().strip().splitlines()]
    assert all(l["run_id"] == "run-abc" for l in lines)


def test_new_run_id_is_unique():
    a = logging_setup.new_run_id()
    b = logging_setup.new_run_id()
    assert a != b
