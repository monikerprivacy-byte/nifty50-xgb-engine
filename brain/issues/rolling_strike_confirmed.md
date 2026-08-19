# Issue: Dhan RollingOption is Relative Strike, NOT Fixed Contract

*Status: CONFIRMED (2026-07-16)*
*Severity: CRITICAL — training data architecture*

## Description

`/v2/charts/rollingoption` returns OHLCV+IV+OI for a "near-expiry expired" option at a relative moneyness position (e.g., ATM-3). The **absolute strike changes** as the underlying moves.

This means:
- Day 1's ATM-3 CALL may be 1260 CE
- Day 2's ATM-3 CALL may be 1280 CE
- These are DIFFERENT contracts — cannot be compared directly

## Empirical Proof

**Scrip**: RELIANCE rollingoption (`NSE|RELIANCE|1260|CE`), ATM-3, 5 trading days

| Day | Date | Absolute Strikes Seen | Strike Switches |
|-----|------|----------------------|-----------------|
| 1 | 2026-07-10 | 1260, 1270 | 21 |
| 2 | 2026-07-13 | 1270, 1280 | 14 |
| 3 | 2026-07-14 | 1280 | 1 |
| 4 | 2026-07-15 | 1290 | 7 |
| 5 | 2026-07-16 | 1300, 1290 | 6 |
| **Total** | **5 days** | **5 unique strikes** | **49 switches** |

Spot range during period: 1289.5 - 1331.2

## Impact

### ❌ Cannot use for:
- Continuous contract-level returns
- OI differences (different contracts)
- Volume differences (different contracts)
- VWAP (different contracts)
- Target-before-stop labels
- MFE/MAE calculations
- Any contract-to-contract comparison

### ✅ Valid for:
- Relative IV surface (IV at ATM-3 regardless of strike)
- Relative OI surface
- Relative volume surface
- IV skew and curvature
- Surface activity migration
- Spot-to-surface distance relationships

## Action Required

1. Establish FIXED_CONTRACT dataset for training (external data source)
2. Keep RELATIVE_ROLLING_SURFACE for surface features only
3. NEVER mix the two in training
4. Document in all downstream code

## Verification Script

See: `brain/experiments/rolling_strike_audit/run_audit.sh` (uses `/v2/charts/rollingoption` with daily polling)
