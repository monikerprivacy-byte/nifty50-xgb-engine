import hashlib
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MarketEventEnvelope:
    schema_version: int = 1
    event_id: str = ""
    connection_id: str = ""
    websocket_message_id: int = 0
    packet_offset: int = 0
    receive_ts_ns: int = 0
    exchange_ts_ns: Optional[int] = None
    response_code: Optional[int] = None
    security_id: Optional[int] = None
    exchange_segment: Optional[int] = None
    raw_payload: bytes = b""
    payload_hash: str = ""
    decoder_version: str = ""
    decode_status: str = ""
    decoded_payload: Optional[dict] = None

    def __post_init__(self):
        if not self.event_id:
            raw = (
                str(self.connection_id)
                + str(self.websocket_message_id)
                + str(self.packet_offset)
                + str(self.payload_hash)
            )
            h = hashlib.sha256(raw.encode()).hexdigest()
            object.__setattr__(self, "event_id", h)


def envelope_from_decoded(
    record: dict,
    connection_id: str = "",
    websocket_message_id: int = 0,
    packet_offset: int = 0,
    receive_ts_ns: Optional[int] = None,
    raw_payload: bytes = b"",
    decoder_version: str = "1.0",
    decode_status: str = "success",
) -> MarketEventEnvelope:
    import time

    ns = receive_ts_ns or time.time_ns()
    ph = hashlib.sha256(raw_payload).hexdigest() if raw_payload else ""

    return MarketEventEnvelope(
        connection_id=connection_id,
        websocket_message_id=websocket_message_id,
        packet_offset=packet_offset,
        receive_ts_ns=ns,
        exchange_ts_ns=record.get("last_trade_time"),
        response_code=record.get("response_code"),
        security_id=record.get("security_id"),
        exchange_segment=record.get("exchange_segment"),
        raw_payload=raw_payload,
        payload_hash=ph,
        decoder_version=decoder_version,
        decode_status=decode_status,
        decoded_payload=record if decode_status == "success" else None,
    )
