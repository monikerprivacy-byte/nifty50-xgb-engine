import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


BRONZE_SCHEMA = pa.schema([
    pa.field("event_id", pa.string()),
    pa.field("security_id", pa.int32()),
    pa.field("mode", pa.string()),
    pa.field("ltp", pa.float64()),
    pa.field("bid", pa.float64()),
    pa.field("ask", pa.float64()),
    pa.field("oi", pa.int64()),
    pa.field("change", pa.float64()),
    pa.field("last_trade_time", pa.int64()),
    pa.field("last_trade_volume", pa.int64()),
    pa.field("open", pa.float64()),
    pa.field("high", pa.float64()),
    pa.field("low", pa.float64()),
    pa.field("close", pa.float64()),
    pa.field("total_bid_qty", pa.int64()),
    pa.field("total_ask_qty", pa.int64()),
    pa.field("capture_timestamp_ns", pa.int64()),
    pa.field("connection_id", pa.string()),
])


class BronzeTempWriter:
    def __init__(self, output_dir: str | Path = "data/bronze/ws_raw"):
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._batch: list[dict] = []
        self._batch_size = 5000
        self._seq = 0

    def append(self, envelope):
        r = envelope.decoded_payload or {}
        self._batch.append({
            "event_id": envelope.event_id,
            "security_id": r.get("security_id", envelope.security_id),
            "mode": r.get("mode", "ticker"),
            "ltp": r.get("ltp"),
            "bid": r.get("bid"),
            "ask": r.get("ask"),
            "oi": r.get("oi"),
            "change": r.get("change"),
            "last_trade_time": r.get("last_trade_time", envelope.exchange_ts_ns),
            "last_trade_volume": r.get("last_trade_volume"),
            "open": r.get("open"),
            "high": r.get("high"),
            "low": r.get("low"),
            "close": r.get("close"),
            "total_bid_qty": r.get("total_bid_qty"),
            "total_ask_qty": r.get("total_ask_qty"),
            "capture_timestamp_ns": envelope.receive_ts_ns,
            "connection_id": envelope.connection_id,
        })

        if len(self._batch) >= self._batch_size:
            return self.flush()
        return True

    def flush(self) -> bool:
        if not self._batch:
            return True

        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        ts = int(time.time() * 1_000_000)

        out_dir = self._output_dir / date_str
        out_dir.mkdir(parents=True, exist_ok=True)

        tmp_path = out_dir / f"{ts:020d}_{self._seq:04d}.parquet.tmp"
        final_path = out_dir / f"{ts:020d}_{self._seq:04d}.parquet"
        self._seq += 1

        try:
            table = pa.Table.from_pylist(self._batch, schema=BRONZE_SCHEMA)
            pq.write_table(table, str(tmp_path))
            os.replace(str(tmp_path), str(final_path))
            self._batch.clear()
            return True
        except Exception as e:
            logger.error(f"Bronze flush failed: {e}")
            self._batch.clear()
            return False

    def recover_temporary_files(self) -> list[Path]:
        recovered = []
        for date_dir in self._output_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for tmp_file in date_dir.glob("*.parquet.tmp"):
                final = tmp_file.with_suffix("")
                try:
                    pq.read_metadata(str(tmp_file))
                    os.replace(str(tmp_file), str(final))
                    recovered.append(final)
                    logger.info(f"Recovered .tmp file: {tmp_file} -> {final}")
                except Exception:
                    logger.warning(f"Corrupt .tmp file, quarantining: {tmp_file}")
                    quarantine = tmp_file.with_suffix(".quarantine")
                    os.replace(str(tmp_file), str(quarantine))
                    recovered.append(quarantine)
        return recovered

    def flush_and_close(self) -> bool:
        return self.flush()

    def __len__(self):
        return len(self._batch)


QUARANTINE_SCHEMA = pa.schema([
    pa.field("event_id", pa.string()),
    pa.field("connection_id", pa.string()),
    pa.field("websocket_message_id", pa.int32()),
    pa.field("packet_offset", pa.int32()),
    pa.field("receive_ts_ns", pa.int64()),
    pa.field("security_id", pa.int32()),
    pa.field("payload_hash", pa.string()),
    pa.field("decoder_version", pa.string()),
    pa.field("decode_status", pa.string()),
    pa.field("raw_payload_hex", pa.string()),
    pa.field("written_at", pa.string()),
])


class QuarantineWriter:
    def __init__(self, output_dir: str | Path = "data/quarantine"):
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._batch: list[dict] = []
        self._batch_size = 1000
        self._seq = 0

    def append(self, envelope) -> bool:
        from datetime import datetime, timezone
        self._batch.append({
            "event_id": envelope.event_id,
            "connection_id": envelope.connection_id,
            "websocket_message_id": envelope.websocket_message_id,
            "packet_offset": envelope.packet_offset,
            "receive_ts_ns": envelope.receive_ts_ns,
            "security_id": envelope.security_id,
            "payload_hash": envelope.payload_hash,
            "decoder_version": envelope.decoder_version,
            "decode_status": envelope.decode_status,
            "raw_payload_hex": envelope.raw_payload.hex(),
            "written_at": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._batch) >= self._batch_size:
            return self.flush()
        return True

    def flush(self, force: bool = False) -> bool:
        if not self._batch:
            return True
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        ts = int(time.time() * 1_000_000)
        out_dir = self._dir / date_str
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_dir / f"{ts:020d}_{self._seq:04d}.quarantine.tmp"
        final = out_dir / f"{ts:020d}_{self._seq:04d}.quarantine.parquet"
        self._seq += 1
        try:
            table = pa.Table.from_pylist(self._batch, schema=QUARANTINE_SCHEMA)
            pq.write_table(table, str(tmp))
            os.replace(str(tmp), str(final))
            self._batch.clear()
            return True
        except Exception as e:
            logger.error(f"Quarantine flush failed: {e}")
            self._batch.clear()
            return False

    def flush_and_close(self) -> bool:
        return self.flush()
