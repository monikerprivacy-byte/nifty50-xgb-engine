from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from src.bars.schema import Bar, BarBuilder, bucket_time

logger = logging.getLogger(__name__)

BAR_INTERVAL_S = 60
LATE_EVENT_WINDOW_S = 300


class BarEngine:
    def __init__(
        self,
        *,
        now_fn: Callable[[], datetime] | None = None,
        late_event_window_s: int = LATE_EVENT_WINDOW_S,
    ):
        self._now_fn = now_fn or (
            lambda: datetime.now(timezone.utc)
        )
        self._late_event_window_s = late_event_window_s
        self._bars: Dict[Tuple[int, datetime], Bar] = OrderedDict()
        self._builders: Dict[Tuple[int, datetime], BarBuilder] = {}
        self._revision_count: int = 0
        self._late_event_count: int = 0

    def accept(
        self,
        security_id: int,
        event_time: datetime,
        price: float,
        volume: int = 0,
        oi: Optional[int] = None,
        trade_sign: int = 0,
    ) -> Optional[Bar]:
        key = (security_id, bucket_time(event_time, BAR_INTERVAL_S))
        bar_time = key[1]

        now = self._now_fn()

        if event_time.tzinfo is None:
            self._late_event_count += 1
            return None

        age_s = (now - bar_time).total_seconds() if now > bar_time else 0

        if age_s > self._late_event_window_s:
            self._late_event_count += 1
            return None

        builder = self._builders.get(key)
        if builder is None:
            builder = BarBuilder(security_id=security_id, event_time=bar_time)
            self._builders[key] = builder

        builder.record_trade(price=price, volume=volume, oi=oi)
        if trade_sign != 0:
            builder.volume_delta += volume * trade_sign

        return None

    def close_bar(self, security_id: int, event_time: datetime) -> Optional[Bar]:
        key = (security_id, bucket_time(event_time, BAR_INTERVAL_S))
        return self._finalize(key)

    def close_all(self) -> List[Bar]:
        bars = []
        for key in list(self._builders.keys()):
            bar = self._finalize(key)
            if bar:
                bars.append(bar)
        return bars

    def _finalize(self, key: Tuple[int, datetime]) -> Optional[Bar]:
        builder = self._builders.pop(key, None)
        if builder is None:
            return None

        existing = self._bars.get(key)
        if existing is not None:
            corrected = Bar(
                security_id=builder.security_id,
                event_time=builder.event_time,
                open=min(existing.open, builder.open if builder.open is not None else existing.open),
                high=max(existing.high, builder.high if builder.high is not None else existing.high),
                low=min(existing.low, builder.low if builder.low is not None else existing.low),
                close=builder.close if builder.close is not None else existing.close,
                volume=existing.volume + builder.volume,
                volume_delta=(existing.volume_delta or 0) + builder.volume_delta,
                open_interest_open=existing.open_interest_open,
                open_interest_high=max(
                    existing.open_interest_high or 0,
                    builder.oi_high or 0,
                ) if (existing.open_interest_high is not None or builder.oi_high is not None) else None,
                open_interest_low=min(
                    existing.open_interest_low or float("inf"),
                    builder.oi_low or float("inf"),
                ) if (existing.open_interest_low is not None or builder.oi_low is not None) else None,
                open_interest_close=builder.oi_close if builder.oi_close is not None else existing.open_interest_close,
                trade_count=existing.trade_count + builder.trade_count,
                revision=existing.revision + 1,
                is_corrected=True,
                correction_of=builder.event_time,
            )
            self._bars[key] = corrected
            self._revision_count += 1
            return corrected

        bar = builder.build()
        self._bars[key] = bar
        return bar

    def get_bar(self, security_id: int, event_time: datetime) -> Optional[Bar]:
        key = (security_id, bucket_time(event_time, BAR_INTERVAL_S))
        return self._bars.get(key)

    def list_bars(self, security_id: int, start: datetime, end: datetime) -> List[Bar]:
        return [
            bar for (sid, bt), bar in self._bars.items()
            if sid == security_id and start <= bt <= end
        ]

    @property
    def revision_count(self) -> int:
        return self._revision_count

    @property
    def late_event_count(self) -> int:
        return self._late_event_count
