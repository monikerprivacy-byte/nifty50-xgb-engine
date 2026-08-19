from __future__ import annotations

import time
from typing import Dict, List, Optional, Set

import pytest

from src.rotation.counters import SubscriptionReferenceCounter
from src.rotation.config import HealthThresholds
from src.rotation.manager import RotationManager
from src.rotation.readiness import ReadinessChecker, ReadinessLevel
from src.rotation.reconciler import PeriodicReconciler
from src.rotation.state import ATMShiftCandidate, RotationEvent, RotationPhase


# ─── Reference counter tests ─────────────────────────────────────────────


class TestReferenceCounter:
    def test_add_and_remove(self):
        c = SubscriptionReferenceCounter()
        assert c.add(101, "consumer_a") == 1
        assert c.add(101, "consumer_b") == 1
        assert c.add(101, "consumer_a") == 2
        assert c.count(101) == 3

        assert c.remove(101, "consumer_a") == 1
        assert c.count(101) == 2
        assert c.remove(101, "consumer_a") == 0
        assert c.count(101) == 1

        assert c.remove(101, "consumer_b") == 0
        assert c.count(101) == 0

    def test_to_unsubscribe(self):
        c = SubscriptionReferenceCounter()
        c.add(101, "a")
        c.add(102, "b")
        c.add(103, "c")

        active = {101, 102, 103, 104}
        to_remove = c.to_unsubscribe(active)
        assert to_remove == {104}

        c.remove(101, "a")
        to_remove = c.to_unsubscribe({101, 102, 103})
        assert 101 in to_remove

    def test_consumers_for(self):
        c = SubscriptionReferenceCounter()
        c.add(101, "a")
        c.add(101, "b")
        consumers = c.consumers_for(101)
        assert sorted(consumers) == ["a", "b"]


# ─── Readiness tests ──────────────────────────────────────────────────────


class TestReadiness:
    def test_contract_starts_not_ready(self):
        r = ReadinessChecker()
        r.register_contract("NSE|RELIANCE|2026-08-06|1500|CE", 1001)
        level = r.level_for_contracts({"NSE|RELIANCE|2026-08-06|1500|CE"})
        assert level == ReadinessLevel.NONE

    def test_contract_quote_ready_after_valid_packet(self):
        r = ReadinessChecker()
        cid = "NSE|RELIANCE|2026-08-06|1500|CE"
        r.register_contract(cid, 1001)
        r.update_from_packet(cid, {
            "ltp": 15.5, "last_trade_time": 1234567890, "last_trade_volume": 1000,
        })
        assert r.level_for_contracts({cid}) == ReadinessLevel.QUOTE_READY

    def test_ce_pe_pair_ready(self):
        r = ReadinessChecker()
        base = "NSE|RELIANCE|2026-08-06|1500"
        ce = f"{base}|CE"
        pe = f"{base}|PE"
        r.register_contract(ce, 1001)
        r.register_contract(pe, 1002)
        packet = {"ltp": 15.5, "last_trade_time": 1234567890, "last_trade_volume": 1000}
        r.update_from_packet(ce, packet)
        r.update_from_packet(pe, packet)
        assert r.ce_pe_pair_ready(base) is True

    def test_pair_not_ready_if_one_missing(self):
        r = ReadinessChecker()
        base = "NSE|RELIANCE|2026-08-06|1500"
        r.register_contract(f"{base}|CE", 1001)
        assert r.ce_pe_pair_ready(base) is False

    def test_fallback_mode_accepts_partial(self):
        r = ReadinessChecker()
        ids = {
            "NSE|A|2026-08-06|100|CE",
            "NSE|A|2026-08-06|100|PE",
            "NSE|A|2026-08-06|110|CE",
            "NSE|A|2026-08-06|110|PE",
        }
        for cid in ids:
            r.register_contract(cid, 1000)
        pkt = {"ltp": 10.0, "last_trade_time": 1234567890, "last_trade_volume": 500}
        r.update_from_packet("NSE|A|2026-08-06|100|CE", pkt)
        r.update_from_packet("NSE|A|2026-08-06|100|PE", pkt)
        r.update_from_packet("NSE|A|2026-08-06|110|CE", pkt)
        level = r.level_for_contracts(ids, strict=False, min_complete_ratio=0.5)
        assert level == ReadinessLevel.QUOTE_READY

    def test_recently_updated(self):
        r = ReadinessChecker()
        cid = "NSE|A|2026-08-06|100|CE"
        r.register_contract(cid, 100)
        assert r.recently_updated(cid) is False
        r.update_from_packet(cid, {"ltp": 10, "last_trade_time": 100, "last_trade_volume": 1})
        assert r.recently_updated(cid, max_age=60) is True

    def test_warm_contracts(self):
        r = ReadinessChecker()
        cid = "NSE|A|2026-08-06|100|CE"
        r.register_contract(cid, 100)
        pkt = {"ltp": 10, "last_trade_time": 100, "last_trade_volume": 1}
        for _ in range(3):
            r.update_from_packet(cid, pkt)
        warm = r.warm_contracts({cid})
        assert cid in warm

    def test_reset_clears_contracts(self):
        r = ReadinessChecker()
        cid = "NSE|A|2026-08-06|100|CE"
        r.register_contract(cid, 100)
        r.update_from_packet(cid, {"ltp": 10, "last_trade_time": 100, "last_trade_volume": 1})
        r.reset_for({cid})
        assert r.level_for_contracts({cid}) == ReadinessLevel.NONE


# ─── Rotation manager tests ────────────────────────────────────────────────


class TestRotationManager:
    def test_initial_state(self):
        mgr = RotationManager()
        assert mgr.phase == RotationPhase.STABLE
        assert mgr.active is None

    def test_stable_to_candidate(self):
        mgr = RotationManager(confirm_ticks=2, confirm_duration_ms=0)
        mgr.set_desired_contracts(
            security_ids={1, 2, 3},
            contract_ids=["c1", "c2", "c3"],
            symbol_atm={"RELIANCE": 1500},
        )

        cand = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
            spot=1512.5, crossing_ratio=0.625,
        )
        mgr.on_atm_candidate(cand)
        cand.confirmations = 1
        mgr.on_atm_candidate(cand)
        cand.confirmations = 2
        mgr.on_atm_candidate(cand)

        assert mgr.desired.phase in (RotationPhase.SUBSCRIBE_NEW, RotationPhase.WAIT_FOR_FIRST_VALID)

    def test_rotation_preserves_active_during_wait(self):
        mgr = RotationManager(confirm_ticks=1, confirm_duration_ms=0)
        mgr.set_desired_contracts(
            security_ids={1, 2, 3}, contract_ids=["c1", "c2", "c3"],
            symbol_atm={"RELIANCE": 1500},
        )
        active_id = mgr.active.snapshot_id if mgr.active else None

        cand = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
            spot=1512.5, crossing_ratio=0.625, confirmed=True,
        )
        mgr.on_atm_candidate(cand)

        if mgr.active:
            assert mgr.active.snapshot_id == active_id or active_id is None

    def test_packets_during_rotation(self):
        mgr = RotationManager(confirm_ticks=1, confirm_duration_ms=0)
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"RELIANCE": 1500},
        )

        cand = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
            spot=1512.5, crossing_ratio=0.625, confirmed=True,
        )
        mgr.on_atm_candidate(cand)

        mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": 100, "last_trade_volume": 500}, gen=mgr.generation)
        mgr.on_packet("c2", {"ltp": 16.0, "last_trade_time": 101, "last_trade_volume": 600}, gen=mgr.generation)

        mgr.check_rotation_timeout()
        assert mgr.active is not None

    def test_failed_rotation_keeps_active(self):
        mgr = RotationManager(confirm_ticks=1, confirm_duration_ms=0, first_packet_timeout=0.1)
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"RELIANCE": 1500},
        )
        active_id = mgr.active.snapshot_id if mgr.active else "init"

        cand = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
            spot=1512.5, crossing_ratio=0.625, confirmed=True,
        )
        mgr.on_atm_candidate(cand)

        time.sleep(0.2)
        mgr.check_rotation_timeout()

        assert mgr.desired.phase == RotationPhase.ROTATION_FAILED
        if mgr.active:
            assert mgr.active.snapshot_id == active_id

    def test_supersede_stale_rotation(self):
        mgr = RotationManager(confirm_ticks=1, confirm_duration_ms=0)
        mgr.set_desired_contracts(
            security_ids={1}, contract_ids=["c1"],
            symbol_atm={"RELIANCE": 1500},
        )

        cand1 = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
            spot=1510, crossing_ratio=0.6, confirmed=True,
        )
        mgr.on_atm_candidate(cand1)

        cand2 = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1540,
            spot=1530, crossing_ratio=0.8, confirmed=True,
        )
        superseded = mgr.supersede_pending(cand2)
        assert superseded is True

    def test_generation_filtering(self):
        mgr = RotationManager(confirm_ticks=1, confirm_duration_ms=0)
        mgr.set_desired_contracts(
            security_ids={1}, contract_ids=["c1"],
            symbol_atm={"RELIANCE": 1500},
        )

        cand1 = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
            spot=1510, crossing_ratio=0.6, confirmed=True,
        )
        mgr.on_atm_candidate(cand1)
        gen1 = mgr.generation

        cand2 = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1540,
            spot=1530, crossing_ratio=0.8, confirmed=True,
        )
        mgr.supersede_pending(cand2)
        gen2 = mgr.generation

        assert gen2 == gen1

    def test_grace_period_preserves_old(self):
        mgr = RotationManager(confirm_ticks=1, confirm_duration_ms=0, old_universe_grace_period=60)
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"RELIANCE": 1500},
        )

        old_active_id = mgr.active.snapshot_id if mgr.active else None

        cand = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
            spot=1512.5, crossing_ratio=0.625, confirmed=True,
        )
        mgr.on_atm_candidate(cand)
        mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": 100, "last_trade_volume": 500}, gen=mgr.generation)
        mgr.on_packet("c2", {"ltp": 16.0, "last_trade_time": 101, "last_trade_volume": 600}, gen=mgr.generation)

        expired = mgr.check_grace_expiry()
        assert expired is False

    def test_grace_expiry_triggers_unsubscribe(self):
        mgr = RotationManager(confirm_ticks=1, confirm_duration_ms=0, old_universe_grace_period=0)
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"RELIANCE": 1500},
        )
        cand = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
            spot=1512.5, crossing_ratio=0.625, confirmed=True,
        )
        mgr.on_atm_candidate(cand)

        expired = mgr.check_grace_expiry()
        assert expired is True

    def test_events_emitted(self):
        events: List[RotationEvent] = []

        def sink(e: RotationEvent):
            events.append(e)

        mgr = RotationManager(confirm_ticks=1, confirm_duration_ms=0, event_sink=sink)
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"RELIANCE": 1500},
        )
        cand = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
            spot=1512.5, crossing_ratio=0.625, confirmed=True,
        )
        mgr.on_atm_candidate(cand)

        types = [e.event_type.value for e in events]
        assert "ATM_CANDIDATE_DETECTED" in types
        assert "ATM_CANDIDATE_CONFIRMED" in types

    def test_reference_count_integration(self):
        mgr = RotationManager(confirm_ticks=1, confirm_duration_ms=0)
        mgr.set_desired_contracts(
            security_ids={101, 102}, contract_ids=["c1", "c2"],
            symbol_atm={"RELIANCE": 1500},
        )
        assert mgr._ref_counter.count(101) >= 1
        assert mgr._ref_counter.count(102) >= 1

    def test_ref_count_consumer_removal(self):
        c = SubscriptionReferenceCounter()
        c.add(101, "consumer_a")
        c.add(101, "consumer_b")
        assert c.count(101) == 2

        c.remove(101, "consumer_a")
        assert c.count(101) == 1

        c.remove(101, "consumer_b")
        assert c.count(101) == 0

    def test_no_unsubscribe_when_still_needed(self):
        c = SubscriptionReferenceCounter()
        c.add(101, "active_1")
        c.add(101, "old_grace")
        assert c.count(101) == 2

        remaining = c.remove(101, "old_grace")
        assert remaining == 0
        assert c.count(101) == 1
        assert 101 in c.all_ids()

    def test_unsubscribe_when_all_released(self):
        c = SubscriptionReferenceCounter()
        c.add(101, "a")
        c.remove(101, "a")
        assert 101 not in c.all_ids()


# ─── Reconciler tests ──────────────────────────────────────────────────────


class TestReconciler:
    def test_reconciler_no_action_when_healthy(self):
        mgr = RotationManager()
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"RELIANCE": 1500},
        )
        rec = PeriodicReconciler(mgr)
        result = rec._run()
        assert result["health"] == "HEALTHY"
        assert result["repaired"] == 0

    def test_reconciler_detects_missing(self):
        subbed: List[int] = []

        def sub_f(ids):
            subbed.extend(ids)

        mgr = RotationManager(subscribe_fn=sub_f)
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"RELIANCE": 1500},
        )

        mgr.broker_subs = {1}

        rec = PeriodicReconciler(mgr, subscribe_fn=sub_f)
        result = rec._run()

        assert result["repaired"] >= 1

    def test_reconciler_degrades_on_failures(self):
        mgr = RotationManager()
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"RELIANCE": 1500},
        )
        rec = PeriodicReconciler(mgr, thresholds=HealthThresholds(degraded_warning=1, degraded=2, blocked=4))

        mgr.broker_subs = set()
        result = rec._run()
        assert result["health"] == "DEGRADED_WARNING"

        result = rec._run()
        assert result["health"] == "DEGRADED"

        result = rec._run()
        assert result["health"] == "DEGRADED"

        result = rec._run()
        assert result["health"] == "BLOCKED"


class TestNonRegression:
    def test_readiness_switch_never_deadlocks(self):
        import threading
        completed = False

        def run():
            nonlocal completed
            mgr = RotationManager(confirm_ticks=1, confirm_duration_ms=0)
            mgr.set_desired_contracts(
                security_ids={1}, contract_ids=["c1"],
                symbol_atm={"RELIANCE": 1500},
            )
            cand = ATMShiftCandidate(
                underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
                spot=1512.5, crossing_ratio=0.625, confirmed=True,
            )
            mgr.on_atm_candidate(cand)
            mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": 100, "last_trade_volume": 500}, gen=mgr.generation)
            nonlocal completed
            completed = True

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=2.0)
        assert completed, "Thread deadlocked — readiness switch blocked on reentrant lock"

    def test_unregistered_packet_increments_counter(self):
        mgr = RotationManager()
        assert mgr.unregistered_packet_count == 0

        mgr.on_packet("unknown_c1", {"ltp": 15.0, "last_trade_time": 100}, gen=mgr.generation)
        assert mgr.unregistered_packet_count == 1

        mgr.on_packet("unknown_c2", {"ltp": 16.0, "last_trade_time": 101}, gen=mgr.generation)
        assert mgr.unregistered_packet_count == 2

        mgr.set_desired_contracts(
            security_ids={1}, contract_ids=["c1"],
            symbol_atm={"RELIANCE": 1500},
        )
        mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": 100, "last_trade_volume": 500}, gen=mgr.generation)
        assert mgr.unregistered_packet_count == 2


# ─── ATM candidate tests ──────────────────────────────────────────────────


class TestATMShiftCandidate:
    def test_defaults(self):
        c = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
            spot=1512.5, crossing_ratio=0.625,
        )
        assert c.confirmations == 0
        assert c.confirmed is False
        assert c.detected_at > 0

    def test_confirm(self):
        c = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
            spot=1512.5, crossing_ratio=0.625,
        )
        c.confirm()
        assert c.confirmations == 1


# ─── Full rotation test (happy path) ──────────────────────────────────────


class TestFullRotation:
    def test_happy_path_rotation(self):
        events: List[RotationEvent] = []
        subscribed: List[int] = []
        unsubscribed: List[int] = []

        def sub_fn(ids):
            subscribed.extend(ids)

        def unsub_fn(ids):
            unsubscribed.extend(ids)

        def sink(e: RotationEvent):
            events.append(e)

        mgr = RotationManager(
            confirm_ticks=2,
            confirm_duration_ms=0,
            subscription_ack_timeout=5,
            first_packet_timeout=10,
            old_universe_grace_period=0.001,
            subscribe_fn=sub_fn,
            unsubscribe_fn=unsub_fn,
            event_sink=sink,
        )

        mgr.set_desired_contracts(
            security_ids={1, 2, 3, 4, 5},
            contract_ids=[f"c{i}" for i in range(1, 6)],
            symbol_atm={"RELIANCE": 1500},
        )

        assert mgr.phase == RotationPhase.STABLE
        assert len(subscribed) == 0

        cand = ATMShiftCandidate(
            underlying="RELIANCE", old_atm=1500, candidate_atm=1520,
            spot=1512.5, crossing_ratio=0.625,
        )
        mgr.on_atm_candidate(cand)
        assert mgr._candidate is not None

        cand.confirmations = 1
        mgr.on_atm_candidate(cand)
        cand.confirmations = 2
        mgr.on_atm_candidate(cand)
        assert mgr.desired.phase in (RotationPhase.SUBSCRIBE_NEW, RotationPhase.WAIT_FOR_FIRST_VALID)

        for cid in [f"c{i}" for i in range(1, 6)]:
            mgr.on_packet(cid, {
                "ltp": 15.0 + int(hash(cid) % 10),
                "last_trade_time": int(time.time()),
                "last_trade_volume": 1000 + int(hash(cid) % 500),
            }, gen=mgr.generation)

        assert mgr.active is not None

        time.sleep(0.01)
        mgr.check_grace_expiry()

        types = {e.event_type.value for e in events}
        assert "UNIVERSE_SWITCHED" in types
