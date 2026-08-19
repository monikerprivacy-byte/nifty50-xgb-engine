# NSE Market Microstructure Research — India

*Last updated: 2026-07-16*

## 1. NSE Trading Mechanism

- Pure electronic limit order book (No designated market maker)
- Trading hours: 9:15 - 15:30 IST
- Pre-open session: 9:00 - 9:15 (call auction)
- Settlement: T+1 (rolling)
- Lot size varies by stock (SEBI regulated)

## 2. Intraday Liquidity Patterns (NSE)

### 2.1 U-Shaped Pattern
Research consistently shows:
- **Opening (9:15-10:00)**: High volume + WIDE spreads (overnight info, cautious MM)
- **Midday (10:00-14:00)**: Tightest spreads, optimal execution window
- **Closing (14:30-15:30)**: Volume surge + WIDER spreads (position squaring)

### 2.2 Spread Characteristics (Bank Nifty study, 2026)
| Moneyness | Spread (bps) | vs ATM |
|-----------|-------------|--------|
| ATM | 45-48 | 1x |
| 5% OTM | 125-128 | 2.7x |
| 10% OTM | 285-295 | 6.2x |

**Note**: These are from a Bank Nifty specifics study. NIFTY-50 stock options will differ. We calibrate from our OWN captured data.

### 2.3 Optimal Execution Window
10:00 - 14:00 IST: 20-35 bps lower transaction costs vs open/close

## 3. Retail Dominance (Agarwal et al., 2025)

- Retail = 42% of 0DTE volume (end of sample)
- 90% of trades are day trades
- 87% of notional volume within 6 days of expiry
- Losses: Significant and persistent
- Retail prefers: Short-dated, ATM, slightly OTM, low-premium contracts

## 4. India VIX Behavior

- Range: 10-80+ historically
- Mean reversion tendency
- Daily close available from NSE archive
- Intraday VIX not available via Dhan
- Documented correlation: High VIX → wider option spreads

## 5. F&O Ban Mechanism

- SEBI rule: When combined open interest > 95% of MWPL (Market Wide Position Limit)
- Stock enters F&O ban period next day
- Only square-off allowed, no new positions
- NIFTY-50 stocks with F&O ban history: Multiple events per year
- Must monitor daily: `https://www.nseindia.com/regulations/market-wide-position-limit-mwpl`

## 6. Price Limits
- NIFTY-50 individual stocks: 20% daily circuit (upper/lower)
- Index options: No circuit
- Stock options: Dependent on underlying

## 7. Corporate Actions Affecting Options
- Stock splits → Strike adjustment
- Bonus issues → Strike adjustment
- Mergers → Contract restructuring
- Dividends (specified dividend adjusted)

Dhan's daily historical API is NOT corporate-action-adjusted (confirmed via community reports). Must apply adjustments manually.

## 8. Implications for This Project

| Microstructure Fact | Project Adaptation |
|---------------------|-------------------|
| U-shaped spreads | Time-of-day feature, not hard-coded timing gate |
| ATM liquidity dominance | Moneyness-aware execution cost model |
| Retail concentration in 0DTE | Expiry-day specific model behavior expected |
| F&O bans | Universe filter: skip banned stocks |
| Corporate actions | Adjustment table: pre-compute, not model-time |
| 20% circuit limits | Feature: distance from circuit |
| VIX mean reversion | Regime feature for vol-aware training |
