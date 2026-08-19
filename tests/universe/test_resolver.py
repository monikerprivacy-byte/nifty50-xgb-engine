import pytest
from datetime import date, datetime, timezone
from src.storage.duckdb_manager import DuckDBManager
from src.universe.resolver import UniverseResolver
from src.universe.nifty50 import members_at_date


AS_OF_DATE = date(2026, 7, 17)


class TestUniverseResolver:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        db_path = tmp_path / "test_resolver.duckdb"
        self.db = DuckDBManager(str(db_path))
        w = self.db.get_writer()

        expiry = date(2026, 8, 6)
        symbols = [
            "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
            "SBIN", "BHARTIARTL", "ITC", "WIPRO", "HINDUNILVR",
        ]

        sid = 1
        for sym in symbols:
            for sp in range(1200, 2001, 10):
                for ot in ("CE", "PE"):
                    w.execute(
                        """INSERT INTO instrument_master_history
                           (security_id, symbol, exchange, segment, instrument_type,
                            expiry_date, strike_price, option_type,
                            lot_size, tick_size, strike_interval,
                            underlying_security_id, source,
                            snapshot_date, valid_from, valid_to, is_current)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        [
                            sid, sym, "NSE", "D", "OPTSTK",
                            expiry, float(sp), ot,
                            75 if sym in ("TCS", "INFY", "WIPRO") else 250,
                            0.05, 10.0, sid, "NSE",
                            AS_OF_DATE, AS_OF_DATE, None, True,
                        ],
                    )
                    sid += 1

        yield
        self.db.close_all()

    def test_resolve_universe_returns_valid_snapshot(self):
        resolver = UniverseResolver(self.db)
        snapshot = resolver.resolve_universe(
            datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc)
        )
        assert snapshot.universe_snapshot_id is not None
        assert snapshot.contract_count >= 100
        assert len(snapshot.eligible_stocks) >= 10
        assert len(snapshot.atm_strikes) >= 10

    def test_save_and_retrieve_snapshot(self):
        resolver = UniverseResolver(self.db)
        original = resolver.resolve_and_save(
            datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc)
        )
        loaded = resolver.get_latest_snapshot()
        assert loaded is not None
        assert loaded.universe_snapshot_id == original.universe_snapshot_id

    def test_contract_identity_verification(self):
        resolver = UniverseResolver(self.db)
        valid = resolver.verify_identity(
            "NSE|RELIANCE|2026-08-06|1500|CE",
            AS_OF_DATE,
        )
        assert valid

        invalid = resolver.verify_identity(
            "NSE|FAKECO|2026-08-06|1500|CE",
            AS_OF_DATE,
        )
        assert not invalid

    def test_universe_stats(self):
        resolver = UniverseResolver(self.db)
        stats = resolver.get_universe_stats(AS_OF_DATE)
        assert stats["fo_eligible_stocks"] >= 10
        assert stats["total_instruments"] >= 1000

    def test_active_expiries(self):
        resolver = UniverseResolver(self.db)
        expiries = resolver.get_active_expiries("RELIANCE", AS_OF_DATE)
        assert len(expiries) >= 1
        for e in expiries:
            assert e >= AS_OF_DATE


@pytest.mark.integration
class TestUniverseResolverIntegration:
    def test_with_real_db(self):
        db = DuckDBManager.get_instance()
        resolver = UniverseResolver(db)
        snapshot = resolver.resolve_universe(
            datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc)
        )
        assert snapshot.contract_count > 0
