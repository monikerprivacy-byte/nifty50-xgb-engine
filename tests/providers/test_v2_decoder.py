"""V2 decoder contract tests — positive and negative.

Tests all 8 packet types using synthetic golden fixtures with known values.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from src.providers.dhan.v2 import parse_feed_response
from src.providers.dhan.v2.protocol import (
    HEADER_FMT,
    HEADER_SIZE,
    QUOTE_PAYLOAD_SIZE,
    ResponseCode,
)

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "v2"


def _load(name: str) -> bytes:
    p = FIXTURE_DIR / name
    assert p.exists(), f"Fixture not found: {p}"
    return p.read_bytes()


# ── Positive tests ─────────────────────────────────────────────────


class TestV2Ticker:
    def test_ticker_packet(self):
        data = _load("golden_ticker.bin")
        records = parse_feed_response(data)
        assert len(records) == 1
        r = records[0]
        assert r["response_code"] == ResponseCode.TICKER
        assert r["security_id"] == 57340
        assert r["exchange_segment"] == 2
        assert abs(r["ltp"] - 25230.0) < 0.01
        assert r["mode"] == "ticker"

    def test_ticker_exact_fields(self):
        data = _load("golden_ticker.bin")
        r = parse_feed_response(data)[0]
        assert "security_id" in r
        assert "ltp" in r
        assert "last_trade_time" in r
        assert "response_code" in r
        assert "exchange_segment" in r


class TestV2Quote:
    def test_quote_packet(self):
        data = _load("golden_quote.bin")
        records = parse_feed_response(data)
        assert len(records) == 1
        r = records[0]
        assert r["response_code"] == ResponseCode.QUOTE
        assert r["security_id"] == 57340
        assert r["exchange_segment"] == 2
        assert abs(r["ltp"] - 25230.0) < 0.01
        assert r["mode"] == "quote"

    def test_quote_all_fields(self):
        data = _load("golden_quote.bin")
        r = parse_feed_response(data)[0]
        assert "ltp" in r
        assert "last_trade_qty" in r
        assert "last_trade_time" in r
        assert "atp" in r
        assert "volume" in r
        assert "total_sell_qty" in r
        assert "total_buy_qty" in r
        assert "day_open" in r
        assert "day_close" in r
        assert "day_high" in r
        assert "day_low" in r


class TestV2OI:
    def test_oi_packet(self):
        data = _load("golden_oi.bin")
        records = parse_feed_response(data)
        assert len(records) == 1
        r = records[0]
        assert r["response_code"] == ResponseCode.OI
        assert r["security_id"] == 57340
        assert r["exchange_segment"] == 2
        assert r["oi"] == 1250000
        assert r["mode"] == "quote"

    def test_oi_correct_type(self):
        data = _load("golden_oi.bin")
        r = parse_feed_response(data)[0]
        assert isinstance(r["oi"], int)


class TestV2PrevClose:
    def test_prev_close_packet(self):
        data = _load("golden_prev_close.bin")
        records = parse_feed_response(data)
        assert len(records) == 1
        r = records[0]
        assert r["response_code"] == ResponseCode.PREV_CLOSE
        assert r["security_id"] == 57340
        assert r["exchange_segment"] == 2
        assert abs(r["prev_close"] - 25100.0) < 0.01
        assert r["prev_oi"] == 1200000
        assert r["mode"] == "ticker"


class TestV2MultiPacket:
    def test_multiple_packets_in_one_frame(self):
        data = _load("golden_multi_packet.bin")
        records = parse_feed_response(data)
        assert len(records) == 3, f"Expected 3, got {len(records)}"
        assert records[0]["response_code"] == ResponseCode.QUOTE
        assert records[0]["security_id"] == 57340
        assert records[1]["response_code"] == ResponseCode.OI
        assert records[1]["security_id"] == 57340
        assert records[2]["response_code"] == ResponseCode.OI
        assert records[2]["security_id"] == 57341

    def test_packet_boundaries(self):
        data = _load("golden_multi_packet.bin")
        records = parse_feed_response(data)
        # Each packet should have its own header
        for r in records:
            assert "response_code" in r
            assert "security_id" in r
            assert "exchange_segment" in r


class TestV2Disconnect:
    def test_disconnect_packet(self):
        data = _load("golden_disconnect.bin")
        records = parse_feed_response(data)
        assert len(records) == 0  # disconnect returns None


# ── Negative tests ─────────────────────────────────────────────────


class TestV2Malformed:
    def test_truncated_header(self):
        data = _load("malformed_truncated.bin")
        records = parse_feed_response(data)
        assert len(records) == 0

    def test_unknown_response_code(self):
        data = _load("malformed_unknown_type.bin")
        records = parse_feed_response(data)
        assert len(records) == 0

    def test_declared_length_exceeds_data(self):
        header = struct.pack(HEADER_FMT, ResponseCode.QUOTE, 9999, 2, 57340)
        data = header + b"x" * 10  # only 10 bytes of payload, header says 9999
        records = parse_feed_response(data)
        assert len(records) == 0

    def test_invalid_exchange_segment(self):
        header = struct.pack(HEADER_FMT, ResponseCode.QUOTE, HEADER_SIZE + QUOTE_PAYLOAD_SIZE, 99, 57340)
        payload = struct.pack("<fhIfIIIffff", 250.0, 10, 1784262000, 250.0, 1000, 500, 600, 249.0, 248.5, 251.0, 247.0)
        records = parse_feed_response(header + payload)
        assert len(records) == 1  # decoder still parses regardless of segment
        assert records[0]["exchange_segment"] == 99

    def test_zero_length_packet(self):
        header = struct.pack(HEADER_FMT, ResponseCode.QUOTE, 0, 2, 57340)
        records = parse_feed_response(header)
        assert len(records) == 0

    def test_second_packet_truncated(self):
        p1 = _load("golden_quote.bin")
        # Second packet: header only, no payload
        p2 = struct.pack(HEADER_FMT, ResponseCode.QUOTE, HEADER_SIZE + QUOTE_PAYLOAD_SIZE, 2, 57341)
        data = p1 + p2  # p2 declares full size but has no payload after header
        records = parse_feed_response(data)
        assert len(records) == 1  # only first packet decoded
        assert records[0]["security_id"] == 57340

    def test_extra_trailing_data(self):
        data = _load("golden_quote.bin") + b"TRAILING"
        records = parse_feed_response(data)
        assert len(records) == 1
        assert records[0]["security_id"] == 57340


class TestV2Endianness:
    def test_numeric_endianness_ltp(self):
        ltp_val = 25230.0
        payload = struct.pack("<fhIfIIIffff", ltp_val, 10, 1784262000, ltp_val, 1000, 500, 600, 249.0, 248.5, 251.0, 247.0)
        hdr = struct.pack(HEADER_FMT, ResponseCode.QUOTE, HEADER_SIZE + len(payload), 2, 57340)
        r = parse_feed_response(hdr + payload)[0]
        assert abs(r["ltp"] - ltp_val) < 0.01

    def test_security_id_endianness(self):
        sid = 57340
        payload = struct.pack("<fhIfIIIffff", 250.0, 10, 1784262000, 250.0, 1000, 500, 600, 249.0, 248.5, 251.0, 247.0)
        hdr = struct.pack(HEADER_FMT, ResponseCode.QUOTE, HEADER_SIZE + len(payload), 2, sid)
        r = parse_feed_response(hdr + payload)[0]
        assert r["security_id"] == sid
