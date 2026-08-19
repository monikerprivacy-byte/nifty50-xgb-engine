"""Adapter layer for purgedcv, absorbing library-specific quirks.

The project's data contracts (``pd.Series`` with aligned index, UTC
timestamps) must not be dictated by a downstream library's API
requirements.
"""

from __future__ import annotations

import pandas as pd


def to_event_end_series(
    event_end_times: pd.Series | pd.DatetimeIndex,
    index: pd.Index,
) -> pd.Series:
    """Normalise event-end timestamps to the ``pd.Series`` purgedcv expects.

    purgedcv's ``BaseTemporalSplitter`` calls ``.reset_index(drop=True)``
    on the input, so a ``DatetimeIndex`` (which lacks that method) will
    raise ``AttributeError``.  This adapter converts safely.

    Parameters
    ----------
    event_end_times:
        Raw event-end timestamps — either a ``pd.Series`` or
        ``pd.DatetimeIndex``.
    index:
        Aligned sample index.  Must have the same length as
        ``event_end_times``.

    Returns
    -------
    ``pd.Series`` with the same length and timezone-aware UTC values.

    Raises
    ------
    TypeError
        If ``event_end_times`` is neither ``pd.Series`` nor
        ``pd.DatetimeIndex``.
    ValueError
        If length does not match ``index`` or if ``NaT`` is present.
    """
    if isinstance(event_end_times, pd.Series):
        result = event_end_times.copy()
    elif isinstance(event_end_times, pd.DatetimeIndex):
        result = pd.Series(event_end_times, index=index)
    else:
        raise TypeError(
            f"event_end_times must be pd.Series or pd.DatetimeIndex, "
            f"got {type(event_end_times).__name__}"
        )

    if len(result) != len(index):
        raise ValueError(
            f"event_end_times length ({len(result)}) must match "
            f"sample index length ({len(index)})"
        )

    result = pd.to_datetime(result, utc=True)

    if result.isna().any():
        raise ValueError("event_end_times contains NaT values")

    return result
