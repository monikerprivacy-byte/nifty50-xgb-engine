from __future__ import annotations

from datetime import datetime, timezone, timedelta
from src.bars.schema import Bar, BarBuilder, bucket_time
from src.bars.engine import BarEngine, BAR_INTERVAL_S, LATE_EVENT_WINDOW_S


FIXED_NOW = datetime(2026, 7, 17, 9, 17, 30, tzinfo=timezone.utc)


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _engine(*, now_fn=None, late_event_window_s=None):
    return BarEngine(
        now_fn=now_fn or (lambda: FIXED_NOW),
        late_event_window_s=late_event_window_s or LATE_EVENT_WINDOW_S,
    )


def test_bucket_time_aligns_to_minute():
    t = _t("2026-07-17T09:15:30")
    b = bucket_time(t, 60)
    assert b == _t("2026-07-17T09:15:00")


def test_bucket_time_aligns_to_five_minutes():
    t = _t("2026-07-17T09:17:30")
    b = bucket_time(t, 300)
    assert b == _t("2026-07-17T09:15:00")


def test_builder_single_trade():
    b = BarBuilder(security_id=100, event_time=_t("2026-07-17T09:15:00"))
    b.record_trade(price=150.0, volume=100, oi=5000)
    bar = b.build()
    assert bar.open == 150.0
    assert bar.high == 150.0
    assert bar.low == 150.0
    assert bar.close == 150.0
    assert bar.volume == 100
    assert bar.trade_count == 1
    assert bar.open_interest_open == 5000
    assert bar.open_interest_close == 5000


def test_builder_multiple_trades():
    b = BarBuilder(security_id=100, event_time=_t("2026-07-17T09:15:00"))
    b.record_trade(price=150.0, volume=100)
    b.record_trade(price=152.0, volume=200)
    b.record_trade(price=148.0, volume=150)
    bar = b.build()
    assert bar.open == 150.0
    assert bar.high == 152.0
    assert bar.low == 148.0
    assert bar.close == 148.0
    assert bar.volume == 450
    assert bar.trade_count == 3


def test_engine_accept_and_close():
    eng = _engine()
    t = _t("2026-07-17T09:15:01")
    eng.accept(security_id=100, event_time=t, price=150.0, volume=100)
    bar = eng.close_bar(100, t)
    assert bar is not None
    assert bar.open == 150.0
    assert bar.high == 150.0
    assert bar.low == 150.0
    assert bar.close == 150.0


def test_engine_buckets_by_minute():
    eng = _engine()
    t1 = _t("2026-07-17T09:15:30")
    t2 = _t("2026-07-17T09:15:45")
    t3 = _t("2026-07-17T09:16:10")

    eng.accept(100, t1, 150.0, 100)
    eng.accept(100, t2, 152.0, 200)
    eng.accept(100, t3, 153.0, 150)

    b1 = eng.close_bar(100, t1)
    b2 = eng.close_bar(100, t3)

    assert b1 is not None
    assert b1.event_time == _t("2026-07-17T09:15:00")
    assert b1.high == 152.0
    assert b1.volume == 300

    assert b2 is not None
    assert b2.event_time == _t("2026-07-17T09:16:00")
    assert b2.close == 153.0
    assert b2.volume == 150


def test_engine_corrects_late_event():
    eng = _engine()
    t = _t("2026-07-17T09:15:01")

    eng.accept(100, t, 150.0, 100)
    b1 = eng.close_bar(100, t)
    assert b1 is not None
    assert b1.revision == 0
    assert not b1.is_corrected

    eng.accept(100, t, 155.0, 50)
    b2 = eng.close_bar(100, t)
    assert b2 is not None
    assert b2.revision == 1
    assert b2.is_corrected
    assert b2.high == 155.0
    assert b2.volume == 150


def test_engine_rejects_very_late_event():
    eng = _engine()
    very_old = FIXED_NOW - timedelta(seconds=LATE_EVENT_WINDOW_S + 10)
    result = eng.accept(100, very_old, 150.0, 100)
    assert result is None
    assert eng.late_event_count == 1
    b = eng.close_bar(100, very_old)
    assert b is None


def test_revision_count():
    eng = _engine()
    t = _t("2026-07-17T09:15:01")

    eng.accept(100, t, 150.0, 100)
    eng.close_bar(100, t)
    assert eng.revision_count == 0

    eng.accept(100, t, 155.0, 50)
    eng.close_bar(100, t)
    assert eng.revision_count == 1

    eng.accept(100, t, 160.0, 25)
    eng.close_bar(100, t)
    assert eng.revision_count == 2


def test_oi_ohlc_tracking():
    b = BarBuilder(security_id=100, event_time=_t("2026-07-17T09:15:00"))
    b.record_trade(price=150, volume=100, oi=5000)
    b.record_trade(price=152, volume=200, oi=5200)
    b.record_trade(price=148, volume=150, oi=5100)
    bar = b.build()
    assert bar.open_interest_open == 5000
    assert bar.open_interest_high == 5200
    assert bar.open_interest_low == 5000
    assert bar.open_interest_close == 5100


def test_list_bars():
    eng = _engine()
    t1 = _t("2026-07-17T09:15:01")
    t2 = _t("2026-07-17T09:16:01")
    t3 = _t("2026-07-17T09:17:01")

    eng.accept(100, t1, 150.0, 100)
    eng.accept(100, t2, 152.0, 200)
    eng.accept(100, t3, 153.0, 150)
    eng.close_all()

    bars = eng.list_bars(
        100,
        _t("2026-07-17T09:15:00"),
        _t("2026-07-17T09:16:30"),
    )
    assert len(bars) == 2
    assert bars[0].event_time == _t("2026-07-17T09:15:00")
    assert bars[1].event_time == _t("2026-07-17T09:16:00")


def test_volume_delta_tracking():
    b = BarBuilder(security_id=100, event_time=_t("2026-07-17T09:15:00"))
    b.record_trade(price=150, volume=100)
    b.volume_delta += 50
    bar = b.build()
    assert bar.volume_delta == 50
    assert bar.volume == 100


def test_close_all():
    eng = _engine()
    eng.accept(100, _t("2026-07-17T09:15:01"), 150.0, 100)
    eng.accept(200, _t("2026-07-17T09:15:01"), 200.0, 50)
    eng.accept(100, _t("2026-07-17T09:16:01"), 152.0, 200)

    bars = eng.close_all()
    assert len(bars) == 3

    bar100 = eng.get_bar(100, _t("2026-07-17T09:15:00"))
    assert bar100 is not None
    assert bar100.security_id == 100


def test_empty_bar_after_no_trades():
    eng = _engine()
    t = _t("2026-07-17T09:15:01")
    bar = eng.close_bar(100, t)
    assert bar is None


def test_get_bar_returns_none_for_missing():
    eng = _engine()
    t = _t("2026-07-17T09:15:00")
    bar = eng.get_bar(100, t)
    assert bar is None


def test_late_event_just_inside_window():
    """Bucket age 270s (< 300) is accepted (event 270s before now)."""
    eng = _engine()
    t = FIXED_NOW - timedelta(seconds=270)
    result = eng.accept(100, t, 150.0, 100)
    assert result is None
    assert eng.late_event_count == 0
    b = eng.close_bar(100, t)
    assert b is not None


def test_late_event_just_outside_window():
    """Bucket age 330s (> 300) is rejected (event 271s before now, bucket floor)."""
    eng = _engine()
    t = FIXED_NOW - timedelta(seconds=271)
    result = eng.accept(100, t, 150.0, 100)
    assert result is None
    assert eng.late_event_count == 1
    b = eng.close_bar(100, t)
    assert b is None


def test_future_timestamp_accepted():
    eng = _engine()
    future = FIXED_NOW + timedelta(hours=1)
    result = eng.accept(100, future, 150.0, 100)
    assert result is None
    assert eng.late_event_count == 0


def test_naive_datetime_rejected():
    eng = _engine()
    t = datetime(2026, 7, 17, 9, 15, 1)
    result = eng.accept(100, t, 150.0, 100)
    assert result is None
    assert eng.late_event_count == 1
