"""Dhan v2 binary decoder."""

from __future__ import annotations

import logging
import struct
from typing import Optional

from .protocol import (
    HEADER_FMT,
    HEADER_SIZE,
    TICKER_PAYLOAD_FMT,
    TICKER_PAYLOAD_SIZE,
    QUOTE_PAYLOAD_FMT,
    QUOTE_PAYLOAD_SIZE,
    OI_PAYLOAD_SIZE,
    FULL_BASE_FMT,
    FULL_BASE_SIZE,
    DEPTH_PACKET_SIZE,
    ResponseCode,
)

logger = logging.getLogger(__name__)


def parse_feed_response(data: bytes) -> list[dict]:
    if len(data) < HEADER_SIZE:
        logger.warning(f"Short packet: {len(data)} bytes")
        return []

    results = []
    offset = 0

    while offset + HEADER_SIZE <= len(data):
        try:
            resp_code, total_len, exchange_seg, security_id = struct.unpack_from(
                HEADER_FMT, data, offset
            )
        except struct.error as e:
            logger.warning(f"Header parse error: {e}")
            break

        if offset + total_len > len(data):
            logger.warning(
                f"Truncated packet: header says {total_len}, have {len(data) - offset}"
            )
            break

        if total_len < HEADER_SIZE:
            logger.warning(
                f"Invalid packet length: {total_len} < header size {HEADER_SIZE}"
            )
            break

        block = data[offset : offset + total_len]
        offset += total_len

        base = {
            "security_id": security_id,
            "response_code": resp_code,
            "exchange_segment": exchange_seg,
        }

        if resp_code == ResponseCode.INDEX:
            parsed = _parse_index(block, security_id)
        elif resp_code == ResponseCode.TICKER:
            parsed = _parse_ticker(block, security_id)
        elif resp_code == ResponseCode.QUOTE:
            parsed = _parse_quote(block, security_id)
        elif resp_code == ResponseCode.OI:
            parsed = _parse_oi(block, security_id)
        elif resp_code == ResponseCode.PREV_CLOSE:
            parsed = _parse_prev_close(block, security_id)
        elif resp_code == ResponseCode.MARKET_STATUS:
            parsed = _parse_market_status(block, security_id)
        elif resp_code == ResponseCode.FULL:
            parsed = _parse_full(block, security_id)
        elif resp_code == ResponseCode.DISCONNECT:
            parsed = _parse_disconnect(block, security_id)
        else:
            logger.debug(f"Unknown response code {resp_code}")
            continue

        if parsed:
            parsed.update(base)
            results.append(parsed)

    return results


def _parse_index(block: bytes, security_id: int) -> Optional[dict]:
    return None


def _parse_market_status(block: bytes, security_id: int) -> Optional[dict]:
    return None


def _parse_disconnect(block: bytes, security_id: int) -> Optional[dict]:
    logger.info(f"Feed disconnect code={security_id}")
    return None


def _parse_ticker(block: bytes, security_id: int) -> Optional[dict]:
    payload_offset = HEADER_SIZE
    if payload_offset + TICKER_PAYLOAD_SIZE > len(block):
        return None
    ltp, ltt = struct.unpack_from(TICKER_PAYLOAD_FMT, block, payload_offset)
    return {
        "security_id": security_id,
        "ltp": ltp,
        "last_trade_time": ltt,
        "mode": "ticker",
    }


def _parse_quote(block: bytes, security_id: int) -> Optional[dict]:
    payload_offset = HEADER_SIZE
    if payload_offset + QUOTE_PAYLOAD_SIZE > len(block):
        return None
    vals = struct.unpack_from(QUOTE_PAYLOAD_FMT, block, payload_offset)
    ltp, ltq, ltt, atp, volume, sell_qty, buy_qty, day_open, day_close, day_high, day_low = vals
    return {
        "security_id": security_id,
        "ltp": ltp,
        "last_trade_qty": ltq,
        "last_trade_time": ltt,
        "atp": atp,
        "volume": volume,
        "total_sell_qty": sell_qty,
        "total_buy_qty": buy_qty,
        "day_open": day_open,
        "day_close": day_close,
        "day_high": day_high,
        "day_low": day_low,
        "mode": "quote",
    }


def _parse_oi(block: bytes, security_id: int) -> Optional[dict]:
    payload_offset = HEADER_SIZE
    if payload_offset + OI_PAYLOAD_SIZE > len(block):
        return None
    oi = struct.unpack_from("<i", block, payload_offset)[0]
    return {
        "security_id": security_id,
        "oi": oi,
        "mode": "quote",
    }


def _parse_prev_close(block: bytes, security_id: int) -> Optional[dict]:
    payload_offset = HEADER_SIZE
    if payload_offset + 8 > len(block):
        return None
    prev_close, prev_oi = struct.unpack_from("<fi", block, payload_offset)
    return {
        "security_id": security_id,
        "prev_close": prev_close,
        "prev_oi": prev_oi,
        "mode": "ticker",
    }


def _parse_full(block: bytes, security_id: int) -> Optional[dict]:
    payload_offset = HEADER_SIZE
    if payload_offset + FULL_BASE_SIZE > len(block):
        return None
    vals = struct.unpack_from(FULL_BASE_FMT, block, payload_offset)
    (ltp, ltq, ltt, atp, volume, sell_qty, buy_qty,
     oi, oi_high, oi_low,
     day_open, day_close, day_high, day_low) = vals

    depth_offset = payload_offset + FULL_BASE_SIZE
    depth_bids = []
    depth_asks = []
    for i in range(5):
        if depth_offset + DEPTH_PACKET_SIZE > len(block):
            break
        (bid_qty, ask_qty, bid_orders, ask_orders, bid_price, ask_price) = struct.unpack_from(
            "<iiHHff", block, depth_offset
        )
        depth_bids.append({"price": bid_price, "qty": bid_qty, "orders": bid_orders})
        depth_asks.append({"price": ask_price, "qty": ask_qty, "orders": ask_orders})
        depth_offset += DEPTH_PACKET_SIZE

    return {
        "security_id": security_id,
        "ltp": ltp,
        "last_trade_qty": ltq,
        "last_trade_time": ltt,
        "atp": atp,
        "volume": volume,
        "total_sell_qty": sell_qty,
        "total_buy_qty": buy_qty,
        "oi": oi,
        "oi_high": oi_high,
        "oi_low": oi_low,
        "day_open": day_open,
        "day_close": day_close,
        "day_high": day_high,
        "day_low": day_low,
        "bids": depth_bids,
        "asks": depth_asks,
        "mode": "full",
    }
