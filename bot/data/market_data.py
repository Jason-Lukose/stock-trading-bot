"""Historical + live bar retrieval, staleness detection, and ingest validation.

Alpaca-touching code is confined to `build_stock_client` / `build_crypto_client`
(real alpaca-py clients) and the thin `fetch_*` wrappers below, which accept an
injected client object. Unit tests inject a fake client — never the real API.
"""
import math
from dataclasses import dataclass
from datetime import datetime


class DataValidationError(ValueError):
    """Raised when ingested bars fail validation."""


@dataclass(frozen=True)
class Bar:
    timestamp: datetime  # tz-aware, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


def is_stale(latest_bar_timestamp, now, bar_interval_seconds, multiplier=2):
    """True if the latest bar is older than `multiplier` x the bar interval.

    Backs RiskRules.md Rule 6 (reject signals on stale data).
    """
    if latest_bar_timestamp.tzinfo is None or now.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    age_seconds = (now - latest_bar_timestamp).total_seconds()
    return age_seconds > bar_interval_seconds * multiplier


def validate_bars(bars, expected_interval_seconds, max_gap_multiplier=1.0, gap_allowed=None):
    """Validate a chronologically-ordered sequence of Bar objects.

    Rejects (raises DataValidationError):
      - duplicate or non-increasing timestamps
      - zero/negative/non-finite OHLC prices, or high < low
      - negative/non-finite volume
      - a gap between consecutive bars beyond `expected_interval_seconds *
        max_gap_multiplier`, unless `gap_allowed(prev_ts, curr_ts)` returns
        True (e.g. an overnight/weekend/holiday gap per bot.calendar).

    Returns `bars` unchanged if valid.
    """
    if not bars:
        return bars

    seen_timestamps = set()
    prev = None
    for bar in bars:
        if bar.timestamp in seen_timestamps:
            raise DataValidationError(f"duplicate timestamp: {bar.timestamp}")
        seen_timestamps.add(bar.timestamp)

        for name, value in (
            ("open", bar.open),
            ("high", bar.high),
            ("low", bar.low),
            ("close", bar.close),
        ):
            if value is None or not math.isfinite(value) or value <= 0:
                raise DataValidationError(f"invalid {name} price {value} at {bar.timestamp}")

        if bar.high < bar.low:
            raise DataValidationError(f"high < low at {bar.timestamp}")

        if bar.volume is None or not math.isfinite(bar.volume) or bar.volume < 0:
            raise DataValidationError(f"invalid volume {bar.volume} at {bar.timestamp}")

        if prev is not None:
            gap_seconds = (bar.timestamp - prev.timestamp).total_seconds()
            if gap_seconds <= 0:
                raise DataValidationError(f"non-increasing timestamp at {bar.timestamp}")
            allowed = gap_allowed(prev.timestamp, bar.timestamp) if gap_allowed else False
            if gap_seconds > expected_interval_seconds * max_gap_multiplier and not allowed:
                raise DataValidationError(
                    f"gap of {gap_seconds}s between {prev.timestamp} and {bar.timestamp} "
                    f"exceeds expected interval"
                )

        prev = bar

    return bars


def bar_from_alpaca(raw_bar):
    """Convert one alpaca-py bar object (duck-typed: .timestamp, .open, .high,
    .low, .close, .volume) into our internal Bar. Pure translation, no I/O —
    testable with a plain fake object, no alpaca-py dependency required.
    """
    return Bar(
        timestamp=raw_bar.timestamp,
        open=float(raw_bar.open),
        high=float(raw_bar.high),
        low=float(raw_bar.low),
        close=float(raw_bar.close),
        volume=float(raw_bar.volume),
    )


def _extract_symbol_bars(response, symbol):
    """alpaca-py BarSet responses are mapping-like (symbol -> list[Bar]) via
    both `response[symbol]` and `response.data[symbol]` depending on version;
    try both rather than assuming one.
    """
    try:
        return response[symbol]
    except (TypeError, KeyError):
        return response.data[symbol]


def fetch_historical_stock_bars(client, request_factory, symbol, timeframe, start, end):
    """`client` is an injected object exposing `.get_stock_bars(request)`.
    `request_factory` builds the alpaca-py StockBarsRequest (injected so this
    function has no compile-time alpaca-py import).
    """
    request = request_factory(symbol_or_symbols=symbol, timeframe=timeframe, start=start, end=end)
    response = client.get_stock_bars(request)
    return [bar_from_alpaca(b) for b in _extract_symbol_bars(response, symbol)]


def fetch_historical_crypto_bars(client, request_factory, symbol, timeframe, start, end):
    """`client` is an injected object exposing `.get_crypto_bars(request)`."""
    request = request_factory(symbol_or_symbols=symbol, timeframe=timeframe, start=start, end=end)
    response = client.get_crypto_bars(request)
    return [bar_from_alpaca(b) for b in _extract_symbol_bars(response, symbol)]


def fetch_latest_stock_bar(client, request_factory, symbol):
    request = request_factory(symbol_or_symbols=symbol)
    response = client.get_stock_latest_bar(request)
    return bar_from_alpaca(response[symbol] if not hasattr(response, "data") else response.data[symbol])


def fetch_latest_crypto_bar(client, request_factory, symbol):
    request = request_factory(symbol_or_symbols=symbol)
    response = client.get_crypto_latest_bar(request)
    return bar_from_alpaca(response[symbol] if not hasattr(response, "data") else response.data[symbol])


def build_stock_client(api_key, secret_key):
    """Real alpaca-py stock historical data client. alpaca-py is imported here,
    not at module scope, so this module (and its unit tests) never require the
    package or network access.
    """
    from alpaca.data.historical import StockHistoricalDataClient

    return StockHistoricalDataClient(api_key, secret_key)


def build_crypto_client(api_key, secret_key):
    """Real alpaca-py crypto historical data client (no keys required by
    alpaca-py for crypto market data, but accepted for a consistent interface).
    """
    from alpaca.data.historical import CryptoHistoricalDataClient

    return CryptoHistoricalDataClient(api_key, secret_key)
