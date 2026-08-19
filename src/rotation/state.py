from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Set


class RotationPhase(str, Enum):
    STABLE = "STABLE"
    CANDIDATE_ATM = "CANDIDATE_ATM"
    SUBSCRIBE_NEW = "SUBSCRIBE_NEW"
    WAIT_FOR_FIRST_VALID = "WAIT_FOR_FIRST_VALID"
    MARK_READY = "MARK_READY"
    SWITCH_ACTIVE = "SWITCH_ACTIVE"
    UNSUBSCRIBE_OLD = "UNSUBSCRIBE_OLD"
    ROTATION_FAILED = "ROTATION_FAILED"
    RECONCILE = "RECONCILE"


class RotationEventType(str, Enum):
    ATM_CANDIDATE_DETECTED = "ATM_CANDIDATE_DETECTED"
    ATM_CANDIDATE_CONFIRMED = "ATM_CANDIDATE_CONFIRMED"
    SUBSCRIPTION_REQUESTED = "SUBSCRIPTION_REQUESTED"
    SUBSCRIPTION_READY = "SUBSCRIPTION_READY"
    FEATURE_WARMUP_STARTED = "FEATURE_WARMUP_STARTED"
    FEATURE_READY = "FEATURE_READY"
    UNIVERSE_SWITCHED = "UNIVERSE_SWITCHED"
    OLD_CONTRACT_UNSUBSCRIBED = "OLD_CONTRACT_UNSUBSCRIBED"
    ROTATION_FAILED = "ROTATION_FAILED"
    RECONCILIATION_REPAIR = "RECONCILIATION_REPAIR"


@dataclass
class RotationEvent:
    event_type: RotationEventType
    rotation_id: int
    underlying: str
    old_atm: Optional[float] = None
    new_atm: Optional[float] = None
    old_snapshot_id: Optional[str] = None
    new_snapshot_id: Optional[str] = None
    security_ids: List[int] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    state_before: Optional[str] = None
    state_after: Optional[str] = None
    reason: str = ""


@dataclass
class ATMShiftCandidate:
    underlying: str
    old_atm: float
    candidate_atm: float
    spot: float
    crossing_ratio: float
    detected_at: float = field(default_factory=time.time)
    confirmations: int = 0
    confirmed: bool = False

    def confirm(self) -> bool:
        self.confirmations += 1
        return self.confirmed


@dataclass
class UniverseRecord:
    snapshot_id: str
    rotation_id: int
    phase: RotationPhase
    generation: int
    symbol_atm: Dict[str, float] = field(default_factory=dict)
    contract_ids: List[str] = field(default_factory=list)
    security_ids: Set[int] = field(default_factory=set)
    broker_subscriptions: Set[int] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    activated_at: Optional[float] = None

    @staticmethod
    def new_snapshot_id() -> str:
        return f"rot_{uuid.uuid4().hex[:12]}"
