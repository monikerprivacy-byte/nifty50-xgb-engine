"""Dhan v2 WebSocket API implementation."""

from __future__ import annotations

from .protocol import (
    DHAN_WS_URL,
    EXCHANGE_SEGMENT,
    TICKER,
    QUOTE,
    FULL,
    FULL_200_DEPTH,
    RequestCode,
    ResponseCode,
)
from .decoder import parse_feed_response
from .subscriptions import build_subscribe_json, build_unsubscribe_json
from .client import DhanWebSocketClient, HealthState

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
]
