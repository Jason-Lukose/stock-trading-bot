# Strategy Specification

## Source and Trust Level

All strategies below originate from the PDF "How to Build a Trading Bot with Claude That makes you $3k/month" (in `/reference`). The PDF is a marketing lead-magnet for a paid community. Its title contains an income claim which the PDF itself admits is "one person's reported result and is not typical."

**Trust policy:** every parameter, threshold, and behavioral claim below is tagged `[UNVERIFIED — PDF CLAIM]` and is a *hypothesis to test*, not a fact. Nothing here is believed until our backtester independently reproduces it under the methodology in docs/Backtesting.md, including transaction costs and out-of-sample validation.

**Profit expectation:** none. The correct prior is that each strategy is unprofitable after costs until walk-forward evidence says otherwise. The majority of retail systematic strategies fail out-of-sample.

## ⚠️ Blocking Precondition: Pattern Day Trader (PDT) Rule

Strategies 1 (SPY, QQQ on 15-minute candles) generate intraday round trips. In a real margin account, 4+ day trades in 5 business days classifies the account as a Pattern Day Trader, requiring **$25,000 minimum equity** or the account is restricted. The PDF never mentions this.

- Paper trading: not an issue (simulated).
- Any future live consideration: PDT compliance is a hard precondition documented in RiskRules.md. Without $25k+, SPY/QQQ intraday strategies must be dropped or converted to swing timeframes.

## Instruments

| Symbol | Asset | Strategy | Timeframe | Session |
|---|---|---|---|---|
| SPY | S&P 500 ETF | Mean reversion | 15-min | Equity market hours |
| QQQ | Nasdaq-100 ETF | Mean reversion | 15-min | Equity market hours |
| BTC/USD | Bitcoin (Alpaca crypto) | Momentum breakout | 1-hr | 24/7 |
| GLD | Gold ETF | Trend following | 4-hr | Equity market hours |
| USO | Oil ETF | Trend following | 4-hr | Equity market hours |

Note: GLD and USO are ETFs, not commodity futures. USO in particular has significant roll-cost/structural decay characteristics that make "commodities trend cleanly" claims questionable when applied to it. `[PDF ASSUMPTION — treats ETFs as proxies for commodity behavior; must be validated]`

## Strategy 1 — Mean Reversion (SPY, QQQ)

**Hypothesis** `[UNVERIFIED — PDF CLAIM]`: indices overextend intraday and revert to the mean.

- Bars: 15-minute
- Indicators: 20-period SMA, 20-period rolling standard deviation
- Entry long: price < SMA − k·σ; Entry short: price > SMA + k·σ
  - k = 1.5 for SPY, k = 1.8 for QQQ `[UNVERIFIED — PDF gives no evidence for these values]`
- Exit: price returns to the SMA
- Stop: hard stop such that loss = 1% of equity (via ATR sizing, see RiskRules.md)

**Documented assumptions to test:** (a) reversion exists at this frequency after spread/slippage; (b) k thresholds aren't curve-fit; (c) shorting is even permitted/practical for the account type; (d) behavior in trending days (mean reversion's known failure mode) doesn't dominate P&L.

## Strategy 2 — Momentum Breakout (BTC/USD)

**Hypothesis** `[UNVERIFIED — PDF CLAIM]`: crypto trends harder than indices; ride breakouts.

- Bars: 1-hour
- Entry long: close breaks above 20-period high AND volume ≥ 1.5× 20-period average volume
- Entry short / exit long: close breaks below 20-period low with same volume confirmation
- Trailing stop: 2× ATR(14) `[UNVERIFIED — PDF parameter]`

**Documented assumptions to test:** (a) breakout edge survives crypto spreads and slippage (crypto slippage is typically worse than the PDF's flat 0.05%); (b) volume data quality from Alpaca crypto feed is sufficient for the filter; (c) shorting BTC may not be supported on Alpaca spot crypto — if not, "go short" degrades to "exit," which changes the strategy materially and must be re-specified.

## Strategy 3 — Trend Following (GLD, USO)

**Hypothesis** `[UNVERIFIED — PDF CLAIM]`: commodities move in cleaner long waves.

- Bars: 4-hour
- Entry long: 50-period EMA crosses above 200-period EMA
- Exit / short: 50 EMA crosses below 200 EMA
- Trailing stop: 3× ATR(14) `[UNVERIFIED — PDF parameter]`

**Documented assumptions to test:** (a) 50/200 EMA on 4-hr bars requires ~200×4 = 800+ hours of warm-up data — signal count over any 6-month window will be tiny (possibly 1–3 trades per instrument), which is statistically meaningless; (b) USO structural decay; (c) 4-hr bars for equities interact with market hours (a "4-hour bar" during a 6.5-hour session is ill-defined — bar construction must be specified exactly).

## Cross-Strategy Rules (see RiskRules.md for enforcement)

- ATR-based sizing: position sized so 1 ATR adverse move ≈ 1% of equity
- Hard stops, never widened, never removed
- Correlation filter v1: if SPY and QQQ are both long, no new BTC/USD longs `[PDF rule — acknowledged as minimal; see RiskRules.md for its limitations]`
- Portfolio circuit breaker: 10% drawdown from peak closes all positions and halts

## Parameter Change Policy

Any change to any parameter above must be logged in `docs/ParameterLog.md` with date, old/new value, and reason. The number of parameter sets ever tried is itself an overfitting metric (see Backtesting.md). Parameters may never be changed in response to out-of-sample or paper-trading results without resetting the validation clock.
