import logging
from datetime import date, datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class ATMSelector:
    def __init__(self, db_manager, hysteresis_fraction: float = 0.5):
        self.db = db_manager
        self.hysteresis_fraction = hysteresis_fraction
        self._current_atm: dict[str, float] = {}

    def get_listed_strikes(
        self,
        symbol: str,
        expiry_date: date,
        as_of_date: date,
    ) -> list[float]:
        conn = self.db.get_reader()
        rows = conn.execute(
            """SELECT DISTINCT strike_price
               FROM instrument_master_history
               WHERE symbol = ?
                 AND expiry_date = ?
                 AND instrument_type IN ('OPTSTK', 'OPTIDX')
                 AND strike_price > 0
                 AND valid_from <= ?
                 AND (valid_to IS NULL OR valid_to >= ?)
               ORDER BY strike_price""",
            [symbol, expiry_date, as_of_date, as_of_date],
        ).fetchall()
        return [float(r[0]) for r in rows]

    def get_underlying_price(self, symbol: str, as_of: datetime) -> Optional[float]:
        return None

    def select_atm(
        self,
        symbol: str,
        underlying_price: float,
        as_of_date: date,
        expiry_date: date,
    ) -> Optional[float]:
        listed = self.get_listed_strikes(symbol, expiry_date, as_of_date)
        if not listed:
            logger.warning(f"No listed strikes for {symbol} expiry {expiry_date}")
            return None

        nearest = min(listed, key=lambda s: (abs(s - underlying_price), -s))

        key = f"{symbol}|{expiry_date}"

        if key in self._current_atm and nearest != self._current_atm[key]:
            current = self._current_atm[key]
            gaps = [listed[i + 1] - listed[i] for i in range(len(listed) - 1)]
            strike_gap = min(gaps) if gaps else 0.0
            if strike_gap > 0:
                shift = abs(nearest - current)
                ratio = shift / strike_gap
                if ratio < self.hysteresis_fraction:
                    return current

        self._current_atm[key] = nearest
        return nearest

    def select_around_atm(
        self,
        symbol: str,
        underlying_price: float,
        as_of_date: date,
        expiry_date: date,
        range_min: int = -5,
        range_max: int = 5,
    ) -> list[float]:
        atm_strike = self.select_atm(symbol, underlying_price, as_of_date, expiry_date)
        if atm_strike is None:
            return []

        listed = self.get_listed_strikes(symbol, expiry_date, as_of_date)
        if not listed:
            return []

        try:
            idx = listed.index(atm_strike)
        except ValueError:
            idx = min(range(len(listed)), key=lambda i: abs(listed[i] - atm_strike))
            atm_strike = listed[idx]

        start = max(0, idx + range_min)
        end = min(len(listed), idx + range_max + 1)

        selected = listed[start:end]

        if len(selected) < (range_max - range_min + 1):
            logger.debug(
                f"ATM±{range_min}/{range_max} for {symbol} returned {len(selected)} "
                f"strikes (wanted {range_max - range_min + 1})"
            )

        return selected

    def build_contracts(
        self,
        symbol: str,
        strikes: list[float],
        expiry_date: date,
    ) -> list[dict]:
        contracts = []
        for strike in strikes:
            for opt_type in ("CE", "PE"):
                contracts.append({
                    "exchange": "NSE",
                    "underlying": symbol,
                    "expiry_date": str(expiry_date),
                    "strike_price": strike,
                    "option_type": opt_type,
                    "contract_identity": f"NSE|{symbol}|{expiry_date}|{strike}|{opt_type}",
                })
        return contracts

    def reset_hysteresis(self, symbol: Optional[str] = None, expiry: Optional[date] = None):
        if symbol and expiry:
            self._current_atm.pop(f"{symbol}|{expiry}", None)
        elif symbol:
            keys = [k for k in self._current_atm if k.startswith(f"{symbol}|")]
            for k in keys:
                self._current_atm.pop(k, None)
        else:
            self._current_atm.clear()
