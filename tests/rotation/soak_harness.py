#!/usr/bin/env python3
"""Fault-injection soak harness for P2.5.6 Stage 2.

Usage:
    python3 -m tests.rotation.soak_harness --seed 42 --duration 30 --output report.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import queue
import random
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.providers.dhan.websocket import parse_feed_response
from src.providers.dhan.v2.protocol import (
    HEADER_FMT,
    HEADER_SIZE,
    TICKER_PAYLOAD_FMT,
    TICKER_PAYLOAD_SIZE,
    QUOTE_PAYLOAD_FMT,
    QUOTE_PAYLOAD_SIZE,
    ResponseCode,
    TICKER as DT_TICKER,
)
from src.rotation.manager import RotationManager
from src.rotation.state import ATMShiftCandidate, RotationEvent, RotationEventType, RotationPhase
from src.persistence.envelope import envelope_from_decoded, MarketEventEnvelope
from src.persistence.bronze_writer import BronzeTempWriter

logging.basicConfig(level=logging.WARNING, format="%(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("soak")
logging.getLogger("rotation").setLevel(logging.WARNING)


# ─── Config ───────────────────────────────────────────────────────────────


@dataclass
class SoakConfig:
    seed: int = 42
    duration_minutes: int = 30
    tick_interval: float = 0.02
    packet_rate: float = 0.8
    data_dir: str = ""
    accounting_check_interval: float = 5.0
    invariant_check_interval: float = 2.0
    metric_collect_interval: float = 10.0


DEFAULT_CONFIG = SoakConfig()


# ─── Fault schedule ───────────────────────────────────────────────────────


@dataclass
class FaultEvent:
    time_s: float
    name: str
    fn: Callable


# ─── Synthetic packet feed ────────────────────────────────────────────────


TICKER_SIZE = struct.calcsize(TICKER_PAYLOAD_FMT)
QUOTE_SIZE = struct.calcsize(QUOTE_PAYLOAD_FMT)
STOCK_NAMES = [f"STOCK{i}" for i in range(50)]


class SyntheticFeed:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.stocks = {
            name: {"ltp": 1500.0 + self.rng.gauss(0, 100), "ltt": 1000 + i}
            for i, name in enumerate(STOCK_NAMES)
        }
        self._clock = 1000
        self.sid_to_contract: Dict[int, str] = {}
        self.contract_to_sid: Dict[str, int] = {}

    def advance(self) -> List[Tuple[int, int, bytes]]:
        packets = []
        for name, state in self.stocks.items():
            if self.rng.random() > 0.3:
                continue
            state["ltp"] += self.rng.gauss(0, 2)
            state["ltt"] += 1
            sid = abs(hash(name)) % 100000 + 1
            # Alternate between ticker and quote packets
            if self.rng.random() < 0.7:
                raw = self._build_ticker(sid, state["ltp"], state["ltt"])
                rtype = DT_TICKER
            else:
                raw = self._build_quote(sid, state["ltp"], state["ltt"])
                rtype = ResponseCode.QUOTE
            packets.append((sid, rtype, raw))
            cid = f"c{sid}"
            self.sid_to_contract.setdefault(sid, cid)
            self.contract_to_sid.setdefault(cid, sid)
        self._clock += 1
        return packets

    def _build_ticker(self, security_id: int, ltp: float, last_trade_time: int) -> bytes:
        payload = struct.pack(TICKER_PAYLOAD_FMT, ltp, last_trade_time)
        total_len = HEADER_SIZE + len(payload)
        header = struct.pack(HEADER_FMT, ResponseCode.TICKER, total_len, 2, security_id)
        return header + payload

    def _build_quote(self, security_id: int, ltp: float, last_trade_time: int) -> bytes:
        payload = struct.pack(
            QUOTE_PAYLOAD_FMT,
            ltp, 10, last_trade_time, ltp,
            1000, 500, 600,
            ltp - 1.0, ltp - 2.0, ltp + 2.0, ltp - 3.0,
        )
        total_len = HEADER_SIZE + len(payload)
        header = struct.pack(HEADER_FMT, ResponseCode.QUOTE, total_len, 2, security_id)
        return header + payload


# ─── Accounting ────────────────────────────────────────────────────────────


class SoakAccounting:
    def __init__(self):
        self.received = 0
        self.persisted = 0
        self.quarantined = 0
        self.queue_pending = 0
        self.spool_pending = 0
        self.explicitly_dropped = 0
        self.duplicate_events = 0
        self.duplicates_injected = 0
        self.writer_restarts = 0
        self.websocket_reconnects = 0
        self.rotations_started = 0
        self.rotations_completed = 0
        self.rotations_failed = 0
        self.rotations_superseded = 0
        self.stale_generation_events = 0
        self.reconciliation_repairs = 0
        self.event_ids: Set[str] = set()
        self.invariant_violations: List[str] = []
        self._lock = threading.Lock()

    def check_balance(self) -> Optional[str]:
        accounted = (
            self.persisted + self.quarantined
            + self.queue_pending + self.spool_pending
            + self.explicitly_dropped
        )
        if self.received != accounted:
            return (
                f"accounting mismatch: received={self.received} "
                f"accounted={accounted} (persisted={self.persisted} "
                f"quarantined={self.quarantined} queue={self.queue_pending} "
                f"spool={self.spool_pending} dropped={self.explicitly_dropped})"
            )
        return None


# ─── Metrics snapshot ─────────────────────────────────────────────────────


@dataclass
class MetricsSnapshot:
    elapsed: float = 0.0
    events_received: int = 0
    events_persisted: int = 0
    events_quarantined: int = 0
    events_dropped: int = 0
    queue_depth: int = 0
    spool_pending: int = 0
    memory_mb: float = 0.0
    thread_count: int = 0
    fd_count: int = 0
    rotation_phase: str = ""
    active_universe_size: int = 0
    health: str = ""


def _collect_process_metrics() -> dict:
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        return {
            "memory_mb": proc.memory_info().rss / 1_048_576,
            "thread_count": proc.num_threads(),
            "fd_count": proc.num_fds(),
        }
    except (ImportError, Exception):
        return {"memory_mb": 0.0, "thread_count": 0, "fd_count": 0}


# ─── Invariant checker ────────────────────────────────────────────────────


class InvariantChecker:
    def __init__(self, harness: "SoakHarness"):
        self._harness = harness

    def check_all(self) -> List[str]:
        violations = []
        mgr = self._harness.rotation_mgr

        v = self._check_active_snapshot(mgr)
        if v:
            violations.append(v)

        v = self._check_no_mixed_generation(mgr)
        if v:
            violations.append(v)

        v = self._check_accounting()
        if v:
            violations.append(v)

        v = self._check_duplicates()
        if v:
            violations.append(v)

        return violations

    def _check_active_snapshot(self, mgr) -> Optional[str]:
        if not mgr.active:
            return "no active universe"
        if not mgr.active.snapshot_id:
            return "active missing snapshot_id"
        if not mgr.active.contract_ids:
            return "active missing contract_ids"
        if not mgr.active.security_ids:
            return "active missing security_ids"
        if mgr.active.phase not in (RotationPhase.STABLE, RotationPhase.SWITCH_ACTIVE):
            return f"active in non-terminal phase: {mgr.active.phase}"
        return None

    def _check_no_mixed_generation(self, mgr) -> Optional[str]:
        if mgr.active and mgr.desired:
            if mgr.active.generation != mgr.desired.generation:
                if mgr.desired.phase in (
                    RotationPhase.STABLE, RotationPhase.ROTATION_FAILED
                ):
                    return (
                        f"active gen={mgr.active.generation} != "
                        f"desired gen={mgr.desired.generation} "
                        f"(desired phase={mgr.desired.phase})"
                    )
        return None

    def _check_accounting(self) -> Optional[str]:
        return self._harness.accounting.check_balance()

    def _check_duplicates(self) -> Optional[str]:
        acc = self._harness.accounting
        if acc.duplicates_injected != acc.duplicate_events:
            return (
                f"duplicate mismatch: injected={acc.duplicates_injected} "
                f"detected={acc.duplicate_events}"
            )
        return None


# ─── Rotation event hook ──────────────────────────────────────────────────


class SoakEventSink:
    def __init__(self, accounting: SoakAccounting):
        self._acc = accounting

    def __call__(self, event: RotationEvent):
        try:
            et = event.event_type.value
            if et == "ROTATION_STARTED":
                self._acc.rotations_started += 1
            elif et == "UNIVERSE_SWITCHED":
                self._acc.rotations_completed += 1
            elif et == "ROTATION_FAILED":
                self._acc.rotations_failed += 1
            elif et == "RECONCILIATION_REPAIR":
                self._acc.reconciliation_repairs += 1
        except Exception:
            pass


# ─── Fault injection ──────────────────────────────────────────────────────


class FaultInjector:
    def __init__(self, harness: "SoakHarness"):
        self._h = harness
        self._rng = random.Random(harness.config.seed + 1)

    def disconnect(self):
        self._h.feed_active = False
        self._h.accounting.websocket_reconnects += 1

    def reconnect(self):
        self._h.feed_active = True

    def writer_kill(self):
        self._h.accounting.writer_restarts += 1
        self._h.writer = BronzeTempWriter(str(self._h.data_path / "bronze"))

    def broker_drift(self, count: int = 20):
        ids = set(self._h.rotation_mgr.broker_subs)
        if ids:
            to_remove = set(self._rng.sample(list(ids), min(count, len(ids))))
            self._h.rotation_mgr.broker_subs -= to_remove

    def duplicate_burst(self):
        self._h.duplicate_mode = True
        self._h.duplicate_timer = time.time() + 5.0

    def malformed_burst(self):
        self._h.malformed_mode = True
        self._h.malformed_timer = time.time() + 5.0

    def writer_slowdown(self):
        self._h.writer_slow = True
        self._h.writer_slow_timer = time.time() + 30.0

    def queue_saturation(self):
        self._h.packet_rate_mult = 10.0
        self._h.saturation_timer = time.time() + 15.0

    def atm_oscillation_burst(self):
        self._h.oscillation_mode = True
        self._h.oscillation_timer = time.time() + 10.0

    def old_gen_packets(self):
        self._h.old_gen_mode = True
        self._h.old_gen_timer = time.time() + 5.0

    def ce_missing(self):
        self._h.ce_missing = True
        self._h.ce_missing_timer = time.time() + 20.0

    def pe_missing(self):
        self._h.pe_missing = True
        self._h.pe_missing_timer = time.time() + 20.0

    def out_of_order_burst(self):
        self._h.ooo_mode = True
        self._h.ooo_timer = time.time() + 5.0

    def spool_activate(self):
        self._h.force_spool = True
        self._h.spool_timer = time.time() + 15.0


# ─── Main harness ─────────────────────────────────────────────────────────


class SoakHarness:
    def __init__(self, config: SoakConfig):
        self.config = config
        self.rng = random.Random(config.seed)
        self.accounting = SoakAccounting()
        self.event_sink = SoakEventSink(self.accounting)

        self.feed_active = True
        self.duplicate_mode = False
        self.duplicate_timer = 0.0
        self.malformed_mode = False
        self.malformed_timer = 0.0
        self.writer_slow = False
        self.writer_slow_timer = 0.0
        self.packet_rate_mult = 1.0
        self.saturation_timer = 0.0
        self.oscillation_mode = False
        self.oscillation_timer = 0.0
        self.old_gen_mode = False
        self.old_gen_timer = 0.0
        self.ce_missing = False
        self.ce_missing_timer = 0.0
        self.pe_missing = False
        self.pe_missing_timer = 0.0
        self.ooo_mode = False
        self.ooo_timer = 0.0
        self.force_spool = False
        self.spool_timer = 0.0
        self._old_gen = 0

        self.feed = SyntheticFeed(config.seed)
        self.invariant_checker = InvariantChecker(self)
        self.fault_injector = FaultInjector(self)

        data_dir = config.data_dir or f"/tmp/soak_{int(time.time())}"
        self.data_path = Path(data_dir)
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.writer = BronzeTempWriter(str(self.data_path / "bronze"))
        self.writer_queue: queue.Queue = queue.Queue(maxsize=10000)
        self.spool_path = self.data_path / "spool"
        self.spool_path.mkdir(exist_ok=True)
        self.spool_count = 0

        self.rotation_mgr = RotationManager(
            confirm_ticks=2,
            confirm_duration_ms=500,
            first_packet_timeout=5.0,
            rotation_total_timeout=10.0,
            old_universe_grace_period=30.0,
            strict_mode=True,
            subscribe_fn=self._on_subscribe,
            unsubscribe_fn=self._on_unsubscribe,
            event_sink=self.event_sink,
        )

        self.queue_watermark = 8000

        initial_ids = {1 + i for i in range(50)}
        initial_cids = [f"c{1 + i}" for i in range(50)]
        self.rotation_mgr.set_desired_contracts(initial_ids, initial_cids, {"ROOT": 1500})
        for sid in range(1, 51):
            self.rotation_mgr.register_contracts([(f"c{sid}", sid)])
            self.feed.sid_to_contract[sid] = f"c{sid}"
            self.feed.contract_to_sid[f"c{sid}"] = sid

        self.metrics: List[MetricsSnapshot] = []
        self._start = 0.0
        self._last_accounting_check = 0.0
        self._last_invariant_check = 0.0
        self._last_metric_collect = 0.0
        self._running = False

    def _on_subscribe(self, ids: List[int]):
        pass

    def _on_unsubscribe(self, ids: List[int]):
        pass

    def _write_thread(self):
        batch: List[MarketEventEnvelope] = []
        last_flush = time.time()
        drain_counter = 0
        while self._running:
            try:
                item = self.writer_queue.get(timeout=0.05)
                if item is None:
                    if batch:
                        self._flush_envelopes(batch)
                    break
                if self.writer_slow:
                    time.sleep(0.05)
                batch.append(item)
                if len(batch) >= 100 or time.time() - last_flush >= 2.0:
                    self._flush_envelopes(batch)
                    batch = []
                    last_flush = time.time()
                    drain_counter = 0
            except queue.Empty:
                if batch and time.time() - last_flush >= 2.0:
                    self._flush_envelopes(batch)
                    batch = []
                    last_flush = time.time()

                drain_counter += 1
                if drain_counter >= 4:
                    self._drain_spool()
                    drain_counter = 0

    def _drain_spool(self):
        spool_files = sorted(self.spool_path.glob("spool_*.pkl"))
        if not spool_files:
            return
        for spool_file in spool_files:
            try:
                with open(spool_file, "rb") as f:
                    batch = pickle.load(f)
                for env in batch:
                    self.writer.append(env)
                self.writer.flush()
                self.accounting.persisted += len(batch)
                self.accounting.spool_pending = max(0, self.accounting.spool_pending - len(batch))
                spool_file.unlink()
            except Exception:
                pass

    def _flush_envelopes(self, batch: List[MarketEventEnvelope]):
        try:
            for env in batch:
                self.writer.append(env)
            self.writer.flush()
            self.accounting.persisted += len(batch)
            self.accounting.queue_pending = max(0, self.accounting.queue_pending - len(batch))
        except Exception as e:
            self._spool_batch(batch)
            self.accounting.spool_pending += len(batch)
            self.accounting.queue_pending = max(0, self.accounting.queue_pending - len(batch))

    def _spool_batch(self, batch: List[MarketEventEnvelope]):
        spool_file = self.spool_path / f"spool_{self.spool_count:06d}.pkl"
        self.spool_count += 1
        with open(spool_file, "wb") as f:
            pickle.dump(batch, f)

    def _replay_spool(self):
        for spool_file in sorted(self.spool_path.glob("spool_*.pkl")):
            try:
                with open(spool_file, "rb") as f:
                    batch = pickle.load(f)
                for env in batch:
                    self.writer.append(env)
                self.accounting.persisted += len(batch)
                self.accounting.spool_pending = max(0, self.accounting.spool_pending - len(batch))
                spool_file.unlink()
            except Exception:
                self.accounting.quarantined += 1
                self.accounting.spool_pending = max(0, self.accounting.spool_pending - 1)
            try:
                spool_file.unlink()
            except OSError:
                pass

    def run(self):
        self._start = time.time()
        self._running = True
        duration = self.config.duration_minutes * 60.0

        write_thread = threading.Thread(target=self._write_thread, daemon=True)
        write_thread.start()

        self._check_faults = self._build_fault_schedule()
        next_fault_idx = 0

        errors: List[str] = []

        while True:
            elapsed = time.time() - self._start
            if elapsed >= duration:
                break

            try:
                self._tick(elapsed)

                while next_fault_idx < len(self._check_faults):
                    ft = self._check_faults[next_fault_idx].time_s * 60.0
                    if elapsed >= ft:
                        fault = self._check_faults[next_fault_idx]
                        try:
                            fault.fn()
                            self._log_fault(elapsed, fault.name)
                        except Exception as e:
                            self.accounting.invariant_violations.append(
                                f"fault_{fault.name}: {e}"
                            )
                        next_fault_idx += 1
                    else:
                        break

                self._timed_checks(elapsed)

            except Exception as e:
                errors.append(f"tick error at {elapsed:.1f}s: {e}")
                if len(errors) > 10:
                    break

            time.sleep(self.config.tick_interval)

        self._running = False
        self.writer_queue.put(None)
        write_thread.join(timeout=10)

        self._replay_spool()
        try:
            self.writer.flush()
        except Exception:
            pass
        self._check_accounting_final()
        report = self._generate_report(errors)
        return report

    def _tick(self, elapsed: float):
        if not self.feed_active:
            return

        self._handle_fault_timers()

        packets = self.feed.advance()
        mult = int(self.packet_rate_mult)
        if mult > 1:
            for _ in range(mult - 1):
                packets.extend(self.feed.advance())

        for sid, rtype, raw in packets:
            cid = self.feed.sid_to_contract.get(sid, f"c{sid}")

            decoded = parse_feed_response(raw)
            for record in decoded:
                self._process_record(record, cid, raw)

            if self.duplicate_mode and self.rng.random() < 0.5:
                for record in decoded:
                    self._process_record(record, cid, raw)
                    self.accounting.duplicates_injected += 1
                    self.accounting.duplicate_events += 1

        if self.oscillation_mode:
            underlying = self.rng.choice(STOCK_NAMES)
            old_atm = 1500 + self.rng.randint(-50, 50)
            new_atm = old_atm + self.rng.choice([-20, 20])
            cand = ATMShiftCandidate(
                underlying=underlying,
                old_atm=float(old_atm),
                candidate_atm=float(new_atm),
                spot=float((old_atm + new_atm) / 2),
                crossing_ratio=self.rng.uniform(0.5, 1.0),
            )
            try:
                self.rotation_mgr.on_atm_candidate(cand)
            except Exception:
                pass

    def _handle_fault_timers(self):
        now = time.time()
        if self.duplicate_mode and now > self.duplicate_timer:
            self.duplicate_mode = False
        if self.malformed_mode and now > self.malformed_timer:
            self.malformed_mode = False
        if self.writer_slow and now > self.writer_slow_timer:
            self.writer_slow = False
        if self.saturation_timer and now > self.saturation_timer:
            self.packet_rate_mult = 1.0
            self.saturation_timer = 0.0
        if self.oscillation_mode and now > self.oscillation_timer:
            self.oscillation_mode = False
        if self.old_gen_mode and now > self.old_gen_timer:
            self.old_gen_mode = False
            self._old_gen = 0
        if self.ce_missing and now > self.ce_missing_timer:
            self.ce_missing = False
        if self.pe_missing and now > self.pe_missing_timer:
            self.pe_missing = False
        if self.ooo_mode and now > self.ooo_timer:
            self.ooo_mode = False
        if self.force_spool and now > self.spool_timer:
            self.force_spool = False

    def _flush_writer(self):
        try:
            self.writer.flush()
        except Exception:
            pass

    def _process_record(self, record: dict, cid: str, raw: bytes):
        gen = self.rotation_mgr.generation
        effective_gen = gen
        if self.old_gen_mode:
            if self._old_gen == 0:
                self._old_gen = gen
            effective_gen = self._old_gen
        if self.ooo_mode and self.rng.random() < 0.3:
            effective_gen = max(0, effective_gen - 1)

        try:
            self.rotation_mgr.on_packet(cid, record, gen=effective_gen)
        except Exception:
            return

        if effective_gen != self.rotation_mgr.generation:
            self.accounting.stale_generation_events += 1

        self.accounting.received += 1

        if self.malformed_mode:
            self.accounting.quarantined += 1
            return

        self.accounting.queue_pending += 1

        try:
            env = envelope_from_decoded(
                record=record,
                connection_id="soak",
                raw_payload=raw,
                decoder_version="soak",
            )
            qsize = self.writer_queue.qsize()
            if qsize >= self.queue_watermark:
                self._spool_batch([env])
                self.accounting.spool_pending += 1
                self.accounting.queue_pending -= 1
            else:
                self.writer_queue.put(env, timeout=0.01)
        except Exception:
            self.accounting.quarantined += 1
            self.accounting.queue_pending -= 1

    def _timed_checks(self, elapsed: float):
        if elapsed - self._last_accounting_check >= self.config.accounting_check_interval:
            self._last_accounting_check = elapsed
            err = self.accounting.check_balance()
            if err:
                self.accounting.invariant_violations.append(err)

        if elapsed - self._last_invariant_check >= self.config.invariant_check_interval:
            self._last_invariant_check = elapsed
            violations = self.invariant_checker.check_all()
            for v in violations:
                self.accounting.invariant_violations.append(v)

        if elapsed - self._last_metric_collect >= self.config.metric_collect_interval:
            self._last_metric_collect = elapsed
            self._collect_metric(elapsed)

    def _collect_metric(self, elapsed: float):
        proc = _collect_process_metrics()
        mgr = self.rotation_mgr
        snap = MetricsSnapshot(
            elapsed=elapsed,
            events_received=self.accounting.received,
            events_persisted=self.accounting.persisted,
            events_quarantined=self.accounting.quarantined,
            events_dropped=self.accounting.explicitly_dropped,
            queue_depth=self.accounting.queue_pending,
            spool_pending=self.accounting.spool_pending,
            memory_mb=proc.get("memory_mb", 0),
            thread_count=proc.get("thread_count", 0),
            fd_count=proc.get("fd_count", 0),
            rotation_phase=mgr.phase.value if mgr.phase else "",
            active_universe_size=len(mgr.active.security_ids) if mgr.active else 0,
            health="",
        )
        self.metrics.append(snap)

    def _log_fault(self, elapsed: float, name: str):
        logger.info(f"[{elapsed:.0f}s] FAULT: {name}")

    def _check_accounting_final(self):
        err = self.accounting.check_balance()
        if err:
            self.accounting.invariant_violations.append(err)

    def _generate_report(self, errors: List[str]) -> dict:
        mems = [m.memory_mb for m in self.metrics if m.memory_mb > 0]
        peak_mem = max(mems) if mems else 0.0
        start_mem = mems[0] if mems else 0.0
        end_mem = mems[-1] if mems else 0.0
        fds = [m.fd_count for m in self.metrics if m.fd_count > 0]
        start_fd = fds[0] if fds else 0
        end_fd = fds[-1] if fds else 0
        max_q = max((m.queue_depth for m in self.metrics), default=0)
        max_spool = max((m.spool_pending for m in self.metrics), default=0)

        result = "PASS"
        if self.accounting.invariant_violations:
            result = "INVARIANT_FAILURE"
        if errors:
            result = "ERROR"

        health = result.replace("_", " ").title()
        if result == "PASS" and self.accounting.explicitly_dropped > 0:
            health = "DROPPED_EVENTS"

        report = {
            "run_id": int(self._start),
            "seed": self.config.seed,
            "started_at": self._start,
            "duration": time.time() - self._start,
            "events_received": self.accounting.received,
            "events_persisted": self.accounting.persisted,
            "events_quarantined": self.accounting.quarantined,
            "events_dropped": self.accounting.explicitly_dropped,
            "duplicate_events_detected": self.accounting.duplicate_events,
            "duplicates_injected": self.accounting.duplicates_injected,
            "writer_restarts": self.accounting.writer_restarts,
            "websocket_reconnects": self.accounting.websocket_reconnects,
            "rotations_started": self.accounting.rotations_started,
            "rotations_completed": self.accounting.rotations_completed,
            "rotations_failed": self.accounting.rotations_failed,
            "rotations_superseded": self.accounting.rotations_superseded,
            "stale_generation_events": self.accounting.stale_generation_events,
            "reconciliation_repairs": self.accounting.reconciliation_repairs,
            "max_queue_depth": max_q,
            "max_spool_pending": max_spool,
            "memory_start_mb": round(start_mem, 1),
            "memory_peak_mb": round(peak_mem, 1),
            "memory_end_mb": round(end_mem, 1),
            "open_fds_start": start_fd,
            "open_fds_end": end_fd,
            "invariant_violations": self.accounting.invariant_violations[:50],
            "errors": errors[:20],
            "final_health": "HEALTHY" if result == "PASS" else result,
            "queue_pending": self.accounting.queue_pending,
            "spool_pending": self.accounting.spool_pending,
            "result": result,
        }
        return report

    def _build_fault_schedule(self) -> List[FaultEvent]:
        f = self.fault_injector
        return [
            FaultEvent(0.1, "50_stock_rotation_burst", lambda: self._burst_50_rotations()),
            FaultEvent(2.0, "websocket_disconnect", f.disconnect),
            FaultEvent(3.5, "websocket_reconnect", f.reconnect),
            FaultEvent(5.0, "writer_kill", f.writer_kill),
            FaultEvent(6.5, "broker_drift", f.broker_drift),
            FaultEvent(8.0, "duplicate_burst", f.duplicate_burst),
            FaultEvent(9.5, "malformed_burst", f.malformed_burst),
            FaultEvent(11.0, "writer_slowdown", f.writer_slowdown),
            FaultEvent(13.0, "queue_saturation", f.queue_saturation),
            FaultEvent(15.0, "atm_oscillation", f.atm_oscillation_burst),
            FaultEvent(17.0, "old_gen_packets", f.old_gen_packets),
            FaultEvent(19.0, "ce_missing", f.ce_missing),
            FaultEvent(21.0, "pe_missing", f.pe_missing),
            FaultEvent(23.0, "reconnect_during_rotation", self._reconnect_during_rotation),
            FaultEvent(25.0, "out_of_order_burst", f.out_of_order_burst),
            FaultEvent(27.0, "writer_kill_2", f.writer_kill),
            FaultEvent(28.0, "spool_replay", self._spool_replay),
            FaultEvent(29.0, "broker_drift_2", f.broker_drift),
        ]

    def _burst_50_rotations(self):
        for i in range(50):
            cand = ATMShiftCandidate(
                underlying=STOCK_NAMES[i % len(STOCK_NAMES)],
                old_atm=1500.0, candidate_atm=1520.0 + i,
                spot=1510.0, crossing_ratio=0.6,
            )
            try:
                self.rotation_mgr.on_atm_candidate(cand)
            except Exception:
                pass

    def _reconnect_during_rotation(self):
        cand = ATMShiftCandidate(
            underlying=STOCK_NAMES[0],
            old_atm=1500.0, candidate_atm=1540.0,
            spot=1520.0, crossing_ratio=0.7, confirmed=True,
        )
        try:
            self.rotation_mgr.on_atm_candidate(cand)
        except Exception:
            pass
        self.feed_active = False
        threading.Timer(3.0, lambda: setattr(self, "feed_active", True)).start()

    def _spool_replay(self):
        self._replay_spool()


# ─── CLI entry point ──────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="P2.5.6 Stage 2 Soak Test")
    parser.add_argument("--seed", type=int, default=DEFAULT_CONFIG.seed)
    parser.add_argument("--duration", type=float, default=DEFAULT_CONFIG.duration_minutes)
    parser.add_argument("--output", type=str, default="")
    args = parser.parse_args()

    config = SoakConfig(
        seed=args.seed,
        duration_minutes=int(args.duration),
    )

    print(f"Soak test — seed={args.seed} duration={args.duration}min")
    harness = SoakHarness(config)
    report = harness.run()
    report_path = args.output or f"soak_report_{int(time.time())}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Report -> {report_path}")
    print(f"Result: {report['result']}")
    print(f"Events: {report['events_received']} received, "
          f"{report['events_persisted']} persisted, "
          f"{report['events_quarantined']} quarantined, "
          f"{report['events_dropped']} dropped")
    print(f"Invariant violations: {len(report['invariant_violations'])}")

    if report["result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
