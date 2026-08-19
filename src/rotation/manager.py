from __future__ import annotations

import logging
import time
from threading import RLock
from typing import Callable, Dict, List, Optional, Set, Tuple

from .counters import SubscriptionReferenceCounter
from .readiness import ReadinessChecker, ReadinessLevel
from .state import (
    ATMShiftCandidate,
    UniverseRecord,
    RotationEvent,
    RotationEventType,
    RotationPhase,
)

logger = logging.getLogger(__name__)


class RotationManager:
    def __init__(
        self,
        confirm_ticks: int = 3,
        confirm_duration_ms: int = 2000,
        subscription_ack_timeout: float = 5.0,
        first_packet_timeout: float = 10.0,
        old_universe_grace_period: float = 60.0,
        rotation_total_timeout: float = 30.0,
        strict_mode: bool = True,
        subscribe_fn: Optional[Callable] = None,
        unsubscribe_fn: Optional[Callable] = None,
        event_sink: Optional[Callable] = None,
    ):
        self._confirm_ticks = confirm_ticks
        self._confirm_duration_ms = confirm_duration_ms
        self._subscription_ack_timeout = subscription_ack_timeout
        self._first_packet_timeout = first_packet_timeout
        self._old_universe_grace_period = old_universe_grace_period
        self._rotation_total_timeout = rotation_total_timeout
        self._strict_mode = strict_mode
        self._subscribe_fn = subscribe_fn
        self._unsubscribe_fn = unsubscribe_fn
        self._event_sink = event_sink

        self._lock = RLock()
        self._generation = 0
        self._rotation_id = 0

        self.desired: UniverseRecord = UniverseRecord(
            snapshot_id="init", rotation_id=0, phase=RotationPhase.STABLE, generation=0
        )
        self.pending: Optional[UniverseRecord] = None
        self.active: Optional[UniverseRecord] = None
        self.broker_subs: Set[int] = set()

        self._candidate: Optional[ATMShiftCandidate] = None
        self._readiness = ReadinessChecker()
        self._ref_counter = SubscriptionReferenceCounter()
        self._grace_start: Optional[float] = None
        self._phase_start: float = time.time()
        self._consumer_id_gen = 0

    @property
    def phase(self) -> RotationPhase:
        return self.desired.phase

    @property
    def generation(self) -> int:
        return self._generation

    # ─── ATM candidate pipeline ────────────────────────────────────────────

    def on_atm_candidate(self, candidate: ATMShiftCandidate):
        now = time.time()
        with self._lock:
            if self._candidate and self._candidate.candidate_atm == candidate.candidate_atm:
                elapsed = now - self._candidate.detected_at
                self._candidate.confirmations += 1
                self._candidate.confirmed = (
                    self._candidate.confirmations >= self._confirm_ticks
                    and elapsed * 1000 >= self._confirm_duration_ms
                )
                candidate.confirmations = self._candidate.confirmations
                candidate.confirmed = self._candidate.confirmed
            else:
                self._candidate = candidate
                self._emit(RotationEvent(
                    event_type=RotationEventType.ATM_CANDIDATE_DETECTED,
                    rotation_id=self._rotation_id,
                    underlying=candidate.underlying,
                    old_atm=candidate.old_atm,
                    new_atm=candidate.candidate_atm,
                    reason=f"crossing_ratio={candidate.crossing_ratio:.3f}",
                ))

            if self._candidate and self._candidate.confirmed:
                self._begin_rotation(self._candidate)
                self._candidate = None

    # ─── Rotation lifecycle ────────────────────────────────────────────────

    def _begin_rotation(self, candidate: ATMShiftCandidate):
        self._generation += 1
        self._rotation_id += 1
        gen = self._generation

        new_atm = {candidate.underlying: candidate.candidate_atm}
        desired = UniverseRecord(
            snapshot_id=UniverseRecord.new_snapshot_id(),
            rotation_id=self._rotation_id,
            phase=RotationPhase.SUBSCRIBE_NEW,
            generation=gen,
            symbol_atm=new_atm,
            contract_ids=list(self.active.contract_ids) if self.active else [],
            security_ids=set(self.active.security_ids) if self.active else set(),
        )

        self.desired = desired
        self._phase_start = time.time()

        self._emit(RotationEvent(
            event_type=RotationEventType.ATM_CANDIDATE_CONFIRMED,
            rotation_id=self._rotation_id,
            underlying=candidate.underlying,
            old_atm=candidate.old_atm,
            new_atm=candidate.candidate_atm,
            state_before=self.active.phase.value if self.active else "NONE",
            state_after=RotationPhase.SUBSCRIBE_NEW.value,
            reason=f"confirmed {candidate.confirmations}/{self._confirm_ticks} ticks",
        ))

        to_sub, to_unsub, to_keep = self._delta()
        if to_sub:
            self._subscribe_ids(to_sub, gen)
        desired.phase = RotationPhase.WAIT_FOR_FIRST_VALID
        self._phase_start = time.time()

    def _delta(self) -> Tuple[Set[int], Set[int], Set[int]]:
        des = self.desired.security_ids if self.desired else set()
        act = set(self.broker_subs)
        to_sub = des - act
        to_unsub = act - des
        to_keep = des & act
        return to_sub, to_unsub, to_keep

    # ─── Subscribe / Unsubscribe ───────────────────────────────────────────

    def _subscribe_ids(self, security_ids: Set[int], gen: int):
        if not security_ids:
            return
        consumer_id = f"rotation_{self._rotation_id}"
        for sid in security_ids:
            self._ref_counter.add(sid, consumer_id)

        if self._subscribe_fn:
            self._subscribe_fn(list(security_ids))

        self.broker_subs.update(security_ids)

        self._emit(RotationEvent(
            event_type=RotationEventType.SUBSCRIPTION_REQUESTED,
            rotation_id=self._rotation_id,
            underlying="",
            security_ids=sorted(security_ids),
            reason=f"gen={gen} {len(security_ids)} ids",
        ))

    def _unsubscribe_ids(self, security_ids: Set[int], consumer_id: str):
        if not security_ids:
            return
        actual = set()
        for sid in security_ids:
            remaining = self._ref_counter.remove(sid, consumer_id)
            if remaining == 0:
                actual.add(sid)

        if actual and self._unsubscribe_fn:
            self._unsubscribe_fn(list(actual))

        self.broker_subs -= actual

        self._emit(RotationEvent(
            event_type=RotationEventType.OLD_CONTRACT_UNSUBSCRIBED,
            rotation_id=self._rotation_id,
            underlying="",
            security_ids=sorted(actual),
            reason=f"removed {len(actual)} ids",
        ))

    # ─── Packet pipeline ──────────────────────────────────────────────────

    def on_packet(self, contract_id: str, packet: dict, gen: Optional[int] = None):
        with self._lock:
            if gen is not None and gen != self._generation:
                return

            self._readiness.update_from_packet(contract_id, packet)

            if self.desired.phase == RotationPhase.WAIT_FOR_FIRST_VALID:
                self._check_readiness()

    def _check_readiness(self):
        if not self.desired or not self.desired.contract_ids:
            return

        level = self._readiness.level_for_contracts(
            set(self.desired.contract_ids),
            strict=self._strict_mode,
        )

        if level == ReadinessLevel.QUOTE_READY:
            self.desired.phase = RotationPhase.MARK_READY
            self._emit(RotationEvent(
                event_type=RotationEventType.SUBSCRIPTION_READY,
                rotation_id=self._rotation_id,
                underlying="",
                old_snapshot_id=self.active.snapshot_id if self.active else None,
                new_snapshot_id=self.desired.snapshot_id,
                state_before=RotationPhase.WAIT_FOR_FIRST_VALID.value,
                state_after=RotationPhase.MARK_READY.value,
                reason="all contracts quote-ready",
            ))
            self._perform_switch()

    # ─── Atomic switch ────────────────────────────────────────────────────

    def _perform_switch(self):
        pending = UniverseRecord(
            snapshot_id=self.desired.snapshot_id,
            rotation_id=self._rotation_id,
            phase=RotationPhase.SWITCH_ACTIVE,
            generation=self._generation,
            symbol_atm=dict(self.desired.symbol_atm),
            contract_ids=list(self.desired.contract_ids),
            security_ids=set(self.desired.security_ids),
            activated_at=time.time(),
        )

        old_snapshot_id = self.active.snapshot_id if self.active else None

        self.active = pending
        self.desired.phase = RotationPhase.STABLE
        self._grace_start = time.time()

        self._emit(RotationEvent(
            event_type=RotationEventType.UNIVERSE_SWITCHED,
            rotation_id=self._rotation_id,
            underlying="",
            old_snapshot_id=old_snapshot_id,
            new_snapshot_id=pending.snapshot_id,
            state_before=RotationPhase.MARK_READY.value,
            state_after=RotationPhase.STABLE.value,
            security_ids=sorted(pending.security_ids),
            reason="atomic universe switch complete",
        ))

    # ─── Grace period / cleanup ──────────────────────────────────────────

    def check_grace_expiry(self) -> bool:
        with self._lock:
            if self._grace_start is None:
                return True
            if time.time() - self._grace_start >= self._old_universe_grace_period:
                if self.active:
                    consumer = f"rotation_{self.active.rotation_id}"
                    self._unsubscribe_ids(self.active.security_ids, consumer)
                self._grace_start = None
                return True
            return False

    # ─── Timeout check ───────────────────────────────────────────────────

    def check_rotation_timeout(self) -> bool:
        with self._lock:
            phase = self.desired.phase
            if phase in (RotationPhase.STABLE, RotationPhase.SWITCH_ACTIVE, RotationPhase.UNSUBSCRIBE_OLD):
                return False

            elapsed = time.time() - self._phase_start
            if phase == RotationPhase.WAIT_FOR_FIRST_VALID and elapsed >= self._first_packet_timeout:
                logger.warning(f"Rotation timeout: no valid packets in {elapsed:.1f}s")
                self._fail_rotation("first_packet_timeout")
                return True
            if elapsed >= self._rotation_total_timeout:
                logger.warning(f"Rotation total timeout: {elapsed:.1f}s")
                self._fail_rotation("total_timeout")
                return True
            return False

    def _fail_rotation(self, reason: str):
        self._emit(RotationEvent(
            event_type=RotationEventType.ROTATION_FAILED,
            rotation_id=self._rotation_id,
            underlying="",
            state_before=self.desired.phase.value,
            state_after=RotationPhase.ROTATION_FAILED.value,
            reason=reason,
        ))
        self.desired.phase = RotationPhase.ROTATION_FAILED
        if self.pending:
            self._unsubscribe_ids(
                self.pending.security_ids - (self.active.security_ids if self.active else set()),
                f"rotation_{self.pending.rotation_id}",
            )
            self.pending = None

    # ─── New candidate during active rotation ────────────────────────────

    def supersede_pending(self, candidate: ATMShiftCandidate) -> bool:
        with self._lock:
            if self._candidate and self._candidate.candidate_atm == candidate.candidate_atm:
                return False

            if self.desired.phase in (RotationPhase.STABLE,):
                return False

            shift = abs(candidate.candidate_atm - (self._candidate.candidate_atm if self._candidate else 0))
            if shift < 0.5 * abs(candidate.candidate_atm - candidate.old_atm):
                logger.info(f"Candidate not materially farther, skipping supersede")
                return False

            self._candidate = candidate
            logger.info(
                f"New candidate supersedes pending: {candidate.old_atm} -> {candidate.candidate_atm}"
            )
            return True

    # ─── Events ──────────────────────────────────────────────────────────

    def _emit(self, event: RotationEvent):
        if self._event_sink:
            try:
                self._event_sink(event)
            except Exception as e:
                logger.warning(f"Event sink error: {e}")
        logger.debug(f"Rotation event: {event.event_type.value} | {event.reason}")

    # ─── Universe contract set ───────────────────────────────────────────

    def set_desired_contracts(
        self,
        security_ids: Set[int],
        contract_ids: List[str],
        symbol_atm: Dict[str, float],
    ):
        with self._lock:
            if self.desired.phase != RotationPhase.STABLE:
                return

            gen = self._generation
            rec = UniverseRecord(
                snapshot_id=self.desired.snapshot_id,
                rotation_id=self._rotation_id,
                phase=RotationPhase.STABLE,
                generation=gen,
                symbol_atm=dict(symbol_atm),
                contract_ids=list(contract_ids),
                security_ids=set(security_ids),
                activated_at=time.time(),
            )
            self.active = rec
            self.desired = rec

            for sid in security_ids:
                self._ref_counter.add(sid, f"active_{gen}")

            self.broker_subs.update(security_ids)

            for cid in contract_ids:
                self._readiness.register_contract(cid, 0)

    def register_contracts(self, contract_ids: List[Tuple[str, int]]):
        for cid, sid in contract_ids:
            self._readiness.register_contract(cid, sid)

    def readiness_for(self, contract_ids: Set[str]) -> ReadinessLevel:
        return self._readiness.level_for_contracts(contract_ids, strict=self._strict_mode)

    def warm_contracts(self, contract_ids: Set[str]) -> Set[str]:
        return self._readiness.warm_contracts(contract_ids)

    @property
    def unregistered_packet_count(self) -> int:
        return self._readiness.unregistered_packet_count
