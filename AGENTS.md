# Agent Instructions

## Project Path
```
/Volumes/Untitled/untitled folder 5/nifty50-xgb-engine/
```

## Key Constraints
- XGBoost is the ONLY prediction engine AND the only model family. LightGBM/CatBoost/neural are excluded.
- Challengers are XGBoost variants only (different objective, featureset, ranker, sector-specific, calibration).
- Dhan rolling option data is RELATIVE STRIKE, not fixed-contract. Never use for training.
- Rolling data resets on strike change: segment_id, premium/OI/VWAP indicators, segment_boundary flag.
- Two strictly separated dataset types: FIXED_CONTRACT and RELATIVE_ROLLING_SURFACE. NEVER mixed.
- CPCV (Combinatorial Purged CV) is primary validation, not walk-forward.
- PBO three-tier gate: <0.25 pass, 0.25-0.40 warning, >0.40 reject. All trialed configs in denominator.
- DSR significant = P(Sharpe > 0 after selection adjustment) >= 95%. Report Sharpe, DSR, trials, skew, kurtosis, sample length.
- External vendor data requires 14-point source audit before Gold layer (see §4.5).
- IV/Greeks: track provider_iv, locally_calculated_iv, model_input_iv separately. NaN+flag for illiquid. Never fill 0.
- Every live prediction requires 12-field lineage block (§7.3): prediction_id, model_version, feature_schema_version, etc.
- IV/Greeks/VIX are candidate features — must pass ablation, not assumed.
- Dhan historical intraday returns empty for options — use NSE bhavcopy or vendor.
- DuckDB 1.4.4 LTS, ARM native (`osx_arm64`), confirmed working.
- dhanhq 2.0.2 confirmed working with RELIANCE test.
- All features must have `available_time <= decision_time` — causal contract.

## Dhan Credentials
- client_id: 1110480081 (from .env or environment)
- Source: `DHAN_ACCESS_TOKEN` environment variable
- Token stored in `.env` file (gitignored — never commit)
- Token expires: 18/07/2026 (renew through Dhan Web)
- Data plan: Active (₹499/mo)
- WARNING: Never store the raw token in repository files.

## Verification Commands
- Lint: `ruff check .`
- Type check: N/A (no types yet)
- Tests: `python -m pytest tests/` (once tests are written)

## Brain Folder Structure
```
brain/
├── research/          ← Deep research on each area
├── issues/            ← Documented issues with evidence
├── solutions/         ← Verified solutions
├── audit/             ← Audit trail for every decision
└── experiments/       ← Experiment tracking
```

## Constitution
`PROJECT_CONSTITUTION.md` is the authoritative design document. Read before making architectural changes.

## Protocol Architecture (Post-Repair)
- `src/providers/dhan/v2/` — v2 API implementation (client, decoder, subscriptions, protocol, packet_types)
- `src/providers/dhan/websocket.py` — backward-compat re-export wrapper only
- Three-tier subscription state: `_desired_subscription_ids` (preserved across reconnects), `_sent_subscription_ids` (cleared on reconnect), `_seen_subscription_ids` (observed via packets)
- Connection generation guard (`_connection_generation`) prevents stale callbacks from old sockets
- Health progression: DISCONNECTED → CONNECTING → CONNECTED → SUBSCRIPTIONS_SENT → FIRST_PACKET_RECEIVED → STREAMING_HEALTHY
- STREAMING_HEALTHY requires `min_healthy_instruments` (default 2) distinct instruments observed
- `notify_packet_received(security_id, generation)` — generation-guarded, per-instrument tracking
