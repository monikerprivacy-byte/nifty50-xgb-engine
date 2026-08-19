from __future__ import annotations

import os
import pickle
import signal
import time
from pathlib import Path
from threading import Thread

import duckdb
import pytest

from src.persistence.bronze_writer import BronzeTempWriter
from src.persistence.envelope import MarketEventEnvelope, envelope_from_decoded
from src.persistence.lock import FileLock
from src.persistence.metrics import MetricsStore
from src.persistence.spool import SpoolManager
from src.persistence.writer import WriterManager, WriterProcess


def _make_env(security_id: int = 1, payload: bytes = b"test_payload", status: str = "success") -> MarketEventEnvelope:
    import hashlib
    ph = hashlib.sha256(payload + str(security_id).encode()).hexdigest()
    return MarketEventEnvelope(
        connection_id="test",
        websocket_message_id=security_id,
        packet_offset=0,
        receive_ts_ns=time.time_ns(),
        exchange_ts_ns=time.time_ns(),
        security_id=security_id,
        raw_payload=payload,
        payload_hash=ph,
        decoder_version="1.0",
        decode_status=status,
        decoded_payload={"security_id": security_id, "mode": "quote", "ltp": 100.0, "bid": 99.5, "ask": 100.5, "oi": 1000, "change": 0.5},
    )


def _make_envelopes(count: int, start_sid: int = 1) -> list[MarketEventEnvelope]:
    return [_make_env(sid) for sid in range(start_sid, start_sid + count)]


def _wait_for_persisted(db_path: str, target_count: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            conn = duckdb.connect(db_path, read_only=True)
            row = conn.execute("SELECT count(*) FROM bronze_raw").fetchone()
            conn.close()
            if row and row[0] >= target_count:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _count_events(db_path: str) -> int:
    try:
        conn = duckdb.connect(db_path, read_only=True)
        row = conn.execute("SELECT count(*) FROM bronze_raw").fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


# ─── test 1: single-writer enforcement ────────────────────────────────────────


class TestSingleWriterEnforcement:
    def test_acquire_and_block(self, tmp_data_dir: Path):
        lock_path = tmp_data_dir / "writer.lock"
        lock_a = FileLock(str(lock_path))
        lock_b = FileLock(str(lock_path))

        assert lock_a.acquire() is True
        assert lock_a.is_locked is True
        assert lock_b.acquire() is False
        assert lock_b.is_locked is False

        lock_a.release()
        assert lock_a.is_locked is False
        assert lock_b.acquire() is True
        lock_b.release()

    def test_writermanager_rejects_second(self, tmp_data_dir: Path):
        db = tmp_data_dir / "test.duckdb"
        lock = tmp_data_dir / "writer.lock"
        m1 = WriterManager(db_path=str(db), lock_path=str(lock), spool_dir=str(tmp_data_dir / "spool1"))
        m2 = WriterManager(db_path=str(db), lock_path=str(lock), spool_dir=str(tmp_data_dir / "spool2"))

        m1.start()
        time.sleep(0.5)
        with pytest.raises(RuntimeError, match="holds lock"):
            m2.start()
        m1.stop()
        m2.stop()

    def test_lock_released_on_stop(self, tmp_data_dir: Path):
        lock_path = tmp_data_dir / "writer.lock"
        m = WriterManager(
            db_path=str(tmp_data_dir / "test.duckdb"),
            lock_path=str(lock_path),
            spool_dir=str(tmp_data_dir / "spool"),
        )
        m.start()
        time.sleep(0.5)
        m.stop()
        time.sleep(0.5)
        lock_b = FileLock(str(lock_path))
        assert lock_b.acquire() is True
        lock_b.release()


# ─── test 2: reader protection ────────────────────────────────────────────────


class TestReaderProtection:
    def test_read_only_cannot_write(self, tmp_data_dir: Path):
        db_path = tmp_data_dir / "test.duckdb"
        conn_w = duckdb.connect(str(db_path))
        conn_w.execute("CREATE TABLE test (id INTEGER)")
        conn_w.close()

        conn_r = duckdb.connect(str(db_path), read_only=True)
        with pytest.raises((duckdb.CatalogException, duckdb.InvalidInputException), match="read.only"):
            conn_r.execute("INSERT INTO test VALUES (1)")
        conn_r.close()
# ─── test 3: writer kill → spool replay → no missing/duplicate ────────────────

# NOTE: DuckDB holds a file-level lock while the writer process is alive.
# We cannot read live DuckDB state from another process on macOS.
# These tests verify persistence state AFTER the writer process has stopped
# (releasing the lock), or use queue/spool state for live monitoring.


class TestWriterKillRecovery:
    def test_writer_kill_and_spool_replay(self, tmp_data_dir: Path):
        db_path = tmp_data_dir / "test.duckdb"
        bronze_dir = tmp_data_dir / "bronze"
        spool_dir = tmp_data_dir / "spool"
        lock_path = tmp_data_dir / "writer.lock"

        phase1 = 10
        phase2 = 5

        spool_prep = SpoolManager(spool_dir)

        mgr = WriterManager(
            db_path=str(db_path),
            bronze_dir=str(bronze_dir),
            spool_dir=str(spool_dir),
            lock_path=str(lock_path),
            queue_maxsize=100,
        )
        mgr.start()
        time.sleep(0.5)

        for env in _make_envelopes(phase1, start_sid=1):
            mgr.submit(env)

        time.sleep(2.0)

        assert mgr._process is not None
        pid = mgr._process.pid
        os.kill(pid, signal.SIGKILL)
        mgr._process = None
        mgr._running = False
        time.sleep(1.0)

        for env in _make_envelopes(phase2, start_sid=100):
            spool_prep.spool(env)

        assert spool_prep.count() == phase2

        mgr2 = WriterManager(
            db_path=str(db_path),
            bronze_dir=str(bronze_dir),
            spool_dir=str(spool_dir),
            lock_path=str(lock_path),
            queue_maxsize=100,
        )
        mgr2.start()
        time.sleep(4.0)
        mgr2.stop()

        total = _count_events(str(db_path))
        assert total == phase1 + phase2, (
            f"Expected {phase1 + phase2} events, got {total}"
        )

    def test_no_duplicate_after_replay(self, tmp_data_dir: Path):
        db_path = tmp_data_dir / "test.duckdb"
        bronze_dir = tmp_data_dir / "bronze"
        spool_dir = tmp_data_dir / "spool"
        lock_path = tmp_data_dir / "writer.lock"

        mgr = WriterManager(
            db_path=str(db_path),
            bronze_dir=str(bronze_dir),
            spool_dir=str(spool_dir),
            lock_path=str(lock_path),
            queue_maxsize=100,
        )
        mgr.start()
        time.sleep(0.5)

        envs = _make_envelopes(5, start_sid=1)
        for env in envs:
            mgr.submit(env)

        time.sleep(2.0)

        assert mgr._process is not None
        pid = mgr._process.pid
        os.kill(pid, signal.SIGKILL)
        mgr._process = None
        mgr._running = False
        time.sleep(1.0)

        spool_mgr = SpoolManager(spool_dir)
        for env in envs:
            spool_mgr.spool(env)
            spool_mgr.spool(env)

        mgr2 = WriterManager(
            db_path=str(db_path),
            bronze_dir=str(bronze_dir),
            spool_dir=str(spool_dir),
            lock_path=str(lock_path),
            queue_maxsize=100,
        )
        mgr2.start()
        time.sleep(4.0)
        mgr2.stop()

        total = _count_events(str(db_path))
        assert total == 5, f"Expected exactly 5 unique events, got {total}"

        conn = duckdb.connect(str(db_path))
        dups = conn.execute(
            "SELECT count(*) - count(DISTINCT event_id) FROM bronze_raw"
        ).fetchone()[0]
        conn.close()
        assert dups == 0, f"Found {dups} duplicate event_ids"


# ─── test 4: queue saturation → spool → no silent drops ───────────────────────


class TestQueueSaturation:
    def test_queue_saturation_spools_no_drops(self, tmp_data_dir: Path):
        db_path = tmp_data_dir / "test.duckdb"
        bronze_dir = tmp_data_dir / "bronze"
        spool_dir = tmp_data_dir / "spool"
        lock_path = tmp_data_dir / "writer.lock"

        mgr = WriterManager(
            db_path=str(db_path),
            bronze_dir=str(bronze_dir),
            spool_dir=str(spool_dir),
            lock_path=str(lock_path),
            queue_maxsize=5,
        )
        mgr.start()
        time.sleep(0.5)

        total = 50
        submitted = 0
        for env in _make_envelopes(total, start_sid=1):
            ok = mgr.submit(env)
            if ok:
                submitted += 1

        time.sleep(3.0)

        mgr.stop()

        total_events = _count_events(str(db_path))
        drops = mgr.metrics.packets_explicitly_dropped_total
        spool_count = mgr._spool.count() if hasattr(mgr, "_spool") else 0

        accounted = total_events + drops + spool_count

        assert drops == 0, f"Expected 0 drops, got {drops}"
        assert total_events == total, f"Expected {total} events in DB, got {total_events}"
        assert accounted == total, f"Expected {total} accounted, got {accounted} (persisted={total_events}, drops={drops}, spool={spool_count})"

        assert mgr.metrics.health_state().value in ("BACKPRESSURED", "SPOOLING", "HEALTHY"), (
            f"Unexpected health state: {mgr.metrics.health_state()}"
        )


# ─── test 5: partial-file recovery ────────────────────────────────────────────


class TestPartialFileRecovery:
    def test_valid_tmp_becomes_parquet(self, tmp_data_dir: Path):
        bronze_dir = tmp_data_dir / "bronze"
        date_dir = bronze_dir / "2026-07-17"
        date_dir.mkdir(parents=True)

        import pyarrow as pa
        import pyarrow.parquet as pq

        tmp_path = date_dir / "20260717_0000.parquet.tmp"
        schema = pa.schema([
            pa.field("event_id", pa.string()),
            pa.field("security_id", pa.int32()),
            pa.field("mode", pa.string()),
            pa.field("ltp", pa.float64()),
            pa.field("capture_timestamp_ns", pa.int64()),
            pa.field("connection_id", pa.string()),
        ])
        table = pa.Table.from_pylist(
            [{"event_id": "e1", "security_id": 1, "mode": "quote", "ltp": 100.0, "capture_timestamp_ns": 0, "connection_id": "t"}],
            schema=schema,
        )
        pq.write_table(table, str(tmp_path))

        writer = BronzeTempWriter(str(bronze_dir))
        recovered = writer.recover_temporary_files()

        final_path = date_dir / "20260717_0000.parquet"
        assert final_path.exists(), f"Final .parquet should exist at {final_path}"
        assert not tmp_path.exists(), ".tmp should be removed"
        assert any(".parquet" in str(p) for p in recovered), "Recovered file should be listed"

    def test_corrupt_tmp_quarantined(self, tmp_data_dir: Path):
        bronze_dir = tmp_data_dir / "bronze"
        date_dir = bronze_dir / "2026-07-17"
        date_dir.mkdir(parents=True)

        tmp_path = date_dir / "corrupt.parquet.tmp"
        tmp_path.write_bytes(b"not_valid_parquet_data")

        writer = BronzeTempWriter(str(bronze_dir))
        recovered = writer.recover_temporary_files()

        assert tmp_path.with_suffix(".quarantine").exists(), "Corrupt .tmp should be quarantined"
        assert not tmp_path.exists(), ".tmp should be removed"
        assert not tmp_path.with_suffix(".parquet").exists(), "No .parquet should be created for corrupt data"

    def test_no_tmp_no_action(self, tmp_data_dir: Path):
        bronze_dir = tmp_data_dir / "bronze"
        date_dir = bronze_dir / "2026-07-17"
        date_dir.mkdir(parents=True)

        final = date_dir / "valid.parquet"
        import pyarrow as pa
        import pyarrow.parquet as pq
        table = pa.Table.from_pylist([{"event_id": "e1", "security_id": 1, "mode": "quote", "ltp": 100.0, "capture_timestamp_ns": 0, "connection_id": "t"}])
        pq.write_table(table, str(final))

        writer = BronzeTempWriter(str(bronze_dir))
        recovered = writer.recover_temporary_files()

        assert len(recovered) == 0, "No .tmp files should mean no recovery"
        assert final.exists(), "Existing .parquet should not be touched"


# ─── test 6: graceful shutdown ────────────────────────────────────────────────


class TestGracefulShutdown:
    def test_shutdown_drains_queue(self, tmp_data_dir: Path):
        db_path = tmp_data_dir / "test.duckdb"
        bronze_dir = tmp_data_dir / "bronze"
        spool_dir = tmp_data_dir / "spool"
        lock_path = tmp_data_dir / "writer.lock"

        mgr = WriterManager(
            db_path=str(db_path),
            bronze_dir=str(bronze_dir),
            spool_dir=str(spool_dir),
            lock_path=str(lock_path),
            queue_maxsize=100,
        )
        mgr.start()
        time.sleep(0.5)

        total = 20
        for env in _make_envelopes(total, start_sid=1):
            mgr.submit(env)

        mgr.stop()

        total_events = _count_events(str(db_path))
        assert total_events == total, f"Expected {total} persisted, got {total_events}"

    def test_shutdown_finalizes_bronze(self, tmp_data_dir: Path):
        db_path = tmp_data_dir / "test.duckdb"
        bronze_dir = tmp_data_dir / "bronze"
        spool_dir = tmp_data_dir / "spool"
        lock_path = tmp_data_dir / "writer.lock"

        mgr = WriterManager(
            db_path=str(db_path),
            bronze_dir=str(bronze_dir),
            spool_dir=str(spool_dir),
            lock_path=str(lock_path),
            queue_maxsize=100,
            batch_size=10,
        )
        mgr.start()
        time.sleep(0.5)

        for env in _make_envelopes(7, start_sid=1):
            mgr.submit(env)

        mgr.stop()

        parquet_files = list(Path(bronze_dir).rglob("*.parquet"))
        parquet_tmp = list(Path(bronze_dir).rglob("*.parquet.tmp"))

        assert len(parquet_files) > 0, "Bronze Parquet files should exist"
        assert len(parquet_tmp) == 0, f"No .tmp files should remain, found {parquet_tmp}"

        total = _count_events(str(db_path))
        assert total == 7, f"Expected 7 events persisted, got {total}"

    def test_lock_released_after_stop(self, tmp_data_dir: Path):
        db_path = tmp_data_dir / "test.duckdb"
        lock_path = tmp_data_dir / "writer.lock"

        mgr = WriterManager(
            db_path=str(db_path),
            lock_path=str(lock_path),
            spool_dir=str(tmp_data_dir / "spool"),
        )
        mgr.start()
        time.sleep(0.5)
        mgr.stop()

        lock = FileLock(str(lock_path))
        assert lock.acquire(), "Lock should be available after stop"
        lock.release()


# ─── test 7: idempotent replay ────────────────────────────────────────────────


class TestIdempotentReplay:
    def test_spool_replayed_twice_no_duplicates(self, tmp_data_dir: Path):
        db_path = tmp_data_dir / "test.duckdb"
        bronze_dir = tmp_data_dir / "bronze"
        spool_dir = tmp_data_dir / "spool"
        lock_path = tmp_data_dir / "writer.lock"

        spool_mgr = SpoolManager(spool_dir)
        envs = _make_envelopes(10, start_sid=1)
        for env in envs:
            spool_mgr.spool(env)
        assert spool_mgr.count() == 10

        mgr = WriterManager(
            db_path=str(db_path),
            bronze_dir=str(bronze_dir),
            spool_dir=str(spool_dir),
            lock_path=str(lock_path),
            queue_maxsize=100,
        )
        mgr.start()
        time.sleep(3.0)
        mgr.stop()

        first_count = _count_events(str(db_path))
        assert first_count == 10, f"Expected 10 events after first replay, got {first_count}"

        conn = duckdb.connect(str(db_path))
        dups = conn.execute(
            "SELECT count(*) - count(DISTINCT event_id) FROM bronze_raw"
        ).fetchone()[0]
        conn.close()
        assert dups == 0, f"Found {dups} duplicate event_ids after first replay"

        spool_mgr2 = SpoolManager(spool_dir)
        assert spool_mgr2.count() == 0, "Spool should be empty after replay"

        for env in envs:
            spool_mgr2.spool(env)
        assert spool_mgr2.count() == 10

        mgr2 = WriterManager(
            db_path=str(db_path),
            bronze_dir=str(bronze_dir),
            spool_dir=str(spool_dir),
            lock_path=str(lock_path),
            queue_maxsize=100,
        )
        mgr2.start()
        time.sleep(3.0)
        mgr2.stop()

        second_count = _count_events(str(db_path))
        assert second_count == 10, f"Expected still 10 events after second replay, got {second_count}"

        conn = duckdb.connect(str(db_path))
        dups = conn.execute(
            "SELECT count(*) - count(DISTINCT event_id) FROM bronze_raw"
        ).fetchone()[0]
        conn.close()
        assert dups == 0, f"Found {dups} duplicate event_ids after second replay"


# ─── envelope unit tests ──────────────────────────────────────────────────────


class TestEnvelope:
    def test_event_id_deterministic(self):
        a = MarketEventEnvelope(
            connection_id="c1", websocket_message_id=1, packet_offset=0,
            payload_hash="h1",
        )
        b = MarketEventEnvelope(
            connection_id="c1", websocket_message_id=1, packet_offset=0,
            payload_hash="h1",
        )
        assert a.event_id == b.event_id
        assert len(a.event_id) == 64

    def test_event_id_changes_on_input(self):
        a = MarketEventEnvelope(
            connection_id="c1", websocket_message_id=1, packet_offset=0,
            payload_hash="h1",
        )
        b = MarketEventEnvelope(
            connection_id="c1", websocket_message_id=1, packet_offset=0,
            payload_hash="h2",
        )
        assert a.event_id != b.event_id

    def test_envelope_from_decoded(self):
        record = {"security_id": 123, "mode": "quote", "ltp": 150.0, "bid": 149.5, "ask": 150.5}
        env = envelope_from_decoded(
            record,
            connection_id="test_conn",
            websocket_message_id=5,
            packet_offset=1,
            raw_payload=b"\x01\x02\x03",
        )
        assert env.security_id == 123
        assert env.decoded_payload == record
        assert env.decode_status == "success"
        assert env.connection_id == "test_conn"
        assert env.websocket_message_id == 5
        assert len(env.event_id) == 64

    def test_envelope_frozen(self):
        env = _make_env()
        with pytest.raises((AttributeError, TypeError)):
            env.event_id = "new_id"


# ─── spool unit tests ─────────────────────────────────────────────────────────


class TestSpool:
    def test_spool_and_replay(self, tmp_data_dir: Path):
        spool_dir = tmp_data_dir / "spool"
        mgr = SpoolManager(spool_dir)

        envs = _make_envelopes(5, start_sid=1)
        for env in envs:
            assert mgr.spool(env)

        assert mgr.count() == 5

        replayed = list(mgr.replay())
        assert len(replayed) == 5
        assert all(isinstance(e, MarketEventEnvelope) for e in replayed)
        assert replayed[0].event_id == envs[0].event_id

    def test_spool_clear(self, tmp_data_dir: Path):
        spool_dir = tmp_data_dir / "spool"
        mgr = SpoolManager(spool_dir)

        for env in _make_envelopes(3, start_sid=1):
            mgr.spool(env)

        assert mgr.count() == 3
        mgr.clear()
        assert mgr.count() == 0


# ─── metrics reconciliation test ──────────────────────────────────────────────


class TestMetrics:
    def test_reconcile_formula(self):
        ms = MetricsStore()
        received = 100
        persisted = 85
        quarantined = 5
        pending_queue = 3
        pending_spool = 5
        dropped = 2
        assert ms.reconcile(received, persisted, quarantined, pending_queue, pending_spool, dropped)

    def test_reconcile_mismatch(self):
        ms = MetricsStore()
        assert not ms.reconcile(100, 80, 0, 5, 5, 0)


# ─── bronze writer unit tests ─────────────────────────────────────────────────


class TestBronzeWriter:
    def test_append_and_flush(self, tmp_data_dir: Path):
        writer = BronzeTempWriter(str(tmp_data_dir / "bronze"))

        envs = _make_envelopes(3, start_sid=1)
        for env in envs:
            result = writer.append(env)
            assert result is True

        assert writer.flush() is True
        assert len(writer) == 0

        parquet_files = list(Path(tmp_data_dir / "bronze").rglob("*.parquet"))
        assert len(parquet_files) >= 1

    def test_auto_flush_on_batch(self, tmp_data_dir: Path):
        writer = BronzeTempWriter(str(tmp_data_dir / "bronze"))
        writer._batch_size = 2

        for env in _make_envelopes(2, start_sid=1):
            writer.append(env)

        assert len(writer) == 0

    def test_no_tmp_after_flush(self, tmp_data_dir: Path):
        writer = BronzeTempWriter(str(tmp_data_dir / "bronze"))
        for env in _make_envelopes(5, start_sid=1):
            writer.append(env)
        writer.flush()

        tmp_files = list(Path(tmp_data_dir / "bronze").rglob("*.parquet.tmp"))
        assert len(tmp_files) == 0
