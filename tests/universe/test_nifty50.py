import pytest
from datetime import date
from src.universe.nifty50 import (
    build_membership_history,
    members_at_date,
    EXCLUSION_HISTORY,
    HISTORICAL_CHANGES,
)


class TestNifty50Membership:
    def test_all_current_members(self):
        members = members_at_date(date.today())
        assert len(members) == 50
        assert "RELIANCE" in members
        assert "TCS" in members
        assert "HDFCBANK" in members
        assert "ETERNAL" in members
        assert "INDIGO" in members
        assert "JIOFIN" in members
        assert "MAXHEALTH" in members

    def test_historical_membership_2024(self):
        members = members_at_date(date(2024, 1, 1))
        assert "DIVISLAB" in members
        assert "UPL" in members
        assert "SHREECEM" in members
        assert "HINDALCO" in members
        assert "BPCL" in members
        assert "HEROMOTOCO" in members
        assert "JSWSTEEL" not in members

    def test_2025_membership(self):
        members = members_at_date(date(2025, 1, 1))
        assert "TRENT" in members
        assert "SHRIRAMFIN" in members
        assert "BEL" in members
        assert "HINDALCO" in members
        assert "BRITANNIA" not in members
        assert "BPCL" in members
        assert "HEROMOTOCO" in members
        assert "TATAMOTORS" in members
        assert "TMPV" not in members
        assert "ETERNAL" not in members
        assert "SBILIFE" in members

    def test_2026_membership(self):
        members = members_at_date(date(2026, 7, 17))
        assert len(members) == 50
        assert "TRENT" in members
        assert "SHRIRAMFIN" in members
        assert "BEL" in members
        assert "DIVISLAB" not in members
        assert "HINDALCO" in members
        assert "SBILIFE" in members
        assert "TMPV" in members
        assert "ETERNAL" in members
        assert "INDIGO" in members
        assert "JIOFIN" in members
        assert "MAXHEALTH" in members

    def test_exact_50_members(self):
        members = members_at_date(date.today())
        assert len(members) == 50
        assert len(set(members)) == 50

    def test_early_membership(self):
        members = members_at_date(date(2010, 1, 1))
        assert "RELIANCE" in members
        assert "ITC" in members
        assert "SBIN" in members
        assert "MARUTI" not in members

    def test_exclusion_handling(self):
        for exc in EXCLUSION_HISTORY:
            before_exclusion = members_at_date(exc["effective_from"])
            after_exclusion = members_at_date(
                exc["effective_to"] + __import__("datetime").timedelta(days=1)
            )
            assert exc["symbol"] in before_exclusion, f"{exc['symbol']} missing before exclusion"
            assert exc["symbol"] not in after_exclusion, f"{exc['symbol']} still present after exclusion"

    def test_no_duplicates(self):
        records = build_membership_history()
        symbols = [(r["symbol"], r["effective_from"]) for r in records]
        assert len(symbols) == len(set(symbols))

    def test_never_empty(self):
        for year in range(2015, 2027):
            members = members_at_date(date(year, 6, 15))
            assert len(members) >= 30, f"Only {len(members)} members in {year}"

    def test_company_names_mapped(self):
        records = build_membership_history()
        for r in records:
            assert r["symbol"] is not None
            assert r["effective_from"] is not None

    def test_future_date_returns_current(self):
        members = members_at_date(date(2030, 1, 1))
        assert len(members) == 50
