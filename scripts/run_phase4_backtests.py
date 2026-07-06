"""Phase 4 backtest runner: fetch real historical bars, run each of the 5
StrategySpec.md instruments through the backtester per Backtesting.md's
protocol, and write metrics tables + equity-curve charts to
reports/backtests/phase4_<run-date>/ (committed evidence artifacts -- an
explicit exception to reports/'s default gitignore, see .gitignore).

This script RUNS strategies exactly as specified. It does not tune
parameters and does not decide whether results are "good enough" for paper
trading -- that interpretation is a separate Fable checkpoint (BuildPlan.md
Phase 4 gate). A strategy producing very few trades (expected for GLD/USO's
50/200 EMA cross on 4-hr bars per StrategySpec.md) is reported as a finding,
not treated as a reason to adjust parameters (see D-007 / the rejected
overfitting loop).

SCOPE NOTE (see bot/strategies/base.py): no stop-loss logic runs here.
StrategySpec.md's per-strategy stops belong to the not-yet-built risk
manager (Phase 5). These numbers are stop-free and will look different --
likely more drawdown, longer losing trades -- once Phase 5 adds real stops.

SCOPE NOTE: no correlation filter (RiskRules.md Rule 7) is applied to the
"combined" section below, because the risk manager that would enforce it
does not exist yet (Phase 5). The combined portfolio numbers are a naive,
unfiltered blend of the five instruments' independent equity curves.

Position sizing here is a flat 1 unit per instrument (no ATR/equity-based
sizing -- that's also Phase 5's job), so `total_return`/drawdown in dollar
terms are illustrative of trade quality, not of what a properly risk-sized
account would experience.
"""
import csv
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alpaca.data.enums import Adjustment
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from bot import calendar as bot_calendar
from bot import config
from bot.backtesting import backtest as bt
from bot.data import market_data
from bot.strategies import mean_reversion as mr
from bot.strategies import momentum_breakout as mb
from bot.strategies import trend_following as tf

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Committed evidence artifacts live under reports/backtests/<run>/, which is
# an explicit exception to reports/'s default gitignore (transient/ad-hoc
# output stays ignored; this directory is the Backtesting.md/BuildPlan.md
# "report artifacts committed to /reports" deliverable). One dated
# subdirectory per run so re-runs don't silently overwrite prior evidence.
REPORTS_DIR = os.path.join(REPO_ROOT, "reports", "backtests", f"phase4_{datetime.now(timezone.utc):%Y%m%d}")
YEARS_OF_HISTORY = 2
IS_FRACTION = 0.70
INITIAL_EQUITY = 100_000.0
QUANTITY = 1.0  # flat 1 unit/instrument -- real sizing is Phase 5's job


def _equity_gap_allowed(prev_ts, curr_ts):
    """A gap is expected (not a data defect) if it spans a period the
    equity market was simply closed -- overnight, weekend, holiday. Compared
    in NY-local dates (NOT raw UTC dates -- a UTC calendar day boundary
    doesn't line up with the exchange's trading day at all).
    """
    prev_local = prev_ts.astimezone(bot_calendar.NY_TZ).date()
    curr_local = curr_ts.astimezone(bot_calendar.NY_TZ).date()
    return prev_local != curr_local and bot_calendar.is_equity_trading_day(curr_local)


def _regular_session_only(bars):
    """Alpaca's bars endpoint returns extended-hours (pre/post-market) data
    by default -- confirmed empirically: SPY 15-min bars start ~04:00 ET
    with a ~30x volume jump exactly at 09:30 ET. StrategySpec.md specifies
    "Equity market hours" for every equity instrument, so extended-hours
    bars are filtered out here, before validation/aggregation. This matters
    beyond just "wrong session": feeding pre-market bars into
    bot.calendar.build_4hr_equity_bars (GLD/USO) would silently corrupt the
    first 4-hr window, since that function assumes its input starts at the
    9:30 open.
    """
    return [b for b in bars if bot_calendar.is_equity_market_open(b.timestamp)]


def fetch_spy_qqq(client, symbol, start, end):
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(15, TimeFrameUnit.Minute),
        start=start,
        end=end,
        adjustment=Adjustment.ALL,
    )
    response = client.get_stock_bars(request)
    bars = [market_data.bar_from_alpaca(b) for b in response[symbol]]
    bars = _regular_session_only(bars)
    return market_data.validate_bars(bars, expected_interval_seconds=15 * 60, gap_allowed=_equity_gap_allowed)


def _crypto_single_bar_gap_allowed(prev_ts, curr_ts):
    """Alpaca's BTC/USD hourly feed occasionally has a single missing hourly
    bar during near-zero-liquidity windows (confirmed empirically: 2 such
    isolated gaps out of 9,094 bars across ~379 days, each exactly one
    missing hour, each flanked by bars with volume < 0.01 BTC). This is a
    bounded, logged exception for EXACTLY one missing bar (gap == 2x the
    expected interval) -- crypto is 24/7, so this is still a real data gap
    being explicitly tolerated, not "no gap," and anything larger still
    fails loudly as a genuine data defect.
    """
    is_single_missing_bar = (curr_ts - prev_ts).total_seconds() == 2 * 3600
    if is_single_missing_bar:
        print(f"  [data quality] tolerating one missing BTC/USD hourly bar: {prev_ts} -> {curr_ts}")
    return is_single_missing_bar


def fetch_btc(client, start, end):
    symbol = "BTC/USD"
    request = CryptoBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(1, TimeFrameUnit.Hour),
        start=start,
        end=end,
    )
    response = client.get_crypto_bars(request)
    bars = [market_data.bar_from_alpaca(b) for b in response[symbol]]
    return market_data.validate_bars(bars, expected_interval_seconds=3600, gap_allowed=_crypto_single_bar_gap_allowed)


def fetch_gld_uso_4hr(client, symbol, start, end):
    """GLD/USO are 4-hr per StrategySpec.md. Per D-017, 4-hr equity bars are
    session-aligned (anchored to 9:30 open), NOT Alpaca's native fixed-clock
    4-hour bars -- so we fetch 15-min bars and aggregate with
    bot.calendar.build_4hr_equity_bars, exactly like SPY/QQQ's raw fetch.
    """
    intraday = fetch_spy_qqq(client, symbol, start, end)
    aggregated = bot_calendar.build_4hr_equity_bars(intraday)
    bars = [
        market_data.Bar(timestamp=w["timestamp"], open=w["open"], high=w["high"],
                         low=w["low"], close=w["close"], volume=w["volume"])
        for w in aggregated
    ]
    # Aggregated 4-hr bars: full session -> 2 bars/day (1 full 4h + 1 short
    # 2.5h trailing), half day -> 1 bar. No fixed expected_interval_seconds
    # applies cleanly across day boundaries and half days, so we validate
    # price/volume sanity only (gap_allowed=True skips the interval check).
    return market_data.validate_bars(bars, expected_interval_seconds=1, gap_allowed=lambda p, c: True)


def _pooled_trade_metrics(all_trades):
    return bt.compute_metrics(all_trades, [], INITIAL_EQUITY, total_bars=0, exposure_bars=0)


def _resample_daily_last(equity_curve):
    daily = {}
    for ts, equity in equity_curve:
        daily[ts.astimezone(timezone.utc).date()] = equity
    return daily


def _combine_daily_equity(per_instrument_curves, per_instrument_initial):
    """Combine independent per-instrument equity curves into one approximate
    daily portfolio series: resample each to end-of-day, forward-fill gaps
    (different instruments trade on different calendars), then sum.
    Documented approximation -- see module scope notes.
    """
    daily_series = [_resample_daily_last(c) for c in per_instrument_curves]
    all_dates = sorted(set().union(*[d.keys() for d in daily_series])) if daily_series else []

    last_known = list(per_instrument_initial)
    combined = []
    for d in all_dates:
        total = 0.0
        for idx, daily in enumerate(daily_series):
            if d in daily:
                last_known[idx] = daily[d]
            total += last_known[idx]
        combined.append((d, total))
    return combined


def _combined_metrics_from_daily(combined_daily, initial_equity):
    values = [v for _, v in combined_daily]
    max_dd = bt._max_drawdown(values)
    returns = bt._bar_returns(values)
    sharpe_per_bar = bt._sharpe_per_bar(returns)
    # Combined series is resampled to CALENDAR days (not trading days),
    # since it blends 24/7 crypto with 5-day equities -- 365 is the
    # correct annualization factor for a calendar-day series, documented
    # as an approximation given the frequency mismatch it's blending.
    sharpe_annualized = sharpe_per_bar * math.sqrt(365) if sharpe_per_bar is not None else None
    total_return = (values[-1] - initial_equity) / initial_equity if values else 0.0
    return {
        "max_drawdown": max_dd,
        "sharpe_per_bar": sharpe_per_bar,
        "sharpe_annualized": sharpe_annualized,
        "periods_per_year_assumption": 365,
        "total_return": total_return,
        "final_equity": values[-1] if values else initial_equity,
    }


def run_one_instrument(name, bars, strategy_fn, cost_model, periods_per_year, warmup_bars):
    is_bars, oos_bars, oos_warmup = bt.split_is_oos(bars, is_fraction=IS_FRACTION, warmup_context_bars=warmup_bars)

    is_result = bt.run_backtest(
        is_bars, strategy_fn, cost_model, quantity=QUANTITY, initial_equity=INITIAL_EQUITY,
        warmup_bars=0, periods_per_year=periods_per_year,
    )
    oos_result = bt.run_backtest(
        oos_bars, strategy_fn, cost_model, quantity=QUANTITY, initial_equity=INITIAL_EQUITY,
        warmup_bars=oos_warmup, periods_per_year=periods_per_year,
    )

    return {
        "name": name,
        "bar_count": len(bars),
        "is_bars": len(is_bars),
        "oos_bars": len(oos_bars),
        "is": is_result,
        "oos": oos_result,
    }


def save_equity_chart(name, is_result, oos_result):
    fig, ax = plt.subplots(figsize=(10, 5))
    if is_result.equity_curve:
        xs, ys = zip(*is_result.equity_curve)
        ax.plot(xs, ys, label="In-sample", color="tab:blue")
    if oos_result.equity_curve:
        xs, ys = zip(*oos_result.equity_curve)
        ax.plot(xs, ys, label="Out-of-sample", color="tab:orange")
    ax.set_title(f"{name} equity curve (stop-free, 1 unit/instrument)")
    ax.set_ylabel("Equity ($)")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    path = os.path.join(REPORTS_DIR, f"{name}_equity.png")
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def _duration_summary(durations):
    if not durations:
        return {"count": 0, "min": None, "median": None, "max": None}
    seconds = sorted(d.total_seconds() for d in durations)
    n = len(seconds)
    median = seconds[n // 2] if n % 2 else (seconds[n // 2 - 1] + seconds[n // 2]) / 2
    return {"count": n, "min_hours": seconds[0] / 3600, "median_hours": median / 3600, "max_hours": seconds[-1] / 3600}


def _row(name, phase, metrics, bar_count):
    return {
        "instrument": name,
        "phase": phase,
        "bars": bar_count,
        "total_trades": metrics["total_trades"],
        "win_rate": metrics["win_rate"],
        "avg_win": metrics["avg_win"],
        "avg_loss": metrics["avg_loss"],
        "profit_factor": metrics["profit_factor"],
        "expectancy": metrics["expectancy"],
        "max_drawdown": metrics["max_drawdown"],
        "sharpe_annualized": metrics.get("sharpe_annualized"),
        "total_return": metrics["total_return"],
        "exposure_pct": metrics["exposure_pct"],
        "trade_duration": _duration_summary(metrics["trade_durations"]),
    }


def main():
    load_dotenv()
    os.makedirs(REPORTS_DIR, exist_ok=True)

    api_key, secret_key = config.get_alpaca_credentials()
    stock_client = market_data.build_stock_client(api_key, secret_key)
    crypto_client = market_data.build_crypto_client(api_key, secret_key)

    end = datetime.now(timezone.utc) - timedelta(minutes=20)  # avoid the recent-data embargo window
    start = end - timedelta(days=365 * YEARS_OF_HISTORY + 14)  # pad for the aggregation/warmup edges

    print(f"Fetching {YEARS_OF_HISTORY}+ years of history: {start.date()} to {end.date()}")

    print("Fetching SPY (15-min, split/dividend adjusted)...")
    spy_bars = fetch_spy_qqq(stock_client, "SPY", start, end)
    print(f"  {len(spy_bars)} bars")

    print("Fetching QQQ (15-min, split/dividend adjusted)...")
    qqq_bars = fetch_spy_qqq(stock_client, "QQQ", start, end)
    print(f"  {len(qqq_bars)} bars")

    print("Fetching BTC/USD (1-hr)...")
    btc_bars = fetch_btc(crypto_client, start, end)
    print(f"  {len(btc_bars)} bars")

    print("Fetching GLD (15-min -> session-aligned 4-hr, split/dividend adjusted)...")
    gld_bars = fetch_gld_uso_4hr(stock_client, "GLD", start, end)
    print(f"  {len(gld_bars)} 4-hr bars")

    print("Fetching USO (15-min -> session-aligned 4-hr, split/dividend adjusted)...")
    uso_bars = fetch_gld_uso_4hr(stock_client, "USO", start, end)
    print(f"  {len(uso_bars)} 4-hr bars")

    instruments = [
        ("SPY", spy_bars, mr.spy_strategy, bt.equity_cost_model(), bt.PERIODS_PER_YEAR_15MIN_EQUITY, mr.SMA_PERIOD),
        ("QQQ", qqq_bars, mr.qqq_strategy, bt.equity_cost_model(), bt.PERIODS_PER_YEAR_15MIN_EQUITY, mr.SMA_PERIOD),
        ("BTC_USD", btc_bars, mb.btc_strategy, bt.crypto_cost_model(), bt.PERIODS_PER_YEAR_1HR_CRYPTO, mb.PERIOD),
        ("GLD", gld_bars, tf.gld_strategy, bt.equity_cost_model(), bt.PERIODS_PER_YEAR_4HR_EQUITY, tf.SLOW_PERIOD),
        ("USO", uso_bars, tf.uso_strategy, bt.equity_cost_model(), bt.PERIODS_PER_YEAR_4HR_EQUITY, tf.SLOW_PERIOD),
    ]

    results = []
    rows = []
    for name, bars, strategy_fn, cost_model, periods_per_year, warmup in instruments:
        print(f"Backtesting {name} ({len(bars)} bars, warmup={warmup})...")
        result = run_one_instrument(name, bars, strategy_fn, cost_model, periods_per_year, warmup)
        results.append(result)
        rows.append(_row(name, "IS", result["is"].metrics, result["is_bars"]))
        rows.append(_row(name, "OOS", result["oos"].metrics, result["oos_bars"]))
        chart_path = save_equity_chart(name, result["is"], result["oos"])
        print(f"  saved {chart_path}")

    # --- Combined: pooled trade metrics + approximate blended daily equity ---
    for phase in ("is", "oos"):
        all_trades = [t for r in results for t in r[phase].trades]
        pooled = _pooled_trade_metrics(all_trades)
        curves = [r[phase].equity_curve for r in results]
        combined_daily = _combine_daily_equity(curves, [INITIAL_EQUITY] * len(results))
        combined_equity_metrics = _combined_metrics_from_daily(combined_daily, INITIAL_EQUITY * len(results))

        rows.append({
            "instrument": "COMBINED (naive, no correlation filter)",
            "phase": phase.upper(),
            "bars": sum(r[f"{phase}_bars"] for r in results),
            "total_trades": pooled["total_trades"],
            "win_rate": pooled["win_rate"],
            "avg_win": pooled["avg_win"],
            "avg_loss": pooled["avg_loss"],
            "profit_factor": pooled["profit_factor"],
            "expectancy": pooled["expectancy"],
            "max_drawdown": combined_equity_metrics["max_drawdown"],
            "sharpe_annualized": combined_equity_metrics["sharpe_annualized"],
            "total_return": combined_equity_metrics["total_return"],
            "exposure_pct": None,  # not meaningful pooled across differing timeframes
            "trade_duration": _duration_summary(pooled["trade_durations"]),
        })

    # --- Write metrics table (CSV) ---
    csv_path = os.path.join(REPORTS_DIR, "metrics.csv")
    fieldnames = ["instrument", "phase", "bars", "total_trades", "win_rate", "avg_win", "avg_loss",
                  "profit_factor", "expectancy", "max_drawdown", "sharpe_annualized", "total_return",
                  "exposure_pct", "trade_duration"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            row = dict(row)
            row["trade_duration"] = json.dumps(row["trade_duration"])
            writer.writerow(row)
    print(f"\nWrote {csv_path}")

    # --- Print markdown table to stdout ---
    print("\n" + _markdown_table(rows))
    return rows


def _fmt(v, pct=False, digits=4):
    if v is None:
        return "-"
    if isinstance(v, float) and math.isinf(v):
        return "inf"
    if pct:
        return f"{v * 100:.2f}%"
    return f"{v:.{digits}f}"


def _markdown_table(rows):
    header = "| Instrument | Phase | Bars | Trades | Win% | AvgWin | AvgLoss | ProfitFactor | Expectancy | MaxDD | Sharpe(ann) | TotalReturn | Exposure% | MedianDur(h) |"
    sep = "|" + "---|" * 14
    lines = [header, sep]
    for r in rows:
        dur = r["trade_duration"]
        median_h = dur.get("median_hours")
        lines.append(
            "| {instrument} | {phase} | {bars} | {total_trades} | {win} | {avgw} | {avgl} | {pf} | {exp} | {dd} | {sharpe} | {tr} | {expo} | {dur} |".format(
                instrument=r["instrument"], phase=r["phase"], bars=r["bars"], total_trades=r["total_trades"],
                win=_fmt(r["win_rate"], pct=True), avgw=_fmt(r["avg_win"]), avgl=_fmt(r["avg_loss"]),
                pf=_fmt(r["profit_factor"]), exp=_fmt(r["expectancy"]), dd=_fmt(r["max_drawdown"], pct=True),
                sharpe=_fmt(r["sharpe_annualized"]), tr=_fmt(r["total_return"], pct=True),
                expo=_fmt(r["exposure_pct"], pct=True), dur=_fmt(median_h, digits=1),
            )
        )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
