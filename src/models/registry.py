"""Model registry — artifact manifest generation and storage."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.models.xgboost_model import TrainManifest


def save_manifest(manifest: TrainManifest, output_dir: str = "artifacts/models") -> str:
    """Write a model manifest to disk and return the file path."""
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / f"{manifest.model_id}_manifest.json"
    with open(file_path, "w") as f:
        json.dump(manifest.to_dict(), f, indent=2, default=str)
    return str(file_path)
