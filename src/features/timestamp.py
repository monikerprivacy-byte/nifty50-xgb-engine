"""Feature timestamp utilities — determines when each feature value is available."""

from datetime import datetime, timedelta, timezone

AVAILABILITY_DELAY_S = 1


def feature_available_time(bar_event_time: datetime) -> datetime:
    """When a feature computed from a bar becomes available for inference.

    Bar closes at event_time + 60s (end of minute). Add a small
    compute / pipeline delay.
    """
    return bar_event_time + timedelta(seconds=60 + AVAILABILITY_DELAY_S)
