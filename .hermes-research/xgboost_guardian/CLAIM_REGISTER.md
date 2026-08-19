# CLAIM REGISTER
- generated_at: 2026-07-19T21:45 Asia/Kolkata

| CLAIM_ID | CLAIM | SOURCE | CONFIDENCE | VALIDATED | NOTES |
|---|---|---|---|---|---|
| C001 | purgedcv implements full Lopez de Prado pipeline | GitHub docs + local test | HIGH-CONFIDENCE | 2026-07-19 | Unvalidated: DSR/PSR/PBO numerical correctness against reference implementation |
| C002 | XGBoost 3.2.0 eval_set-based early stopping with PurgedKFold works correctly | Not yet tested | UNVERIFIED | — | Must verify test-set isolation when eval_set is used inside cross_val_score |
| C003 | The project's primary validation library was not installed | pip list | CONFIRMED | 2026-07-19 | requirements.txt lists purgedcv>=0.1.2 but actual install was missing |
