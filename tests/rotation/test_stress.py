from __future__ import annotations

import threading
import time
from typing import List, Set

import pytest

from src.rotation.manager import RotationManager
from src.rotation.reconciler import PeriodicReconciler
from src.rotation.state import ATMShiftCandidate, RotationPhase


# ─── Helpers ──────────────────────────────────────────────────────────────


def _quick_mgr(**kw) -> RotationManager:
    return RotationManager(
        confirm_ticks=1,
        confirm_duration_ms=0,
        first_packet_timeout=0.1,
        rotation_total_timeout=0.5,
        **kw,
    )


def _send_packets(
    mgr: RotationManager,
    cids: List[str],
    gen: int,
    ltp_base: float = 15.0,
):
    for cid in cids:
        mgr.on_packet(cid, {
            "ltp": ltp_base + hash(cid) % 10,
            "last_trade_time": int(time.time()),
            "last_trade_volume": 1000 + hash(cid) % 500,
        }, gen=gen)


def _check_no_mixed_snapshot(mgr: RotationManager):
    """Invariant: active universe is always a complete snapshot, never mixed."""
    assert mgr.active is not None
    assert mgr.active.snapshot_id is not None
    assert mgr.active.contract_ids is not None
    assert mgr.active.security_ids is not None
    assert mgr.active.phase in (RotationPhase.STABLE, RotationPhase.SWITCH_ACTIVE)


# ─── 1. 50 stocks simultaneously ATM shift ────────────────────────────────


class Test50SimultaneousATMShifts:
    def test_rapid_sequential_rotations(self):
        mgr = _quick_mgr()
        mgr.set_desired_contracts(
            security_ids=set(range(1, 51)),
            contract_ids=[f"c{i}" for i in range(1, 51)],
            symbol_atm={"ROOT": 1000},
        )

        for i in range(50):
            cand = ATMShiftCandidate(
                underlying=f"STOCK{i}", old_atm=1000,
                candidate_atm=1020, spot=1010,
                crossing_ratio=0.6, confirmed=True,
            )
            mgr.on_atm_candidate(cand)
            _check_no_mixed_snapshot(mgr)

        _check_no_mixed_snapshot(mgr)
        assert mgr.phase in (RotationPhase.STABLE, RotationPhase.ROTATION_FAILED, RotationPhase.WAIT_FOR_FIRST_VALID)

    def test_50_rotations_with_packets(self):
        mgr = _quick_mgr()
        mgr.set_desired_contracts(
            security_ids=set(range(1, 51)),
            contract_ids=[f"c{i}" for i in range(1, 51)],
            symbol_atm={"ROOT": 1000},
        )

        for i in range(50):
            cand = ATMShiftCandidate(
                underlying=f"STOCK{i}", old_atm=1000,
                candidate_atm=1020, spot=1010,
                crossing_ratio=0.6, confirmed=True,
            )
            mgr.on_atm_candidate(cand)
            gen = mgr.generation
            _send_packets(mgr, [f"c{j}" for j in range(1, 51)], gen)
            time.sleep(0.001)
            _check_no_mixed_snapshot(mgr)

        _check_no_mixed_snapshot(mgr)


# ─── 2. Rapid up/down ATM oscillation ─────────────────────────────────────


class TestRapidATMOscillation:
    def test_oscillation_supersedes_safely(self):
        mgr = _quick_mgr()
        mgr.set_desired_contracts(
            security_ids={1}, contract_ids=["c1"],
            symbol_atm={"STOCK": 1000},
        )

        for strike in [1020, 980, 1020, 980, 1020, 980, 1020]:
            cand = ATMShiftCandidate(
                underlying="STOCK", old_atm=1000,
                candidate_atm=strike, spot=1000,
                crossing_ratio=0.8, confirmed=True,
            )
            mgr.on_atm_candidate(cand)
            _check_no_mixed_snapshot(mgr)

        assert mgr.phase in (RotationPhase.STABLE, RotationPhase.ROTATION_FAILED, RotationPhase.WAIT_FOR_FIRST_VALID)

    def test_oscillation_with_partial_readiness(self):
        mgr = _quick_mgr()
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"STOCK": 1000},
        )

        for i, strike in enumerate([1020, 980, 1030, 970]):
            gen_before = mgr.generation
            cand = ATMShiftCandidate(
                underlying="STOCK", old_atm=1000,
                candidate_atm=strike, spot=1000,
                crossing_ratio=0.8, confirmed=True,
            )
            mgr.on_atm_candidate(cand)
            gen = mgr.generation

            if gen != gen_before:
                mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": 100, "last_trade_volume": 500}, gen=gen)
                time.sleep(0.001)

            _check_no_mixed_snapshot(mgr)

        _check_no_mixed_snapshot(mgr)


# ─── 3. New generation during rotation warm-up ────────────────────────────


class TestNewGenerationDuringWarmup:
    def test_new_candidate_during_wait(self):
        mgr = _quick_mgr()
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"STOCK": 1000},
        )

        cand1 = ATMShiftCandidate(
            underlying="STOCK", old_atm=1000,
            candidate_atm=1020, spot=1010,
            crossing_ratio=0.6, confirmed=True,
        )
        mgr.on_atm_candidate(cand1)
        gen1 = mgr.generation

        mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": 100, "last_trade_volume": 500}, gen=gen1)

        cand2 = ATMShiftCandidate(
            underlying="STOCK", old_atm=1000,
            candidate_atm=1040, spot=1030,
            crossing_ratio=0.7, confirmed=True,
        )
        mgr.on_atm_candidate(cand2)
        gen2 = mgr.generation

        assert gen2 > gen1
        _check_no_mixed_snapshot(mgr)

        old_active = mgr.active
        mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": 100, "last_trade_volume": 500}, gen=gen2)
        mgr.on_packet("c2", {"ltp": 16.0, "last_trade_time": 101, "last_trade_volume": 600}, gen=gen2)

        _check_no_mixed_snapshot(mgr)
        assert mgr.active is not None
        assert mgr.active.snapshot_id != old_active.snapshot_id


# ─── 4. Duplicate packets ────────────────────────────────────────────────


class TestDuplicatePackets:
    def test_duplicate_packets_no_ill_effects(self):
        mgr = _quick_mgr()
        mgr.set_desired_contracts(
            security_ids={1}, contract_ids=["c1"],
            symbol_atm={"STOCK": 1000},
        )

        cand = ATMShiftCandidate(
            underlying="STOCK", old_atm=1000,
            candidate_atm=1020, spot=1010,
            crossing_ratio=0.6, confirmed=True,
        )
        mgr.on_atm_candidate(cand)
        gen = mgr.generation

        for _ in range(10):
            mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": 100, "last_trade_volume": 500}, gen=gen)
            _check_no_mixed_snapshot(mgr)

        assert mgr.active is not None


# ─── 5. Out-of-order packets ──────────────────────────────────────────────


class TestOutOfOrderPackets:
    def test_out_of_order_no_switch_without_all_ready(self):
        mgr = _quick_mgr()
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"STOCK": 1000},
        )

        cand = ATMShiftCandidate(
            underlying="STOCK", old_atm=1000,
            candidate_atm=1020, spot=1010,
            crossing_ratio=0.6, confirmed=True,
        )
        mgr.on_atm_candidate(cand)
        gen = mgr.generation

        mgr.on_packet("c2", {"ltp": 16.0, "last_trade_time": 101, "last_trade_volume": 600}, gen=gen)
        assert mgr.desired.phase == RotationPhase.WAIT_FOR_FIRST_VALID

        mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": 100, "last_trade_volume": 500}, gen=gen)
        assert mgr.active is not None


# ─── 6. One CE/PE never ready ─────────────────────────────────────────────


class TestOneLegNeverReady:
    def test_missing_leg_blocks_switch(self):
        mgr = _quick_mgr()
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"STOCK": 1000},
        )

        cand = ATMShiftCandidate(
            underlying="STOCK", old_atm=1000,
            candidate_atm=1020, spot=1010,
            crossing_ratio=0.6, confirmed=True,
        )
        mgr.on_atm_candidate(cand)
        gen = mgr.generation

        mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": 100, "last_trade_volume": 500}, gen=gen)
        mgr.on_packet("c1", {"ltp": 15.1, "last_trade_time": 102, "last_trade_volume": 510}, gen=gen)
        mgr.on_packet("c1", {"ltp": 15.2, "last_trade_time": 104, "last_trade_volume": 520}, gen=gen)

        assert mgr.desired.phase == RotationPhase.WAIT_FOR_FIRST_VALID

        time.sleep(0.15)
        mgr.check_rotation_timeout()
        assert mgr.phase == RotationPhase.ROTATION_FAILED
        _check_no_mixed_snapshot(mgr)


# ─── 7. Subscription callback failure ──────────────────────────────────────


class TestSubscriptionCallbackFailure:
    def test_callback_exception_does_not_crash(self):
        events: List[str] = []

        def failing_sub(ids):
            events.append(f"sub_{ids}")
            raise RuntimeError("simulated broker error")

        def failing_unsub(ids):
            events.append(f"unsub_{ids}")
            raise RuntimeError("simulated broker error")

        mgr = RotationManager(
            confirm_ticks=1, confirm_duration_ms=0,
            subscribe_fn=failing_sub,
            unsubscribe_fn=failing_unsub,
        )
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"STOCK": 1000},
        )

        cand = ATMShiftCandidate(
            underlying="STOCK", old_atm=1000,
            candidate_atm=1020, spot=1010,
            crossing_ratio=0.6, confirmed=True,
        )
        mgr.on_atm_candidate(cand)

        _check_no_mixed_snapshot(mgr)

    def test_none_callback_does_not_crash(self):
        mgr = RotationManager(
            confirm_ticks=1, confirm_duration_ms=0,
            subscribe_fn=None, unsubscribe_fn=None,
        )
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"STOCK": 1000},
        )

        cand = ATMShiftCandidate(
            underlying="STOCK", old_atm=1000,
            candidate_atm=1020, spot=1010,
            crossing_ratio=0.6, confirmed=True,
        )
        mgr.on_atm_candidate(cand)
        _check_no_mixed_snapshot(mgr)


# ─── 8. Reconciler and rotation thread concurrently ────────────────────────


class TestReconcilerAndRotationConcurrent:
    def test_reconciler_during_active_rotation(self):
        mgr = _quick_mgr()
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"STOCK": 1000},
        )

        rec = PeriodicReconciler(mgr, subscribe_fn=lambda ids: None)

        cand = ATMShiftCandidate(
            underlying="STOCK", old_atm=1000,
            candidate_atm=1020, spot=1010,
            crossing_ratio=0.6, confirmed=True,
        )
        mgr.on_atm_candidate(cand)
        gen = mgr.generation

        for _ in range(5):
            rec._run()
            _check_no_mixed_snapshot(mgr)

        mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": 100, "last_trade_volume": 500}, gen=gen)
        mgr.on_packet("c2", {"ltp": 16.0, "last_trade_time": 101, "last_trade_volume": 600}, gen=gen)

        rec._run()
        _check_no_mixed_snapshot(mgr)


# ─── 9. Writer slow, queue pressure ───────────────────────────────────────


class TestQueuePressure:
    def test_tight_loop_packets_no_block(self):
        mgr = _quick_mgr()
        mgr.set_desired_contracts(
            security_ids={1}, contract_ids=["c1"],
            symbol_atm={"STOCK": 1000},
        )

        cand = ATMShiftCandidate(
            underlying="STOCK", old_atm=1000,
            candidate_atm=1020, spot=1010,
            crossing_ratio=0.6, confirmed=True,
        )
        mgr.on_atm_candidate(cand)
        gen = mgr.generation

        for _ in range(1000):
            mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": _, "last_trade_volume": 500}, gen=gen)

        _check_no_mixed_snapshot(mgr)
        assert mgr.active is not None

    def test_concurrent_packet_stream(self):
        mgr = _quick_mgr()
        mgr.set_desired_contracts(
            security_ids=set(range(1, 11)),
            contract_ids=[f"c{i}" for i in range(1, 11)],
            symbol_atm={"STOCK": 1000},
        )

        cand = ATMShiftCandidate(
            underlying="STOCK", old_atm=1000,
            candidate_atm=1020, spot=1010,
            crossing_ratio=0.6, confirmed=True,
        )
        mgr.on_atm_candidate(cand)
        gen = mgr.generation

        errors = []

        def send_packets(start, count):
            try:
                for i in range(start, start + count):
                    mgr.on_packet(
                        f"c{i % 10 + 1}",
                        {"ltp": 15.0 + i % 10, "last_trade_time": i, "last_trade_volume": 1000 + i},
                        gen=gen,
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=send_packets, args=(i * 100, 100)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Errors during concurrent packet send: {errors}"
        _check_no_mixed_snapshot(mgr)


# ─── 10. Grace expiry and new rotation simultaneously ─────────────────────


class TestGraceExpiryAndNewRotation:
    def test_grace_expiry_during_new_rotation(self):
        mgr = _quick_mgr(old_universe_grace_period=0.001)
        mgr.set_desired_contracts(
            security_ids={1, 2}, contract_ids=["c1", "c2"],
            symbol_atm={"STOCK": 1000},
        )

        cand1 = ATMShiftCandidate(
            underlying="STOCK", old_atm=1000,
            candidate_atm=1020, spot=1010,
            crossing_ratio=0.6, confirmed=True,
        )
        mgr.on_atm_candidate(cand1)
        gen1 = mgr.generation
        mgr.on_packet("c1", {"ltp": 15.0, "last_trade_time": 100, "last_trade_volume": 500}, gen=gen1)
        mgr.on_packet("c2", {"ltp": 16.0, "last_trade_time": 101, "last_trade_volume": 600}, gen=gen1)

        _check_no_mixed_snapshot(mgr)

        time.sleep(0.005)

        expired = mgr.check_grace_expiry()
        assert expired is True

        cand2 = ATMShiftCandidate(
            underlying="STOCK", old_atm=1000,
            candidate_atm=1040, spot=1030,
            crossing_ratio=0.7, confirmed=True,
        )
        mgr.on_atm_candidate(cand2)

        _check_no_mixed_snapshot(mgr)
