import hashlib
import logging
from datetime import date, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

SEBI_MWPL_KNOWN_BANS: list[dict] = []


def derive_from_instrument_master(
    db_manager,
    target_date: date,
) -> list[str]:
    conn = db_manager.get_reader()

    rows = conn.execute(
        """SELECT DISTINCT symbol
           FROM instrument_master_history
           WHERE instrument_type IN ('OPTSTK', 'FUTSTK')
             AND valid_from <= ?
             AND (valid_to IS NULL OR valid_to >= ?)
             AND is_current = true
           ORDER BY symbol""",
        [target_date, target_date],
    ).fetchall()

    return [r[0] for r in rows]


def get_nse_fo_list() -> Optional[list[str]]:
    try:
        symbols = derive_from_instrument_master(None, date.today())
        return symbols
    except Exception:
        pass
    return None


def build_fo_eligibility_history(
    db_manager,
    start_date: date,
    end_date: date,
) -> list[dict]:
    fo_symbols_over_time: dict[str, list[tuple[date, date]]] = {}

    current = start_date
    conn = db_manager.get_reader() if db_manager else None

    while current <= end_date:
        rows = conn.execute(
            """SELECT DISTINCT symbol
               FROM instrument_master_history
               WHERE instrument_type IN ('OPTSTK', 'FUTSTK')
                 AND valid_from <= ?
                 AND (valid_to IS NULL OR valid_to >= ?)
               ORDER BY symbol""",
            [current, current],
        ).fetchall() if conn else []

        symbols = {r[0] for r in rows}
        for sym in symbols:
            if sym not in fo_symbols_over_time:
                fo_symbols_over_time[sym] = []
            existing = fo_symbols_over_time[sym]
            if existing and existing[-1][1] is None:
                continue
            elif existing and existing[-1][1] >= current - timedelta(days=5):
                continue
            else:
                existing.append((current, None))

        current += timedelta(days=7)

    records = []
    for symbol, periods in fo_symbols_over_time.items():
        for period in periods:
            records.append({
                "symbol": symbol,
                "effective_from": period[0],
                "effective_to": period[1],
                "is_current": period[1] is None,
            })

    return records


def import_fo_eligibility(db_manager, target_date: Optional[date] = None):
    if target_date is None:
        target_date = date.today()

    conn = db_manager.get_writer()

    derived = conn.execute(
        """SELECT DISTINCT symbol
           FROM instrument_master_history
           WHERE instrument_type IN ('OPTSTK', 'FUTSTK')
             AND valid_from <= ?
             AND (valid_to IS NULL OR valid_to >= ?)
             AND is_current = true""",
        [target_date, target_date],
    ).fetchall()

    for (symbol,) in derived:
        conn.execute(
            """INSERT INTO fo_eligibility_history
               (symbol, effective_from, effective_to, is_current, source)
               VALUES (?, ?, NULL, true, 'derived')
               ON CONFLICT (symbol, effective_from) DO UPDATE SET
                 is_current = true,
                 effective_to = NULL""",
            [symbol, target_date],
        )

    existing_current = conn.execute(
        """SELECT symbol FROM fo_eligibility_history
           WHERE is_current = true AND effective_from < ?
             AND symbol NOT IN (SELECT DISTINCT symbol FROM instrument_master_history
                                WHERE instrument_type IN ('OPTSTK', 'FUTSTK')
                                  AND valid_from <= ?
                                  AND (valid_to IS NULL OR valid_to >= ?)
                                  AND is_current = true)""",
        [target_date, target_date, target_date],
    ).fetchall()

    for (symbol,) in existing_current:
        conn.execute(
            """UPDATE fo_eligibility_history
               SET effective_to = ?, is_current = false
               WHERE symbol = ? AND is_current = true""",
            [target_date, symbol],
        )

    total = conn.execute("SELECT count(*) FROM fo_eligibility_history").fetchone()[0]
    return {"total_records": total, "current_date": str(target_date)}
