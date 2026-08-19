from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Callable, Dict, List, Optional, Set, Tuple

from .config import DEFAULT_HEALTH_THRESHOLDS, HealthThresholds
from .manager import RotationManager
from .state import RotationEvent, RotationEventType, RotationPhase

logger = logging.getLogger(__name__)


class PeriodicReconciler:
    def __init__(
        self,
        manager: RotationManager,
        interval: float = 300.0,
        subscribe_fn: Optional[Callable] = None,
        unsubscribe_fn: Optional[Callable] = None,
        event_sink: Optional[Callable] = None,
        thresholds: HealthThresholds = DEFAULT_HEALTH_THRESHOLDS,
    ):
        self._manager = manager
        self._interval = interval
        self._subscribe_fn = subscribe_fn or manager._subscribe_fn
        self._unsubscribe_fn = unsubscribe_fn or manager._unsubscribe_fn
        self._event_sink = event_sink
        self._last_run: float = 0.0
        self._lock = Lock()
        self._consecutive_failures = 0
        self._thresholds = thresholds

    def check(self, force: bool = False) -> Optional[dict]:
        now = time.time()
        if not force and now - self._last_run < self._interval:
            return None

        self._last_run = now
        return self._run()

    def _run(self) -> dict:
        issues = []
        repairs = 0

        desired = self._manager.desired
        active = self._manager.active
        broker = set(self._manager.broker_subs)

        des_ids = desired.security_ids if desired else set()
        act_ids = active.security_ids if active else set()
        pend = self._manager.pending

        if des_ids:
            broker = set(self._manager.broker_subs)
            missing = des_ids - broker
            extras = broker - des_ids
            if missing:
                issues.append(f"missing={len(missing)}")
                if self._subscribe_fn:
                    self._subscribe_fn(list(missing))
                broker.update(missing)
                repairs += 1
            if extras:
                issues.append(f"extras={len(extras)}")
                for sid in extras:
                    if sid not in act_ids:
                        if self._unsubscribe_fn:
                            self._unsubscribe_fn([sid])
                        broker.discard(sid)
                        repairs += 1

        stale_pend = False
        if pend and (time.time() - pend.created_at) > self._manager._rotation_total_timeout * 2:
            stale_pend = True
            issues.append("stale_pending")

        expired = self._check_expired_contracts()
        if expired:
            issues.append(f"expired={len(expired)}")

        is_healthy = len(issues) == 0
        if not is_healthy:
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0

        health = "HEALTHY"
        if self._consecutive_failures >= self._thresholds.degraded_warning:
            health = "DEGRADED_WARNING"
        if self._consecutive_failures >= self._thresholds.degraded:
            health = "DEGRADED"
        if self._consecutive_failures >= self._thresholds.blocked:
            health = "BLOCKED"

        result = {
            "repaired": repairs,
            "issues": issues,
            "health": health,
            "desired_count": len(des_ids),
            "broker_count": len(broker),
            "active_count": len(act_ids),
            "consecutive_failures": self._consecutive_failures,
        }

        if repairs > 0:
            self._emit_event(issues, repairs, result)

        return result

    def _check_expired_contracts(self) -> Set[int]:
        return set()

    def _emit_event(self, issues: List[str], repairs: int, result: dict):
        if self._event_sink:
            try:
                self._event_sink(RotationEvent(
                    event_type=RotationEventType.RECONCILIATION_REPAIR,
                    rotation_id=self._manager._rotation_id,
                    underlying="",
                    reason=f"repaired={repairs} issues={','.join(issues)} health={result['health']}",
                ))
            except Exception as e:
                logger.warning(f"Reconciliation event error: {e}")

    def reset(self):
        self._consecutive_failures = 0
        self._last_run = 0.0
