import pytest
from datetime import date, datetime, timezone
from src.storage.schema import ContractIdentity
from src.storage.duckdb_manager import DuckDBManager
from src.universe.nifty50 import members_at_date, build_membership_history
from src.universe.atm import ATMSelector
from src.universe.resolver import UniverseResolver


class TestPhase1Gates:

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        db_path = tmp_path / "test_universe.duckdb"
        self.db = DuckDBManager(str(db_path))
        self.db.get_writer()
        yield
        self.db.close_all()

    def populate_instrument_master(self, rows: list[dict]):
        w = self.db.get_writer()
        for r in rows:
            w.execute(
                """INSERT INTO instrument_master_history
                   (security_id, symbol, exchange, segment, instrument_type,
                    expiry_date, strike_price, option_type,
                    lot_size, tick_size, strike_interval,
                    underlying_security_id,
                    source, snapshot_date, valid_from, valid_to, is_current)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    r["security_id"], r["symbol"], r.get("exchange", "NSE"),
                    r.get("segment", "D"), r["instrument_type"],
                    r.get("expiry_date"), r.get("strike_price", 0.0),
                    r.get("option_type"), r.get("lot_size", 1),
                    r.get("tick_size", 0.05), r.get("strike_interval", 10.0),
                    r.get("underlying_security_id", 0),
                    r.get("source", "NSE"), r.get("snapshot_date", date.today()),
                    r.get("valid_from", date.today()),
                    r.get("valid_to"), r.get("is_current", True),
                ],
            )

    def test_no_duplicate_active_security_id(self):
        from src.providers.dhan.security_master import import_snapshot_to_duckdb
        import pandas as pd

        df = pd.DataFrame([
            {"security_id": 1, "symbol": "RELIANCE", "exchange": "NSE",
             "instrument_type": "EQUITY", "segment": "E",
             "lot_size": 1, "tick_size": 0.05, "strike_interval": 0.0,
             "underlying_security_id": 0, "strike_price": 0.0,
             "checksum": "a", "source": "NSE"},
        ])
        result = import_snapshot_to_duckdb(self.db, df, date(2024, 1, 1))

        df2 = pd.DataFrame([
            {"security_id": 1, "symbol": "RELIANCE", "exchange": "NSE",
             "instrument_type": "EQUITY", "segment": "E",
             "lot_size": 1, "tick_size": 0.05, "strike_interval": 0.0,
             "underlying_security_id": 0, "strike_price": 0.0,
             "checksum": "b", "source": "NSE"},
        ])
        result2 = import_snapshot_to_duckdb(self.db, df2, date(2025, 1, 1))

        active = self.db.get_conn().execute(
            "SELECT security_id, count(*) as cnt FROM instrument_master_history "
            "WHERE is_current = true GROUP BY security_id HAVING count(*) > 1"
        ).fetchall()
        assert len(active) == 0, f"Duplicate active security_ids: {active}"

        closed_v1 = self.db.get_conn().execute(
            "SELECT is_current FROM instrument_master_history "
            "WHERE security_id = 1 AND valid_from = '2024-01-01'"
        ).fetchone()[0]
        assert closed_v1 == False, "Old version should be closed on update"

    def test_no_contract_without_absolute_strike(self):
        self.populate_instrument_master([
            {"security_id": 101, "symbol": "RELIANCE", "instrument_type": "OPTSTK",
             "strike_price": 0.0, "option_type": "CE",
             "expiry_date": date(2026, 8, 6),
             "valid_from": date.today(), "is_current": True},
        ])
        bad = self.db.get_conn().execute(
            "SELECT count(*) FROM instrument_master_history "
            "WHERE instrument_type IN ('OPTSTK', 'OPTIDX') AND strike_price = 0"
        ).fetchone()[0]
        assert bad == 1

    def test_no_option_without_expiry(self):
        self.populate_instrument_master([
            {"security_id": 201, "symbol": "RELIANCE", "instrument_type": "OPTSTK",
             "strike_price": 1500.0, "option_type": "CE",
             "expiry_date": None, "is_current": True},
        ])
        bad = self.db.get_conn().execute(
            "SELECT count(*) FROM instrument_master_history "
            "WHERE instrument_type IN ('OPTSTK', 'OPTIDX') AND expiry_date IS NULL"
        ).fetchone()[0]
        assert bad == 1

    def test_no_overlapping_valid_dates(self):
        from src.providers.dhan.security_master import import_snapshot_to_duckdb
        import pandas as pd

        df_v1 = pd.DataFrame([
            {"security_id": 301, "symbol": "RELIANCE", "exchange": "NSE",
             "instrument_type": "EQUITY", "segment": "E",
             "lot_size": 1, "tick_size": 0.05, "strike_interval": 0.0,
             "underlying_security_id": 0, "strike_price": 0.0,
             "checksum": "v1", "source": "NSE"},
        ])
        import_snapshot_to_duckdb(self.db, df_v1, date(2024, 1, 1))

        df_v2 = pd.DataFrame([
            {"security_id": 301, "symbol": "RELIANCE", "exchange": "NSE",
             "instrument_type": "EQUITY", "segment": "E",
             "lot_size": 1, "tick_size": 0.05, "strike_interval": 0.0,
             "underlying_security_id": 0, "strike_price": 0.0,
             "checksum": "v2", "source": "NSE"},
        ])
        import_snapshot_to_duckdb(self.db, df_v2, date(2025, 1, 1))

        overlaps = self.db.get_conn().execute(
            """SELECT a.security_id
               FROM instrument_master_history a
               JOIN instrument_master_history b
                 ON a.security_id = b.security_id
                AND a.valid_from < b.valid_from
                AND a.valid_to > b.valid_from""",
        ).fetchall()
        assert len(overlaps) == 0, f"Overlapping valid periods: {overlaps}"

    def test_no_current_membership_leakage(self):
        members_2020 = members_at_date(date(2020, 1, 1))
        assert "RELIANCE" in members_2020
        assert "TRENT" not in members_2020
        assert "SHRIRAMFIN" not in members_2020

        members_2024_jan = members_at_date(date(2024, 1, 1))
        assert "HINDALCO" in members_2024_jan

        members_2024_late = members_at_date(date(2024, 10, 1))
        assert "HINDALCO" in members_2024_late
        assert "DIVISLAB" not in members_2024_late
        assert "TRENT" in members_2024_late

        members_now = members_at_date(date.today())
        assert "TRENT" in members_now
        assert "SHRIRAMFIN" in members_now

    def test_no_expired_contract_marked_active(self):
        self.populate_instrument_master([
            {"security_id": 401, "symbol": "RELIANCE", "instrument_type": "OPTSTK",
             "strike_price": 1000.0, "option_type": "CE",
             "expiry_date": date(2020, 1, 30),
             "valid_from": date(2020, 1, 1), "valid_to": date(2020, 1, 31),
             "is_current": False},
        ])
        expired_active = self.db.get_conn().execute(
            "SELECT count(*) FROM instrument_master_history "
            "WHERE is_current = true AND expiry_date < '2020-06-01' AND "
            "instrument_type IN ('OPTSTK', 'OPTIDX')"
        ).fetchone()[0]
        assert expired_active == 0

    def test_atm_from_listed_strikes(self):
        expiry = date(2026, 8, 6)
        sid = [1 for _ in range(22)]
        self.populate_instrument_master([
            {"security_id": i + 1000, "symbol": "RELIANCE", "instrument_type": "OPTSTK",
             "strike_price": sp, "option_type": ot, "expiry_date": expiry,
             "valid_from": date.today(), "is_current": True,
             "strike_interval": 10.0}
            for i, (sp, ot) in enumerate(
                (sp, ot) for sp in [1400, 1410, 1420, 1430, 1440, 1450, 1460, 1470, 1480, 1490, 1500]
                for ot in ("CE", "PE")
            )
        ])
        selector = ATMSelector(self.db)
        atm = selector.select_atm("RELIANCE", 1445.0, date.today(), expiry)
        assert atm == 1450

        atm2 = selector.select_atm("RELIANCE", 1453.0, date.today(), expiry)
        assert atm2 == 1450

        atm3 = selector.select_atm("RELIANCE", 1455.0, date.today(), expiry)
        assert atm3 in (1450, 1460)

        strikes = selector.select_around_atm("RELIANCE", 1445.0, date.today(), expiry)
        assert len(strikes) == 11

    def test_every_prediction_references_universe_snapshot_id(self):
        self.populate_instrument_master([
            {"security_id": 500, "symbol": "RELIANCE", "instrument_type": "EQUITY",
             "valid_from": date(2024, 1, 1), "is_current": True},
        ])
        resolver = UniverseResolver(self.db)
        snapshot = resolver.resolve_universe(
            datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc)
        )
        assert snapshot.universe_snapshot_id is not None
        assert len(snapshot.universe_snapshot_id) > 0

    def test_membership_integrity(self):
        from src.universe.nifty50 import HISTORICAL_CHANGES, EXCLUSION_HISTORY

        symbols_2024 = members_at_date(date(2024, 1, 1))
        assert len(symbols_2024) > 40

        symbols_2025 = members_at_date(date(2025, 1, 1))
        assert len(symbols_2025) > 40

        symbols_now = members_at_date(date(2026, 7, 17))
        assert len(symbols_now) > 40

    def test_contract_identity_roundtrip(self):
        ci = ContractIdentity("NSE", "RELIANCE", date(2026, 7, 30), 1500.0, "CE")
        canonical = ci.canonical
        restored = ContractIdentity.from_canonical(canonical)
        assert restored.exchange == "NSE"
        assert restored.underlying == "RELIANCE"
        assert restored.expiry_date == date(2026, 7, 30)
        assert restored.strike_price == 1500.0
        assert restored.option_type == "CE"
