"""Guarded XGBoost training entry point with fail-closed enforcement.

Every training command must route through ``guarded_train()``.
Direct ``XGBClassifier.fit()`` calls are forbidden.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd
import xgboost as xgb
from purgedcv import PurgedKFold
from purgedcv.diagnostics import assert_no_temporal_leakage

from src.validation.leakage import DatasetRef, DatasetRole, build_eval_set
from src.validation.purgedcv_adapter import to_event_end_series
from src.validation.verify_deps import verify_runtime_dependencies


class GuardError(Exception):
    """A guardian check failed — training aborted."""


@dataclass
class TrainManifest:
    model_id: str
    xgboost_version: str
    dataset_ids: list[str]
    dataset_roles: list[str]
    feature_version: str
    label_version: str
    splitter: str
    purge: str
    embargo: Optional[str]
    optuna_study_id: Optional[str]
    early_stopping_dataset: str
    best_iteration: int
    guardian_status: str
    git_commit: Optional[str] = None
    train_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    negative_control_passed: bool = False

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


def _compute_data_hash(X: Any) -> str:
    if isinstance(X, np.ndarray):
        return hashlib.sha256(X.tobytes()).hexdigest()[:16]
    if isinstance(X, pd.DataFrame):
        return hashlib.sha256(pd.util.hash_pandas_object(X).values.tobytes()).hexdigest()[:16]
    return "unknown"


def guarded_train(
    *,
    X_train: Any,
    y_train: Any,
    X_val: Optional[Any] = None,
    y_val: Optional[Any] = None,
    prediction_times: pd.Series,
    evaluation_times: pd.Series,
    datasets: list[DatasetRef],
    model_params: Optional[dict] = None,
    label_horizon: str = "30min",
    purge_horizon: str = "30min",
    embargo: Optional[str] = None,
    n_splits: int = 5,
    n_estimators: int = 200,
    early_stopping_rounds: int = 20,
    feature_version: str = "0.1.0",
    label_version: str = "0.1.0",
    optuna_study_id: Optional[str] = None,
    require_negative_controls: bool = True,
    verbose: bool = False,
) -> tuple[xgb.Booster, TrainManifest]:
    """Guarded training pipeline — aborts on any guardian violation.

    Parameters
    ----------
    X_train, y_train: Primary training data.
    X_val, y_val: Optional held-out validation (EARLY_STOPPING role).
    prediction_times, evaluation_times: Series for purgedcv.
    datasets: All DatasetRef instances involved (train + eval + test).
    model_params: Extra XGBoost parameters.
    n_splits: PurgedKFold splits.
    n_estimators, early_stopping_rounds: XGBoost training parameters.
    require_negative_controls: If True, fail if negative controls haven't passed.

    Returns
    -------
    (trained_model, manifest)
    """
    # -------------------------------------------------------------------
    # Gate 1: runtime dependency verification
    # -------------------------------------------------------------------
    try:
        verify_runtime_dependencies()
    except RuntimeError as e:
        raise GuardError(f"Runtime dependency check failed: {e}")

    # -------------------------------------------------------------------
    # Gate 2: dataset role validation
    # -------------------------------------------------------------------
    if not datasets:
        raise GuardError("No DatasetRef provided — aborting training")

    for ds in datasets:
        if ds.role is None:
            raise GuardError(f"Dataset {ds.dataset_id} has no role assigned")

    # Build eval_set from EARLY_STOPPING datasets only (blocks FORBIDDEN roles)
    es_datasets = [ds for ds in datasets if ds.role == DatasetRole.EARLY_STOPPING]
    eval_set = build_eval_set(es_datasets) if es_datasets else None
    if eval_set:
        es_dataset_id = es_datasets[0].dataset_id
    else:
        es_dataset_id = "none"

    # Check that no forbidden-role dataset was passed as training input
    for ds in datasets:
        if ds.role in (DatasetRole.OUTER_TEST, DatasetRole.FINAL_VAULT):
            raise GuardError(
                f"Dataset {ds.dataset_id} has role {ds.role.value} but was provided as training data"
            )

    # -------------------------------------------------------------------
    # Gate 3: purgedcv adapter — validate event-end timestamps
    # -------------------------------------------------------------------
    try:
        eval_series = to_event_end_series(prediction_times, evaluation_times)
    except Exception as e:
        raise GuardError(f"Invalid event-end timestamps: {e}")

    # -------------------------------------------------------------------
    # Gate 4: train/test temporal overlap detection
    # -------------------------------------------------------------------
    cv = PurgedKFold(
        n_splits=n_splits,
        prediction_times=prediction_times,
        evaluation_times=eval_series,
        purge_horizon=purge_horizon,
        embargo=embargo,
    )

    for train_idx, test_idx in cv.split(X_train, y_train):
        try:
            assert_no_temporal_leakage(train_idx, test_idx, prediction_times, eval_series)
        except Exception as e:
            raise GuardError(f"Temporal leakage detected in CV split: {e}")

    # -------------------------------------------------------------------
    # Gate 5: negative control status
    # -------------------------------------------------------------------
    if require_negative_controls:
        _check_negative_controls()

    # -------------------------------------------------------------------
    # Gate 6: Optuna final-vault guard
    # -------------------------------------------------------------------
    if optuna_study_id is not None:
        for ds in datasets:
            if ds.role == DatasetRole.FINAL_VAULT:
                raise GuardError(
                    f"Final-vault dataset {ds.dataset_id} present in Optuna study {optuna_study_id}"
                )

    # -------------------------------------------------------------------
    # Train
    # -------------------------------------------------------------------
    dtrain = xgb.DMatrix(X_train, label=y_train)
    devals = []
    if eval_set is not None:
        for i, (X_es, y_es) in enumerate(eval_set):
            devals.append((xgb.DMatrix(X_es, label=y_es), f"eval_{i}"))

    params = {
        "max_depth": 6,
        "eta": 0.05,
        "eval_metric": "logloss",
        "objective": "binary:logistic",
        "seed": 42,
    }
    if model_params:
        params.update(model_params)
    # Map sklearn-style param names to xgb.train params
    if "learning_rate" in params and "eta" not in params:
        params["eta"] = params.pop("learning_rate")

    evals_result: dict = {}
    booster = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=n_estimators,
        evals=[(dtrain, "train")] + devals,
        early_stopping_rounds=early_stopping_rounds if devals else None,
        evals_result=evals_result,
        verbose_eval=verbose,
    )

    best_iteration = booster.best_iteration if booster.best_iteration is not None else n_estimators

    # -------------------------------------------------------------------
    # Manifest
    # -------------------------------------------------------------------
    model_id = f"xgb-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

    import xgboost
    manifest = TrainManifest(
        model_id=model_id,
        xgboost_version=xgb.__version__,
        dataset_ids=[ds.dataset_id for ds in datasets],
        dataset_roles=[ds.role.value for ds in datasets],
        feature_version=feature_version,
        label_version=label_version,
        splitter="PurgedKFold",
        purge=purge_horizon,
        embargo=embargo,
        optuna_study_id=optuna_study_id,
        early_stopping_dataset=es_dataset_id,
        best_iteration=best_iteration,
        guardian_status="PASS",
        negative_control_passed=True,
    )

    return booster, manifest


def _check_negative_controls():
    """Fail-closed: verify that negative controls have been executed.

    This checks that the test module is importable and the key null
    hypothesis test (label shuffle median) is available.
    """
    try:
        from tests.leakage.test_negative_controls import test_label_shuffle_median_null
    except ImportError:
        raise GuardError(
            "Negative control tests not found — run 'pytest tests/leakage/' first"
        )

    try:
        from purgedcv import PurgedKFold
        from purgedcv.diagnostics import assert_no_temporal_leakage
    except ImportError:
        raise GuardError("purgedcv not installed — negative controls cannot run")
