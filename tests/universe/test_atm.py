import pytest
from datetime import date
from src.universe.atm import ATMSelector
from src.storage.duckdb_manager import DuckDBManager


class TestATMSelector:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        db_path = tmp_path / "test_atm.duckdb"
        self.db = DuckDBManager(str(db_path))
        w = self.db.get_writer()

        expiry = date(2026, 8, 6)
        self.strikes = list(range(1000, 2001, 10))
        for i, sp in enumerate(self.strikes):
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
                        i * 2 + {"CE": 0, "PE": 1}[ot],
                        "RELIANCE", "NSE", "D", "OPTSTK",
                        expiry, float(sp), ot,
                        250, 0.05, 10.0,
                        0, "NSE",
                        date.today(), date.today(), None, True,
                    ],
                )
        yield
        self.db.close_all()

    def test_atm_selection_rounds_to_nearest_listed(self):
        selector = ATMSelector(self.db)
        expiry = date(2026, 8, 6)
        atm = selector.select_atm("RELIANCE", 1445.0, date.today(), expiry)
        assert atm == 1450
        assert atm in self.strikes

    def test_atm_hysteresis_retains_old(self):
        selector = ATMSelector(self.db)
        expiry = date(2026, 8, 6)

        first = selector.select_atm("RELIANCE", 1445.0, date.today(), expiry)
        assert first == 1450

        second = selector.select_atm("RELIANCE", 1453.0, date.today(), expiry)
        assert second == 1450

        third = selector.select_atm("RELIANCE", 1460.0, date.today(), expiry)
        assert third == 1460

    def test_around_atm_returns_correct_range(self):
        selector = ATMSelector(self.db)
        expiry = date(2026, 8, 6)

        strikes = selector.select_around_atm("RELIANCE", 1445.0, date.today(), expiry)
        assert len(strikes) == 11
        assert 1450 in strikes

    def test_around_atm_at_edge(self):
        selector = ATMSelector(self.db)
        expiry = date(2026, 8, 6)

        strikes = selector.select_around_atm("RELIANCE", 1005.0, date.today(), expiry)
        assert len(strikes) <= 11

    def test_build_contracts_creates_ce_pe(self):
        selector = ATMSelector(self.db)
        expiry = date(2026, 8, 6)
        contracts = selector.build_contracts("RELIANCE", [1450, 1460], expiry)
        assert len(contracts) == 4
        types = {c["option_type"] for c in contracts}
        assert types == {"CE", "PE"}

    def test_reset_hysteresis(self):
        selector = ATMSelector(self.db)
        expiry = date(2026, 8, 6)

        selector.select_atm("RELIANCE", 1445.0, date.today(), expiry)
        assert "RELIANCE|2026-08-06" in selector._current_atm

        selector.reset_hysteresis("RELIANCE", expiry)
        assert "RELIANCE|2026-08-06" not in selector._current_atm
