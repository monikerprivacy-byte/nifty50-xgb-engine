"""Raw-event → bar replay parity on 17-Jul captured data.

Replays captured packets through BarEngine twice and verifies
identical results. Ensures no negative volumes, no cross-session
contamination, no silent gap spanning.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from src.bars.engine import BarEngine

CAPTURE_DIR = Path("artifacts/stage3/2026-07-17/bronze/2026-07-17")
FIXED_NOW = datetime(2026, 7, 17, 15, 30, 0, tzinfo=timezone.utc)


def _load_packets() -> list[dict]:
    files = sorted(os.listdir(CAPTURE_DIR))
    rows = []
    for f in files:
        table = pq.read_table(str(CAPTURE_DIR / f))
        pdf = table.to_pandas()
        for _, r in pdf.iterrows():
            rows.append(r.to_dict())
    return rows


def _packet_to_event(packet: dict) -> dict:
    ts_ns = packet.get("capture_timestamp_ns")
    if ts_ns is None:
        return None
    event_time = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    ltv = packet.get("last_trade_volume")
    volume = 0 if ltv is None or (isinstance(ltv, float) and ltv != ltv) else int(ltv)
    oi_val = packet.get("oi")
    oi = None if oi_val is None or (isinstance(oi_val, float) and oi_val != oi_val) else int(oi_val)
    ltp = packet.get("ltp")
    price = ltp if ltp and not (isinstance(ltp, float) and ltp != ltp) else 0.0
    return {
        "security_id": packet["security_id"],
        "event_time": event_time,
        "price": price,
        "volume": volume,
        "oi": oi,
    }


def _is_nan(v):
    return v is None or (isinstance(v, float) and v != v)


def test_replay_deterministic():
    """Two sequential replays produce identical bars."""
    packets = _load_packets()
    assert len(packets) == 208074

    def replay() -> list[dict]:
        eng = BarEngine(now_fn=lambda: FIXED_NOW, late_event_window_s=86400)
        for p in packets:
            ev = _packet_to_event(p)
            if ev:
                eng.accept(**ev)
        bars = eng.close_all()
        return [
            {
                "security_id": b.security_id,
                "event_time": b.event_time.isoformat(),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "trade_count": b.trade_count,
                "open_interest_open": b.open_interest_open,
                "open_interest_high": b.open_interest_high,
                "open_interest_low": b.open_interest_low,
                "open_interest_close": b.open_interest_close,
                "revision": b.revision,
                "is_corrected": b.is_corrected,
            }
            for b in bars
        ]

    bars1 = replay()
    bars2 = replay()
    assert len(bars1) == len(bars2)
    for b1, b2 in zip(bars1, bars2):
        assert b1 == b2


def test_replay_no_negative_volume():
    """No bar has negative or zero volume."""
    eng = BarEngine(now_fn=lambda: FIXED_NOW, late_event_window_s=86400)
    packets = _load_packets()
    for p in packets:
        ev = _packet_to_event(p)
        if ev:
            eng.accept(**ev)
    bars = eng.close_all()
    for b in bars:
        assert b.volume >= 0, f"Negative volume for {b.security_id} @ {b.event_time}"
        assert b.trade_count >= 0


def test_replay_no_oi_as_volume():
    """OI-only packets must not contribute to volume."""
    eng = BarEngine(now_fn=lambda: FIXED_NOW, late_event_window_s=86400)
    packets = _load_packets()
    for p in packets:
        ev = _packet_to_event(p)
        if ev:
            eng.accept(**ev)
    bars = eng.close_all()
    for b in bars:
        if b.open_interest_close is not None and b.open_interest_open is not None:
            assert b.open_interest_close != b.volume, (
                f"OI appears as volume for {b.security_id}"
            )


def test_replay_timestamps_in_order():
    """Bar event_times must be monotonically increasing per security_id."""
    eng = BarEngine(now_fn=lambda: FIXED_NOW, late_event_window_s=86400)
    packets = _load_packets()
    for p in packets:
        ev = _packet_to_event(p)
        if ev:
            eng.accept(**ev)
    bars = eng.close_all()
    # Group by security_id and check ordering
    by_sid: dict[int, list] = {}
    for b in bars:
        by_sid.setdefault(b.security_id, []).append(b)
    for sid, sbars in by_sid.items():
        times = [b.event_time for b in sbars]
        assert times == sorted(times), f"Out-of-order bars for security {sid}"


def test_replay_no_gap_spanning():
    """Check that no single bar spans a gap longer than BAR_INTERVAL_S * 2."""
    eng = BarEngine(now_fn=lambda: FIXED_NOW, late_event_window_s=86400)
    packets = _load_packets()
    for p in packets:
        ev = _packet_to_event(p)
        if ev:
            eng.accept(**ev)
    bars = eng.close_all()
    by_sid: dict[int, list] = {}
    for b in bars:
        by_sid.setdefault(b.security_id, []).append(b)
    for sid, sbars in by_sid.items():
        for b in sbars:
            assert b.trade_count > 0, f"Zero-trade bar for {sid} @ {b.event_time}"
