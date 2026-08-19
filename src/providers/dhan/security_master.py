import hashlib
import io
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"

COLUMN_MAP = {
    "security_id": "security_id",
    "SEM_EXM_EXCH_ID": "exchange",
    "SEM_INSTRUMENT_TYPE": "instrument_type",
    "SEM_EXPIRY_DATE": "expiry_date",
    "SEM_STRIKE_PRICE": "strike_price",
    "SEM_OPTION_TYPE": "option_type",
    "SEM_LOT_SIZE": "lot_size",
    "SEM_TICK_SIZE": "tick_size",
    "SEM_TM_SYMBOL": "symbol",
    "SEM_NSE_SYMBOL": "nse_symbol",
    "SEM_BSE_SYMBOL": "bse_symbol",
    "SEM_DV_SYMBOL": "dv_symbol",
    "SEM_UNDERLYING_SECURITY_ID": "underlying_security_id",
    "SEGMENT": "segment",
    "SEM_ISIN": "isin",
    "SEM_SERIES": "series",
    "SOURCE": "source",
}

DTYPE_MAP = {
    "security_id": "int64",
    "SEM_EXM_EXM_ID": "str",
    "SEM_EXM_EXCH_ID": "str",
    "SEM_INSTRUMENT_TYPE": "str",
    "SEM_EXPIRY_DATE": "str",
    "SEM_STRIKE_PRICE": "float64",
    "SEM_OPTION_TYPE": "str",
    "SEM_LOT_SIZE": "float64",
    "SEM_TICK_SIZE": "float64",
    "SEM_TM_SYMBOL": "str",
    "SEM_NSE_SYMBOL": "str",
    "SEM_BSE_SYMBOL": "str",
    "SEM_DV_SYMBOL": "str",
    "SEM_UNDERLYING_SECURITY_ID": "float64",
    "SEGMENT": "str",
    "SEM_ISIN": "str",
    "SEM_SERIES": "str",
    "SOURCE": "str",
    "SEM_EXM_SERIES": "str",
}

REQUIRED_COLS = [
    "security_id",
    "exchange",
    "instrument_type",
    "symbol",
    "source",
]

OPTION_TYPES = ("OPTSTK", "OPTIDX")
FUTURE_TYPES = ("FUTSTK", "FUTIDX")
DERIVATIVE_TYPES = OPTION_TYPES + FUTURE_TYPES


def parse_dhan_date(val) -> Optional[date]:
    if pd.isna(val) or val in ("", None):
        return None
    try:
        s = str(val).strip().replace("-", " ").replace("/", " ")
        for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None
    except Exception:
        return None


def compute_checksum(df: pd.DataFrame) -> str:
    raw = df.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def download_security_master(
    url: str = DHAN_SCRIP_MASTER_URL,
    timeout: float = 120.0,
) -> pd.DataFrame:
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        content = response.content

    df = pd.read_csv(
        io.BytesIO(content),
        dtype=DTYPE_MAP,
        low_memory=False,
    )

    df.columns = [
        COLUMN_MAP.get(c, c.lower().replace(" ", "_").replace("-", "_"))
        for c in df.columns
    ]

    if "source" in df.columns:
        df["source"] = df["source"].fillna("NSE")
    else:
        df["source"] = "NSE"
    if "exchange" in df.columns:
        df["exchange"] = df["exchange"].fillna("NSE")
    else:
        df["exchange"] = "NSE"
    df["segment"] = df.get("segment", None)
    df["isin"] = df.get("isin", None)

    df["security_id"] = df["security_id"].astype("int64")
    df["lot_size"] = pd.to_numeric(df.get("lot_size", 0), errors="coerce").fillna(0).astype("int64")
    df["tick_size"] = pd.to_numeric(df.get("tick_size", 0), errors="coerce").fillna(0)
    df["strike_price"] = pd.to_numeric(df.get("strike_price", 0), errors="coerce").fillna(0)
    df["underlying_security_id"] = (
        pd.to_numeric(df.get("underlying_security_id", 0), errors="coerce")
        .fillna(0)
        .astype("int64")
    )

    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = ""

    return df


def compute_strike_interval(df: pd.DataFrame) -> pd.DataFrame:
    option_mask = df["instrument_type"].isin(OPTION_TYPES)
    if not option_mask.any():
        df["strike_interval"] = 0.0
        return df

    intervals = {}
    for (symbol, expiry), group in df[option_mask].groupby(["symbol", "expiry_date"]):
        strikes = sorted(group["strike_price"].dropna().unique())
        if len(strikes) >= 2:
            diffs = [round(strikes[i + 1] - strikes[i], 2) for i in range(len(strikes) - 1)]
            intervals[(symbol, expiry)] = max(set(diffs), key=diffs.count)
        elif len(strikes) == 1:
            intervals[(symbol, expiry)] = 10.0

    df["strike_interval"] = 0.0
    for (symbol, expiry), interval in intervals.items():
        mask = (df["symbol"] == symbol) & (df["expiry_date"] == expiry) & option_mask
        df.loc[mask, "strike_interval"] = interval

    return df


def normalize_security_master(df: pd.DataFrame) -> pd.DataFrame:
    df["expiry_date"] = df["expiry_date"].apply(parse_dhan_date)
    df["expiry_date"] = df["expiry_date"].where(df["instrument_type"].isin(DERIVATIVE_TYPES), None)
    df["strike_price"] = df["strike_price"].where(df["instrument_type"].isin(OPTION_TYPES), 0.0)
    df["option_type"] = df["option_type"].where(df["instrument_type"].isin(OPTION_TYPES), None)

    df["symbol"] = df["symbol"].str.strip().str.upper()
    df["nse_symbol"] = df.get("nse_symbol", df["symbol"]).str.strip().str.upper()
    df["exchange"] = df["exchange"].str.strip().str.upper()

    df = compute_strike_interval(df)

    df["checksum"] = df.apply(
        lambda r: hashlib.md5(
            f"{r['security_id']}|{r['symbol']}|{r['expiry_date']}|{r['strike_price']}|{r['option_type']}|{r['lot_size']}|{r['tick_size']}".encode()
        ).hexdigest(),
        axis=1,
    )

    return df


def build_row_hash(row: pd.Series) -> str:
    return hashlib.md5(
        "|".join(
            str(row.get(c, ""))
            for c in [
                "security_id", "symbol", "exchange", "instrument_type",
                "expiry_date", "strike_price", "option_type",
                "lot_size", "tick_size", "underlying_security_id",
                "isin", "source",
            ]
        ).encode()
    ).hexdigest()


def compute_snapshot_diff(
    old_df: pd.DataFrame,
    new_df: pd.DataFrame,
    snapshot_date: date,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    old_ids = set(old_df["security_id"])
    new_ids = set(new_df["security_id"])

    added_ids = new_ids - old_ids
    removed_ids = old_ids - new_ids

    common_ids = new_ids & old_ids

    old_indexed = old_df.set_index("security_id")
    new_indexed = new_df.set_index("security_id")

    changed_rows = []
    for sid in common_ids:
        old_hash = build_row_hash(old_indexed.loc[sid])
        new_hash = build_row_hash(new_indexed.loc[sid])
        if old_hash != new_hash:
            r = new_indexed.loc[sid].copy()
            r["_change_type"] = "updated"
            changed_rows.append(r)

    added_rows = new_indexed.loc[list(added_ids)].copy()
    added_rows["_change_type"] = "added"

    removed_rows = old_indexed.loc[list(removed_ids)].copy()
    removed_rows["_change_type"] = "removed"

    inserted = pd.DataFrame(changed_rows + list(added_rows.iteritems())[1] if len(changed_rows) > 0 and len(added_rows) > 0 else changed_rows or added_rows)
    if isinstance(inserted, list) or len(inserted) == 0:
        inserted = pd.DataFrame(changed_rows) if changed_rows else added_rows
        if isinstance(inserted, list):
            inserted = pd.DataFrame()

    removed = removed_rows if len(removed_rows) > 0 else pd.DataFrame()

    return inserted, removed, new_df


def import_snapshot_to_duckdb(
    db_manager,
    df: pd.DataFrame,
    snapshot_date: date,
) -> dict:
    w = db_manager.get_writer()

    existing = db_manager.fetch_df(
        """SELECT security_id, checksum, valid_from, valid_to, is_current
           FROM instrument_master_history
           WHERE is_current = true"""
    )

    existing_current = {}
    for _, row in existing.iterrows():
        existing_current[row["security_id"]] = {
            "checksum": row["checksum"],
            "valid_from": row["valid_from"],
        }

    new_by_id = {}
    for _, row in df.iterrows():
        sid = int(row["security_id"])
        new_by_id[sid] = row

    insert_rows = []
    update_old_rows = []

    current_ids = set(existing_current.keys())
    new_ids = set(new_by_id.keys())

    for sid in current_ids - new_ids:
        update_old_rows.append(sid)

    for sid in new_ids - current_ids:
        insert_rows.append(sid)

    for sid in current_ids & new_ids:
        old_hash = existing_current[sid]["checksum"]
        new_hash = new_by_id[sid].get("checksum", "")
        if old_hash != new_hash:
            update_old_rows.append(sid)
            insert_rows.append(sid)

    removed_count = len(current_ids - new_ids)

    for sid in update_old_rows:
        w.execute(
            """UPDATE instrument_master_history
               SET valid_to = ?, is_current = false
               WHERE security_id = ? AND is_current = true""",
            [snapshot_date, int(sid)],
        )

    rows_to_insert = []
    for sid in insert_rows:
        r = new_by_id[sid]
        rows_to_insert.append((
            int(r["security_id"]),
            str(r.get("symbol", "")),
            str(r.get("exchange", "NSE")),
            str(r.get("segment", "")),
            str(r.get("instrument_type", "")),
            r.get("expiry_date") if pd.notna(r.get("expiry_date")) else None,
            float(r.get("strike_price", 0)) if pd.notna(r.get("strike_price")) else 0.0,
            str(r.get("option_type")) if pd.notna(r.get("option_type")) else None,
            int(r.get("lot_size", 0)) if pd.notna(r.get("lot_size")) else 0,
            float(r.get("tick_size", 0)) if pd.notna(r.get("tick_size")) else 0.0,
            float(r.get("strike_interval", 0)) if pd.notna(r.get("strike_interval")) else 0.0,
            int(r.get("underlying_security_id", 0)) if pd.notna(r.get("underlying_security_id")) else 0,
            str(r.get("isin", "")) if pd.notna(r.get("isin")) else None,
            str(r.get("nse_symbol", "")) if pd.notna(r.get("nse_symbol")) else None,
            str(r.get("bse_symbol", "")) if pd.notna(r.get("bse_symbol")) else None,
            str(r.get("dv_symbol", "")) if pd.notna(r.get("dv_symbol")) else None,
            str(r.get("source", "NSE")),
            snapshot_date,
            snapshot_date,
            None,
            True,
            str(r.get("checksum", "")),
        ))

    if rows_to_insert:
        w.executemany(
            """INSERT INTO instrument_master_history
               (security_id, symbol, exchange, segment, instrument_type,
                expiry_date, strike_price, option_type,
                lot_size, tick_size, strike_interval, underlying_security_id,
                isin, nse_symbol, bse_symbol, dv_symbol,
                source, snapshot_date, valid_from, valid_to, is_current, checksum)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows_to_insert,
        )

    checksum = compute_checksum(df)
    w.execute(
        """INSERT INTO instrument_master_snapshots
           (snapshot_date, download_timestamp, row_count, checksum, status)
           VALUES (?, ?, ?, ?, 'imported')""",
        [snapshot_date, datetime.now(timezone.utc), len(df), checksum],
    )

    updated_count = len(set(update_old_rows) & set(insert_rows))
    return {
        "snapshot_date": str(snapshot_date),
        "total_incoming": len(df),
        "inserted": len(insert_rows),
        "updated": updated_count,
        "removed": removed_count,
        "unchanged": len(current_ids & new_ids) - updated_count,
        "checksum": checksum,
    }


def fetch_and_import(
    db_manager,
    snapshot_date: date | None = None,
) -> dict:
    if snapshot_date is None:
        snapshot_date = date.today()

    logger.info(f"Downloading security master for {snapshot_date}...")
    raw = download_security_master()
    df = normalize_security_master(raw)
    logger.info(f"Downloaded {len(df)} records, {len(df.columns)} columns")

    result = import_snapshot_to_duckdb(db_manager, df, snapshot_date)
    logger.info(f"Import complete: {result}")

    return result
