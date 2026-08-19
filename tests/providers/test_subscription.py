import pytest
from datetime import date, datetime
from src.storage.duckdb_manager import DuckDBManager
from src.providers.dhan.subscription import SubscriptionEngine
from src.universe.resolver import UniverseResolver


def _build_test_db(db_path):
    db = DuckDBManager(str(db_path))
    w = db.get_writer()
    expiry = date(2026, 8, 6)
    symbols = ["RELIANCE", "TCS", "HDFCBANK"]

    sid = 1
    for sym in symbols:
        for sp in [1500, 1510, 1520, 1530, 1540, 1550, 1560, 1570, 1580, 1590, 1600]:
            for ot in ("CE", "PE"):
                w.execute(
                    """INSERT INTO instrument_master_history
                       (security_id, symbol, exchange, segment, instrument_type,
                        expiry_date, strike_price, option_type,
                        lot_size, tick_size, strike_interval,
                        underlying_security_id, source,
                        snapshot_date, valid_from, valid_to, is_current)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [sid, sym, "NSE", "D", "OPTSTK", expiry,
                     float(sp), ot, 250, 0.05, 10.0, sid, "NSE",
                     date.today(), date.today(), None, True],
                )
                sid += 1

    for sym in symbols:
        w.execute(
            """INSERT INTO instrument_master_history
               (security_id, symbol, exchange, segment, instrument_type,
                source, snapshot_date, valid_from, valid_to, is_current)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [sid, sym, "NSE", "E", "EQUITY", "NSE",
             date.today(), date.today(), None, True],
        )
        sid += 1

    resolver = UniverseResolver(db)
    snapshot = resolver.resolve_universe(datetime.now())
    assert snapshot.contract_count > 0, f"Resolver returned 0 contracts! eligible={snapshot.eligible_stocks}"
    return db


class TestSubscriptionEngine:
    def test_refresh_subscribes_contracts(self, tmp_path):
        db = _build_test_db(tmp_path / "test1.duckdb")
        engine = SubscriptionEngine(db, None)
        ids = engine._refresh_universe()
        assert len(ids) > 0
        assert engine.get_current_snapshot_id() is not None
        db.close_all()

    def test_contract_to_security_id_mapping(self, tmp_path):
        db = _build_test_db(tmp_path / "test2.duckdb")
        engine = SubscriptionEngine(db, None)
        engine._refresh_universe()
        contracts = engine.get_current_contracts()
        assert len(contracts) > 0
        for ci, sid in contracts.items():
            assert "|" in ci
            assert isinstance(sid, int)
            assert sid > 0
        db.close_all()
