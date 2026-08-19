# Dhan API Research — Complete Capability Map

*Last updated: 2026-07-16*
*Status: VERIFIED via live credentials (client_id: 1110480081, Data Plan active)*

## 1. Authentication

| Method | Detail |
|--------|--------|
| Type | JWT access token + client_id |
| Token generation | From Dhan Web → Settings → API |
| Token expiry | 24 hours (renewable via API) |
| Data API cost | ₹499/month |
| Trading API | Free |

**Verified**: Token valid until 17/07/2026 07:54, segments active: E (Equity), D (Derivatives), C (Currency), M (Commodity)

## 2. REST API Endpoints

### 2.1 Data APIs

| Endpoint | Method | Purpose | Rate Limit | Tested |
|----------|--------|---------|------------|--------|
| `/v2/charts/historical` | POST | Daily OHLCV | 5 req/s | ✅ Works for cash/futures |
| `/v2/charts/intraday` | POST | Intraday OHLCV (1/5/15/25/60m) | 5 req/s | ✅ Works for cash/futures |
| `/v2/charts/rollingoption` | POST | Expired options (rolling) | 5 req/s | ✅ CONFIRMED rolling (see issue) |
| `/v2/optionchain` | POST | Live option chain + Greeks | 1 req/3s | ✅ 101 strikes for RELIANCE |
| `/v2/optionchain/expirylist` | POST | Available expiries | 1 req/3s | ✅ 3 expiries for RELIANCE |
| `/v2/marketfeed/ltp` | POST | Snapshot LTP (1000 instruments) | 1 req/s | Not yet tested |
| `/v2/marketfeed/ohlc` | POST | Snapshot OHLC | 1 req/s | Not yet tested |
| `/v2/marketfeed/quote` | POST | Full quote + depth | 1 req/s | Not yet tested |

### 2.2 Trading APIs (Not Used — informational)

| Endpoint | Purpose |
|----------|---------|
| `/v2/orders` | Place/modify/cancel orders |
| `/v2/orders/slicing` | Slice over freeze limit |
| `/v2/positions` | Current positions |
| `/v2/holdings` | Demat holdings |
| `/v2/funds` | Margin/funds |
| `/v2/ip/setIP` | Static IP for SEBI compliance |

## 3. WebSocket — Live Market Feed

| Property | Value |
|----------|-------|
| Max connections | 5 per account |
| Instruments per connection | 5,000 |
| Instruments per subscription message | 100 |
| Modes | Ticker, Quote, Full (LTP + OI + Depth) |
| 20-level depth | Available |
| 200-level depth (Full) | Available (requires explicit option) |
| Data format | Binary (Little Endian) |
| Ping interval | Server ping every 10s |
| Disconnect timeout | 40s without response |
| Authentication | URL query params or header |

### Recommended Socket Architecture
```
Socket A: Cash stocks (50) + futures (50) + NIFTY index — Quote mode
Socket B: Stock options, stocks 1-25 — Quote mode
Socket C: Stock options, stocks 26-50 — Quote mode
Socket D: Shortlisted Full-mode instruments (dynamic)
```

## 4. Historical Data — Key Findings

### 4.1 Cash/Futures (✅ Works)
- Daily: Full history from inception
- Intraday: Last 5 years, up to 90 days per call
- Timeframes: 1, 5, 15, 25, 60 min
- OI available for F&O

### 4.2 Options (🚫 ISSUES)
- Standard intraday endpoint (`/charts/intraday`): Returns **empty arrays** for option contracts
- Standard daily endpoint (`/charts/historical`): Returns **HTTP 400** for option contracts
- Only accessible via `/charts/rollingoption` which is **rolling (relative strike)**

## 5. Expired Options (/charts/rollingoption) — DETAILED

### What it returns
- CE and PE arrays separately
- Per array: open, high, low, close, volume, iv, oi, strike, spot, timestamp
- Up to 45 days per call
- Last 5 years available

### Critical: Rolling Strike Behavior
**Empirically verified (RELIANCE, 5 days, ATM-3 CALL)**:
- 373 records
- 5 different absolute strikes (1260, 1270, 1280, 1290, 1300)
- 49 intraday strike switches
- Spot range: 1289.5 - 1331.2

This means ATM-3 on the rolling endpoint does NOT represent a single contract.

### What Rolling Data IS Good For
- Relative IV surface (IV at ATM-3 regardless of which strike)
- Relative OI/volume surface
- IV skew (difference between ATM-5 and ATM+5)
- Surface curvature and activity migration
- Spot-to-surface relationship

## 6. Option Chain — DETAILED

### Response Fields per Strike
| Field | Available |
|-------|-----------|
| last_price | ✅ |
| implied_volatility | ✅ |
| delta, gamma, theta, vega | ✅ |
| oi | ✅ |
| volume | ✅ |
| top_bid_price/qty | ✅ |
| top_ask_price/qty | ✅ |
| security_id (per option) | ✅ |
| previous_close/oi | ✅ |

### Rate Limit Impact
- 1 req / 3 sec per unique underlying
- 50 stocks → minimum 150 seconds for full refresh
- Solution: Poll in background, not for real-time trading

## 7. Security Master

| File | Records | Use |
|------|---------|-----|
| Compact CSV (~50MB) | ~600K rows | Security ID lookup, lot size, tick size |
| Detailed CSV | ~600K rows | Additional ISIN, underlying mapping |

### Stock Options Count: 210 unique stocks, 69,155 contract rows

### Known Issues
- Underlying security ID mapping may be incorrect for some indices (per MadeForTrade community reports)
- Derivative security IDs change every expiry (must resolve fresh)
- Update daily at 8:30 AM

## 8. Rate Limits Summary

| API Category | Per Second | Per Day |
|-------------|-----------|---------|
| Order | 10 | 7,000 |
| Data (historical) | 5 | 100,000 |
| Quote (snapshot) | 1 | Unlimited |
| Non-trading | 20 | Unlimited |
| Option Chain | 1 per 3s per underlying | Unlimited |

Historical minute/hourly: "No rate limits" (official doc)
Historical per-second: 5 requests/second
