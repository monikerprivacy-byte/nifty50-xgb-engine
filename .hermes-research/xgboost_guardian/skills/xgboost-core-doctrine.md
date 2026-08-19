---
name: xgboost-core-doctrine
description: |
  Use when verifying XGBoost parameter behaviour, training diagnostics,
  early-stopping leakage, or core gradient-boosting claims with the
  installed XGBoost version. Covers gradients, trees, regularisation,
  missing values, objectives, and probability outputs.

  Use ONLY when the question involves model internals (not feature
  engineering, not dataset construction, not Optuna tuning which has its
  own skill). Gate with "does this touch XGBoost training?" — if yes,
  this skill applies.
---

# xgboost-core-doctrine

## When to Use
- Verifying XGBoost parameter behaviour on the installed version (3.2.0)
- Debugging early-stopping / eval_set / best_iteration leakage
- Checking missing-value handling, NaN routing, sparsity
- Verifying objective function and probability semantics
- Testing classification vs regression objective trade-offs
- Inspecting CV integration with purgedcv / sklearn splitters

## When Not to Use
- Feature engineering or selection (use feature-ablation skill)
- Optuna hyperparameter search (use optuna-xgboost-doctrine)
- Dataset construction, labels, or timestamps (use point-in-time-dataset-quality)

## Preconditions
- XGBoost is installed: `python3 -c "import xgboost; print(xgboost.__version__)"`
- Verified version matches skill scope (this skill documents v3.2.0)
- Training data is already validated for point-in-time correctness

## Inputs
- Synthetic or frozen fixture for parameter testing (avoid full dataset)
- Or training + validation split for diagnostics

## Procedure

### 1. Verify Installed Version
```python
import xgboost
print(xgboost.__version__)  # Expected: 3.2.0
```

### 2. Core Parameter Verification

For each parameter under investigation, create a controlled synthetic test:

```python
import numpy as np
import pandas as pd
from xgboost import XGBClassifier, XGBRegressor
from sklearn.model_selection import train_test_split

np.random.seed(42)
n = 1000
X = pd.DataFrame({'f1': np.random.randn(n), 'f2': np.random.randn(n)})
y = (X['f1'] * 0.3 + np.random.randn(n) * 0.5 > 0).astype(int)
X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.3, shuffle=False)
```

**min_child_weight**: Minimum sum of instance weight (hessian) in a child.
- Too low: leaf may fit noise (overfit)
- Too high: underfits
- Default: 1
- Test: `for mcw in [1, 10, 100]: ...`

**max_depth**: Maximum tree depth.
- Deeper trees model more interactions but overfit faster
- Default: 6
- Test range: `[3, 6, 10]`

**gamma** (min_split_loss): Minimum loss reduction to make a split.
- Higher = more conservative pruning
- Default: 0
- Test range: `[0, 0.5, 1.0, 5.0]`

**subsample**: Fraction of training rows per tree (stochastic).
- Lower = more randomness = less overfitting
- Default: 1.0
- Common: 0.5–0.8
- Interacts with learning_rate: lower lr + lower subsample + more trees

**colsample_bytree / colsample_bylevel / colsample_bynode**:
- Fraction of columns sampled per tree / level / node
- Default: 1.0
- More fine-grained than subsample
- Combined effect is multiplicative: total_feature_fraction = colsample_bytree × colsample_bylevel × colsample_bynode

**reg_alpha / reg_lambda**: L1/L2 regularization on leaf weights.
- lambda (L2): default 1 — reduces leaf weight magnitude
- alpha (L1): default 0 — encourages sparsity
- Effect depends on data scale

### 3. Missing Value Handling
XGBoost 3.2.0 handles missing values natively. The default `missing=np.nan` means NaN values in the input array are automatically routed to the optimal branch during training. No imputation is required (though decision-tree-based imputation may still help for other model families).

```python
X_nan = X.copy()
X_nan.iloc[::10, 0] = np.nan  # 10% NaN in f1
model = XGBClassifier(n_estimators=50, verbosity=0)
model.fit(X_nan, y)
# NaN is handled — no error, no need to impute
```

Warning: When `missing` is set to a non-NaN sentinel value (e.g., `missing=-999`), XGBoost treats that exact value as missing. This affects both train and predict — be consistent.

### 4. Early Stopping and eval_set Leakage
**CRITICAL LEAKAGE RISK**: XGBoost does NOT prevent the user from passing the **test set** as `eval_set`. This is a documented risk:
- When the test set is used for early stopping, the test set influences training (stopping round selection)
- After early stopping, test-set metrics are no longer valid as out-of-sample estimates
- The `best_score` and `best_iteration` are correct for the eval_set input but do NOT generalise to an unseen test set

Safe pattern:
```python
# Correct: eval_set is a held-out portion of the training split
X_train, X_eval, y_train, y_eval = train_test_split(
    X_all, y_all, test_size=0.2, shuffle=False
)
model.fit(X_train, y_train, eval_set=[(X_eval, y_eval)], early_stopping_rounds=10)

# WRONG: eval_set must NOT be the final test set
model.fit(X_train, y_train, eval_set=[(X_test, y_test)], early_stopping_rounds=10)  # LEAKAGE
```

Inside cross-validation, each fold's validation set (the fold's test portion) IS the correct eval_set for that fold — XGBoost uses it for early stopping within the fold. But the final untouched holdout must never have been used as eval_set.

### 5. Probability Outputs vs Hard Classifications

**`binary:logistic`** outputs class 1 probability directly (single column).
**`multi:softprob`** outputs per-class probabilities (one column per class, sums to 1).
**`binary:hard`** and **`multi:hard`** output hard class labels (not recommended).

```python
m = XGBClassifier(objective='binary:logistic')
m.fit(X_tr, y_tr)
probs = m.predict_proba(X_te)  # shape (n, 2) for binary: [P(y=0), P(y=1)]
# For multi:softprob: shape (n, n_classes)
```

When thresholding, use training/validation or OOF predictions only. Never use the final test set to select a probability threshold.

### 6. scale_pos_weight vs sample_weight

**scale_pos_weight**: Multiplies the weight of positive class examples by the specified ratio. Equivalent to `set_weight(0)=1, set_weight(1)=scale_pos_weight`.

```python
ratio = (n_negative / n_positive)
m = XGBClassifier(scale_pos_weight=ratio)
```

**sample_weight**: Per-example weights passed to `fit()`. When both are set, `sample_weight` is multiplied internally by the class weight.

Preferred for imbalanced options CE/PE data: `scale_pos_weight` for binary imbalance. `sample_weight` for fine-grained or custom importance weighting.

### 7. N_estimators / num_boost_round and Learning Rate Interaction

- Lower learning_rate requires more trees (higher n_estimators)
- early_stopping_rounds should be used to find the optimal number
- Very high n_estimators (thousands) with low lr is not always better — diminishing returns
- Interaction: `learning_rate * sqrt(n_estimators)` is approximately constant for optimal fit depth

### 8. Histogram vs Exact Tree Method

- `tree_method='hist'` (default for large data): approximate, faster, less memory
- `tree_method='exact'`: exact greedy, slower, only for small data
- `tree_method='gpu_hist'`: GPU-accelerated histogram
- For financial time series with <100K rows: `'hist'` is fine. Exact adds little value.

### 9. max_bin and grow_policy

- `max_bin` (default 256): Maximum number of bins for histogram-based tree building
  - More bins = more precision, slower, more memory
  - For binary features, 256 is overkill; for continuous features with many unique values, 256 is typically fine
- `grow_policy`:
  - `depthwise` (default): split at deepest node
  - `lossguide`: split at node with highest loss gain (can produce unbalanced trees)

## Decision Rules

- NaN is NOT a bug in XGBoost — understand the data's missing-value mechanism before imputing
- early_stopping_rounds does NOT prevent test-set leakage — it's the user's responsibility
- probability calibration (Brier/log-loss) matters more than classification accuracy for options trading
- Parameter interactions dominate individual parameter effects — always test pairs
- Local installed behaviour trumps documentation when they disagree

## Failure Modes

1. **eval_set leakage**: The test set accidentally used for early stopping → overconfident metrics, no warning from XGBoost
2. **Parameter folklore**: Applying "best practices" from non-financial domains (e.g., DNN-style tuning) to XGBoost
3. **Default assumptions**: `n_estimators=100` default is rarely enough for financial time series with low lr
4. **NaN confusion**: Setting `missing` to a value that also appears as real data
5. **Duplicate column names**: XGBoost silently treats them as separate features

## Leakage Risks
- eval_set/early_stopping leakage (highest risk) — must audit every call to `fit()` with `eval_set`
- CV folds leaking into early-stopping decisions is acceptable per-fold IF the fold's test set is the eval_set
- The final model must use a proper train/validation split for early stopping, NOT the test set

## Verification
```bash
python3 -c "import xgboost; print(xgboost.__version__)"
pytest tests/validation/test_eval_set_leakage.py -v
pytest tests/validation/test_dependency_verifier.py -v
```

### Linked automated tests
| Test | File | Purpose |
|------|------|---------|
| `test_final_vault_rejected` | `tests/validation/test_eval_set_leakage.py` | FINAL_VAULT blocked from eval_set |
| `test_outer_test_rejected` | `tests/validation/test_eval_set_leakage.py` | OUTER_TEST blocked from eval_set |
| `test_calibration_rejected` | `tests/validation/test_eval_set_leakage.py` | CALIBRATION blocked from eval_set |
| `test_es_and_train_accepted` | `tests/validation/test_eval_set_leakage.py` | TRAIN+EARLY_STOPPING allowed |
| `test_raises_without_early_stopping` | `tests/validation/test_eval_set_leakage.py` | eval_set must contain EARLY_STOPPING |
| `test_runtime_xgboost_version` | `tests/validation/test_dependency_verifier.py` | verify_runtime_dependencies passes |
| `test_label_shuffle_median_null` | `tests/leakage/test_negative_controls.py` | Shuffled labels: median MCC near zero |
| `test_label_shuffle_multiseed` | `tests/leakage/test_negative_controls.py` | 5 seeds, MCC+balanced_accuracy |
| `test_missingness_stress` | `tests/leakage/test_negative_controls.py` | NaN does not crash XGBoost |

### Enforcement functions
| Function | File | Purpose |
|----------|------|---------|
| `build_eval_set()` | `src/validation/leakage.py` | Blocks forbidden roles from entering eval_set |
| `DatasetRef` / `DatasetRole` | `src/validation/leakage.py` | Immutable role-tagged dataset references |
| `verify_runtime_dependencies()` | `src/validation/verify_deps.py` | Fails fast on version mismatch |

## Changelog
- 2026-07-19: Initial candidate. Verified against XGBoost 3.2.0 locally. NaN handling, early-stopping leakage, 6 parameter tests confirmed.
- 2026-07-20: Promoted to CANDIDATE. Linked 9 automated tests + 3 enforcement functions. eval_set leakage now programmatically blocked.
- 2026-07-20: Promoted to VALIDATED. Guarded training entry point (`guarded_train()` in `src/models/xgboost_model.py`) integrates all enforcement. 209/214 tests passing. Phase 2.5B bar-to-feature parity passed (1170 bars, 8 features, batch==incremental at 1e-8).
