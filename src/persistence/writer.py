from __future__ import annotations

import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Callable, Optional

import duckdb

from .bronze_writer import BronzeTempWriter, QuarantineWriter
from .envelope import MarketEventEnvelope
from .lock import FileLock
from .metrics import HealthState, MetricsStore
from .spool import SpoolManager

logger = logging.getLogger(__name__)

PERSISTENCE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bronze_raw (
    event_id            VARCHAR PRIMARY KEY,
    schema_version      INTEGER NOT NULL,
    connection_id       VARCHAR NOT NULL,
    websocket_message_id INTEGER NOT NULL,
    packet_offset       INTEGER NOT NULL,
    receive_ts_ns       BIGINT NOT NULL,
    exchange_ts_ns      BIGINT,
    response_code       INTEGER,
    security_id         INTEGER,
    exchange_segment    INTEGER,
    payload_hash        VARCHAR NOT NULL,
    decoder_version     VARCHAR NOT NULL,
    decode_status       VARCHAR NOT NULL,
    decoded_payload     JSON,
    written_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS writer_checkpoints (
    id                  INTEGER PRIMARY KEY DEFAULT 1,
    last_event_id       VARCHAR,
    events_persisted    BIGINT NOT NULL DEFAULT 0,
    last_checkpoint_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS writer_metrics (
    metric_name         VARCHAR PRIMARY KEY,
    metric_value        BIGINT NOT NULL DEFAULT 0,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _create_persistence_tables(conn: duckdb.DuckDBPyConnection):
    for stmt in PERSISTENCE_SCHEMA_SQL.split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s + ";")


class WriterManager:
    def __init__(
        self,
        db_path: str | Path = "data/bronze/nifty50_xgb.duckdb",
        bronze_dir: str | Path = "data/bronze/ws_raw",
        spool_dir: str | Path = "data/spool",
        lock_path: str | Path = "data/writer.lock",
        queue_maxsize: int = 10000,
        batch_size: int = 5000,
        checkpoint_interval: float = 10.0,
    ):
        self._db_path = Path(db_path)
        self._bronze_dir = Path(bronze_dir)
        self._spool_dir = Path(spool_dir)
        self._lock_path = Path(lock_path)
        self._queue_maxsize = queue_maxsize
        self._batch_size = batch_size
        self._checkpoint_interval = checkpoint_interval

        self._queue: Queue = Queue(maxsize=queue_maxsize)
        self._metrics = MetricsStore()
        self._metrics.set_gauge("writer_queue_capacity", float(queue_maxsize))
        self._spool = SpoolManager(self._spool_dir)

        self._process: Optional[WriterProcess] = None
        self._lock: Optional[FileLock] = None
        self._running = False
        self._start_time = 0.0

    @property
    def metrics(self) -> MetricsStore:
        return self._metrics

    @property
    def health(self) -> HealthState:
        return self._metrics.health_state()

    def start(self):
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self._lock_path))
        if not self._lock.acquire():
            raise RuntimeError(
                f"Cannot start writer: another writer holds lock at {self._lock_path}"
            )
        self._lock.release()

        self._running = True
        self._start_time = time.time()
        self._process = WriterProcess(
            queue=self._queue,
            db_path=str(self._db_path),
            bronze_dir=str(self._bronze_dir),
            spool_dir=str(self._spool_dir),
            lock_path=str(self._lock_path),
            batch_size=self._batch_size,
            checkpoint_interval=self._checkpoint_interval,
        )
        self._process.start()
        logger.info("Writer process started")

    def stop(self, timeout: float = 30.0):
        self._running = False
        if self._process is not None and self._process.is_alive():
            self._queue.put(None)
            self._process.join(timeout=timeout)
            if self._process.is_alive():
                logger.warning("Writer did not stop gracefully, terminating")
                self._process.terminate()
                self._process.join(5.0)
        self._process = None
        logger.info("Writer process stopped")

    def submit(self, envelope: MarketEventEnvelope) -> bool:
        if not self._running:
            logger.warning("Writer not running, dropping event")
            self._metrics.increment("packets_explicitly_dropped_total")
            return False

        self._metrics.increment("ws_frames_received_total")
        try:
            qsize = self._queue.qsize()
        except NotImplementedError:
            qsize = 0
        self._metrics.set_gauge("writer_queue_depth", float(qsize))

        if self._queue.full():
            spooled = self._spool.spool(envelope)
            if spooled:
                self._metrics.increment("packets_decoded_total")
                self._metrics.set_gauge(
                    "spool_pending_records", float(self._spool.count())
                )
                self._metrics.set_gauge(
                    "spool_pending_bytes", float(self._spool.total_bytes)
                )
            else:
                self._metrics.increment("packets_explicitly_dropped_total")
                logger.error(
                    f"Queue full AND spool failed: dropping event {envelope.event_id}"
                )
            return spooled

        try:
            self._queue.put(envelope, block=False)
            self._metrics.increment("packets_decoded_total")
            return True
        except Exception:
            spooled = self._spool.spool(envelope)
            if spooled:
                self._metrics.increment("packets_decoded_total")
            else:
                self._metrics.increment("packets_explicitly_dropped_total")
            return spooled

    def restart(self):
        logger.info("Restarting writer process")
        self.stop(timeout=10.0)
        self._metrics.increment("writer_restart_count")
        self.start()

    def health_dict(self) -> dict:
        state = self.health
        try:
            qsize = self._queue.qsize() if self._queue else -1
        except NotImplementedError:
            qsize = -1
        return {
            "state": state.value,
            "uptime_seconds": time.time() - self._start_time if self._start_time else 0,
            "counters": self._metrics.counters_snapshot(),
            "gauges": self._metrics.gauges_snapshot(),
            "queue_depth": qsize,
            "spool_count": self._spool.count(),
        }


class WriterProcess(Process):
    def __init__(
        self,
        queue: Queue,
        db_path: str,
        bronze_dir: str,
        spool_dir: str,
        lock_path: str,
        quarantine_dir: str = "data/quarantine",
        batch_size: int = 5000,
        checkpoint_interval: float = 10.0,
    ):
        super().__init__(daemon=False)
        self._queue = queue
        self._db_path = db_path
        self._bronze_dir = bronze_dir
        self._spool_dir = spool_dir
        self._quarantine_dir = quarantine_dir
        self._lock_path = lock_path
        self._batch_size = batch_size
        self._checkpoint_interval = checkpoint_interval
        self._lock: Optional[FileLock] = None
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._bronze: Optional[BronzeTempWriter] = None
        self._quarantine: Optional[QuarantineWriter] = None
        self._event_count = 0
        self._last_checkpoint = 0.0
        self._local_metrics = None

    def run(self):
        self._local_metrics = MetricsStore()
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        self._lock = FileLock(self._lock_path)
        if not self._lock.acquire():
            logger.error("Another writer holds the lock; exiting")
            return

        try:
            self._connect_duckdb()
            self._bronze = BronzeTempWriter(self._bronze_dir)
            self._quarantine = QuarantineWriter(self._quarantine_dir)
            self._run_recovery()
            self._main_loop()
        except Exception as e:
            logger.exception(f"Writer process failed: {e}")
        finally:
            self._cleanup()

    def _connect_duckdb(self):
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = duckdb.connect(self._db_path)
        self._conn.execute("SET memory_limit='2GB'")
        _create_persistence_tables(self._conn)

    def _run_recovery(self):
        self._local_metrics.increment("writer_restart_count")

        recovered = self._bronze.recover_temporary_files()
        if recovered:
            logger.info(f"Bronze recovery handled {len(recovered)} .tmp files")

        last_id, last_count = self._read_checkpoint()
        logger.info(f"Checkpoint: event_id={last_id}, count={last_count}")
        self._event_count = last_count

        spool_count = self._replay_spool(last_id)
        if spool_count > 0:
            logger.info(f"Replayed {spool_count} events from spool")

    def _read_checkpoint(self):
        try:
            row = self._conn.execute(
                "SELECT last_event_id, events_persisted FROM writer_checkpoints WHERE id = 1"
            ).fetchone()
            if row:
                return row[0] or "", row[1] or 0
        except Exception:
            pass
        return "", 0

    def _save_checkpoint(self, event_id: str):
        now = time.time()
        if now - self._last_checkpoint < self._checkpoint_interval:
            return
        self._last_checkpoint = now
        try:
            now_ts = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                """INSERT INTO writer_checkpoints (id, last_event_id, events_persisted, last_checkpoint_at)
                   VALUES (1, ?, ?, ?::TIMESTAMP)
                   ON CONFLICT (id) DO UPDATE SET
                       last_event_id = EXCLUDED.last_event_id,
                       events_persisted = EXCLUDED.events_persisted,
                       last_checkpoint_at = ?::TIMESTAMP""",
                [event_id, self._event_count, now_ts, now_ts],
            )
        except Exception as e:
            logger.warning(f"Checkpoint save failed: {e}")

    def _replay_spool(self, skip_until_event_id: str) -> int:
        replayed = 0
        for env in self._spool_replay_iterator():
            if skip_until_event_id and env.event_id == skip_until_event_id:
                skip_until_event_id = ""
                continue
            if skip_until_event_id and env.event_id != skip_until_event_id:
                continue
            if skip_until_event_id == "" or not skip_until_event_id:
                self._write_one(env)
                replayed += 1
        return replayed

    def _spool_replay_iterator(self):
        spool_dir = Path(self._spool_dir)
        if not spool_dir.exists():
            return
        mgr = SpoolManager(spool_dir)
        yield from mgr.replay()

    def _main_loop(self):
        while True:
            try:
                item = self._queue.get(timeout=1.0)
            except Exception:
                self._check_spool_replay()
                continue

            if item is None:
                logger.info("Writer received shutdown sentinel")
                self._check_spool_replay()
                self._flush_and_checkpoint()
                break

            if isinstance(item, MarketEventEnvelope):
                self._write_one(item)

    def _check_spool_replay(self):
        spool_dir = Path(self._spool_dir)
        if not spool_dir.exists():
            return
        files = list(spool_dir.glob("*.env"))
        if not files:
            return
        logger.info(f"Replaying {len(files)} spooled events")
        mgr = SpoolManager(spool_dir)
        for env in mgr.replay():
            self._write_one(env)
        try:
            mgr.clear()
        except Exception:
            pass
        self._local_metrics.set_gauge("spool_pending_records", 0.0)
        self._local_metrics.set_gauge("spool_pending_bytes", 0.0)

    def _write_one(self, env: MarketEventEnvelope):
        try:
            existing = self._conn.execute(
                "SELECT 1 FROM bronze_raw WHERE event_id = ?", [env.event_id]
            ).fetchone()
            if existing:
                self._local_metrics.increment("duplicate_event_count")
                return

            self._conn.execute(
                """INSERT INTO bronze_raw
                   (event_id, schema_version, connection_id, websocket_message_id,
                    packet_offset, receive_ts_ns, exchange_ts_ns,
                    response_code, security_id, exchange_segment,
                    payload_hash, decoder_version, decode_status, decoded_payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON)""",
                [
                    env.event_id,
                    env.schema_version,
                    env.connection_id,
                    env.websocket_message_id,
                    env.packet_offset,
                    env.receive_ts_ns,
                    env.exchange_ts_ns,
                    env.response_code,
                    env.security_id,
                    env.exchange_segment,
                    env.payload_hash,
                    env.decoder_version,
                    env.decode_status,
                    json.dumps(env.decoded_payload) if env.decoded_payload else None,
                ],
            )

            if env.decode_status == "success" and env.decoded_payload:
                self._bronze.append(env)
            elif env.decode_status not in ("success", ""):
                self._local_metrics.increment("packets_quarantined_total")
                if self._quarantine:
                    self._quarantine.append(env)

            self._event_count += 1
            self._local_metrics.increment("packets_persisted_total")
            self._save_checkpoint(env.event_id)

        except Exception as e:
            logger.warning(f"Write failed for event {env.event_id}: {e}")

    def _flush_and_checkpoint(self):
        if self._bronze:
            self._bronze.flush()
        self._save_checkpoint("__shutdown__")
        now_ts = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO writer_metrics (metric_name, metric_value, updated_at)
               VALUES ('events_persisted', ?, ?::TIMESTAMP)
               ON CONFLICT (metric_name) DO UPDATE SET
                   metric_value = EXCLUDED.metric_value,
                   updated_at = ?::TIMESTAMP""",
            [self._event_count, now_ts, now_ts],
        )

    def _cleanup(self):
        try:
            self._flush_and_checkpoint()
        except Exception:
            pass
        if self._bronze:
            try:
                self._bronze.flush_and_close()
            except Exception:
                pass
        if self._quarantine:
            try:
                self._quarantine.flush_and_close()
            except Exception:
                pass
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        if self._lock:
            self._lock.release()
        logger.info("Writer process cleaned up")
