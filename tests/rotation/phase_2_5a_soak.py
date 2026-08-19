#!/usr/bin/env python3
"""Phase 2.5A closure — Full-session controlled soak with reconnect validation.

Reads manifest for security IDs, thresholds, and forced-event schedule.
Usage:
    python3 -m tests.rotation.phase_2_5a_soak --output artifacts/phase_2_5a/2026-07-20
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.providers.dhan.v2.client import DhanWebSocketClient, HealthState
from src.providers.dhan.v2.protocol import QUOTE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("p2.5a")
logging.getLogger("websockets").setLevel(logging.WARNING)

MANIFEST_PATH = Path(__file__).resolve().parent / "phase_2_5a_manifest.json"


def _load_manifest() -> dict:
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def _config_hash(manifest: dict) -> str:
    raw = json.dumps(manifest, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def controlled_disconnect(client: DhanWebSocketClient):
    logger.info("[EVENT] Forced disconnect")
    await client._close_existing_socket()
    client._connection_generation += 1


async def main():
    manifest = _load_manifest()
    output_dir = (
        sys.argv[sys.argv.index("--output") + 1]
        if "--output" in sys.argv
        else "/tmp/p2.5a"
    )
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    client_id = os.environ.get("DHAN_CLIENT_ID")
    access_token = os.environ.get("DHAN_ACCESS_TOKEN")
    if not client_id or not access_token:
        logger.error("Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN")
        sys.exit(1)

    run_id = (
        f"p2.5a-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        f"-{uuid.uuid4().hex[:6]}"
    )
    desired_ids = manifest["desired_universe"]["security_ids"]
    critical_ids: set[int] = set()
    for cat in manifest["critical_universe"].values():
        if isinstance(cat, list):
            critical_ids.update(int(s) for s in cat)
    c_hash = _config_hash(manifest)

    client = DhanWebSocketClient(
        client_id=client_id,
        access_token=access_token,
        mode=QUOTE,
    )

    desired_set = set(desired_ids)
    for sid in desired_set:
        client.add_subscription(sid)

    schedule = manifest["forced_events"]["schedule"]
    stall_warn = manifest["stall_thresholds"]["stall_warning_seconds"]
    stall_max = manifest["stall_thresholds"]["max_stall_seconds"]

    # --- shared mutable state ---
    start_time = time.time()
    event_index = 0
    reconnect_count = 0
    successful_recoveries = 0
    restore_latencies: list[float] = []
    first_packet_at: Optional[float] = None
    last_packet_at: Optional[float] = None
    longest_gap = 0.0
    total_stalled = 0.0
    seen_set: set[int] = set()
    violations: list[str] = []
    prev_health: Optional[str] = None
    stall_start: Optional[float] = None
    read_active = True

    logger.info(f"Run ID: {run_id}")
    logger.info(f"Config hash: {c_hash}")
    logger.info(f"Desired security IDs: {len(desired_ids)}")

    async def on_records(records: list[dict]):
        nonlocal first_packet_at, last_packet_at, longest_gap, total_stalled
        nonlocal stall_start, seen_set

        now = time.time()
        if first_packet_at is None:
            first_packet_at = now
            sids = [r.get("security_id") for r in records if r.get("security_id")]
            logger.info(f"First packet(s) received: {sids}")

        for r in records:
            sid = r.get("security_id")
            if sid is not None:
                seen_set.add(int(sid))

        if last_packet_at is not None:
            gap = now - last_packet_at
            if gap > longest_gap:
                longest_gap = gap
            if gap > stall_warn and gap <= stall_max:
                if stall_start is None:
                    stall_start = now
                sid = records[0].get("security_id", "?")
                logger.warning(f"Stall detected: {gap:.1f}s gap before security {sid}")
            elif gap > stall_max:
                sid = records[0].get("security_id", "?")
                violations.append(f"stall_exceeded: {gap:.1f}s gap at security {sid}")
        last_packet_at = now
        if stall_start is not None:
            total_stalled += now - stall_start
            stall_start = None

    client.set_callback(on_records)

    # --- read loop that survives forced disconnects ---
    async def resilient_read_loop():
        nonlocal read_active
        while read_active:
            try:
                await client._run_forever()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug(f"Read loop exited ({exc}), will restart if connected")
            await asyncio.sleep(0.2)

    # --- connect ---
    try:
        await client.connect()
    except Exception as e:
        logger.error(f"Initial connect failed: {e}")
        violations.append(f"connect_failed: {e}")
        await client._close_existing_socket()
        report = {
            "run_id": run_id,
            "config_hash": c_hash,
            "start_time": start_time,
            "end_time": time.time(),
            "result": "CONNECT_FAIL",
            "violations": violations,
        }
        (output_path / f"{run_id}_report.json").write_text(json.dumps(report, indent=2, default=str))
        return

    logger.info("Connected. Starting forced-event schedule...")

    # --- event loop ---
    async def event_loop():
        nonlocal event_index, reconnect_count, successful_recoveries
        while True:
            if event_index >= len(schedule):
                await asyncio.sleep(10)
                continue
            event = schedule[event_index]
            elapsed = time.time() - start_time
            if elapsed >= event["time_after_start_s"]:
                event_index += 1
                etype = event["event"]
                logger.info(f"[EVENT {event_index}/{len(schedule)}] {etype}: {event['description']}")
                if etype == "forced_disconnect":
                    disconnect_start = time.time()
                    await controlled_disconnect(client)
                    reconnect_count += 1
                    await asyncio.sleep(0.5)
                    try:
                        await client.connect()
                        await asyncio.sleep(2)
                        ok_states = {
                            HealthState.CONNECTED, HealthState.SUBSCRIPTIONS_SENT,
                            HealthState.FIRST_PACKET_RECEIVED, HealthState.STREAMING_HEALTHY,
                        }
                        if client.health in ok_states:
                            successful_recoveries += 1
                            restore_lat = time.time() - disconnect_start
                            restore_latencies.append(restore_lat)
                            logger.info(
                                f"Reconnect {reconnect_count} OK "
                                f"(restore latency: {restore_lat:.1f}s)"
                            )
                        else:
                            violations.append(
                                f"reconnect_failed: health={client.health.value}"
                            )
                    except Exception as e:
                        violations.append(f"reconnect_exception: {e}")
                elif etype == "universe_grow":
                    for sid in [57331, 57332, 57333]:
                        client.add_subscription(sid)
                    await client.subscribe([57331, 57332, 57333])
                elif etype == "universe_shrink":
                    for sid in [57365, 57366, 57367]:
                        client.remove_subscription(sid)
                    await client.unsubscribe([57365, 57366, 57367])
                elif etype == "atm_rotation":
                    logger.info("ATM rotation event — hysteresis check point")
                elif etype == "writer_slowdown":
                    logger.info("Writer slowdown simulation point")
                elif etype == "stale_callback_simulation":
                    gen = client._connection_generation - 1
                    if gen >= 0:
                        client.notify_packet_received(security_id=9999, gen=gen)
                        logger.info(f"Injected stale callback from gen {gen}")
            else:
                await asyncio.sleep(1)

    async def health_monitor():
        nonlocal prev_health
        while True:
            h = client.health.value
            if h != prev_health:
                logger.info(f"Health: {h}")
                prev_health = h
            await asyncio.sleep(1)

    # --- run ---
    try:
        tasks = [
            asyncio.create_task(resilient_read_loop()),
            asyncio.create_task(event_loop()),
            asyncio.create_task(health_monitor()),
        ]
        await asyncio.wait_for(
            asyncio.gather(*tasks),
            timeout=36000,
        )
    except asyncio.TimeoutError:
        logger.info("Session timeout (10h) — shutting down")
    except Exception as e:
        logger.error(f"Session error: {e}")
        violations.append(f"session_error: {e}")
    finally:
        read_active = False
        await client._close_existing_socket()

    # --- build report ---
    end_time = time.time()
    duration = end_time - start_time
    final_health = client.health.value

    desired_count = len(client._desired_subscription_ids)
    sent_count = len(client._sent_subscription_ids)
    seen_count = len(client._seen_subscription_ids)
    critical_seen = len(seen_set & critical_ids)
    desired_not_sent = desired_set - client._sent_subscription_ids
    sent_not_seen = client._sent_subscription_ids - client._seen_subscription_ids
    seen_not_desired = client._seen_subscription_ids - desired_set

    transport_healthy = final_health in ("STREAMING_HEALTHY", "FIRST_PACKET_RECEIVED")
    critical_universe_ready = (
        critical_seen
        >= manifest["coverage_thresholds"]["critical_universe_required_absolute"]
    )
    broad_universe_ready = (
        seen_count
        >= manifest["coverage_thresholds"]["broad_universe_required_absolute"]
    )

    all_violations_ok = len(violations) == 0
    continuity = (
        "PASS"
        if (transport_healthy and critical_universe_ready and all_violations_ok)
        else "CONDITIONAL_FAIL"
        if (transport_healthy or critical_universe_ready)
        else "CONTINUITY_FAIL"
    )

    report = {
        "run_id": run_id,
        "config_hash": c_hash,
        "start_time": start_time,
        "end_time": end_time,
        "duration_s": round(duration, 1),
        "result": continuity,
        "final_health": final_health,
        "violations": violations,
        "desired_count": desired_count,
        "sent_count": sent_count,
        "seen_count": seen_count,
        "critical_seen_count": critical_seen,
        "desired_not_sent": sorted(int(s) for s in desired_not_sent),
        "sent_not_seen": sorted(int(s) for s in sent_not_seen),
        "seen_not_desired": sorted(int(s) for s in seen_not_desired),
        "reconnect_count": reconnect_count,
        "successful_recoveries": successful_recoveries,
        "restore_latencies_s": [round(l, 1) for l in restore_latencies],
        "first_packet_latency_s": (
            round(first_packet_at - start_time, 1) if first_packet_at else None
        ),
        "longest_packet_gap_s": round(longest_gap, 1),
        "total_stalled_seconds": round(total_stalled, 1),
        "transport_healthy": transport_healthy,
        "critical_universe_ready": critical_universe_ready,
        "broad_universe_observed": broad_universe_ready,
        "inference_allowed": False,
    }

    report_path = output_path / f"{run_id}_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info(f"Report saved to {report_path}")
    logger.info(f"Result: {continuity}")
    logger.info(
        f"Transport healthy: {transport_healthy}, "
        f"Critical universe ready: {critical_universe_ready}"
    )
    logger.info(
        f"Reconnects: {reconnect_count}, Successful: {successful_recoveries}"
    )
    if violations:
        logger.warning(f"Violations ({len(violations)}): {violations}")


if __name__ == "__main__":
    asyncio.run(main())
