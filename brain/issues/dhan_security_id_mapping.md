# Issue: Dhan Security ID Mapping and Limitations

*Status: DIAGNOSED (2026-07-16)*
*Severity: MEDIUM — requires periodic refresh*

## Description

Dhan's security master CSV changes every expiry. New contracts appear, old ones vanish. Security IDs must be resolved dynamically.

## Findings

### Security Master (Compact CSV)
- Total records: ~600K
- Stock options (OPTSTK): 69,155 rows
- Unique option underlying symbols: 210
- Stock futures: 643 rows (67 symbols)

### Underlying Mapping Issue
Derivative contracts reference underlying via `security_id`. For options, the `underlying_security_id` field should map to the cash instrument. Community reports (MadeForTrade) indicate some index option underlying mappings may be incorrect.

### Contract Security ID Mutation
Option security IDs change on:
- New expiry listing (every week/month)
- Strike addition mid-series
- Corporate action adjustment

## Action Required

1. Refresh security master daily at 8:30 AM
2. Build lookup table: `(symbol, expiry, strike, type) → security_id`
3. Do NOT cache security IDs beyond 24 hours
4. Verify underlying mapping at startup
5. Handle stale security IDs gracefully (log + skip)
