import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd

from .atm import ATMSelector
from .nifty50 import members_at_date, get_current_members
from .fo_eligibility import derive_from_instrument_master
from ..storage.schema import ContractIdentity, UniverseSnapshotRecord

logger = logging.getLogger(__name__)


class UniverseResolver:
    def __init__(self, db_manager):
        self.db = db_manager
        self.atm = ATMSelector(db_manager)

    def get_active_expiries(
        self,
        symbol: str,
        as_of_date: date,
        min_expiries: int = 3,
    ) -> list[date]:
        conn = self.db.get_reader()
        rows = conn.execute(
            """SELECT DISTINCT expiry_date
               FROM instrument_master_history
               WHERE symbol = ?
                 AND instrument_type IN ('OPTSTK', 'OPTIDX')
                 AND expiry_date >= ?
                 AND valid_from <= ?
                 AND (valid_to IS NULL OR valid_to >= ?)
               ORDER BY expiry_date
               LIMIT ?""",
            [symbol, as_of_date, as_of_date, as_of_date, min_expiries],
        ).fetchall()
        return [r[0] for r in rows]

    def resolve_universe(
        self,
        as_of: datetime,
        strike_range_min: int = -5,
        strike_range_max: int = 5,
        expiry_selection: str = "nearest",
    ) -> UniverseSnapshotRecord:
        as_of_date = as_of.date()

        members = members_at_date(as_of_date)
        if not members:
            logger.warning(f"No NIFTY-50 members found for {as_of_date}, using current list")
            members = get_current_members()

        fo_eligible = derive_from_instrument_master(self.db, as_of_date)
        eligible = sorted(set(members) & set(fo_eligible))

        expiry_map: dict[str, date] = {}
        expiry_dates: set[date] = set()

        for symbol in eligible:
            expiries = self.get_active_expiries(symbol, as_of_date)
            if expiries:
                expiry_map[symbol] = expiries[0]
                expiry_dates.add(expiries[0])

        sorted_expiries = sorted(expiry_dates)

        atm_strikes: dict[str, float] = {}
        all_contracts: list[dict] = []

        for symbol in eligible:
            expiry = expiry_map.get(symbol)
            if expiry is None:
                continue

            atm_strike = self.atm.select_atm(symbol, 0.0, as_of_date, expiry)
            if atm_strike is None:
                conn = self.db.get_reader()
                row = conn.execute(
                    """SELECT DISTINCT strike_price
                       FROM instrument_master_history
                       WHERE symbol = ?
                         AND instrument_type IN ('OPTSTK', 'OPTIDX')
                         AND strike_price > 0
                         AND valid_from <= ?
                         AND (valid_to IS NULL OR valid_to >= ?)
                       ORDER BY strike_price""",
                    [symbol, as_of_date, as_of_date],
                ).fetchone()
                if row is None:
                    logger.warning(f"No listed strikes for {symbol}, skipping")
                    continue
                atm_strike = float(row[0])

            atm_strikes[symbol] = atm_strike

            strikes = self.atm.select_around_atm(
                symbol, atm_strike, as_of_date, expiry,
                strike_range_min, strike_range_max,
            )

            contracts = self.atm.build_contracts(symbol, strikes, expiry)
            all_contracts.extend(contracts)

        snapshot_id = str(uuid.uuid4())

        snapshot = UniverseSnapshotRecord(
            universe_snapshot_id=snapshot_id,
            snapshot_timestamp=as_of,
            business_date=as_of_date,
            atm_selection_method="hysteresis",
            expiry_selection=expiry_selection,
            strike_range_min=strike_range_min,
            strike_range_max=strike_range_max,
            eligible_stocks=eligible,
            expiry_dates=sorted_expiries,
            atm_strikes=atm_strikes,
            selected_contracts=[c["contract_identity"] for c in all_contracts],
            contract_count=len(all_contracts),
            is_live=False,
        )

        return snapshot

    def save_snapshot(self, snapshot: UniverseSnapshotRecord):
        w = self.db.get_writer()
        w.execute(
            """INSERT INTO universe_snapshots
               (universe_snapshot_id, snapshot_timestamp, business_date,
                instrument_master_snapshot_date, membership_snapshot_date,
                atm_selection_method, expiry_selection,
                strike_range_min, strike_range_max,
                eligible_stocks, expiry_dates, atm_strikes,
                selected_contracts, contract_count, is_live)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (universe_snapshot_id) DO NOTHING""",
            [
                snapshot.universe_snapshot_id,
                snapshot.snapshot_timestamp,
                snapshot.business_date,
                snapshot.instrument_master_snapshot_date,
                snapshot.membership_snapshot_date,
                snapshot.atm_selection_method,
                snapshot.expiry_selection,
                snapshot.strike_range_min,
                snapshot.strike_range_max,
                json.dumps(snapshot.eligible_stocks),
                json.dumps([str(d) for d in snapshot.expiry_dates]),
                json.dumps(snapshot.atm_strikes),
                json.dumps(snapshot.selected_contracts),
                snapshot.contract_count,
                snapshot.is_live,
            ],
        )

    def resolve_and_save(
        self,
        as_of: datetime,
        strike_range_min: int = -5,
        strike_range_max: int = 5,
    ) -> UniverseSnapshotRecord:
        snapshot = self.resolve_universe(
            as_of, strike_range_min, strike_range_max
        )
        self.save_snapshot(snapshot)
        return snapshot

    def get_latest_snapshot(self) -> Optional[UniverseSnapshotRecord]:
        conn = self.db.get_reader()
        row = conn.execute(
            """SELECT * FROM universe_snapshots
               ORDER BY snapshot_timestamp DESC
               LIMIT 1"""
        ).fetchone()
        if row is None:
            return None

        col_names = [d[0] for d in conn.description]
        row_dict = dict(zip(col_names, row))
        return UniverseSnapshotRecord(
            universe_snapshot_id=row_dict["universe_snapshot_id"],
            snapshot_timestamp=row_dict["snapshot_timestamp"],
            business_date=row_dict["business_date"],
            instrument_master_snapshot_date=row_dict.get("instrument_master_snapshot_date"),
            membership_snapshot_date=row_dict.get("membership_snapshot_date"),
            selected_contracts=json.loads(row_dict.get("selected_contracts", "[]")),
            contract_count=row_dict.get("contract_count", 0),
        )

    def verify_identity(self, contract_identity: str, as_of: date) -> bool:
        try:
            ci = ContractIdentity.from_canonical(contract_identity)
        except (ValueError, IndexError):
            logger.error(f"Malformed contract identity: {contract_identity}")
            return False

        conn = self.db.get_reader()
        row = conn.execute(
            """SELECT count(*) FROM instrument_master_history
               WHERE symbol = ?
                 AND expiry_date = ?
                 AND strike_price = ?
                 AND option_type = ?
                 AND instrument_type IN ('OPTSTK', 'OPTIDX')
                 AND valid_from <= ?
                 AND (valid_to IS NULL OR valid_to >= ?)""",
            [ci.underlying, ci.expiry_date, ci.strike_price, ci.option_type,
             as_of, as_of],
        ).fetchone()
        return row[0] > 0

    def verify_point_in_time(self, symbol: str, as_of: date) -> bool:
        members = members_at_date(as_of)
        return symbol in members

    def get_universe_stats(self, as_of: date) -> dict:
        conn = self.db.get_reader()

        total_instruments = conn.execute(
            """SELECT count(*) FROM instrument_master_history
               WHERE valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)""",
            [as_of, as_of],
        ).fetchone()[0]

        option_contracts = conn.execute(
            """SELECT count(*) FROM instrument_master_history
               WHERE instrument_type IN ('OPTSTK', 'OPTIDX')
                 AND valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)""",
            [as_of, as_of],
        ).fetchone()[0]

        fo_stocks = conn.execute(
            """SELECT count(DISTINCT symbol) FROM instrument_master_history
               WHERE instrument_type IN ('OPTSTK', 'FUTSTK')
                 AND valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)""",
            [as_of, as_of],
        ).fetchone()[0]

        members = members_at_date(as_of)

        return {
            "as_of": str(as_of),
            "nifty50_members": len(members),
            "fo_eligible_stocks": fo_stocks,
            "total_instruments": total_instruments,
            "option_contracts": option_contracts,
            "nifty50_list": members,
        }
