# EVIDENCE LEDGER
- generated_at: 2026-07-19T21:45 Asia/Kolkata
- format: | claim_id | claim | source | confidence | date_validated

| EVIDENCE_ID | CLAIM | SOURCE | CONFIDENCE | VALIDATED_AT | NOTES |
|---|---|---|---|---|---|
| E001 | purgedcv v0.1.2 works with XGBoost 3.2.0 + sklearn 1.7.2 | Local test (500-row synthetic, PurgedKFold + CPCV) | CONFIRMED | 2026-07-19 | All leak checks pass. Timestamps must be pd.Series, not DatetimeIndex. |
| E002 | purgedcv was not installed despite being in requirements.txt | pip list | CONFIRMED | 2026-07-19 | pip install purgedcv fixed this. |
| E003 | PurgedKFold with XGBoost: CV scores working end-to-end | Local test | CONFIRMED | 2026-07-19 | Synthetic accuracy not meaningful; pipeline OK |
| E004 | XGBoost 3.2.0 does NOT prevent test-set usage as eval_set | Local test | CONFIRMED | 2026-07-19 | Critical leakage risk — user must enforce isolation |
| E005 | XGBoost 3.2.0 min_child_weight=100 causes underfit (0.517 acc) | Local test (synthetic, 1000 rows) | CONFIRMED | 2026-07-19 | Parameter behaves as documented |
| E006 | XGBoost NaN handling confirmed: 10% NaN in one feature, no error | Local test | CONFIRMED | 2026-07-19 | Default missing=np.nan works. imputation not required |
| E007 | XGBoost scale_pos_weight ratio changes positive prediction rate | Local test | CONFIRMED | 2026-07-19 | Default=1.0 → predicts all 0 for 9% rare class; ratio=10.1 → 6.67% positive |
| E008 | XGBoost gamma (min_split_loss): higher values increase regularization | Local test | CONFIRMED | 2026-07-19 | gamma=5.0 best on synthetic noisy data (regularization beneficial) |

