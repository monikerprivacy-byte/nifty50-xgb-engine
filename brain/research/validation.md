# Validation Framework Research — CPCV, Purging, Embargo, DSR

*Last updated: 2026-07-16*

## 1. The Problem with Standard Validation

### 1.1 K-Fold Cross-Validation
❌ SHUFFLES TIME SERIES — uses future to predict past
❌ UNUSABLE for financial data

### 1.2 Walk-Forward Validation
✅ Preserves temporal order
❌ Single historical path → high variance
❌ Path dependency: different starting points = different results
❌ Cannot detect overfitting (no distribution of outcomes)

## 2. Combinatorial Purged Cross-Validation (CPCV)

### 2.1 What CPCV Does
- Partitions data into N contiguous time groups
- Tests on C(N,K) combinations of K test groups
- Produces multiple out-of-sample backtest paths
- Every data point appears in test sets

### 2.2 Example
N=8, K=2 → C(8,2) = 28 train/test splits → 7 backtest paths

### 2.3 Advantages over Walk-Forward
- Distribution of outcomes (not single point)
- Probability of Backtest Overfitting (PBO) measurable
- More robust parameter selection
- Reveals regime-dependent performance

### 2.4 Parameter Selection
```python
CombinatorialPurgedCV(
    n_splits=8,          # Number of groups
    n_test_groups=2,     # Test groups per split
    purge_gap=30,        # Minutes to purge (label horizon)
    embargo=5,           # Minutes to embargo (autocorrelation buffer)
)
```

## 3. Purging and Embargo

### 3.1 Purging
Remove training samples that OVERLAP with test period labels.

```
Train  [████████████████░░░░░░░░]
                        ↑
                  Label overlaps →
                  These rows are PURGED

Test         [░░░░░░░░████████████]
```

For 30-minute labels: purge 30 minutes of training data BEFORE each test fold.

### 3.2 Embargo
Remove training samples immediately AFTER test period (autocorrelation).
Financial data has serial correlation — test period info leaks into adjacent training.

```
Test  [████]░░░░░░░░
            ↑
        Embargo zone (remove from next training fold)
```

For intraday data with 5m bars: minimum embargo = 2-5 bars.

## 4. Probability of Backtest Overfitting (PBO)

### 4.1 How PBO Works
- Uses Combinatorially Symmetric CV (CSCV)
- Compares in-sample vs out-of-sample performance across all paths
- PBO = fraction of paths where best IS param performs WORSE OOS
- PBO > 0.50 → likely overfit

### 4.2 Interpretation
| PBO | Verdict |
|-----|---------|
| < 0.25 | Strong — proceed |
| 0.25 - 0.50 | Uncertain — more data needed |
| > 0.50 | Reject — likely overfit |

### 4.3 Caveats
- CSCV ≠ CPCV (different combinatorial method)
- PBO not available in purgedcv directly — needs CSCV
- Must be implemented separately OR we verify purgedcv source

## 5. Deflated Sharpe Ratio (DSR)

### 5.1 Why DSR
Standard Sharpe Ratio ignores:
- Non-normal return distributions
- Multiple testing (we test many parameter sets)
- Short backtest length

### 5.2 DSR Correction
DSR = PSR corrected for number of independent trials:
- More trials → lower DSR
- Higher correlation between trials → lower effective N

### 5.3 Min Track Record Length (MinTRL)
Minimum observations needed to distinguish a given Sharpe from noise.
```
MinTRL = f(observed_sharpe, target_sharpe, alpha, skew, kurtosis)
```

## 6. Three-Layer Validation Architecture

```
Layer 1 — Inner Hyperparameter Tuning
├── Purged Group K-Fold
├── Optuna searches here
└── Each trial evaluated on multiple inner folds

Layer 2 — Primary Model Selection  
├── CPCV with path reconstruction
├── Distribution of OOS metrics
├── Selection based on median + lower tail
└── PBO/DSR as gates

Layer 3 — Final Deployment Verification
├── Untouched chronological holdout
├── Walk-forward (for production simulation)
├── Latest regime test
└── Live shadow test
```

## 7. Key Libraries

### 7.1 purgedcv (v0.1.2, 2026)
- sklearn-compatible CPCV, PurgedKFold, WalkForwardSplit
- PSR, DSR, MinTRL
- 285 tests, MIT license
- `pip install purgedcv`

### 7.2 What purgedcv does NOT have (verify)
- Direct PBO via CSCV (may need separate implementation)
- Multi-asset group purging (need custom implementation for NIFTY-50 stocks)

## 8. Multi-Asset CPCV Design for NIFTY-50

Challenge: Same timestamp has 50 stocks. If RELIANCE is in test and HDFCBANK in train, NIFTY breadth features cause contamination.

### Solution: Time-Block Grouping
- Primary group = trading date/time block (e.g., 1 hour blocks)
- ALL stocks from same time block go to same fold
- Purge/embargo calculated at block level
- NIFTY membership and F&O eligibility also fold-specific
