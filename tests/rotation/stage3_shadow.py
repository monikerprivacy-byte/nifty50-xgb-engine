#!/usr/bin/env python3
"""P2.5.6 Stage 3 — Full live-session shadow soak.

Connects to Dhan WebSocket, decodes live packets, routes through
production RotationManager, persists to BronzeTempWriter.

Usage:
    export DHAN_CLIENT_ID=1110480081
    export DHAN_ACCESS_TOKEN=your_token_here
    python3 -m tests.rotation.stage3_shadow --output /tmp/stage3
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.persistence.bronze_writer import BronzeTempWriter
from src.persistence.envelope import envelope_from_decoded
from src.providers.dhan.websocket import (
    DhanWebSocketClient,
    parse_feed_response,
    QUOTE as DT_QUOTE,
)
from src.rotation.manager import RotationManager
from src.rotation.state import RotationEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("stage3")
logging.getLogger("websockets").setLevel(logging.WARNING)


def _coalesce_metrics(mems):
    return max(mems) if mems else 0.0


@dataclass
class MetricsPoint:
    elapsed: float = 0.0
    packets_decoded: int = 0
    packets_persisted: int = 0
    packets_quarantined: int = 0
    rotations_started: int = 0
    rotations_completed: int = 0
    rotations_failed: int = 0
    subscription_drift: int = 0
    memory_mb: float = 0.0
    disk_free_mb: float = 0.0


MAX_STALL_SECONDS = 300  # longest allowed unexplained packet gap

class Stage3Harness:
    def __init__(self, output_dir: str = "/tmp/stage3"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = self.output_dir / "bronze"
        self.data_dir.mkdir(exist_ok=True)
        self._client: Optional[DhanWebSocketClient] = None
        self.metrics: List[MetricsPoint] = []
        self._start = 0.0
        self._running = False
        self._packets_decoded = 0
        self._packets_persisted = 0
        self._packets_quarantined = 0
        self._rotations_started = 0
        self._rotations_completed = 0
        self._rotations_failed = 0
        self._reconnect_count = 0
        self._drops = 0
        self._violations: List[str] = []

        # continuity tracking
        self._first_packet_at: Optional[float] = None
        self._last_packet_at: Optional[float] = None
        self._longest_gap = 0.0
        self._stall_count = 0
        self._successful_recoveries = 0
        self._pending_recovery = False

        self.writer = BronzeTempWriter(str(self.data_dir))

        # desired_subs — preserved across reconnects
        self._desired_subs: set[int] = set()
        # outstanding_subs — cleared after each successful send
        self._outstanding_subs: set[int] = set()
        self.rotation_mgr = RotationManager(
            confirm_ticks=3,
            confirm_duration_ms=2000,
            first_packet_timeout=10.0,
            rotation_total_timeout=30.0,
            old_universe_grace_period=60.0,
            strict_mode=True,
            subscribe_fn=self._queue_subscribe,
            unsubscribe_fn=self._queue_unsubscribe,
            event_sink=self._on_rotation_event,
        )
        self._sid_to_cid: Dict[int, str] = {}
        self._cid_to_sid: Dict[str, int] = {}

    # -- subscription queueing (called before WS is connected) --

    def _queue_subscribe(self, ids: List[int]):
        self._desired_subs.update(ids)
        self._outstanding_subs.update(ids)
        logger.info(f"Queued subscribe: {len(ids)} ids (total outstanding: {len(self._outstanding_subs)})")

    def _queue_unsubscribe(self, ids: List[int]):
        logger.info(f"Unsubscribe requested: {len(ids)} ids")

    def _on_rotation_event(self, event: RotationEvent):
        et = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        if "SWITCHED" in et:
            self._rotations_completed += 1
            logger.info(f"Rotation completed: {event.old_snapshot_id} -> {event.new_snapshot_id}")
        elif "FAILED" in et:
            self._rotations_failed += 1
            logger.warning(f"Rotation failed: {event.reason}")
        elif "CONFIRMED" in et or "DETECTED" in et:
            self._rotations_started += 1

    # -- main run loop --

    async def run(self, client_id: str, access_token: str):
        self._start = time.time()
        self._running = True

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # seed rotation manager with real NIFTY ATM±5 option contracts
        # Weekly expiry 2026-07-21, strikes 23800-24600 (50-step), CE+PE
        nifty_contracts = [
            (57331, "NIFTY-23800-CE", 23800),
            (57333, "NIFTY-23800-PE", 23800),
            (57336, "NIFTY-23900-CE", 23900),
            (57337, "NIFTY-23900-PE", 23900),
            (57340, "NIFTY-24000-CE", 24000),
            (57341, "NIFTY-24000-PE", 24000),
            (57344, "NIFTY-24100-CE", 24100),
            (57345, "NIFTY-24100-PE", 24100),
            (57348, "NIFTY-24200-CE", 24200),
            (57349, "NIFTY-24200-PE", 24200),
        ]
        security_ids = {sid for sid, _, _ in nifty_contracts}
        contract_ids = [cid for _, cid, _ in nifty_contracts]
        for sid, cid, _ in nifty_contracts:
            self._sid_to_cid[sid] = cid
            self._cid_to_sid[cid] = sid
        self.rotation_mgr.set_desired_contracts(
            security_ids=security_ids,
            contract_ids=contract_ids,
            symbol_atm={"NIFTY": 24000},
        )
        # load outstanding from desired
        self._outstanding_subs = set(self._desired_subs)
        for sid, cid, _ in nifty_contracts:
            self.rotation_mgr.register_contracts([(cid, sid)])

        # build client
        self._client = DhanWebSocketClient(
            client_id=client_id,
            access_token=access_token,
            mode=DT_QUOTE,
        )
        # no callback needed — reading _ws directly in _client_loop

        logger.info(f"Connecting to Dhan WebSocket (pending subs: {len(self._pending_subs)})...")

        try:
            await asyncio.wait_for(
                self._run_session(),
                timeout=36000,
            )
        except asyncio.TimeoutError:
            logger.info("Session timeout (10h) — shutting down")
        except Exception as e:
            logger.error(f"Session error: {e}")
            self._violations.append(f"session_error: {e}")
        finally:
            await self._shutdown()

    async def _run_session(self):
        connect_task = asyncio.create_task(self._client_loop())
        metric_task = asyncio.create_task(self._metric_loop())
        await asyncio.gather(connect_task, metric_task)

    async def _client_loop(self):
        while self._running:
            try:
                await self._client.connect()
                logger.info("WebSocket connected — subscribing")

                # Send all outstanding subscribe requests
                if self._outstanding_subs:
                    ids = sorted(self._outstanding_subs)
                    batch_size = 10
                    for i in range(0, len(ids), batch_size):
                        batch = ids[i:i + batch_size]
                        await self._client.subscribe(batch)
                        logger.info(f"  subscribed {batch[0]}-{batch[-1]} ({len(batch)})")
                        await asyncio.sleep(1.0)
                    self._outstanding_subs.clear()
                    logger.info(f"All {len(ids)} securities subscribed")
                else:
                    logger.info("No outstanding subscriptions to send")

                last_msg = time.time()
                while self._running:
                    try:
                        message = await asyncio.wait_for(
                            self._client._ws.recv(),
                            timeout=30.0,
                        )
                        now = time.time()
                        if self._last_packet_at is not None:
                            gap = now - self._last_packet_at
                            if gap > self._longest_gap:
                                self._longest_gap = gap
                        last_msg = now
                        if isinstance(message, bytes):
                            for record in parse_feed_response(message):
                                await self._process_record(record)
                        elif isinstance(message, str):
                            logger.debug(f"WS text: {message[:100]}")
                    except asyncio.TimeoutError:
                        dead = time.time() - last_msg > 120.0
                        if dead:
                            self._stall_count += 1
                            self._pending_recovery = True
                            logger.warning("No data for 120s — reconnecting")
                            # restore outstanding subs from desired manifest
                            self._outstanding_subs = set(self._desired_subs)
                            break
                        continue

            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._running:
                    break
                self._reconnect_count += 1
                self._pending_recovery = True
                self._outstanding_subs = set(self._desired_subs)
                delay = min(30.0 * self._reconnect_count, 300.0)
                logger.warning(f"Connection failed ({e}) — retry in {delay:.0f}s")
                await asyncio.sleep(delay)

    async def _process_record(self, record: dict):
        now = time.time()
        if self._first_packet_at is None:
            self._first_packet_at = now
        if self._last_packet_at is not None:
            gap = now - self._last_packet_at
            if gap > self._longest_gap:
                self._longest_gap = gap
            if gap > MAX_STALL_SECONDS:
                self._stall_count += 1
        self._last_packet_at = now

        if self._pending_recovery:
            self._successful_recoveries += 1
            self._pending_recovery = False

        sid = record.get("security_id")
        if sid is None:
            self._packets_quarantined += 1
            return
        cid = self._sid_to_cid.get(sid, f"c{sid}")
        gen = self.rotation_mgr.generation
        try:
            self.rotation_mgr.on_packet(cid, record, gen=gen)
        except Exception:
            self._packets_quarantined += 1
            return

        self._packets_decoded += 1

        try:
            raw_bytes = json.dumps(record).encode()
            env = envelope_from_decoded(
                record=record,
                connection_id="stage3_live",
                raw_payload=raw_bytes,
                decoder_version="stage3",
            )
            self.writer.append(env)
            if self._packets_decoded % 5000 == 0:
                self.writer.flush()
        except Exception:
            self._packets_quarantined += 1

    async def _metric_loop(self):
        first = True
        while self._running:
            if first:
                await asyncio.sleep(60)
                first = False
            else:
                await asyncio.sleep(60)
            self._collect_metric()

    def _collect_metric(self):
        elapsed = time.time() - self._start
        mgr = self.rotation_mgr

        mem_mb = 0.0
        try:
            import psutil
            mem_mb = psutil.Process(os.getpid()).memory_info().rss / 1_048_576
        except ImportError:
            pass

        disk_free = 0.0
        try:
            st = os.statvfs(str(self.output_dir))
            disk_free = st.f_frsize * st.f_bavail / 1_048_576
        except Exception:
            pass

        desired_ids = set(mgr.desired.security_ids) if mgr.desired else set()
        actual_ids = set(mgr.broker_subs)
        drift = len(desired_ids - actual_ids)

        mp = MetricsPoint(
            elapsed=elapsed,
            packets_decoded=self._packets_decoded,
            packets_persisted=self._packets_persisted,
            packets_quarantined=self._packets_quarantined,
            rotations_started=self._rotations_started,
            rotations_completed=self._rotations_completed,
            rotations_failed=self._rotations_failed,
            subscription_drift=drift,
            memory_mb=round(mem_mb, 1),
            disk_free_mb=round(disk_free, 1),
        )
        self.metrics.append(mp)

        logger.info(
            f"[{int(elapsed//60):d}m] "
            f"decoded={self._packets_decoded} "
            f"persisted={self._packets_persisted} "
            f"quar={self._packets_quarantined} "
            f"rot={self._rotations_started}/{self._rotations_completed}/{self._rotations_failed} "
            f"drift={drift} "
            f"mem={mp.memory_mb:.0f}MB "
            f"disk={mp.disk_free_mb:.0f}MB"
        )

    def _signal_handler(self, signum, frame):
        logger.info(f"Signal {signum} — shutting down")
        self._running = False

    async def _shutdown(self):
        logger.info("=== Shutdown ===")
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass

        try:
            self.writer.flush()
            self._packets_persisted = self.writer._seq * 5000 + len(self.writer._batch)
        except Exception as e:
            logger.warning(f"Writer flush: {e}")

        # final metric
        self._collect_metric()
        report = self._generate_report()
        rp = self.output_dir / "stage3_report.json"
        with open(rp, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"Report -> {rp}")
        logger.info(f"Result: {report['result']}")

    def _generate_report(self) -> dict:
        mems = [m.memory_mb for m in self.metrics if m.memory_mb > 0]
        peak_mem = _coalesce_metrics(mems)
        start_mem = mems[0] if mems else 0.0
        end_mem = mems[-1] if mems else 0.0
        max_drift = max((m.subscription_drift for m in self.metrics), default=0)

        # continuity metrics
        now = time.time()
        session_duration = now - self._start
        if self._first_packet_at and self._last_packet_at:
            covered_seconds = self._last_packet_at - self._first_packet_at
            coverage_pct = round(100.0 * covered_seconds / max(session_duration, 1), 1)
        else:
            covered_seconds = 0.0
            coverage_pct = 0.0

        violations = list(self._violations)

        result = "PASS"

        if self._drops > 0:
            result = "DROPPED_EVENTS"
            violations.append(f"explicit_drops={self._drops}")

        if self._packets_quarantined > self._packets_decoded * 0.5 and self._packets_decoded > 100:
            result = "HIGH_QUARANTINE"
            violations.append(
                f"quarantine_rate={self._packets_quarantined}/{self._packets_decoded}"
            )

        # Continuity gate: if session was expected to cover market hours and has
        # an unexplained gap exceeding MAX_STALL_SECONDS, the session is a FAIL
        # regardless of other metrics.
        if self._longest_gap > MAX_STALL_SECONDS and self._packets_decoded > 0:
            result = "CONTINUITY_FAIL"
            violations.append(
                f"longest_gap={self._longest_gap:.0f}s exceeds "
                f"MAX_STALL_SECONDS={MAX_STALL_SECONDS}"
            )

        if result == "PASS" and violations:
            result = "PARTIAL_FAIL"

        if not self._running and not violations:
            final_health = "STOPPED_CLEANLY"
        else:
            final_health = result

        return {
            "session_date": time.strftime("%Y-%m-%d"),
            "startup_time": self._start,
            "shutdown_time": now,
            "duration_s": round(session_duration, 1),
            "packets_decoded": self._packets_decoded,
            "packets_persisted": self._packets_persisted,
            "packets_quarantined": self._packets_quarantined,
            "explicit_drops": self._drops,
            "rotations_started": self._rotations_started,
            "rotations_completed": self._rotations_completed,
            "rotations_failed": self._rotations_failed,
            "websocket_reconnects": self._reconnect_count,
            "max_subscription_drift": max_drift,
            "memory_start_mb": round(start_mem, 1),
            "memory_peak_mb": round(peak_mem, 1),
            "memory_end_mb": round(end_mem, 1),
            "first_packet_at": self._first_packet_at,
            "last_packet_at": self._last_packet_at,
            "longest_packet_gap_s": round(self._longest_gap, 1),
            "stall_count": self._stall_count,
            "successful_stream_recoveries": self._successful_recoveries,
            "coverage_seconds": round(covered_seconds, 1),
            "coverage_percent": coverage_pct,
            "final_health": final_health,
            "invariant_violations": violations,
            "result": result,
        }


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="P2.5.6 Stage 3 — Live Shadow Soak")
    parser.add_argument("--output", type=str, default="/tmp/stage3")
    args = parser.parse_args()

    client_id = os.environ.get("DHAN_CLIENT_ID")
    access_token = os.environ.get("DHAN_ACCESS_TOKEN")
    if not client_id or not access_token:
        logger.error("Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN")
        sys.exit(1)

    harness = Stage3Harness(output_dir=args.output)
    await harness.run(client_id=client_id, access_token=access_token)


if __name__ == "__main__":
    asyncio.run(main())
