"""Tests for DatasetRole, DatasetRef, and build_eval_set guard."""
from __future__ import annotations

import pytest
from src.validation.leakage import (
    DatasetRef,
    DatasetRole,
    build_eval_set,
)


def _make_train():
    return DatasetRef(
        X=[[1.0], [2.0]], y=[0, 1],
        role=DatasetRole.TRAIN,
        dataset_id="train-001",
    )


def _make_early_stopping():
    return DatasetRef(
        X=[[3.0]], y=[0],
        role=DatasetRole.EARLY_STOPPING,
        dataset_id="eval-001",
    )


def _make_outer_test():
    return DatasetRef(
        X=[[4.0]], y=[1],
        role=DatasetRole.OUTER_TEST,
        dataset_id="outer-001",
    )


def _make_final_vault():
    return DatasetRef(
        X=[[5.0]], y=[0],
        role=DatasetRole.FINAL_VAULT,
        dataset_id="final-2026-q2",
    )


def _make_calibration():
    return DatasetRef(
        X=[[6.0]], y=[1],
        role=DatasetRole.CALIBRATION,
        dataset_id="cal-001",
    )


class TestDatasetRole:
    def test_enum_values(self):
        assert DatasetRole.TRAIN.value == "train"
        assert DatasetRole.EARLY_STOPPING.value == "early_stopping"
        assert DatasetRole.OUTER_TEST.value == "outer_test"
        assert DatasetRole.FINAL_VAULT.value == "final_vault"
        assert DatasetRole.CALIBRATION.value == "calibration"

    def test_distinct(self):
        roles = list(DatasetRole)
        assert len(roles) == len({r.value for r in roles})


class TestDatasetRef:
    def test_immutable_by_default(self):
        ref = DatasetRef(
            X=[1], y=0, role=DatasetRole.TRAIN, dataset_id="x",
        )
        with pytest.raises(Exception):
            ref.role = DatasetRole.FINAL_VAULT  # frozen dataclass

    def test_minimal_construction(self):
        ref = DatasetRef(
            X=[1], y=0, role=DatasetRole.TRAIN, dataset_id="test",
        )
        assert ref.dataset_id == "test"
        assert ref.role == DatasetRole.TRAIN
        assert ref.start_ts is None
        assert ref.end_ts is None


class TestBuildEvalSet:
    def test_final_vault_rejected(self):
        ds = [_make_early_stopping(), _make_final_vault()]
        with pytest.raises(ValueError, match="Leakage blocked"):
            build_eval_set(ds)

    def test_outer_test_rejected(self):
        ds = [_make_early_stopping(), _make_outer_test()]
        with pytest.raises(ValueError, match="Leakage blocked"):
            build_eval_set(ds)

    def test_calibration_rejected(self):
        ds = [_make_early_stopping(), _make_calibration()]
        with pytest.raises(ValueError, match="Leakage blocked"):
            build_eval_set(ds)

    def test_train_and_early_stopping_accepted(self):
        ds = [_make_train(), _make_early_stopping()]
        result = build_eval_set(ds)
        assert len(result) == 2

    def test_early_stopping_only_accepted(self):
        ds = [_make_early_stopping()]
        result = build_eval_set(ds)
        assert len(result) == 1

    def test_raises_without_early_stopping(self):
        with pytest.raises(ValueError, match="EARLY_STOPPING"):
            build_eval_set([_make_train()])

    def test_multiple_forbidden_rejected(self):
        ds = [_make_final_vault(), _make_outer_test(), _make_calibration()]
        with pytest.raises(ValueError, match="Leakage blocked"):
            build_eval_set(ds)

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="EARLY_STOPPING"):
            build_eval_set([])
