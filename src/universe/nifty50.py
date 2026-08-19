import csv
import hashlib
import io
import logging
from datetime import date, datetime
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

NSE_INDICES_LIST_URL = "https://archives.nseindia.com/content/indices/ind_nifty50list.csv"

HISTORICAL_CHANGES: list[dict] = [
    {"symbol": "ADANIENT", "isin": "INE423A01024", "effective_from": date(2020, 3, 9)},
    {"symbol": "ADANIPORTS", "isin": "INE621F01014", "effective_from": date(2018, 4, 2)},
    {"symbol": "APOLLOHOSP", "isin": "INE437A01024", "effective_from": date(2017, 7, 10)},
    {"symbol": "ASIANPAINT", "isin": "INE021A01026", "effective_from": date(2009, 4, 1)},
    {"symbol": "AXISBANK", "isin": "INE238A01034", "effective_from": date(2009, 4, 1)},
    {"symbol": "BAJAJ-AUTO", "isin": "INE917I01010", "effective_from": date(2009, 4, 1)},
    {"symbol": "BAJFINANCE", "isin": "INE296A01032", "effective_from": date(2017, 7, 10)},
    {"symbol": "BAJAJFINSV", "isin": "INE918I01026", "effective_from": date(2018, 4, 2)},
    {"symbol": "BEL", "isin": "INE263A01024", "effective_from": date(2024, 3, 4)},
    {"symbol": "BHARTIARTL", "isin": "INE397D01024", "effective_from": date(2009, 4, 1)},
    {"symbol": "CIPLA", "isin": "INE059A01026", "effective_from": date(2009, 4, 1)},
    {"symbol": "COALINDIA", "isin": "INE522F01014", "effective_from": date(2012, 5, 25)},
    {"symbol": "DRREDDY", "isin": "INE089A01031", "effective_from": date(2009, 4, 1)},
    {"symbol": "EICHERMOT", "isin": "INE066A01021", "effective_from": date(2015, 4, 1)},
    {"symbol": "ETERNAL", "isin": "INE758T01015", "effective_from": date(2025, 6, 21)},
    {"symbol": "GRASIM", "isin": "INE047A01021", "effective_from": date(2009, 4, 1)},
    {"symbol": "HCLTECH", "isin": "INE860A01027", "effective_from": date(2009, 4, 1)},
    {"symbol": "HDFCBANK", "isin": "INE040A01034", "effective_from": date(2009, 4, 1)},
    {"symbol": "HDFCLIFE", "isin": "INE795G01014", "effective_from": date(2020, 11, 13)},
    {"symbol": "HINDALCO", "isin": "INE038A01020", "effective_from": date(2024, 9, 23)},
    {"symbol": "HINDUNILVR", "isin": "INE030A01027", "effective_from": date(2009, 4, 1)},
    {"symbol": "ICICIBANK", "isin": "INE090A01021", "effective_from": date(2009, 4, 1)},
    {"symbol": "INDIGO", "isin": "INE646L01027", "effective_from": date(2024, 9, 23)},
    {"symbol": "INFY", "isin": "INE009A01021", "effective_from": date(2009, 4, 1)},
    {"symbol": "ITC", "isin": "INE154A01025", "effective_from": date(2009, 4, 1)},
    {"symbol": "JIOFIN", "isin": "INE758E01017", "effective_from": date(2024, 9, 23)},
    {"symbol": "JSWSTEEL", "isin": "INE019A01038", "effective_from": date(2024, 3, 4)},
    {"symbol": "KOTAKBANK", "isin": "INE237A01036", "effective_from": date(2009, 4, 1)},
    {"symbol": "LT", "isin": "INE018A01030", "effective_from": date(2009, 4, 1)},
    {"symbol": "M&M", "isin": "INE101A01026", "effective_from": date(2009, 4, 1)},
    {"symbol": "MARUTI", "isin": "INE585B01010", "effective_from": date(2010, 8, 13)},
    {"symbol": "MAXHEALTH", "isin": "INE027H01010", "effective_from": date(2024, 9, 23)},
    {"symbol": "NTPC", "isin": "INE733E01010", "effective_from": date(2009, 4, 1)},
    {"symbol": "NESTLEIND", "isin": "INE239A01016", "effective_from": date(2018, 4, 2)},
    {"symbol": "ONGC", "isin": "INE213A01029", "effective_from": date(2009, 4, 1)},
    {"symbol": "POWERGRID", "isin": "INE752E01010", "effective_from": date(2013, 4, 1)},
    {"symbol": "RELIANCE", "isin": "INE002A01018", "effective_from": date(2009, 4, 1)},
    {"symbol": "SBILIFE", "isin": "INE123W01016", "effective_from": date(2025, 12, 1)},
    {"symbol": "SHRIRAMFIN", "isin": "INE721A01047", "effective_from": date(2024, 3, 4)},
    {"symbol": "SBIN", "isin": "INE062A01020", "effective_from": date(2009, 4, 1)},
    {"symbol": "SUNPHARMA", "isin": "INE044A01036", "effective_from": date(2009, 4, 1)},
    {"symbol": "TCS", "isin": "INE467B01029", "effective_from": date(2009, 4, 1)},
    {"symbol": "TATACONSUM", "isin": "INE192A01025", "effective_from": date(2009, 4, 1)},
    {"symbol": "TMPV", "isin": "INE155A01022", "effective_from": date(2025, 4, 1)},
    {"symbol": "TATASTEEL", "isin": "INE081A01012", "effective_from": date(2009, 4, 1)},
    {"symbol": "TECHM", "isin": "INE669C01036", "effective_from": date(2013, 4, 1)},
    {"symbol": "TITAN", "isin": "INE280A01028", "effective_from": date(2013, 4, 1)},
    {"symbol": "TRENT", "isin": "INE849A01020", "effective_from": date(2024, 9, 23)},
    {"symbol": "ULTRACEMCO", "isin": "INE481G01011", "effective_from": date(2009, 4, 1)},
    {"symbol": "WIPRO", "isin": "INE075A01022", "effective_from": date(2009, 4, 1)},
]

EXCLUSION_HISTORY: list[dict] = [
    {"symbol": "BPCL", "isin": "INE029A01011", "effective_from": date(2009, 4, 1), "effective_to": date(2025, 6, 20)},
    {"symbol": "BRITANNIA", "isin": "INE216A01030", "effective_from": date(2017, 7, 10), "effective_to": date(2024, 9, 22)},
    {"symbol": "HEROMOTOCO", "isin": "INE158A01026", "effective_from": date(2009, 4, 1), "effective_to": date(2025, 6, 20)},
    {"symbol": "HINDALCO", "isin": "INE038A01020", "effective_from": date(2009, 4, 1), "effective_to": date(2024, 3, 3)},
    {"symbol": "IOC", "isin": "INE242A01010", "effective_from": date(2009, 4, 1), "effective_to": date(2024, 3, 3)},
    {"symbol": "JSWSTEEL", "isin": "INE019A01022", "effective_from": date(2019, 9, 23), "effective_to": date(2022, 6, 20)},
    {"symbol": "DIVISLAB", "isin": "INE361B01024", "effective_from": date(2020, 3, 9), "effective_to": date(2024, 9, 22)},
    {"symbol": "UPL", "isin": "INE628A01036", "effective_from": date(2017, 7, 10), "effective_to": date(2024, 3, 3)},
    {"symbol": "SHREECEM", "isin": "INE070A01015", "effective_from": date(2009, 4, 1), "effective_to": date(2024, 3, 3)},
    {"symbol": "SBILIFE", "isin": "INE171G01012", "effective_from": date(2020, 11, 13), "effective_to": date(2025, 6, 20)},
    {"symbol": "TATAMOTORS", "isin": "INE155A01022", "effective_from": date(2009, 4, 1), "effective_to": date(2025, 3, 31)},
]


def build_membership_history() -> list[dict]:
    records = []

    for m in HISTORICAL_CHANGES:
        records.append({
            "symbol": m["symbol"],
            "isin": m["isin"],
            "effective_from": m["effective_from"],
            "effective_to": None,
            "is_current": True,
        })

    for exc in EXCLUSION_HISTORY:
        rec = _find_record(records, exc["symbol"], exc["effective_from"])
        if rec:
            rec["effective_to"] = exc["effective_to"]
            rec["is_current"] = False
        else:
            records.append({
                "symbol": exc["symbol"],
                "isin": exc.get("isin", ""),
                "effective_from": exc["effective_from"],
                "effective_to": exc["effective_to"],
                "is_current": False,
            })

    return records


def _find_record(records: list[dict], symbol: str, as_of: date) -> Optional[dict]:
    for r in records:
        if r["symbol"] == symbol:
            if r["effective_from"] <= as_of:
                if r["effective_to"] is None or r["effective_to"] >= as_of:
                    return r
    return None


def reconcile_membership() -> dict:
    nse_df = fetch_current_list_nse()
    nse_symbols = set(nse_df["SYMBOL"].tolist()) if nse_df is not None else set()

    records = build_membership_history()
    today = date.today()
    my_current = {
        r["symbol"] for r in records
        if r["is_current"]
        and r["effective_from"] <= today
        and (r["effective_to"] is None or r["effective_to"] >= today)
    }

    if nse_symbols:
        in_nse_not_mine = nse_symbols - my_current
        in_mine_not_nse = my_current - nse_symbols
    else:
        in_nse_not_mine = set()
        in_mine_not_nse = set()

    return {
        "nse_count": len(nse_symbols) if nse_symbols else 0,
        "my_count": len(my_current),
        "in_nse_not_mine": sorted(in_nse_not_mine),
        "in_mine_not_nse": sorted(in_mine_not_nse),
        "nse_list": sorted(nse_symbols) if nse_symbols else [],
        "my_list": sorted(my_current),
    }


def fetch_current_list_nse() -> Optional[pd.DataFrame]:
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            client.headers.update({
                "User-Agent": "Mozilla/5.0 (compatible; NIFTY50-XGB-Engine/1.0)",
                "Accept": "text/csv, application/csv",
            })
            response = client.get(NSE_INDICES_LIST_URL)
            response.raise_for_status()
            content = response.content.decode("utf-8-sig")
            df = pd.read_csv(io.StringIO(content))
            df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]
            df["SYMBOL"] = df["SYMBOL"].str.strip().str.upper()
            return df
    except Exception as e:
        logger.warning(f"NSE archive fetch failed: {e}")
        return None


def get_current_members() -> list[str]:
    df = fetch_current_list_nse()
    if df is not None:
        return sorted(df["SYMBOL"].tolist())

    records = build_membership_history()
    today = date.today()
    return sorted([
        r["symbol"] for r in records
        if r["effective_from"] <= today
        and (r["effective_to"] is None or r["effective_to"] >= today)
    ])


def import_membership_to_duckdb(db_manager):
    w = db_manager.get_writer()
    records = build_membership_history()

    current_nse = fetch_current_list_nse()
    nse_symbols = set(current_nse["SYMBOL"].tolist()) if current_nse is not None else set()

    today = date.today()

    for r in records:
        w.execute(
            """INSERT INTO nifty50_membership_history
               (symbol, company_name, isin, series, industry,
                effective_from, effective_to, is_current, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (symbol, effective_from) DO NOTHING""",
            [
                r["symbol"],
                r.get("company_name", ""),
                r.get("isin", ""),
                r.get("series", "EQ"),
                r.get("industry", ""),
                r["effective_from"],
                r.get("effective_to"),
                nse_symbols and r["is_current"],
                "historical",
            ],
        )

    w.execute(
        """INSERT INTO nifty50_membership_snapshots
           (snapshot_date, member_count, checksum, status)
           VALUES (?, ?, ?, 'recorded')
           ON CONFLICT (snapshot_date) DO NOTHING""",
        [today, len(nse_symbols) if nse_symbols else len(records),
         hashlib.md5(str(records).encode()).hexdigest()],
    )

    return {"records": len(records), "nse_current": len(nse_symbols) if nse_symbols else 0}


def members_at_date(target: date) -> list[str]:
    records = build_membership_history()
    return sorted([
        r["symbol"] for r in records
        if r["effective_from"] <= target
        and (r["effective_to"] is None or r["effective_to"] >= target)
    ])


def export_exclusion_reasons(db_manager) -> list[dict]:
    conn = db_manager.get_reader()
    nse_df = fetch_current_list_nse()

    membership_df = conn.execute(
        """SELECT DISTINCT symbol, is_current
           FROM nifty50_membership_history
           WHERE is_current = true"""
    ).fetchdf() if False else None

    nse_symbols = set(nse_df["SYMBOL"].tolist()) if nse_df is not None else set()
    recs = build_membership_history()
    today = date.today()

    my_current = {
        r["symbol"] for r in recs
        if r["is_current"]
        and r["effective_from"] <= today
        and (r["effective_to"] is None or r["effective_to"] >= today)
    }

    histories = {}
    for r in recs:
        histories.setdefault(r["symbol"], []).append(r)

    rows = []

    if nse_symbols:
        missing = nse_symbols - my_current
        for sym in sorted(missing):
            hist = histories.get(sym, [])
            last = hist[-1] if hist else None
            rows.append({
                "symbol": sym,
                "in_nse50": True,
                "in_membership_history": sym in histories,
                "is_current": last["is_current"] if last else False,
                "effective_from": str(last["effective_from"]) if last else "",
                "effective_to": str(last["effective_to"]) if last and last.get("effective_to") else "",
                "reason": "NSE constituent; missing from history data",
                "action": "ADD",
            })

        extra = my_current - nse_symbols
        for sym in sorted(extra):
            hist = histories.get(sym, [])
            last = hist[-1] if hist else None
            rows.append({
                "symbol": sym,
                "in_nse50": False,
                "in_membership_history": True,
                "is_current": last["is_current"] if last else False,
                "effective_from": str(last["effective_from"]) if last else "",
                "effective_to": str(last["effective_to"]) if last and last.get("effective_to") else "",
                "reason": "In history as current but not in current NSE 50",
                "action": "ADD_EXCLUSION",
            })

    return rows
