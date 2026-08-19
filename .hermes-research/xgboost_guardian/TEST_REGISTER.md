# TEST REGISTER
- generated_at: 2026-07-19T21:45 Asia/Kolkata
- purpose: Track all tests created or inspected during research

| TEST_ID | DESCRIPTION | SCOPE | STATUS | CREATED | PASS/FAIL | NOTES |
|---|---|---|---|---|---|---|
| T001 | Synthetic purgedcv PurgedKFold + CPCV + leak assertion | 500 rows, 1-min timestamps, random features/labels | COMPLETE | 2026-07-19 | PASS | Confirms purgedcv 0.1.2 works with XGBoost 3.2.0 |
| T002 | XGBoost early-stopping eval_set leakage demonstration | 500 rows, eval_set=[X_test] vs proper val split | COMPLETE | 2026-07-19 | PASS | Confirms LEAKAGE IS NOT PREVENTED by XGBoost |
| T003 | XGBoost 6-parameter synthetic tests (min_child_weight, subsample, colsample, gamma, L1/L2, NaN) | 1000 rows synthetic | COMPLETE | 2026-07-19 | PASS | All parameters behave as documented |
| T004 | XGBoost scale_pos_weight prevalence effect | 1000 rows, 9% rare class | COMPLETE | 2026-07-19 | PASS | ratio=10.1 → 6.67% positive vs default 1.0 → 1.00% |
| T005 | Negative control: shuffled labels near baseline | tests/leakage/test_negative_controls.py | COMPLETE | 2026-07-19 | PASS | |
| T006 | Negative control: random features near baseline | tests/leakage/test_negative_controls.py | COMPLETE | 2026-07-19 | PASS | |
| T007 | Negative control: timestamp reversal detected | tests/leakage/test_negative_controls.py | COMPLETE | 2026-07-19 | PASS | purgedcv rejects non-monotonic timestamps |
| T008 | Negative control: duplicate rows variance check | tests/leakage/test_negative_controls.py | COMPLETE | 2026-07-19 | PASS | |
| T009 | Negative control: missingness (50% NaN) stress | tests/leakage/test_negative_controls.py | COMPLETE | 2026-07-19 | PASS | XGBoost handles NaN without crash |
| T010 | Negative control: null model baseline comparison | tests/leakage/test_negative_controls.py | COMPLETE | 2026-07-19 | PASS | |
| T011 | Negative control: purgedcv assert_no_temporal_leakage | tests/leakage/test_negative_controls.py | COMPLETE | 2026-07-19 | PASS | Deliberately overlapping indices detected |
