from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Dict


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    BACKPRESSURED = "BACKPRESSURED"
    SPOOLING = "SPOOLING"
    RECOVERING = "RECOVERING"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


@dataclass
class MetricsStore:
    _lock: Lock = field(default_factory=Lock)
    _counters: Dict[str, int] = field(default_factory=dict)
    _gauges: Dict[str, float] = field(default_factory=dict)

    ws_frames_received_total: int = 0
    packets_decoded_total: int = 0
    packets_persisted_total: int = 0
    packets_quarantined_total: int = 0
    packets_explicitly_dropped_total: int = 0

    writer_queue_depth: int = 0
    writer_queue_capacity: int = 10000
    writer_lag_ms: float = 0.0
    oldest_pending_event_age_ms: float = 0.0

    spool_pending_records: int = 0
    spool_pending_bytes: int = 0
    writer_restart_count: int = 0
    duplicate_event_count: int = 0

    def __post_init__(self):
        self._counters = {
            "ws_frames_received_total": 0,
            "packets_decoded_total": 0,
            "packets_persisted_total": 0,
            "packets_quarantined_total": 0,
            "packets_explicitly_dropped_total": 0,
            "writer_restart_count": 0,
            "duplicate_event_count": 0,
        }
        self._gauges = {
            "writer_queue_depth": 0.0,
            "writer_queue_capacity": 10000.0,
            "writer_lag_ms": 0.0,
            "oldest_pending_event_age_ms": 0.0,
            "spool_pending_records": 0.0,
            "spool_pending_bytes": 0.0,
        }

    def increment(self, name: str, amount: int = 1):
        with self._lock:
            if name in self._counters:
                self._counters[name] += amount
                setattr(self, name, self._counters[name])
            elif name in self._gauges:
                self._gauges[name] += amount
                setattr(self, name, int(self._gauges[name]))

    def set_gauge(self, name: str, value: float):
        with self._lock:
            if name in self._gauges:
                self._gauges[name] = value
                setattr(self, name, int(value) if name in (
                    "writer_queue_depth", "writer_queue_capacity", "spool_pending_records", "spool_pending_bytes",
                    "writer_restart_count", "duplicate_event_count",
                ) else value)
            elif name in self._counters:
                self._counters[name] = int(value)
                setattr(self, name, int(value))

    def counters_snapshot(self) -> dict:
        with self._lock:
            return dict(self._counters)

    def gauges_snapshot(self) -> dict:
        with self._lock:
            return dict(self._gauges)

    def health_state(self) -> HealthState:
        with self._lock:
            depth = self._gauges.get("writer_queue_depth", 0)
            cap = self._gauges.get("writer_queue_capacity", 10000)
            spool = self._gauges.get("spool_pending_records", 0)

        if spool > 0 and depth >= cap:
            return HealthState.BLOCKED
        if spool > 0:
            return HealthState.SPOOLING
        if cap > 0 and depth / cap > 0.9:
            return HealthState.BACKPRESSURED
        return HealthState.HEALTHY

    def reconcile(self, received: int, persisted: int, quarantined: int,
                  pending_queue: int, pending_spool: int, dropped: int) -> bool:
        total_accounted = persisted + quarantined + pending_queue + pending_spool + dropped
        return total_accounted == received
