"""Dhan v2 protocol constants and binary layouts."""

from __future__ import annotations

import struct
from enum import IntEnum

# WebSocket endpoint
DHAN_WS_URL = "wss://api-feed.dhan.co"

# Feed request codes (sent from client)
class RequestCode(IntEnum):
    CONNECT = 11
    DISCONNECT = 12
    SUB_TICKER = 15
    UNSUB_TICKER = 16
    SUB_QUOTE = 17
    UNSUB_QUOTE = 18
    SUB_FULL = 21
    UNSUB_FULL = 22
    SUB_FULL_DEPTH = 23
    UNSUB_FULL_DEPTH = 24

# Feed response codes (received from server)
class ResponseCode(IntEnum):
    INDEX = 1
    TICKER = 2
    QUOTE = 4
    OI = 5
    PREV_CLOSE = 6
    MARKET_STATUS = 7
    FULL = 8
    DISCONNECT = 50

# Subscription modes
TICKER = 1
QUOTE = 2
FULL = 3
FULL_200_DEPTH = 4

MODE_TO_REQ = {
    TICKER: RequestCode.SUB_TICKER,
    QUOTE: RequestCode.SUB_QUOTE,
    FULL: RequestCode.SUB_FULL,
}
MODE_TO_UNSUB_REQ = {
    TICKER: RequestCode.UNSUB_TICKER,
    QUOTE: RequestCode.UNSUB_QUOTE,
    FULL: RequestCode.UNSUB_FULL,
}

EXCHANGE_SEGMENT = "NSE_FNO"

# Header: FeedResponseCode(1B) + MessageLength(2B LE) + ExchangeSegment(1B) + SecurityID(4B LE)
HEADER_FMT = "<BHbI"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# Ticker payload: float32 LTP, int32 LTT
TICKER_PAYLOAD_FMT = "<fI"
TICKER_PAYLOAD_SIZE = struct.calcsize(TICKER_PAYLOAD_FMT)

# Quote payload: float32 LTP, int16 LTQ, int32 LTT, float32 ATP,
#                int32 Volume, int32 SellQty, int32 BuyQty,
#                float32 Open, float32 Close, float32 High, float32 Low
QUOTE_PAYLOAD_FMT = "<fhIfIIIffff"
QUOTE_PAYLOAD_SIZE = struct.calcsize(QUOTE_PAYLOAD_FMT)

# OI payload: int32 OI
OI_PAYLOAD_SIZE = 4

# Full payload base
FULL_BASE_FMT = "<fhIfIIIiiiiffff"
FULL_BASE_SIZE = struct.calcsize(FULL_BASE_FMT)
DEPTH_PACKET_SIZE = 20
FULL_TOTAL_SIZE = FULL_BASE_SIZE + 100  # 5 depth levels × 20 bytes
