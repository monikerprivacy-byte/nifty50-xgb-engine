# RESEARCH QUEUE
- generated_at: 2026-07-19T21:45 Asia/Kolkata
- status: INITIALIZED

## Priority 0 (Current Session Top)
1. [ ] Verify CPP/CPCV isolation: Does the actual local XGBoost version (3.2.0) support eval_set-based early stopping with multi-metric objectives, and what are the exact semantics of purge/embargo in local scikit-learn TimeSeriesSplit vs purgedcv?
2. [ ] Rolling moneyness vs fixed-contract identity: Audit all code paths that could mix these two dataset types. Verify partition enforcement.
3. [ ] Timestamp semantics: Does the BarEngine's event_time correctly map to the prediction decision time? What is the actual 30-minute horizon implementation in code vs config?

## Priority 1
4. [ ] XGBoost 3.2.0 hyperparameter parameter interactions: Confirm installed-version behaviour for max_depth vs min_child_weight, learning_rate vs n_estimators, subsample vs colsample_by*
5. [ ] Negative control suite: Create reusable test — shuffled labels, random features, timestamp reversal, duplicate rows
6. [ ] Underfit/overfit diagnostics: Create diagnostic procedure accessing learning curves, seed stability, nearby-parameter stability
7. [ ] Probability calibration and OOF-only threshold selection doctrine

## Priority 2
8. [ ] Optuna 4.8.0 study persistence, TPE sampler behaviour, conditional spaces
9. [ ] Feature ablation and stability testing procedure
10. [ ] IV/Greeks/VIX candidate feature testing procedure per Constitution §6.2

## Low Priority
11. [ ] Regime and drift detection
12. [ ] Cost-aware evaluation with bid/ask, slippage, fees
13. [ ] Reproducibility and model lineage
14. [ ] Shadow model promotion gates
