# XGBoost for Financial ML — Research Findings

*Last updated: 2026-07-16*

## 1. XGBoost Best Practices for Time Series

### 1.1 Walk-Forward is NOT Enough
Standard walk-forward validation evaluates on a single historical path. This:
- Produces high variance in performance metrics
- Rewards parameters that overfit to that specific path
- Provides no distribution of outcomes

**Solution**: CPCV (Combinatorial Purged Cross-Validation) — generates C(N,K) backtest paths.

### 1.2 Purging and Embargo are Mandatory
For overlapping label horizons (30m prediction → 30m future labels):
- Purging: Remove training samples whose label window overlaps test period
- Embargo: Remove buffer samples after test period (handles autocorrelation)
- Without these: Leakage inflates performance 5-15%

### 1.3 Hyperparameter Guidelines for Finance
| Parameter | Typical Range | Note |
|-----------|--------------|------|
| max_depth | 2-6 | Keep shallow for finance (noise) |
| min_child_weight | 3-100 (log) | Higher = more conservative |
| learning_rate | 0.005-0.08 | Lower for noisy data |
| subsample | 0.6-1.0 | Lower = more randomness |
| colsample_bytree | 0.5-1.0 | Feature sampling |
| reg_alpha | 1e-5-20 (log) | L1 regularization |
| reg_lambda | 0.1-100 (log) | L2 regularization |
| n_estimators | 300-3000 | Use with early_stopping |

### 1.4 Early Stopping
- Use validation set (inner fold)
- Save `best_iteration`
- Retrain on full train + validation using best_iteration
- NEVER use final test set for early stopping decisions

### 1.5 Probability Calibration
For options trading, calibrated probabilities are critical:
- Platt scaling (parametric) — preferred for smaller samples
- Isotonic regression — only with sufficient calibration data
- Calibration fold MUST be separate from final test

## 2. Hybrid Models — Research Evidence

### Meta-analysis (Dolon, 2025 — 110 papers)
- 65.5% of papers use hybrid models
- 84.7% of hybrids show significant gains over single models
- Median RMSE improvement: 8.7%
- Median directional accuracy improvement: 5.2 percentage points
- Hybrid gains LARGER during volatile periods (+2.1 to +3.4 pp)

### Recommendation
Keep XGBoost as PRODUCTION default but allow challengers:
- LightGBM for categorical features (sector, symbol)
- CatBoost for high-cardinality with small data
- LSTM for sequence-dependent features (order flow)

Champion model promoted only under strict CPCV evaluation.

## 3. Feature Importance in Options ML

### Known Strong Predictors (from literature)
| Feature | Papers Citing | Notes |
|---------|--------------|-------|
| PCR (Volume) | Multiple | Strongest in some Nifty studies |
| IV Skew | Multiple | Less tested on stock options |
| OI Change | Multiple | Directional agreement |
| India VIX | Nifty VRP study | +0.148 R² on variance forecast |
| Moneyness | All | Dominates spread models |
| IV Level | On-chain | Mixed results for direction |

### Contradictory Findings
One NIFTY study found IV not in top 15 features. Another found it dominant.
→ Ablation tests essential. IV/Greeks as CANDIDATE features, accepted only through testing.

## 4. Optuna Configuration

### TPE Sampler Settings
```python
optuna.samplers.TPESampler(
    seed=42,
    multivariate=True,
    group=True,
    n_startup_trials=50
)
```

### Staged Tuning Order
1. Structural (max_depth, min_child_weight, gamma)
2. Sampling (subsample, colsample_bytree)
3. Regularization (reg_alpha, reg_lambda, max_delta_step)
4. Learning (learning_rate, n_estimators)
5. Advanced (max_bin, grow_policy) — optional

### Multi-Objective Function
```
objective = 
  +0.20 × Balanced Accuracy
  +0.15 × MCC
  +0.15 × PR-AUC
  +0.15 × Brier Score (inverted)
  +0.20 × Cost-adjusted expectancy
  +0.15 × Fold stability
  - penalty × overfit_score
  - penalty × drawdown
  - penalty × symbol_concentration
```

## 5. SHAP for Interpretability
- Global SHAP: Understand feature importance per model
- Local SHAP: Debug individual predictions
- Monitor SHAP distribution drift over time (concept drift)
