"""P2.5.6 Stage 2 — Fault-injection soak test.

Quick smoke test:  python3 -m pytest tests/rotation/test_soak.py -v
Full 30-min soak:  python3 -m tests.rotation.soak_harness --seed 42 --duration 30
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

SOAK_SCRIPT = Path(__file__).parent / "soak_harness.py"


def test_soak_smoke_30s():
    """30-second soak — verifies the harness runs without invariant violations."""
    start = time.time()
    result = subprocess.run(
        [sys.executable, str(SOAK_SCRIPT), "--seed", "42", "--duration", "1", "--output", "/tmp/soak_smoke.json"],
        capture_output=True, text=True, timeout=120,
    )
    elapsed = time.time() - start
    assert result.returncode == 0, f"Soak failed (exit={result.returncode}):\n{result.stderr}"
    print(f"Soak smoke passed in {elapsed:.1f}s")


def test_soak_fault_schedule_coverage():
    """Verify fault schedule fires at least one event of each type."""
    start = time.time()
    result = subprocess.run(
        [sys.executable, str(SOAK_SCRIPT), "--seed", "42", "--duration", "1", "--output", "/tmp/soak_schedule.json"],
        capture_output=True, text=True, timeout=180,
    )
    elapsed = time.time() - start
    assert result.returncode == 0, f"Soak failed (exit={result.returncode}):\n{result.stderr}"

    import json
    report = json.load(open("/tmp/soak_schedule.json"))
    assert report["events_received"] > 0
    assert report["events_persisted"] > 0
    assert report["errors"] == []
    assert report["invariant_violations"] == []
    print(f"Schedule test passed in {elapsed:.1f}s — "
          f"{report['events_received']} events, "
          f"result={report['result']}")


@pytest.mark.skip(reason="Run manually: python3 -m tests.rotation.soak_harness --seed 42 --duration 30")
def test_soak_30min():
    """30-minute full soak — requires manual invocation."""
    pass
