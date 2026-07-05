"""Single source of truth for "is this instrument tradeable right now."

Equity sessions follow NYSE hours, US federal-market holidays, and the three
customary early-close (half) days. Crypto trades 24/7. All datetime
comparisons are done in America/New_York for equities (the exchange's local
time) and UTC for crypto/internal bookkeeping.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")

EQUITY_OPEN = time(9, 30)
EQUITY_CLOSE = time(16, 0)
EQUITY_HALF_DAY_CLOSE = time(13, 0)

# 4-hour equity bar construction (Architecture.md / Backtesting.md require this
# to be defined explicitly): bars are SESSION-ALIGNED, anchored to the 9:30
# market open, not to wall-clock hour boundaries. A 6.5-hour regular session
# does not divide evenly by 4 hours, so the session yields one full 4-hour bar
# (09:30-13:30) and one short trailing bar (13:30-16:00, 2.5 hours) rather than
# a padded or dropped final bar. Half days (13:00 close) yield a single
# 3.5-hour bar and no second window.


def _nth_weekday(year, month, weekday, n):
    """nth (1-indexed) occurrence of `weekday` (Mon=0) in a given month/year."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d = d + timedelta(days=offset + 7 * (n - 1))
    return d


def _last_weekday(year, month, weekday):
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def _easter(year):
    """Anonymous Gregorian algorithm; used to derive Good Friday."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(d):
    """Federal holiday observed-date shift: Saturday -> Friday, Sunday -> Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(year):
    """NYSE full-market-closure holidays for a given year."""
    good_friday = _easter(year) - timedelta(days=2)
    holidays = {
        _observed(date(year, 1, 1)),                       # New Year's Day
        _nth_weekday(year, 1, 0, 3),                        # MLK Day (3rd Monday Jan)
        _nth_weekday(year, 2, 0, 3),                        # Presidents' Day (3rd Monday Feb)
        good_friday,
        _last_weekday(year, 5, 0),                          # Memorial Day (last Monday May)
        _observed(date(year, 6, 19)),                       # Juneteenth
        _observed(date(year, 7, 4)),                        # Independence Day
        _nth_weekday(year, 9, 0, 1),                        # Labor Day (1st Monday Sept)
        _nth_weekday(year, 11, 3, 4),                        # Thanksgiving (4th Thursday Nov)
        _observed(date(year, 12, 25)),                      # Christmas
    }
    return holidays


def nyse_half_days(year):
    """NYSE early-close (1:00pm ET) days: day before Independence Day,
    day after Thanksgiving, Christmas Eve — when they fall on a weekday and
    are not already a full holiday.
    """
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    candidates = {
        date(year, 7, 3),
        thanksgiving + timedelta(days=1),
        date(year, 12, 24),
    }
    holidays = nyse_holidays(year)
    return {d for d in candidates if d.weekday() < 5 and d not in holidays}


def is_equity_trading_day(d):
    if d.weekday() >= 5:
        return False
    return d not in nyse_holidays(d.year)


def is_equity_market_open(dt):
    """dt must be timezone-aware. Returns whether the equity market is open at dt."""
    if dt.tzinfo is None:
        raise ValueError("dt must be timezone-aware")
    local = dt.astimezone(NY_TZ)
    if not is_equity_trading_day(local.date()):
        return False
    close = EQUITY_HALF_DAY_CLOSE if local.date() in nyse_half_days(local.year) else EQUITY_CLOSE
    return EQUITY_OPEN <= local.time() < close


def is_crypto_market_open(dt):
    return True


def is_tradeable(asset_class, dt):
    """asset_class: 'equity' or 'crypto'."""
    if asset_class == "crypto":
        return is_crypto_market_open(dt)
    if asset_class == "equity":
        return is_equity_market_open(dt)
    raise ValueError(f"unknown asset_class: {asset_class}")


def session_close_time(d):
    return EQUITY_HALF_DAY_CLOSE if d in nyse_half_days(d.year) else EQUITY_CLOSE


def build_4hr_equity_bars(intraday_bars):
    """Aggregate sub-4hr equity bars (e.g. 15-min) into session-aligned 4-hour
    bars. Each `intraday_bars` item is any object/mapping with .timestamp
    (tz-aware, ascending, same trading day) and .open/.high/.low/.close/.volume.

    Windows are anchored to the 9:30 session open per instrument-day:
    [09:30, 13:30), [13:30, close). The regular session's second window is a
    short 2.5-hour bar; on half days there is only the first window, truncated
    to the 13:00 close.

    Returns a list of dicts: {timestamp (window start), open, high, low, close, volume}.
    """
    by_window = {}
    order = []
    for bar in intraday_bars:
        local = bar.timestamp.astimezone(NY_TZ)
        session_open = datetime.combine(local.date(), EQUITY_OPEN, tzinfo=NY_TZ)
        elapsed = (local - session_open).total_seconds()
        window_index = 0 if elapsed < 4 * 3600 else 1
        window_start = session_open if window_index == 0 else session_open + timedelta(hours=4)
        key = (local.date(), window_index)

        if key not in by_window:
            by_window[key] = {
                "timestamp": window_start,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            order.append(key)
        else:
            agg = by_window[key]
            agg["high"] = max(agg["high"], bar.high)
            agg["low"] = min(agg["low"], bar.low)
            agg["close"] = bar.close
            agg["volume"] += bar.volume

    return [by_window[key] for key in order]
