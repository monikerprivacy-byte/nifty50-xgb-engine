# NIFTY-50 XGBoost Stock Options Intelligence Platform — Project Constitution

## 1. Core Doctrine

### 1.1 Prediction Ownership
Market prediction → केवल XGBoost  
Direction probability → केवल XGBoost  
CE/PE selection → केवल XGBoost outputs  
Target probability → केवल XGBoost  
Stop probability → केवल XGBoost  
Expected gain/MFE → केवल XGBoost  
Expected loss/MAE → केवल XGBoost  
Spike probability → केवल XGBoost  
Contract ranking → XGBoost/XGBRanker  

Hermes, Telegram, dashboard rules, hand-written net-power formula market direction predict नहीं करेंगे।

### 1.2 Data Source Rules
| Source | Purpose | Restriction |
|--------|---------|------------|
| Dhan WebSocket Live | Fixed-strike live capture | Only for live/silver layer |
| Dhan Rolling Expired (`/charts/rollingoption`) | Relative surface features | NOT for contract-level training |
| Dhan Option Chain | IV/Greeks snapshots | Periodic, not real-time |
| Dhan Historical Intraday | Cash/futures OHLCV | Options return empty |
| NSE Bhavcopy / Vendor | Fixed-strike historical | Mandatory for training |
| Local capture (from now) | Fixed-strike accumulation | Future training after sufficient history |

### 1.3 Correction: Dhan Rolling Data is NOT Fixed-Contract History
**CONFIRMED via empirical test (2026-07-16):**
- RELIANCE ATM-3 CALL: 5 different absolute strikes in 5 days, 49 intraday strike switches
- This is relative-moneyness slot data, NOT a fixed option contract
- Cannot use for: continuous returns, OI differences, VWAP, target-before-stop labels, MFE/MAE

Rolling data is valid for:
- ATM-relative IV levels and changes
- ATM-relative OI/volume surface
- IV skew and curvature
- Surface activity migration
- Spot-to-surface relationships

### 1.4 Fixed-Contract Identity
Every training/prediction row must have:
```
exchange | underlying | expiry_date | absolute_strike | option_type
```
Example: `NSE | RELIANCE | 2026-07-30 | 1500 | CE`

Two dataset types, NEVER mixed:
- `FIXED_CONTRACT` — for model training
- `RELATIVE_ROLLING_SURFACE` — for feature research only

### 1.5 Rolling Data Reset Rule
Whenever `absolute_strike` changes in rolling data:
- Increment `rolling_segment_id`
- Reset premium returns
- Reset OI differences
- Reset VWAP
- Reset rolling premium indicators
- Mark first row as `segment_boundary = True`

Allowed surface comparison (ATM-3 at T vs ATM-3 at T-1) ONLY when the feature explicitly represents a relative-surface state — NOT a contract return.

---

## 2. Universe Definition

### 2.1 Primary Universe
- NIFTY-50 constituent stocks (point-in-time historical membership)
- Per F&O-eligible stock: ATM-5 to ATM+5 strikes, CE + PE
- Nearest valid expiry (primary); next expiry added as controlled experiment

### 2.2 Universe Size
- 11 strikes × 2 types × 50 stocks = 1,100 option contracts (single expiry)
- +50 cash, +50 futures, +sector indices, +NIFTY index/futures
- Two expiries: ~2,200 contracts

### 2.3 ATM Calculation
- ATM strike = nearest valid LISTED strike to current underlying price
- NOT mathematical rounding — verify against instrument master
- ATM hysteresis: old ATM retained until spot crosses confirmation threshold

---

## 3. Prediction Horizon

| Horizon | Purpose |
|---------|---------|
| 30 min | Primary prediction |
| 5 min | Diagnostic |
| 15 min | Diagnostic |
| 60 min | Diagnostic |

---

## 4. Data Storage Architecture

### 4.1 DuckDB Configuration
- Version: 1.4.4 LTS (ARM compatible — verified `osx_arm64` wheel)
- One dedicated writer process
- Multiple read-only readers
- Dashboard NEVER writes market data
- Training process NEVER shares writer connection

### 4.2 Bronze Layer — Immutable Raw
- Exact decoded WebSocket packets
- No cleaning, no forward filling, no overwriting
- Date-partitioned Parquet

### 4.3 Silver Layer — Normalized
- Correct security mapping (symbol + expiry + strike + type)
- Sorted timestamps
- Duplicates marked, late events marked
- Invalid events quarantined

### 4.4 Gold Layer — Model-Ready
- 1m/5m/15m bars
- Point-in-time feature tables
- Labels with timestamps
- Prediction-ready matrices

### 4.5 Fixed Historical Data Acceptance Gate
External vendor data does NOT enter Gold layer without a passing source audit:

| Audit Item | Required |
|-----------|----------|
| Contract identity accuracy | 100% |
| Timestamp timezone + precision | Documented |
| Expiry mapping | Verified |
| Strike adjustment (splits/bonus) | Verified |
| OI semantics (opening vs EOD) | Documented |
| Volume semantics (derived vs reported) | Documented |
| Bid/ask availability | Known, not assumed |
| Missing-minute rate | Measured |
| Duplicate primary keys | 0% |
| Unexplained negative volume | 0% |
| Timezone ambiguity | 0% |
| Corporate-action handling | Verified |
| Late correction policy | Documented |
| Survivorship coverage | Known |

Missing bars are measured and reported, NEVER silently forward-filled. OI discontinuities are explicitly flagged.

---

## 5. Validation & Leakage Prevention

### 5.1 Three-Layer Validation
| Layer | Method | Purpose |
|-------|--------|---------|
| 1 — Hyperparameter tuning | Purged Group K-Fold (inner) | Optuna search |
| 2 — Model selection | Combinatorial Purged CV (CPCV) | Primary validation with multiple paths |
| 3 — Final deployment | Chronological holdout walk-forward | Production simulation |

### 5.2 Mandatory Protections
- Random split: PROHIBITED
- Purging: Remove training samples whose labels overlap test period
- Embargo: Remove buffer after test period (autocorrelation)
- Normalization: Fit ONLY on training fold
- Feature timestamps: `available_time <= decision_time`
- Point-in-time NIFTY-50 membership: historical, not current
- Point-in-time ATM: per timestamp, not expiry-end

### 5.3 Rejection Criteria
Model promoted to production registry ONLY when:

#### 5.3.1 PBO Methodology (Frozen)
PBO computed via CSCV on cost-adjusted Sharpe/expectancy.
Experiment registry MUST record all genuinely tested alternatives:
- Feature variants
- Label variants
- Parameter trials
- Threshold variants

Optuna's final selected trials alone are insufficient; all trialed configurations enter the denominator.

#### 5.3.2 PBO Gates
| Range | Verdict |
|-------|---------|
| < 0.25 | Pass — proceed |
| 0.25 – 0.40 | Warning — requires justification |
| > 0.40 | Reject — likely overfit |

#### 5.3.3 DSR Gate
DSR significance threshold:
```
P(Sharpe > 0 after selection adjustment) >= 95%
```
Every promotion report MUST include:
- Observed Sharpe
- Deflated Sharpe
- Number of trials (denominator)
- Return skewness
- Return kurtosis
- Sample length (bars)

#### 5.3.4 Additional Gates
- Leakage suite: 100% pass
- Multiple outer folds: pass
- No single-stock dependency
- No single-month dependency
- Calibration: acceptable
- Live/replay parity: pass
- Shadow performance: pass
- Positive median CPCV path
- Acceptable worst-decile CPCV path
- Stable chronological holdout

---

## 6. Feature Engineering Rules

- Features are deterministic calculations
- Features are causal (timestamped with availability time)
- No feature is a trading signal itself
- Support/resistance, effort-result, net-power are feature families, NOT predictions
- IV/Greeks are MANDATORY candidate features (tested via ablation)
- India VIX is a mandatory regime feature

### 6.1 Feature Timestamp Contract
Every feature:
- `event_time`: when source data occurred
- `available_time`: when feature value is computable
- `input_cutoff_time`: latest time for input data
- Invariant: `available_time <= decision_time`

### 6.2 IV/Greeks Source-of-Truth Policy
Three distinct values may exist for IV/Greeks — they MUST be separately tracked:

| Field | Definition |
|-------|-----------|
| `provider_iv` | Raw value from Dhan option chain |
| `locally_calculated_iv` | Re-computed using Black-Scholes or Binomial |
| `model_input_iv` | The value actually fed to models |

#### `model_input_iv` Selection Order
1. Locally validated IV — when solver quality passes convergence checks
2. Provider IV — when provider quote quality passes freshness + spread checks
3. Missing (NaN) — when neither is reliable

#### Greeks Metadata (per record)
```python
{
  "pricing_model": "black_scholes" | "binomial",
  "risk_free_rate": float,
  "dividend_or_forward_method": str,
  "underlying_reference": str,
  "calculation_timestamp": datetime,
  "solver_status": "converged" | "max_iter" | "boundary",
  "quote_spread": float,  # bps
}
```

#### Prohibited
- Filling illiquid-contract IV/Greeks with 0 (zero is a valid financial value — use NaN + quality flag)
- Silently falling back to provider IV without recording which source was used
- Mixing provider_iv and locally_calculated_iv in the same training fold without metadata

---

## 7. Model Architecture

### 7.1 Model Suite
| Model | Input | Output |
|-------|-------|--------|
| Direction Classifier | All features + cross-sectional | p_up, p_neutral, p_down |
| CE Outcome Classifier | Contract features | p_target_before_stop |
| PE Outcome Classifier | Contract features | p_target_before_stop |
| Spike Classifier | Contract features | p_spike |
| MFE Regressor | Contract features | Expected MFE |
| MAE Regressor | Contract features | Expected MAE |
| Trade Quality | All above OOF | p_trade_quality |
| Contract Ranker | All above + ranking features | Ranked contracts |

### 7.2 Production Rule
- XGBoost is the ONLY production model family
- Challengers are restricted to XGBoost variants:
  - Different XGBoost objectives (multi:softprob vs binary:logistic vs rank:ndcg)
  - Different XGBoost feature sets (ablation variants)
  - XGBClassifier versus XGBRanker architectures
  - Global versus sector-specific XGBoost models
  - Different XGBoost calibration strategies (Platt vs isotonic)
- No non-XGBoost model family (LightGBM, CatBoost, neural) enters production
- Variant selection uses CPCV; champion promoted only on stable improvement

### 7.3 Prediction Lineage
Every live prediction record MUST include:

| Field | Purpose |
|-------|---------|
| `prediction_id` | Unique identifier |
| `decision_time` | When prediction was made |
| `model_version` | Exact model provenance |
| `feature_schema_version` | Feature set hash |
| `dataset_version` | Gold snapshot version |
| `calibration_version` | Calibration model version |
| `universe_snapshot_id` | Point-in-time universe identity |
| `contract_identity` | exchange, underlying, expiry, strike, type |
| `input_cutoff_time` | Latest input data timestamp |
| `prediction_values` | All model outputs |
| `data_quality_state` | Data quality at decision time |
| `guardian_state` | Guardian monitor snapshot |

Without lineage, a bad prediction cannot be traced to its source feature, model, or instrument-master version.

---

## 8. Hermes + Telegram Integration

### 8.1 Hermes is SIDECAR Only
Hermes can:
- Read project status
- Send reports
- Run approved backtests
- Summarize logs
- Draft research
- Route Telegram commands

Hermes CANNOT:
- Make predictions
- Modify features
- Place live orders
- Deploy production models
- Approve Optuna winners

### 8.2 Critical Alerts
Feed disconnect, model stale, data leakage, daily loss limit, writer failure — these are DIRECT deterministic alerts, bypassing Hermes model entirely.

---

## 9. Execution Model

### 9.1 Realistic Fills
- Entry: ask + slippage (not LTP)
- Exit: bid - slippage (not LTP)
- Backtest: executable prices only

### 9.2 No Hard-Coded Timing Rules
Execution gates are data-driven:
- Current spread <= historical allowed percentile
- Quote is fresh
- Sufficient depth
- Expected XGBoost edge > expected execution cost

The 10:00-14:00 optimal window is a research finding, NOT a hard rule.

---

## 10. Implementation Order

```
Phase 0: Constitution + Environment Setup
Phase 1: Instrument + Membership History  
Phase 2: Dhan Raw WebSocket Capture
Phase 3: ATM±5 Live Universe (fixed-strike)
Phase 4: External Fixed-Strike Historical Data
Phase 5: Bar Engine (1m/5m/15m)
Phase 6: Core Features
Phase 7: Advanced Mechanics + IV/Greeks/VIX
Phase 8: Labels
Phase 9: Baselines
Phase 10: XGBoost Model Suite
Phase 11: Optuna + CPCV
Phase 12: Calibration + Ranking
Phase 13: Event-Driven Backtest
Phase 14: Dashboard
Phase 15: Hermes + Telegram
Phase 16: Historical Replay
Phase 17: Shadow Mode
```

---

## 11. Definition of Done

System is complete when:
- Prediction lineage recorded on every live prediction
- NIFTY-50 point-in-time universe is correct
- ATM±5 live fixed-strike capture is stable
- Historical fixed-strike data source is integrated
- All features are causal
- All predictions come from XGBoost
- CPCV is primary validation (not just walk-forward)
- Probabilities are calibrated
- Bid/ask backtest with costs
- Dashboard isolated from engine
- Hermes is sidecar only
- No LLM in trading path
- IV/Greeks/VIX tested via ablation (not excluded a priori)

---

*Last updated: 2026-07-17* (corrections: challenger rule, PBO methodology, DSR definition, rolling reset, data acceptance gate, IV/Greeks policy, prediction lineage)
*Based on empirical Dhan API testing and 2024-2026 financial ML research*
