---
name: financial-time-series-leakage-guardian
description: |
  Use when auditing financial time-series datasets and validation
  pipelines for data leakage. Covers 10 leakage types (timestamp,
  look-ahead, target, preprocessing, feature-selection, overlapping-
  label, early-stopping, Optuna, cross-sectional, rolling-contract),
  plus project-specific Dhan rolling-moneyness risks.

  Use ONLY when constructing features, labels, or validation splits.
  Not for general XGBoost parameter tuning.
---

# Financial Time-Series Leakage Guardian

## When to Use
- Auditing datasets, features, or labels for information leaks
- Setting up train/validation/test splits for time-series XGBoost
- Verifying purgedcv splitter correctness
- Checking rolling-moneyness vs fixed-contract dataset separation
- Before any Optuna study or training run

## When Not to Use
- General XGBoost parameter tuning (use xgboost-core-doctrine)
- Optuna study design (use optuna-xgboost-doctrine)
- Positive-control benchmark building (use adversarial-ml-test-suite)

## Preconditions
- purgedcv >= 0.1.2 installed
- Dataset has verified timestamps (event_time, available_time, input_cutoff_time)
- Decision/prediction timestamp known for each row
- Label horizon known (confirmed from code: 30 minutes)

## Inputs
- Dataset DataFrame with row-level timestamps
- Feature and label definitions
- Train/validation/test split indices or splitter specification

## Procedure

### 0. Install and Import Tools
```python
from purgedcv import (
    PurgedKFold, WalkForwardSplit, CombinatorialPurgedCV,
    purge, apply_embargo, validate_times
)
from purgedcv.diagnostics import (
    assert_no_temporal_leakage,
    assert_embargo_respected,
    assert_groups_disjoint,
)
```

### 1. Timestamp Leakage (Type A)
**Principle**: Feature timestamp must be ≤ decision timestamp.

```python
# Check: every feature is available at decision time
for row in dataset:
    assert row["feature_available_time"] <= row["prediction_time"]
```

**Project rule**: `available_time < input_cutoff_time ≤ prediction_time` (Constitution §6.1)

**Test**: Introduce a deliberately shifted future feature → the leak detector must catch it.

### 2. Completed-Bar Leakage (Type B)
**Principle**: A bar may only be consumed after its configured close time plus realistic availability delay.

```python
# 1-minute bar closed at HH:MM:00 is NOT available at HH:MM:00
# Add margin: bar_available_time = bar_close_time + margin
bar_available = bar_close + timedelta(seconds=AVAILABILITY_DELAY)
assert bar_available <= decision_time
```

**Project rule**: BarEngine buckets by minute, rejects naive datetimes. Default margin: 1 second (at minimum). Production: 1–5 seconds for data pipeline latency.

### 3. Label Overlap Leakage (Type C)
**Principle**: Training observations whose label (outcome) windows overlap a validation/test period must be purged.

```python
# Define label interval for each row
prediction_times = df["prediction_time"]
evaluation_times = df["prediction_time"] + label_horizon  # 30 min

# purgedcv handles this:
cv = PurgedKFold(
    n_splits=5,
    prediction_times=prediction_times,
    evaluation_times=evaluation_times,
    purge_horizon="30min",
)
for train_idx, test_idx in cv.split(X, y):
    assert_no_temporal_leakage(train_idx, test_idx, prediction_times, evaluation_times)
```

### 4. Embargo (Type D)
**Principle**: After each test block, remove a buffer of training rows with temporally adjacent features.

```python
cv = PurgedKFold(
    n_splits=5,
    prediction_times=prediction_times,
    evaluation_times=evaluation_times,
    purge_horizon="30min",
    embargo="30min",  # post-test buffer
)
```

**Project rule**: Constitution specifies purge_gap=30m, embargo=5 bars (5 minutes if 1-min bars).

### 5. Preprocessing Leakage (Type E)
**Principle**: Imputation, scaling, encoding, feature selection, and calibration must be fit using training-fold data only.

```python
# WRONG: full-dataset imputation
from sklearn.preprocessing import StandardScaler
scaler = StandardScaler().fit(X)  # LEAKAGE
X_scaled = scaler.transform(X)

# CORRECT: per-fold imputation in pipeline
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
pipeline = Pipeline([
    ("imputer", SimpleImputer()),
    ("scaler", StandardScaler()),
    ("classifier", XGBClassifier()),
])
```

**Test**: Fit imputer on full dataset → train model → compare CV scores with per-fold imputation. If different, leakage is confirmed.

### 6. Early-Stopping Leakage (Type F)
**Principle**: The final untouched test set must not be used as the early-stopping eval_set.

```python
# WRONG — test set leaks into early stopping:
model.fit(X_train, y_train, eval_set=[(X_test, y_test)], early_stopping_rounds=10)  # LEAK

# CORRECT — inner validation split for early stopping:
model.fit(X_train, y_train, eval_set=[(X_val, y_val)], early_stopping_rounds=10)
```

**Test**: See `xgboost-core-doctrine` skill §4. Compare leaked vs non-leaked best_iteration, best_score, and true test accuracy.

### 7. Optuna Leakage (Type G)
**Principle**: The final test set must not determine trials, search ranges, thresholds, features, or early stopping.

See `optuna-xgboost-doctrine` skill for full procedure. Key rules:
- Pre-register objective and search space before study starts
- Study must not access test-set metrics
- Threshold selection must use OOF or training/validation predictions only

### 8. Cross-Sectional Contamination (Type H)
**Principle**: Rows from the same market event, contract family, or highly related security must not leak between train and test through an inappropriate split.

```python
# Group-aware split for multi-contract data
from purgedcv import PurgedGroupKFold

cv = PurgedGroupKFold(
    n_splits=5,
    groups=df["contract_identity"],  # e.g., NSE|RELIANCE|2026-08-06|1500|CE
    prediction_times=prediction_times,
    evaluation_times=evaluation_times,
    purge_horizon="30min",
)
```

**Project rule**: Contracts from the same expiry, same underlying, CE/PE pairs may be cross-contaminated. Use contract_identity as group key.

### 9. Rolling-Contract Identity (Type I)
**Principle**: Ensure rolling ATM-relative history is not represented as continuous fixed-strike history.

```python
# Two strictly separated dataset types (Constitution §6.3):
FIXED_CONTRACT: canonical identity = "exchange | underlying | expiry_date | absolute_strike | option_type"
RELATIVE_ROLLING_SURFACE: tracks segment_id, resets on ATM strike change

# Verify: every row has unambiguous type
assert df["dataset_type"].isin(["FIXED_CONTRACT", "RELATIVE_ROLLING_SURFACE"]).all()
```

**Project rule**: Dhan rolling option data is NOT fixed-contract history (AGENTS.md, brain/issues/rolling_strike_confirmed.md). Never mix in same training set. Never use rolling data for contract-level training.

### 10. Negative Controls (Type J)
**Principle**: Shuffled labels should fall near chance; random features must not create stable OOS advantage.

```python
# Label shuffle test
y_shuffled = y.sample(frac=1, random_state=42).values
scores = cross_val_score(model, X, y_shuffled, cv=cv)
assert abs(scores.mean()) < 0.05  # near zero

# Random feature test
X_rand = pd.DataFrame({"noise": np.random.randn(n)})
scores = cross_val_score(model, X_rand, y, cv=cv)
assert abs(scores.mean() - y.mean()) < 0.05  # near baseline

# Timestamp reversal test
df_reversed = df.sort_values("prediction_time", ascending=False)
# train on future, test on past → should fail
```

## Decision Rules
- Any single leakage type failing blocks model promotion
- Both temporal AND cross-sectional contamination must be checked
- Rolling-moneyness contamination is project-specific and blocks promotion
- Preprocessing leakage is the most common "silent" leak — always use Pipeline with per-fold fit

## Failure Modes
1. **Ignoring early-stopping leakage**: XGBoost does not prevent it
2. **Full-dataset imputation**: The scaler/imputer sees test data statistics
3. **Missing group awareness**: Multi-contract data split without contract_identity guarantee
4. **Assumed independence**: Time-series with auto-correlation violates i.i.d. — purging alone doesn't fix all leakage
5. **Optuna optimizing on test set**: Using test metrics in objective function

## Leakage Risks
This skill documents leakage risks. Use it to audit — the audit itself does not introduce leakage.

## Verification
```bash
pytest tests/leakage/ -v --tb=short
python3 -c "from purgedcv.diagnostics import assert_no_temporal_leakage; print('OK')"
```

### Linked automated tests
| Test | Purpose | Role |
|------|---------|------|
| `test_label_shuffle_multiseed[7/19/42/73/101]` | Multi-seed label shuffle — MCC, balanced_accuracy | §10 (Type J) |
| `test_label_shuffle_median_null` | Median MCC across 5 seeds near zero | §10 (Type J) |
| `test_random_features_null` | Noise features produce near-0.5 balanced_accuracy | §10 (Type J) |
| `test_timestamp_reversal_detected` | purgedcv rejects non-monotonic timestamps | §3 (Type C) |
| `test_duplicate_rows_stress` | Duplicate rows do not collapse CV variance | §9 (Type J) |
| `test_missingness_stress` | NaN does not crash or produce NaN scores | §5 (Type E) |
| `test_null_model_baseline` | Model must beat null baseline | §10 (Type J) |
| `test_purgedcv_leak_assertion` | assert_no_temporal_leakage detects overlap | §3 (Type C) |
| `test_final_vault_rejected` (from eval_set_leakage) | FINAL_VAULT blocked from eval_set | §6 (Type F) |
| `test_outer_test_rejected` (from eval_set_leakage) | OUTER_TEST blocked from eval_set | §6 (Type F) |
| `test_calibration_rejected` (from eval_set_leakage) | CALIBRATION blocked from eval_set | §6 (Type F) |
| `test_es_and_train_accepted` (from eval_set_leakage) | TRAIN+EARLY_STOPPING allowed in eval_set | §6 (Type F) |

### Enforcement functions
| Function | File | Purpose |
|----------|------|---------|
| `build_eval_set()` | `src/validation/leakage.py` | Guards against early-stopping leakage (§6) |
| `verify_runtime_dependencies()` | `src/validation/verify_deps.py` | Boot-time purgedcv/XGBoost version check |
| `to_event_end_series()` | `src/validation/purgedcv_adapter.py` | Absorbs pd.Series vs DatetimeIndex quirk |

## Changelog
- 2026-07-19: Initial candidate. Verified purgedcv diagnostics tools locally. 10 leakage types enumerated with project-specific rules.
- 2026-07-20: Promoted to CANDIDATE. Linked 12 automated tests + 3 enforcement functions across 6 leakage types.
- 2026-07-20: Promoted to VALIDATED. All enforcement functions executed in guarded training entry point. 7 fail-closed gates (deps, roles, timestamps, overlap, neg-ctrl, final-vault, missing roles). Dry-run trained on 1170 replay bars — manifest written.
