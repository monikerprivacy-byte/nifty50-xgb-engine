"""Feature computation engine — batch and incremental paths for parity."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from src.bars.schema import Bar

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ATR_PERIOD = 14
RSI_PERIOD = 14
RVOL_PERIOD = 20
EXPIRY_2026_07_21 = datetime(2026, 7, 21, 15, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _true_range(bar: Bar, prev_close: Optional[float]) -> float:
    if prev_close is None:
        return bar.high - bar.low
    return max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))


# ---------------------------------------------------------------------------
# Batch feature engine
# ---------------------------------------------------------------------------

class BatchFeatureEngine:
    """Compute features over the full bar set at once."""

    def compute(self, bars: List[Bar]) -> Dict[Tuple[int, datetime], Dict[str, float]]:
        """Return dict keyed by (security_id, event_time) -> feature dict."""
        by_sid: Dict[int, List[Bar]] = defaultdict(list)
        for b in bars:
            by_sid[b.security_id].append(b)

        result: Dict[Tuple[int, datetime], Dict[str, float]] = {}

        for sid, sbars in by_sid.items():
            sbars.sort(key=lambda x: x.event_time)
            n = len(sbars)
            closes = [b.close for b in sbars]
            opens = [b.open for b in sbars]
            highs = [b.high for b in sbars]
            lows = [b.low for b in sbars]
            volumes = [b.volume for b in sbars]
            oi_closes = [b.open_interest_close for b in sbars]

            # Returns (None for first bar — no prev_close)
            returns = [None]
            for i in range(1, n):
                if closes[i - 1] is not None and closes[i - 1] != 0:
                    returns.append((closes[i] - closes[i - 1]) / closes[i - 1])
                else:
                    returns.append(None)

            # Range
            ranges = []
            for i in range(n):
                r = (highs[i] - lows[i]) / closes[i] if closes[i] != 0 else 0.0
                ranges.append(r)

            # True range
            trs = []
            for i in range(n):
                prev_c = closes[i - 1] if i > 0 else None
                trs.append(_true_range(sbars[i], prev_c))

            # ATR
            atrs = [None] * min(ATR_PERIOD - 1, n)
            if n >= ATR_PERIOD:
                avg = sum(trs[:ATR_PERIOD]) / ATR_PERIOD
                atrs.append(avg)
                for i in range(ATR_PERIOD, n):
                    avg = (avg * (ATR_PERIOD - 1) + trs[i]) / ATR_PERIOD
                    atrs.append(avg)

            # VWAP (cumulative)
            vwaps = []
            cum_pv = 0.0
            cum_v = 0
            for i in range(n):
                cum_pv += (highs[i] + lows[i] + closes[i]) / 3.0 * volumes[i]
                cum_v += volumes[i]
                vwaps.append(cum_pv / cum_v if cum_v > 0 else 0.0)

            # RSI — first value at bar index RSI_PERIOD (15th bar for RSI 14)
            rsis = [None] * n
            if n > RSI_PERIOD:
                gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
                losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]
                avg_gain = sum(gains[:RSI_PERIOD]) / RSI_PERIOD
                avg_loss = sum(losses[:RSI_PERIOD]) / RSI_PERIOD
                rsis[RSI_PERIOD] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss) if avg_loss > 0 else 100.0
                for i in range(RSI_PERIOD + 1, n):
                    avg_gain = (avg_gain * (RSI_PERIOD - 1) + gains[i - 1]) / RSI_PERIOD
                    avg_loss = (avg_loss * (RSI_PERIOD - 1) + losses[i - 1]) / RSI_PERIOD
                    rsis[i] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss) if avg_loss > 0 else 100.0

            # RVOL
            rvols = [None] * min(RVOL_PERIOD - 1, n)
            if n >= RVOL_PERIOD:
                for i in range(RVOL_PERIOD - 1, n):
                    avg_v = sum(volumes[i - RVOL_PERIOD + 1:i + 1]) / RVOL_PERIOD
                    rvols.append(volumes[i] / avg_v if avg_v > 0 else 0.0)

            # OI change
            oi_changes = [None]
            for i in range(1, n):
                if oi_closes[i - 1] is not None and oi_closes[i - 1] != 0 and oi_closes[i] is not None:
                    oi_changes.append((oi_closes[i] - oi_closes[i - 1]) / oi_closes[i - 1])
                else:
                    oi_changes.append(None)

            # Minutes to expiry
            mins_to_exp = [(EXPIRY_2026_07_21 - b.event_time).total_seconds() / 60.0 for b in sbars]

            for i, b in enumerate(sbars):
                row = {
                    "returns": returns[i],
                    "range": ranges[i],
                    "atr": atrs[i] if i < len(atrs) else None,
                    "vwap": vwaps[i],
                    "rsi": rsis[i],
                    "rvol": rvols[i] if i < len(rvols) else None,
                    "oi_change": oi_changes[i],
                    "minutes_to_expiry": mins_to_exp[i],
                }
                result[(sid, b.event_time)] = row

        return result


# ---------------------------------------------------------------------------
# Incremental feature engine
# ---------------------------------------------------------------------------

class IncrementalFeatureEngine:
    """Feed bars one-by-one in timestamp order. Yields identical feature rows."""

    def __init__(self):
        self._state: Dict[int, dict] = defaultdict(lambda: {
            "prev_close": None,
            "tr_sum": 0.0,
            "atr": None,
            "atr_count": 0,
            "cum_pv": 0.0,
            "cum_v": 0,
            "prev_close_rsi": None,
            "avg_gain": 0.0,
            "avg_loss": 0.0,
            "rsi_count": 0,
            "vol_history": [],
            "prev_oi_close": None,
            "bar_count": 0,
        })

    def accept(self, bar: Bar) -> Dict[str, float]:
        s = self._state[bar.security_id]
        s["bar_count"] += 1

        close = bar.close
        high = bar.high
        low = bar.low
        volume = bar.volume

        # Returns
        ret = None
        if s["prev_close"] is not None and s["prev_close"] != 0:
            ret = (close - s["prev_close"]) / s["prev_close"]

        # Range
        rng = (high - low) / close if close != 0 else 0.0

        # ATR
        atr_val = None
        tr = _true_range(bar, s["prev_close"])
        if s["atr_count"] < ATR_PERIOD:
            s["tr_sum"] += tr
            s["atr_count"] += 1
            if s["atr_count"] == ATR_PERIOD:
                s["atr"] = s["tr_sum"] / ATR_PERIOD
                atr_val = s["atr"]
        else:
            s["atr"] = (s["atr"] * (ATR_PERIOD - 1) + tr) / ATR_PERIOD
            atr_val = s["atr"]

        # VWAP
        typ_price = (high + low + close) / 3.0
        s["cum_pv"] += typ_price * volume
        s["cum_v"] += volume
        vwap = s["cum_pv"] / s["cum_v"] if s["cum_v"] > 0 else 0.0

        # RSI
        rsi_val = None
        if s["prev_close_rsi"] is not None:
            gain = max(close - s["prev_close_rsi"], 0.0)
            loss = max(s["prev_close_rsi"] - close, 0.0)
            if s["rsi_count"] < RSI_PERIOD:
                s["avg_gain"] += gain
                s["avg_loss"] += loss
                s["rsi_count"] += 1
                if s["rsi_count"] == RSI_PERIOD:
                    ag = s["avg_gain"] / RSI_PERIOD
                    al = s["avg_loss"] / RSI_PERIOD
                    rsi_val = 100.0 - 100.0 / (1.0 + ag / al) if al > 0 else 100.0
                    s["avg_gain"] = ag
                    s["avg_loss"] = al
            else:
                s["avg_gain"] = (s["avg_gain"] * (RSI_PERIOD - 1) + gain) / RSI_PERIOD
                s["avg_loss"] = (s["avg_loss"] * (RSI_PERIOD - 1) + loss) / RSI_PERIOD
                rsi_val = 100.0 - 100.0 / (1.0 + s["avg_gain"] / s["avg_loss"]) if s["avg_loss"] > 0 else 100.0
        s["prev_close_rsi"] = close

        # RVOL
        rvol_val = None
        s["vol_history"].append(volume)
        if len(s["vol_history"]) >= RVOL_PERIOD:
            avg_v = sum(s["vol_history"][-RVOL_PERIOD:]) / RVOL_PERIOD
            rvol_val = volume / avg_v if avg_v > 0 else 0.0

        # OI change
        oi_chg = None
        if s["prev_oi_close"] is not None and s["prev_oi_close"] != 0 and bar.open_interest_close is not None:
            oi_chg = (bar.open_interest_close - s["prev_oi_close"]) / s["prev_oi_close"]
        s["prev_oi_close"] = bar.open_interest_close

        # Minutes to expiry
        mins_to_exp = (EXPIRY_2026_07_21 - bar.event_time).total_seconds() / 60.0

        s["prev_close"] = close

        return {
            "returns": ret,
            "range": rng,
            "atr": atr_val,
            "vwap": vwap,
            "rsi": rsi_val,
            "rvol": rvol_val,
            "oi_change": oi_chg,
            "minutes_to_expiry": mins_to_exp,
        }
