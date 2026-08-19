# Dhan API Test Results — Audit Log

*Generated: 2026-07-16 22:30 IST*
*Credentials: client_id=1110480081, Data Plan=Active*
*Token expiry: 2026-07-17 07:54:44 IST*

---

## Test 1: Profile Verification

**Date**: 2026-07-16  
**Endpoint**: `GET /v2/profile`  
**Result**: ✅ PASS

| Field | Value |
|-------|-------|
| client_id | 1110480081 |
| data_plan | ACTIVE |
| token_expiry | 17/07/2026 07:54:44 |
| dpi_activated | DEACTIVATED |
| mtf_activated | DEACTIVATED |
| order_types | [

] (empty — trading not used) |
| Segments | E (Equity), D (Derivatives), C (Currency), M (Commodity) |

**Validation**: Data plan active, token valid. All required segments present.

---

## Test 2: Security Master Download (Compact)

**Date**: 2026-07-16  
**URL**: `https://images.dhan.co/api-data/api-scrip-master.csv`  
**Result**: ✅ PASS (~50MB, ~600K rows)

| Category | Record Count | Notes |
|----------|-------------|-------|
| Total | ~600K | Full file |
| OPTSTK | 69,155 | Stock options |
| OPTIDX | ~8K | Index options |
| FUTSTK | 643 | Stock futures |
| FUTIDX | ~100 | Index futures |
| EQUITY | 9,609 | Cash equities |
| Unique OPTSTK underlyings | 210 | Stocks with F&O |
| Unique FUTSTK underlyings | 67 | |

---

## Test 3: RELIANCE Option Chain

**Date**: 2026-07-16  
**Endpoint**: `POST /v2/optionchain`  
**Underlying**: RELIANCE  
**Result**: ✅ PASS

| Field | Value |
|-------|-------|
| Underlying LTP | 1296.6 |
| Total strikes | 101 |
| Strike range | 680 to 1920 |
| Greeks present | IV, Delta, Gamma, Theta, Vega |
| Bid/Ask | Yes |
| OI | Yes |
| Volume | Yes |

---

## Test 4: RELIANCE Expiry List

**Date**: 2026-07-16  
**Endpoint**: `POST /v2/optionchain/expirylist`  
**Underlying**: RELIANCE  
**Result**: ✅ PASS (3 active expiries)

---

## Test 5: Rolling Option (Expired) — RELIANCE ATM-3 CALL

**Date**: 2026-07-16  
**Endpoint**: `POST /v2/charts/rollingoption`  
**Params**: NSE|RELIANCE|1260|CE, ATM-3, daily, 5 days  
**Result**: ✅ PASS but CONFIRMED ROLLING STRIKE

- 373 records
- 5 unique absolute strikes (1260, 1270, 1280, 1290, 1300)
- 49 intraday strike switches
- Spot range: 1289.5 - 1331.2

**See**: `brain/issues/rolling_strike_confirmed.md` for full analysis

---

## Test 6: Intraday Historical — Cash (Control)

**Date**: 2026-07-16  
**Endpoint**: `POST /v2/charts/intraday`  
**Instrument**: RELIANCE (EQUITY)  
**Timeframe**: 5 min  
**Result**: ✅ PASS — full OHLCV returned

---

## Test 7: Intraday Historical — Options

**Date**: 2026-07-16  
**Endpoint**: `POST /v2/charts/intraday`  
**Instrument**: RELIANCE23AUG20241260CE (OPTIDX)  
**Timeframe**: 5 minute  
**Result**: ❌ FAIL — empty arrays

---

## Test 8: Daily Historical — Options

**Date**: 2026-07-16  
**Endpoint**: `POST /v2/charts/historical`  
**Instrument**: RELIANCE23AUG20241260CE (OPTSTK)  
**Result**: ❌ FAIL — HTTP 400 Bad Request

---

## Summary

| API | Status | Note |
|-----|--------|------|
| Profile | ✅ | Valid, active |
| Security Master | ✅ | Downloaded, parsed |
| Option Chain | ✅ | Full Greeks, 101 strikes |
| Expiry List | ✅ | 3 expiries |
| Rolling Option | ✅ | Usable for surface features only |
| Intraday Cash | ✅ | Full OHLCV |
| Intraday Options | ❌ | Empty — no historical storage |
| Daily Options | ❌ | HTTP 400 |
| WebSocket | ⏳ | Untested (requires code) |
| Snapshot LTP | ⏳ | Untested |
| Quote | ⏳ | Untested |

## Key Takeaway

1. Live market data: ✅ WebSocket should work (verify next)
2. Option chain + Greeks: ✅ Available periodically
3. Historical options: ❌ Must use external source
4. Rolling data: ✅ Only for surface, not for contract training
