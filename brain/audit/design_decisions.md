# Design Decisions — Audit Trail

*This file documents every material design decision, the rationale, and alternatives considered.*

---

## D001: XGBoost-Only Prediction Ownership

**Date**: 2026-07-16  
**Decision**: All market predictions come exclusively from XGBoost models. Hermes, Telegram, and rules do not make directional predictions.  
**Rationale**: 
- Hermes (LLM) lacks quantifiable prediction confidence and calibration
- Rule-based systems hard-code brittle heuristics
- XGBoost provides calibrated probabilities, feature importance, and shapley values
- Single model family simplifies debugging, validation, and monitoring  
**Alternatives Considered**: 
- Hermes generates predictions (rejected: no calibration, hard to validate)
- Hybrid Hermes-XGBoost (rejected: creates unaccountable grey zone)
- Pure rule-based (rejected: too brittle, doesn't learn)  
**Consequence**: Hermes development scope reduced; focus on data pipeline and ML.

---

## D002: DuckDB 1.4.4 LTS

**Date**: 2026-07-16  
**Decision**: DuckDB v1.4.4 LTS as primary storage  
**Rationale**: Verified ARM native wheel (`osx_arm64` column store, vectorized engine, Python-native, single-file or partitioned Parquet, date-partitioned for time-series, zero-config, no server)  
**ARM Compatibility**: Confirmed — DuckDB 1.4.4 LTS has `osx_arm64` wheel on PyPI  
**Alternatives Considered**: 
- SQLite (no column-store, no Parquet)
- PostgreSQL (operational overhead, server management)
- Apache Arrow + Parquet files only (no SQL query capability)
- TimescaleDB (overkill for local/data analysis setup)

---

## D003: Fixed-Contract vs Rolling Data Separation

**Date**: 2026-07-16  
**Decision**: Two strictly separated dataset types: FIXED_CONTRACT (training) and RELATIVE_ROLLING_SURFACE (surface features only)  
**Rationale**: Dhan rolling data confirmed to switch strikes intraday — cannot be used for contract-level training  
**Alternatives Considered**: Treat rolling data as proxy (rejected: introduces unknown bias in 49+ switches per 5 days)

---

## D004: CPCV as Primary Validation

**Date**: 2026-07-16  
**Decision**: Combinatorial Purged Cross-Validation replaces single-path walk-forward  
**Rationale**: 
- Single walk-forward path has high variance
- CPCV produces distribution of outcomes
- PBO measurable  
**Alternatives Considered**: 
- Walk-forward only (rejected: high variance, no overfitting detection)
- K-fold shuffled (rejected: temporal leakage)

---

## D005: IV/Greeks/VIX as Candidate Features

**Date**: 2026-07-16  
**Decision**: IV, Greeks, and India VIX are MANDATORY CANDIDATE features, accepted or rejected through controlled ablation  
**Rationale**: 
- Literature shows mixed results (some studies find IV dominant, some find it irrelevant)
- Ablation is the only way to resolve for this specific dataset  
**Alternatives Considered**: 
- Include unconditionally (risks noise if irrelevant)
- Exclude unconditionally (risks missing signal)
- Only include what Dhan provides for free (but VIX may require external fetch)

---

## D006: Historical Option Data from External Source

**Date**: 2026-07-16  
**Decision**: Dhan cannot provide fixed-strike option historical data. Must use NSE bhavcopy (daily) + potentially vendor (intraday)  
**Alternatives Considered**: 
- Accept Dhan limitation and train only on rolling data (rejected: unsound)
- Wait for self-capture to accumulate (rejected: too long for initial training)
- Use synthetic data (rejected: unsound)

---

## D007: ATM Hysteresis

**Date**: 2026-07-16  
**Decision**: ATM strike retained until spot crosses a confirmation threshold, not changed on every tick  
**Rationale**: 
- Tick-level ATM re-selection introduces noise
- Small spot movements shouldn't change the entire surface view  
**Implementation**: 
- New ATM candidate must be >0.3% above/below current ATM midpoint
- ATM threshold as configurable parameter

---

## D008: Hermes as Sidecar

**Date**: 2026-07-16  
**Decision**: Hermes has read-only access to the project; cannot predict, trade, or modify production configurations  
**Rationale**: LLMs cannot provide calibrated probability estimates; separation of concerns keeps the ML pipeline clean  
**Alternatives Considered**: Hermes as co-pilot (rejected: creates unaccountable decisions)

---

## D009: No Hard-Coded Timing Gates

**Date**: 2026-07-16  
**Decision**: Execution windows are data-driven, not hard-coded  
**Rationale**: 
- The 10:00-14:00 optimal window is a research finding, not a law
- Market regimes change; hard-coded gates become stale  
**Implementation**: Spread, depth, age-of-quote, and expected edge are the gates.

---

## D010: Challenger Model Family Restriction

**Date**: 2026-07-17  
**Decision**: LightGBM and CatBoost removed as challengers. Only XGBoost variants compete.  
**Rationale**: 
- Original doctrine (XGBoost-only prediction) conflicted with permitting LightGBM/CatBoost
- Multi-family introduces model-selection complexity without guaranteed benefit
- XGBoost variants (objective, featureset, ranker, sector-specific, calibration) provide sufficient search space
- Simpler deployment, monitoring, and debugging  
**Consequence**: Removed from Constitution §7.2, replaced with XGBoost-only variant list

---

## D011: PBO Methodology Freeze

**Date**: 2026-07-17  
**Decision**: PBO methodology frozen in Constitution — CSCV on cost-adjusted Sharpe, all genuinely tested alternatives in denominator, three-tier gate (pass/warning/reject)  
**Rationale**: PBO < 0.25 as hard gate without methodology invites manipulation; freezing method before experiments protects integrity

---

## D012: DSR Definition Freeze

**Date**: 2026-07-17  
**Decision**: DSR "significant" defined as `P(Sharpe > 0 after selection adjustment) >= 95%` with mandatory supporting statistics  
**Rationale**: Ambiguous "significant" is not auditable; explicit threshold + required report fields make promotion verifiable

---

## D013: Rolling Data Segment Reset

**Date**: 2026-07-17  
**Decision**: Rolling data gets segment boundaries and reset rules on strike change  
**Rationale**: Prevents accidental contract-return calculations across strike switches; protects downstream features from incorrect premium/OI/VWAP comparisons

---

## D014: Fixed Historical Data Acceptance Gate

**Date**: 2026-07-17  
**Decision**: External vendor data requires 14-point source audit before entering Gold layer  
**Rationale**: Prevents garbage-in-garbage-out; vendor data often has contract-identity, timestamp, or corporate-action issues that silently corrupt training

---

## D015: IV/Greeks Source-of-Truth Policy

**Date**: 2026-07-17  
**Decision**: Three separate IV/Greeks values tracked (provider, locally-calculated, model-input) with strict fallback order; NaN + quality flag required for illiquid contracts  
**Rationale**: Illiquid IV/Greeks filled with 0 have caused real trading losses; separate tracking enables ablation on source effects

---

## D016: Prediction Lineage

**Date**: 2026-07-17  
**Decision**: Every live prediction record requires 12-field lineage block  
**Rationale**: Without lineage, bad predictions cannot be traced to their source; this is the minimum metadata for root-cause analysis of prediction failures
