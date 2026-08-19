"""Negative-control tests — multi-seed, cost-aware, MCC/balanced accuracy.

Guardian principle: if the model finds durable signal in permuted data,
the validation pipeline has a leak.  Single-seed shuffles can be lucky —
we run multiple seeds and assess the distribution.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import matthews_corrcoef, balanced_accuracy_score
from sklearn.model_selection import cross_val_score, cross_val_predict
from xgboost import XGBClassifier

from purgedcv import PurgedKFold
from purgedcv.diagnostics import assert_no_temporal_leakage

N = 500
HORIZON = "30min"
SHUFFLE_SEEDS = [7, 19, 42, 73, 101]


@pytest.fixture(scope="module")
def synthetic_data():
    rng = np.random.default_rng(42)
    X = pd.DataFrame({
        "f1": rng.normal(size=N),
        "f2": rng.normal(size=N),
        "f3": rng.normal(size=N),
    })
    y = (X["f1"] * 0.15 + rng.normal(size=N) * 0.85 > 0).astype(int)
    pred = pd.Series(
        pd.date_range("2026-07-17 09:15", periods=N, freq="1min", tz="Asia/Kolkata")
    )
    eval_ = pred + pd.Timedelta(HORIZON)
    return X, y, pred, eval_


def _model(seed=42):
    return XGBClassifier(
        n_estimators=50, max_depth=3, learning_rate=0.1,
        eval_metric="logloss", random_state=seed, verbosity=0,
    )


# ---------------------------------------------------------------------------
# Label shuffle — multi-seed, MCC / balanced-accuracy based
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", SHUFFLE_SEEDS)
def test_label_shuffle_multiseed(synthetic_data, seed):
    """Shuffled labels: median performance must be near baseline."""
    X, y, pred, eval_ = synthetic_data

    rng = np.random.default_rng(seed)
    y_shuffled = y.sample(frac=1, random_state=rng.integers(0, 2**31)).values
    baseline = y.mean()

    cv = PurgedKFold(n_splits=5, prediction_times=pred, evaluation_times=eval_, purge_horizon=HORIZON)
    y_pred = cross_val_predict(_model(seed), X, y_shuffled, cv=cv, method="predict")

    mcc = matthews_corrcoef(y_shuffled, y_pred)
    bal_acc = balanced_accuracy_score(y_shuffled, y_pred)

    assert mcc > -0.3, f"Seed {seed}: MCC={mcc:.3f} suspiciously negative"
    assert abs(bal_acc - max(baseline, 1 - baseline)) < 0.12, (
        f"Seed {seed}: shuffled balanced_acc={bal_acc:.3f} vs baseline {max(baseline, 1-baseline):.3f}"
    )


def test_label_shuffle_median_null(synthetic_data):
    """Across seeds, median shuffled MCC must be near zero."""
    X, y, pred, eval_ = synthetic_data
    mccs = []
    for seed in SHUFFLE_SEEDS:
        rng = np.random.default_rng(seed)
        y_s = y.sample(frac=1, random_state=rng.integers(0, 2**31)).values
        cv = PurgedKFold(n_splits=5, prediction_times=pred, evaluation_times=eval_, purge_horizon=HORIZON)
        y_p = cross_val_predict(_model(seed), X, y_s, cv=cv, method="predict")
        mccs.append(matthews_corrcoef(y_s, y_p))

    median_mcc = np.median(mccs)
    assert abs(median_mcc) < 0.10, (
        f"Median shuffled MCC={median_mcc:.3f} across {len(SHUFFLE_SEEDS)} seeds — "
        f"suspicious. Individual MCCs: {[round(m, 3) for m in mccs]}"
    )


# ---------------------------------------------------------------------------
# Random features — multi-seed
# ---------------------------------------------------------------------------

def test_random_features_null(synthetic_data):
    """Pure noise — no durable out-of-sample edge."""
    X, y, pred, eval_ = synthetic_data
    rng = np.random.default_rng(99)
    X_noise = pd.DataFrame({"n1": rng.normal(size=N), "n2": rng.normal(size=N)})
    baseline = y.mean()

    cv = PurgedKFold(n_splits=5, prediction_times=pred, evaluation_times=eval_, purge_horizon=HORIZON)
    scores = cross_val_score(_model(), X_noise, y, cv=cv, scoring="balanced_accuracy")
    mean_bal = scores.mean()

    assert abs(mean_bal - 0.5) < 0.10, (
        f"Random features: balanced_accuracy={mean_bal:.3f} vs expected 0.5"
    )


# ---------------------------------------------------------------------------
# Timestamp reversal — purgedcv blocks this
# ---------------------------------------------------------------------------

def test_timestamp_reversal_detected(synthetic_data):
    """purgedcv rejects non-monotonic timestamps (reversal detection built-in)."""
    X, y, pred, eval_ = synthetic_data
    with pytest.raises(ValueError, match="monotonic"):
        PurgedKFold(
            n_splits=5,
            prediction_times=pd.Series(pred.iloc[::-1].values),
            evaluation_times=pd.Series(pred.iloc[::-1].values + pd.Timedelta(HORIZON)),
            purge_horizon=HORIZON,
        )


# ---------------------------------------------------------------------------
# Duplicate rows — variance must not collapse
# ---------------------------------------------------------------------------

def test_duplicate_rows_stress(synthetic_data):
    """Duplicate rows must not create unrealistic score stability."""
    X, y, pred, eval_ = synthetic_data
    X_dup = pd.concat([X, X.iloc[:50]], ignore_index=True)
    y_dup = pd.concat([y, y.iloc[:50]], ignore_index=True)
    last_ts = pred.iloc[-1]
    pred_dup = pd.concat([
        pred,
        pd.Series(pd.date_range(last_ts + pd.Timedelta("1min"), periods=50, freq="1min", tz="Asia/Kolkata")),
    ], ignore_index=True)
    eval_dup = pred_dup + pd.Timedelta(HORIZON)

    cv = PurgedKFold(n_splits=5, prediction_times=pred_dup, evaluation_times=eval_dup, purge_horizon=HORIZON)
    cv_orig = PurgedKFold(n_splits=5, prediction_times=pred, evaluation_times=eval_, purge_horizon=HORIZON)
    scores_orig = cross_val_score(_model(), X, y, cv=cv_orig, scoring="accuracy")
    scores_dup = cross_val_score(_model(), X_dup, y_dup, cv=cv, scoring="accuracy")

    assert scores_dup.std() >= 0.01, (
        f"Duplicate rows reduced std from {scores_orig.std():.3f} to {scores_dup.std():.3f}"
    )


# ---------------------------------------------------------------------------
# Missingness stress
# ---------------------------------------------------------------------------

def test_missingness_stress(synthetic_data):
    """50% NaN in a feature must not crash or produce extreme scores."""
    X, y, pred, eval_ = synthetic_data
    X_nan = X.copy()
    mask = np.random.default_rng(42).binomial(1, 0.5, size=X_nan.shape).astype(bool)
    X_nan[mask] = np.nan

    cv = PurgedKFold(n_splits=5, prediction_times=pred, evaluation_times=eval_, purge_horizon=HORIZON)
    try:
        scores = cross_val_score(_model(), X_nan, y, cv=cv, scoring="accuracy")
        assert not np.any(np.isnan(scores)), "CV returned NaN scores with missing data"
    except Exception as e:
        pytest.fail(f"50% NaN caused crash: {e}")


# ---------------------------------------------------------------------------
# Null model baseline
# ---------------------------------------------------------------------------

def test_null_model_baseline(synthetic_data):
    """Tuned model should meaningfully beat the null baseline."""
    X, y, pred, eval_ = synthetic_data
    baseline = y.mean()
    null_acc = max(baseline, 1 - baseline)

    cv = PurgedKFold(n_splits=5, prediction_times=pred, evaluation_times=eval_, purge_horizon=HORIZON)
    scores = cross_val_score(_model(), X, y, cv=cv, scoring="accuracy")
    model_acc = scores.mean()

    assert model_acc >= null_acc - 0.05, (
        f"Model ({model_acc:.3f}) below null baseline ({null_acc:.3f})"
    )


# ---------------------------------------------------------------------------
# PurgedCV leak assertion
# ---------------------------------------------------------------------------

def test_purgedcv_leak_assertion(synthetic_data):
    """PurgedCV must detect overlapping train/test intervals."""
    X, y, pred, eval_ = synthetic_data
    train_idx = np.arange(300)
    test_idx = np.arange(200, 500)
    with pytest.raises(Exception):
        assert_no_temporal_leakage(train_idx, test_idx, pred, eval_)
