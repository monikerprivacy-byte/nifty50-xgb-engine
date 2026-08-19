# Solution: Fixed-Strike Data Strategy

*Last updated: 2026-07-16*
*Status: DESIGN PROPOSAL — not yet implemented*

## Problem

Dhan cannot provide historical fixed-strike option data. Model training requires:
- Daily OHLCV per absolute strike + expiry
- At least 2-3 years of history for robust training
- CE and PE both required
- NIFTY-50 constituents (varying by point-in-time membership)

## Option A: NSE Bhavcopy (Free, Daily Only)

### What's Available
- NSE equity derivatives bhavcopy: daily snapshot of all F&O contracts
- Fields: SYMBOL, EXPIRY, STRIKE, OPTION_TYPE, OPEN, HIGH, LOW, CLOSE, SETTLE_PR, CONTRACTS, VAL_INLAKH, OPEN_INT, CHG_IN_OI
- Free, no API key required
- Available for download at: `https://www.nseindia.com/api/...`

### Pros
- Zero cost
- Official NSE data
- All strikes, all expiries
- Point-in-time
- Daily coverage since 2019+ (equity derivatives bhavcopy)

### Cons
- Daily only (no intraday)
- Must scrape (NSE has anti-bot measures)
- Data cleaning required (format changes, missing days)

### Implementation
```python
# Mock structure
class NseBhavcopyProvider:
    def fetch_daily(self, date: datetime) -> pd.DataFrame:
        # 1. Download from NSE
        # 2. Parse CSV
        # 3. Filter for F&O contracts (OPTIDX + OPTSTK)
        # 4. Map to unified schema
        # 5. Store in DuckDB bronze layer
        pass
```

## Option B: Vendor Intraday (Paid)

### Available Vendors
| Vendor | Intraday? | Cost | NSE Options? |
|--------|-----------|------|-------------|
| TrueData | 1m | ~$100/mo | Likely (verify) |
| BQNT by Bloomberg | Yes | Enterprise | Yes |
| Quantsapp | Yes | ₹5K+/mo | Yes |
| Opstra | Daily | Free limited | Yes |
| NSE CDS (Data Services) | Yes | Expensive | Yes |

### Pros
- Intraday data (5m, 15m)
- Clean and normalized
- API access

### Cons
- Cost recurring
- Verification needed for completeness
- Contract mapping may vary from Dhan

## Option C: Self-Capture (From Now Forward)

### What We Can Do
1. Start Dhan WebSocket capture NOW
2. Capture ATM±5 fixed-strike contracts for NIFTY-50
3. Accumulate over days/weeks/months
4. Build historical training pipeline from Day 1

### Timeline
- 1 month: ~1,000 bars per contract (5m)
- 1 year: ~12,000 bars per contract (5m)
- For initial training: Need external data OR wait 6+ months

## Recommended Strategy

```
Phase 1 (Immediate): Start Dhan WebSocket live fixed-strike capture
    → Accumulate gold layer from Day 1
    → Enable live prediction from Day 1

Phase 2 (1-2 weeks): Implement NSE Bhavcopy daily scraper
    → Backfill daily OHLCV for all NIFTY-50 option strikes
    → Enable daily feature training
    → Initial daily models

Phase 3 (1-2 months): Evaluate vendor for intraday
    → If cost-justified: integrate intraday vendor data
    → If not: train on daily + accumulate intraday from live

Phase 4 (Ongoing): Hybrid training
    → Daily models: NSE bhavcopy (long history)
    → Intraday models: Live capture (growing history)
    → Surface models: Dhan rolling data (relative features)
```

## Fallback

If NSE scraping proves unreliable:
1. Use NSE's official Excel bhavcopy (manual download)
2. Use Opstra's free tier for verification
3. Consider HistoricalData.com or similar vendor
4. Accept daily-only models until self-capture reaches critical mass
