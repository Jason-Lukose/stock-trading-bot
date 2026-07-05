from datetime import datetime, timedelta, timezone

import pytest

from bot.data import market_data


def _bar(ts, o=100.0, h=101.0, l=99.0, c=100.5, v=1000.0):
    return market_data.Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _ts(minutes_from_epoch):
    return datetime(2025, 1, 2, tzinfo=timezone.utc) + timedelta(minutes=minutes_from_epoch)


def test_is_stale_true_when_old():
    latest = _ts(0)
    now = latest + timedelta(minutes=31)
    assert market_data.is_stale(latest, now, bar_interval_seconds=15 * 60, multiplier=2)


def test_is_stale_false_when_fresh():
    latest = _ts(0)
    now = latest + timedelta(minutes=20)
    assert not market_data.is_stale(latest, now, bar_interval_seconds=15 * 60, multiplier=2)


def test_is_stale_requires_tz_aware():
    with pytest.raises(ValueError):
        market_data.is_stale(datetime(2025, 1, 1), datetime(2025, 1, 1), 900)


def test_validate_bars_accepts_clean_series():
    bars = [_bar(_ts(i * 15)) for i in range(5)]
    assert market_data.validate_bars(bars, expected_interval_seconds=900) == bars


def test_validate_bars_rejects_duplicate_timestamps():
    ts = _ts(0)
    bars = [_bar(ts), _bar(ts)]
    with pytest.raises(market_data.DataValidationError):
        market_data.validate_bars(bars, expected_interval_seconds=900)


def test_validate_bars_rejects_zero_price():
    bars = [_bar(_ts(0), o=0.0)]
    with pytest.raises(market_data.DataValidationError):
        market_data.validate_bars(bars, expected_interval_seconds=900)


def test_validate_bars_rejects_negative_price():
    bars = [_bar(_ts(0), c=-5.0)]
    with pytest.raises(market_data.DataValidationError):
        market_data.validate_bars(bars, expected_interval_seconds=900)


def test_validate_bars_rejects_negative_volume():
    bars = [_bar(_ts(0), v=-1.0)]
    with pytest.raises(market_data.DataValidationError):
        market_data.validate_bars(bars, expected_interval_seconds=900)


def test_validate_bars_rejects_high_below_low():
    bars = [_bar(_ts(0), h=90.0, l=99.0)]
    with pytest.raises(market_data.DataValidationError):
        market_data.validate_bars(bars, expected_interval_seconds=900)


def test_validate_bars_rejects_unexpected_gap():
    bars = [_bar(_ts(0)), _bar(_ts(15)), _bar(_ts(300))]  # huge jump
    with pytest.raises(market_data.DataValidationError):
        market_data.validate_bars(bars, expected_interval_seconds=900)


def test_validate_bars_allows_gap_when_gap_allowed_callback_permits():
    bars = [_bar(_ts(0)), _bar(_ts(300))]
    assert market_data.validate_bars(
        bars, expected_interval_seconds=900, gap_allowed=lambda prev, curr: True
    ) == bars


def test_validate_bars_rejects_non_increasing_timestamps():
    ts = _ts(0)
    bars = [_bar(ts + timedelta(minutes=15)), _bar(ts)]
    with pytest.raises(market_data.DataValidationError):
        market_data.validate_bars(bars, expected_interval_seconds=900)


def test_validate_bars_empty_is_fine():
    assert market_data.validate_bars([], expected_interval_seconds=900) == []


class _FakeAlpacaBar:
    def __init__(self, timestamp, o, h, l, c, v):
        self.timestamp = timestamp
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v


def test_bar_from_alpaca_translates_fields():
    raw = _FakeAlpacaBar(_ts(0), 1, 2, 0.5, 1.5, 100)
    bar = market_data.bar_from_alpaca(raw)
    assert bar.timestamp == raw.timestamp
    assert bar.open == 1.0
    assert bar.high == 2.0
    assert bar.low == 0.5
    assert bar.close == 1.5
    assert bar.volume == 100.0


class _FakeStockClient:
    def __init__(self, bars_by_symbol):
        self._bars_by_symbol = bars_by_symbol

    def get_stock_bars(self, request):
        return {"SPY": self._bars_by_symbol["SPY"]}


def test_fetch_historical_stock_bars_uses_injected_client_no_network():
    raw_bars = [_FakeAlpacaBar(_ts(i * 15), 1, 2, 0.5, 1.5, 100) for i in range(3)]
    client = _FakeStockClient({"SPY": raw_bars})

    captured = {}

    def request_factory(**kwargs):
        captured.update(kwargs)
        return kwargs

    result = market_data.fetch_historical_stock_bars(
        client, request_factory, "SPY", "15Min", _ts(0), _ts(45)
    )

    assert len(result) == 3
    assert all(isinstance(b, market_data.Bar) for b in result)
    assert captured["symbol_or_symbols"] == "SPY"
