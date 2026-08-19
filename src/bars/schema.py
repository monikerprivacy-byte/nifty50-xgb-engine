"""Authoritative 1-minute executable bar schema.

Every bar is built from raw tick/quote/OHLC events and represents the
canonical record for a contract over a wall-clock minute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Bar:
    security_id: int
    event_time: datetime  # start of the bar minute (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: int
    volume_delta: Optional[int]  # cumulative buy-sell volume delta
    open_interest_open: Optional[int]
    open_interest_high: Optional[int]
    open_interest_low: Optional[int]
    open_interest_close: Optional[int]
    trade_count: int
    revision: int = 0  # incremented on late-event correction
    is_corrected: bool = False
    correction_of: Optional[datetime] = None  # source bar time if corrected


BAR_FIELDS = [
    "security_id", "event_time", "open", "high", "low", "close",
    "volume", "volume_delta", "open_interest_open", "open_interest_high",
    "open_interest_low", "open_interest_close", "trade_count",
    "revision", "is_corrected", "correction_of",
]


@dataclass
class BarBuilder:
    security_id: int
    event_time: datetime
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: int = 0
    volume_delta: int = 0
    oi_open: Optional[int] = None
    oi_high: Optional[int] = None
    oi_low: Optional[int] = None
    oi_close: Optional[int] = None
    trade_count: int = 0

    def record_trade(self, price: float, volume: int, oi: Optional[int] = None):
        if self.open is None:
            self.open = price
        self.high = price if self.high is None else max(self.high, price)
        self.low = price if self.low is None else min(self.low, price)
        self.close = price
        self.volume += volume
        self.trade_count += 1

        if oi is not None:
            if self.oi_open is None:
                self.oi_open = oi
            self.oi_high = oi if self.oi_high is None else max(self.oi_high, oi)
            self.oi_low = oi if self.oi_low is None else min(self.oi_low, oi)
            self.oi_close = oi

    def build(self) -> Bar:
        o = self.open if self.open is not None else 0.0
        h = self.high if self.high is not None else o
        l = self.low if self.low is not None else o
        c = self.close if self.close is not None else o
        return Bar(
            security_id=self.security_id,
            event_time=self.event_time,
            open=o, high=h, low=l, close=c,
            volume=self.volume,
            volume_delta=self.volume_delta,
            open_interest_open=self.oi_open,
            open_interest_high=self.oi_high,
            open_interest_low=self.oi_low,
            open_interest_close=self.oi_close,
            trade_count=self.trade_count,
        )


def bucket_time(event_time: datetime, interval_s: int = 60) -> datetime:
    ts = int(event_time.timestamp())
    bucket = ts - (ts % interval_s)
    return datetime.fromtimestamp(bucket, tz=event_time.tzinfo)
