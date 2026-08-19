# Issue: Dhan Historical Intraday/Daily APIs Return Empty for Options

*Status: CONFIRMED (2026-07-16)*
*Severity: HIGH — blocks fixed-strike historical acquisition from Dhan*

## Description

Dhan's standard `POST /v2/charts/intraday` and `POST /v2/charts/historical` endpoints return empty arrays or HTTP 400 errors for OPTSTK (stock options) and OPTIDX (index options) instrument types.

## Test Results

### Test 1: OPTIDX Intraday
- Instrument: `RELIANCE23AUG20241260CE` (OPTIDX)
- Timeframe: 5 min
- Exchange: NSE
- Result: Empty arrays returned

### Test 2: OPTSTK Daily  
- Instrument: `RELIANCE23AUG20241260CE` (OPTSTK)
- Exchange: NSE
- Result: HTTP 400 Bad Request

### Test 3: Cash Intraday (Control)
- Instrument: `RELIANCE` (EQUITY)
- Timeframe: 5 min
- Exchange: NSE
- Result: ✅ Full OHLCV data returned

## Root Cause

Unknown — could be:
- Internal Dhan limitation (don't store option tick data historically)
- API bug (historical endpoints not wired for options)
- Policy decision (options have shorter record-keeping requirements)

## Impact

Dhan cannot be used to:
- Backfill historical option training data
- Build fixed-contract historical OHLCV dataset
- Validate live capture against published history

## Workaround

External data source required:
1. **NSE Bhavcopy** (free, daily only) — daily snapshot, no intraday
2. **Vendor** (e.g., TrueData, IQFeed, historical NSE feed) — intraday but paid
3. **Self-capture** — from now forward, accumulate live WebSocket data

## Resolution Timeline

Short term: Start live capture now (Dhan WebSocket for live fixed-strike)
Medium term: Source NSE daily bhavcopy for backfill
Long term: Evaluate vendor for intraday historical depth
