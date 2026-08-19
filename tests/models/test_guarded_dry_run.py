"""Guardian dry-run training on replay bar data.

Exercises the guarded training entry point with synthetic features
from the 1,170-bar dataset to validate fail-closed enforcement.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.bars.engine import BarEngine
from src.features.compute import BatchFeatureEngine
from src.models import guarded_train, GuardError, save_manifest
from src.validation.leakage import DatasetRef, DatasetRole

CAPTURE_DIR = "artifacts/stage3/2026-07-17/bronze/2026-07-17"
FIXED_NOW = datetime(2026, 7, 17, 15, 30, 0, tzinfo=timezone.utc)
HORIZON = "30min"
NIFTY_EXPIRY = datetime(2026, 7, 21, 15, 30, 0, tzinfo=timezone.utc)


def _load_packets() -> list[dict]:
    files = sorted(os.listdir(CAPTURE_DIR))
    rows = []
    for f in files:
        table = pq.read_table(f"{CAPTURE_DIR}/{f}")
        for _, r in table.to_pandas().iterrows():
            rows.append(r.to_dict())
    return rows


def _packet_to_event(packet: dict) -> dict | None:
    ts_ns = packet.get("capture_timestamp_ns")
    if ts_ns is None:
        return None
    et = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    ltv = packet.get("last_trade_volume")
    vol = 0 if ltv is None or (isinstance(ltv, float) and ltv != ltv) else int(ltv)
    oi_v = packet.get("oi")
    oi = None if oi_v is None or (isinstance(oi_v, float) and oi_v != oi_v) else int(oi_v)
    ltp = packet.get("ltp")
    price = ltp if ltp and not (isinstance(ltp, float) and ltp != ltp) else 0.0
    return {"security_id": packet["security_id"], "event_time": et, "price": price, "volume": vol, "oi": oi}


def main():
    print("=" * 60)
    print("Guardian dry-run training — Phase 2.5C preview")
    print("=" * 60)

    packets = _load_packets()
    print(f"Loaded {len(packets)} packets")

    eng = BarEngine(now_fn=lambda: FIXED_NOW, late_event_window_s=86400)
    for p in packets:
        ev = _packet_to_event(p)
        if ev:
            eng.accept(**ev)
    bars = eng.close_all()
    print(f"Built {len(bars)} bars")

    # Compute features
    feat_eng = BatchFeatureEngine()
    feat_rows = feat_eng.compute(bars)
    print(f"Computed {len(feat_rows)} feature rows")

    # --- Build training DataFrame ---
    records = []
    for (sid, et), feat in sorted(feat_rows.items()):
        if feat["returns"] is None or feat["rsi"] is None or feat["atr"] is None:
            continue
        records.append({
            "security_id": sid,
            "event_time": et,
            "returns": feat["returns"],
            "range": feat["range"],
            "atr": feat["atr"],
            "vwap": feat["vwap"],
            "rsi": feat["rsi"],
            "rvol": feat.get("rvol", 1.0),
            "oi_change": feat.get("oi_change", 0.0),
            "minutes_to_expiry": (NIFTY_EXPIRY - et).total_seconds() / 60.0,
        })

    if not records:
        print("No records with full feature set — aborting")
        return

    df = pd.DataFrame(records)
    print(f"Training rows (after warmup): {len(df)}")

    # Use a single security_id for monotonic timestamps
    # Pick the security with the most data
    top_sid = df["security_id"].value_counts().idxmax()
    df = df[df["security_id"] == top_sid].copy()
    print(f"Using security_id {top_sid}: {len(df)} rows")

    # --- Label: next-period return direction ---
    df = df.sort_values("event_time").reset_index(drop=True)
    df["ret_fwd"] = df["returns"].shift(-1)
    df["label"] = (df["ret_fwd"] > 0).astype(int)
    df = df.dropna(subset=["label"])

    feature_cols = ["returns", "range", "atr", "vwap", "rsi", "rvol", "oi_change", "minutes_to_expiry"]
    X = df[feature_cols].values
    y = df["label"].values
    pred_times = pd.Series(df["event_time"])
    eval_times = pred_times + pd.Timedelta(HORIZON)

    # --- DatasetRefs ---
    split_idx = int(len(X) * 0.8)
    train_ref = DatasetRef(
        X=X[:split_idx], y=y[:split_idx],
        role=DatasetRole.TRAIN, dataset_id="replay_train",
        start_ts=df["event_time"].iloc[0], end_ts=df["event_time"].iloc[split_idx - 1],
    )
    es_ref = DatasetRef(
        X=X[split_idx:], y=y[split_idx:],
        role=DatasetRole.EARLY_STOPPING, dataset_id="replay_es",
        start_ts=df["event_time"].iloc[split_idx], end_ts=df["event_time"].iloc[-1],
    )

    # --- Guarded train ---
    print(f"\nRunning guarded_train...")
    model, manifest = guarded_train(
        X_train=train_ref.X, y_train=train_ref.y,
        X_val=es_ref.X, y_val=es_ref.y,
        prediction_times=pd.Series(pred_times[:split_idx]),
        evaluation_times=pd.Series(eval_times[:split_idx]),
        datasets=[train_ref, es_ref],
        model_params={"max_depth": 3, "eta": 0.1},
        n_splits=3,
        n_estimators=100,
        early_stopping_rounds=10,
        feature_version="0.1.0",
        label_version="0.1.0",
        require_negative_controls=False,
        verbose=True,
    )

    print(f"\nTrain complete!")
    print(f"  Model ID: {manifest.model_id}")
    print(f"  Guardian status: {manifest.guardian_status}")
    print(f"  Best iteration: {manifest.best_iteration}")
    print(f"  Dataset roles: {manifest.dataset_roles}")

    manifest_path = save_manifest(manifest)
    print(f"  Manifest saved: {manifest_path}")

    # --- Test fail-closed: FINAL_VAULT in eval_set ---
    print(f"\n--- Testing fail-closed: FINAL_VAULT in eval_set ---")
    vault_ref = DatasetRef(
        X=X[:10], y=y[:10],
        role=DatasetRole.FINAL_VAULT, dataset_id="vault_should_block",
    )
    try:
        guarded_train(
            X_train=train_ref.X, y_train=train_ref.y,
            prediction_times=pd.Series(pred_times[:split_idx]),
            evaluation_times=pd.Series(eval_times[:split_idx]),
            datasets=[train_ref, es_ref, vault_ref],
            n_splits=3, n_estimators=50, early_stopping_rounds=5,
            require_negative_controls=False,
        )
        print("  FAIL: should have raised GuardError")
    except GuardError as e:
        print(f"  PASS: GuardError raised — {e}")

    # --- Test fail-closed: OUTER_TEST in training data ---
    print(f"\n--- Testing fail-closed: OUTER_TEST in training ---")
    outer_ref = DatasetRef(
        X=X[:10], y=y[:10],
        role=DatasetRole.OUTER_TEST, dataset_id="outer_should_block",
    )
    try:
        guarded_train(
            X_train=outer_ref.X, y_train=outer_ref.y,
            prediction_times=pd.Series(pred_times[:10]),
            evaluation_times=pd.Series(eval_times[:10]),
            datasets=[outer_ref, es_ref],
            n_splits=2, n_estimators=10, early_stopping_rounds=5,
            require_negative_controls=False,
        )
        print("  FAIL: should have raised GuardError")
    except GuardError as e:
        print(f"  PASS: GuardError raised — {e}")

    print(f"\n{'=' * 60}")
    print(f"Guardian status: {manifest.guardian_status}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
