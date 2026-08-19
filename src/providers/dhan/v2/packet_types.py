"""Dhan v2 packet type definitions — response codes as an enum."""

from __future__ import annotations

from .protocol import ResponseCode


PACKET_TYPE_NAMES = {
    ResponseCode.INDEX: "index",
    ResponseCode.TICKER: "ticker",
    ResponseCode.QUOTE: "quote",
    ResponseCode.OI: "quote",
    ResponseCode.PREV_CLOSE: "ticker",
    ResponseCode.MARKET_STATUS: "market_status",
    ResponseCode.FULL: "full",
    ResponseCode.DISCONNECT: "disconnect",
}
