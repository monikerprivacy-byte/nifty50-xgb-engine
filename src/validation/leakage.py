"""Guardian enforcement: DatasetRole, DatasetRef, and safe eval_set builder.

Prevents test-set / final-vault datasets from entering XGBoost's eval_set
for early stopping, Optuna objective evaluation, calibration, or threshold
selection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class DatasetRole(str, Enum):
    """Canonical role for every dataset slice in the training pipeline.

    Each slice must be declared with a role before entering any ML
    function.  The role determines where the slice may flow.

    =================  ============================================
    Role               Allowed usage
    =================  ============================================
    TRAIN              XGBoost ``fit()`` primary training data
    EARLY_STOPPING     ``eval_set`` for early stopping only
    CALIBRATION        ``predict_proba`` → calibrator fit
    OUTER_TEST         Outer / walk-forward test (never train)
    FINAL_VAULT        Final untouched holdout (never any ML
                       function)
    =================  ============================================
    """

    TRAIN = "train"
    EARLY_STOPPING = "early_stopping"
    CALIBRATION = "calibration"
    OUTER_TEST = "outer_test"
    FINAL_VAULT = "final_vault"


EVAL_SET_FORBIDDEN = {
    DatasetRole.OUTER_TEST,
    DatasetRole.FINAL_VAULT,
    DatasetRole.CALIBRATION,
}

TRAINING_FORBIDDEN = {
    DatasetRole.OUTER_TEST,
    DatasetRole.FINAL_VAULT,
}


@dataclass(frozen=True)
class DatasetRef:
    """Immutable reference to a dataset slice with its role and identity."""

    X: Any = field(repr=False)
    y: Any = field(repr=False)
    role: DatasetRole
    dataset_id: str
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None


def build_eval_set(
    datasets: list[DatasetRef],
) -> list[tuple[Any, Any]]:
    """Build an ``eval_set`` list for XGBoost, rejecting forbidden roles.

    Parameters
    ----------
    datasets:
        One or more ``DatasetRef`` instances.  At least one must have
        role ``EARLY_STOPPING``.

    Returns
    -------
    ``[(X, y), ...]`` — the input suitable for ``model.fit(eval_set=...)``.

    Raises
    ------
    ValueError
        If any dataset has a role that is forbidden for ``eval_set``.
    """
    violations = [
        ds.dataset_id for ds in datasets if ds.role in EVAL_SET_FORBIDDEN
    ]
    if violations:
        raise ValueError(
            "Leakage blocked: forbidden datasets in eval_set: "
            + ", ".join(violations)
        )

    early_stopping = [ds for ds in datasets if ds.role == DatasetRole.EARLY_STOPPING]
    if not early_stopping:
        raise ValueError(
            "At least one EARLY_STOPPING dataset is required in eval_set"
        )

    return [(ds.X, ds.y) for ds in datasets]
