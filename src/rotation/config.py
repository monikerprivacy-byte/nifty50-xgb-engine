from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HealthThresholds:
    degraded_warning: int = 1
    degraded: int = 2
    blocked: int = 5


DEFAULT_HEALTH_THRESHOLDS = HealthThresholds()
