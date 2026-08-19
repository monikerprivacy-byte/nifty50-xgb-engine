"""Dhan WebSocket client — v2 API.

This module re-exports from src.providers.dhan.v2 for backward compatibility.

Protocol reference: https://dhanhq.co/docs/v2/live-market-feed/
"""

from __future__ import annotations

import warnings

from .v2 import (
    DhanWebSocketClient,
    HealthState,
    parse_feed_response,
    build_subscribe_json,
    build_unsubscribe_json,
    DHAN_WS_URL,
    EXCHANGE_SEGMENT,
    TICKER,
    QUOTE,
    FULL,
    FULL_200_DEPTH,
    RequestCode,
    ResponseCode,
)

# Re-export constants for backward compat
REQ_CONNECT = RequestCode.CONNECT
REQ_DISCONNECT = RequestCode.DISCONNECT
REQ_SUB_TICKER = RequestCode.SUB_TICKER
REQ_UNSUB_TICKER = RequestCode.UNSUB_TICKER
REQ_SUB_QUOTE = RequestCode.SUB_QUOTE
REQ_UNSUB_QUOTE = RequestCode.UNSUB_QUOTE
REQ_SUB_FULL = RequestCode.SUB_FULL
REQ_UNSUB_FULL = RequestCode.UNSUB_FULL
REQ_SUB_FULL_DEPTH = RequestCode.SUB_FULL_DEPTH
REQ_UNSUB_FULL_DEPTH = RequestCode.UNSUB_FULL_DEPTH

RESP_INDEX = ResponseCode.INDEX
RESP_TICKER = ResponseCode.TICKER
RESP_QUOTE = ResponseCode.QUOTE
RESP_OI = ResponseCode.OI
RESP_PREV_CLOSE = ResponseCode.PREV_CLOSE
RESP_MARKET_STATUS = ResponseCode.MARKET_STATUS
RESP_FULL = ResponseCode.FULL
RESP_DISCONNECT = ResponseCode.DISCONNECT

__all__ = [
    "DhanWebSocketClient",
    "HealthState",
    "parse_feed_response",
    "build_subscribe_json",
    "build_unsubscribe_json",
    "DHAN_WS_URL",
    "EXCHANGE_SEGMENT",
    "TICKER",
    "QUOTE",
    "FULL",
    "FULL_200_DEPTH",
    "RequestCode",
    "ResponseCode",
    "REQ_CONNECT",
    "REQ_DISCONNECT",
    "REQ_SUB_TICKER",
    "REQ_UNSUB_TICKER",
    "REQ_SUB_QUOTE",
    "REQ_UNSUB_QUOTE",
    "REQ_SUB_FULL",
    "REQ_UNSUB_FULL",
    "REQ_SUB_FULL_DEPTH",
    "REQ_UNSUB_FULL_DEPTH",
    "RESP_INDEX",
    "RESP_TICKER",
    "RESP_QUOTE",
    "RESP_OI",
    "RESP_PREV_CLOSE",
    "RESP_MARKET_STATUS",
    "RESP_FULL",
    "RESP_DISCONNECT",
]
