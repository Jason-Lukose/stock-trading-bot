from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bot import calendar as bot_calendar

NY = ZoneInfo("America/New_York")


def test_regular_trading_day_open_closed():
    # Wednesday, June 4, 2025 — an ordinary trading day.
    d = date(2025, 6, 4)
    assert bot_calendar.is_equity_trading_day(d)
    assert bot_calendar.is_equity_market_open(datetime(2025, 6, 4, 10, 0, tzinfo=NY))
    assert not bot_calendar.is_equity_market_open(datetime(2025, 6, 4, 8, 0, tzinfo=NY))
    assert not bot_calendar.is_equity_market_open(datetime(2025, 6, 4, 16, 0, tzinfo=NY))


def test_weekend_is_not_a_trading_day():
    saturday = date(2025, 6, 7)
    assert not bot_calendar.is_equity_trading_day(saturday)


def test_holiday_is_not_a_trading_day():
    # July 4, 2025 is a Friday -> observed on the day itself.
    independence_day = date(2025, 7, 4)
    assert not bot_calendar.is_equity_trading_day(independence_day)


def test_holiday_weekend_observance_shift():
    # July 4, 2026 falls on a Saturday -> observed Friday July 3, 2026.
    assert date(2026, 7, 3) in bot_calendar.nyse_holidays(2026)


def test_half_day_early_close():
    # Day after Thanksgiving 2025 (Nov 28) is a half day, closing at 13:00 ET.
    half_day = date(2025, 11, 28)
    assert half_day in bot_calendar.nyse_half_days(2025)
    assert bot_calendar.is_equity_market_open(datetime(2025, 11, 28, 12, 0, tzinfo=NY))
    assert not bot_calendar.is_equity_market_open(datetime(2025, 11, 28, 13, 30, tzinfo=NY))


def test_crypto_always_open():
    assert bot_calendar.is_tradeable("crypto", datetime(2025, 12, 25, 3, 0, tzinfo=NY))


def test_is_tradeable_unknown_asset_class():
    try:
        bot_calendar.is_tradeable("futures", datetime(2025, 6, 4, 10, 0, tzinfo=NY))
        assert False, "expected ValueError"
    except ValueError:
        pass


class _FakeBar:
    def __init__(self, timestamp, o, h, l, c, v):
        self.timestamp = timestamp
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v


def _bars_15m(day, start_time, count):
    bars = []
    ts = datetime.combine(day, start_time, tzinfo=NY)
    for i in range(count):
        bars.append(_FakeBar(ts + timedelta(minutes=15 * i), 100 + i, 101 + i, 99 + i, 100.5 + i, 1000))
    return bars


def test_4hr_bar_construction_session_aligned():
    """A full regular session (9:30-16:00) built from 15-min bars must yield
    exactly two 4-hour windows: 09:30-13:30 (full 4h, 16 bars) and 13:30-16:00
    (short 2.5h trailing bar, 10 bars) — not three windows, not one padded one.
    """
    day = date(2025, 6, 4)
    from bot.calendar import EQUITY_OPEN

    bars = _bars_15m(day, EQUITY_OPEN, 26)  # 9:30 .. 15:45 inclusive, 26 bars
    result = bot_calendar.build_4hr_equity_bars(bars)

    assert len(result) == 2
    assert result[0]["timestamp"] == datetime.combine(day, EQUITY_OPEN, tzinfo=NY)
    assert result[1]["timestamp"] == datetime.combine(day, EQUITY_OPEN, tzinfo=NY) + timedelta(hours=4)

    # first window: bars[0:16] (9:30 through 13:15)
    first_chunk = bars[0:16]
    assert result[0]["open"] == first_chunk[0].open
    assert result[0]["close"] == first_chunk[-1].close
    assert result[0]["high"] == max(b.high for b in first_chunk)
    assert result[0]["low"] == min(b.low for b in first_chunk)
    assert result[0]["volume"] == sum(b.volume for b in first_chunk)

    # second window: remaining bars (13:30 through 15:45)
    second_chunk = bars[16:26]
    assert result[1]["open"] == second_chunk[0].open
    assert result[1]["close"] == second_chunk[-1].close
    assert result[1]["volume"] == sum(b.volume for b in second_chunk)


def test_4hr_bar_construction_half_day_single_window():
    day = date(2025, 11, 28)  # half day
    from bot.calendar import EQUITY_OPEN

    bars = _bars_15m(day, EQUITY_OPEN, 14)  # 9:30 .. 12:45, all within window 1
    result = bot_calendar.build_4hr_equity_bars(bars)

    assert len(result) == 1
    assert result[0]["timestamp"] == datetime.combine(day, EQUITY_OPEN, tzinfo=NY)
