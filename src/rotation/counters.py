from __future__ import annotations

from threading import Lock
from typing import Dict, List, Optional, Set, Tuple


class SubscriptionReferenceCounter:
    def __init__(self):
        self._lock = Lock()
        self._refs: Dict[int, Dict[str, int]] = {}

    def add(self, security_id: int, consumer_id: str) -> int:
        with self._lock:
            if security_id not in self._refs:
                self._refs[security_id] = {}
            consumers = self._refs[security_id]
            consumers[consumer_id] = consumers.get(consumer_id, 0) + 1
            return consumers[consumer_id]

    def remove(self, security_id: int, consumer_id: str) -> int:
        with self._lock:
            consumers = self._refs.get(security_id)
            if not consumers:
                return 0
            current = consumers.get(consumer_id, 0)
            if current <= 1:
                consumers.pop(consumer_id, None)
                remaining = 0
            else:
                consumers[consumer_id] = current - 1
                remaining = consumers[consumer_id]
            if not consumers:
                del self._refs[security_id]
            return remaining

    def count(self, security_id: int) -> int:
        with self._lock:
            consumers = self._refs.get(security_id)
            if not consumers:
                return 0
            return sum(consumers.values())

    def all_ids(self) -> Set[int]:
        with self._lock:
            return set(self._refs.keys())

    def consumers_for(self, security_id: int) -> List[str]:
        with self._lock:
            consumers = self._refs.get(security_id, {})
            return list(consumers.keys())

    def to_unsubscribe(self, active_ids: Set[int]) -> Set[int]:
        needed = self.all_ids()
        return {sid for sid in active_ids if sid not in needed}

    def clear(self):
        with self._lock:
            self._refs.clear()
