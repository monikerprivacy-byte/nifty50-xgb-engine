"""Tests for the purgedcv adapter layer."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.validation.purgedcv_adapter import to_event_end_series


@pytest.fixture
def sample_series():
    return pd.Series(
        pd.date_range("2026-07-17 09:15", periods=5, freq="1min", tz="Asia/Kolkata"),
        name="event_end",
    )


@pytest.fixture
def sample_index():
    return pd.RangeIndex(5)


class TestToEventEndSeries:
    def test_series_accepted(self, sample_series, sample_index):
        result = to_event_end_series(sample_series, sample_index)
        assert isinstance(result, pd.Series)
        assert len(result) == 5
        assert result.dt.tz is not None  # timezone-aware

    def test_datetimeindex_accepted(self, sample_series, sample_index):
        dti = pd.DatetimeIndex(sample_series)
        result = to_event_end_series(dti, sample_index)
        assert isinstance(result, pd.Series)
        assert len(result) == 5

    def test_series_preserves_index(self, sample_series, sample_index):
        result = to_event_end_series(sample_series, sample_index)
        assert list(result.index) == list(sample_index)

    def test_length_mismatch_rejected(self, sample_series):
        wrong_index = pd.RangeIndex(10)
        with pytest.raises(ValueError, match="must match"):
            to_event_end_series(sample_series, wrong_index)

    def test_invalid_type_rejected(self, sample_index):
        with pytest.raises(TypeError, match="pd.Series or pd.DatetimeIndex"):
            to_event_end_series([1, 2, 3], sample_index)

    def test_naive_datetime_converted_to_utc(self, sample_index):
        naive = pd.Series(pd.date_range("2026-07-17 09:15", periods=3, freq="1min"))
        result = to_event_end_series(naive, sample_index[:3])
        assert result.dt.tz is not None

    def test_nat_rejected(self, sample_index):
        with_nat = pd.Series(
            [pd.Timestamp("2026-07-17 09:15", tz="UTC"), pd.NaT]
        )
        with pytest.raises(ValueError, match="NaT"):
            to_event_end_series(with_nat, sample_index[:2])

    def test_output_utc_normalised(self, sample_series, sample_index):
        result = to_event_end_series(sample_series, sample_index)
        assert str(result.dt.tz) == "UTC"
