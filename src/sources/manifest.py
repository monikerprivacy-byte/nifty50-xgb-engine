"""Sample manifest definitions for vendor data sources.

Each vendor delivery must include a manifest file that documents
coverage, format, and metadata for the 14-point audit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Dict, Optional

from src.sources.adapter import SourceManifest


MANIFEST_FILENAME = "source_manifest.json"


@dataclass
class ManifestBuilder:
    source_name: str
    source_version: str = "1.0.0"
    coverage_start: Optional[date] = None
    coverage_end: Optional[date] = None
    contract_count: int = 0
    day_count: int = 0
    file_format: str = "parquet"
    checksum_algorithm: str = "sha256"
    metadata: Dict[str, str] = field(default_factory=dict)

    def with_coverage(self, start: date, end: Optional[date] = None):
        self.coverage_start = start
        self.coverage_end = end
        return self

    def with_counts(self, contracts: int, days: int):
        self.contract_count = contracts
        self.day_count = days
        return self

    def with_format(self, fmt: str):
        self.file_format = fmt
        return self

    def with_metadata(self, key: str, value: str):
        self.metadata[key] = value
        return self

    def build(self, data_checksum: str) -> SourceManifest:
        if self.coverage_start is None:
            raise ValueError("coverage_start is required")
        return SourceManifest(
            source_name=self.source_name,
            source_version=self.source_version,
            coverage_start=self.coverage_start,
            coverage_end=self.coverage_end,
            contract_count=self.contract_count,
            day_count=self.day_count,
            file_format=self.file_format,
            checksum_algorithm=self.checksum_algorithm,
            checksum=data_checksum,
            ingestion_timestamp=datetime.now(timezone.utc),
            metadata=dict(self.metadata),
        )


def compute_checksum(file_path: Path, algo: str = "sha256") -> str:
    h = sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    if algo == "sha256":
        return h.hexdigest()
    raise ValueError(f"unsupported algorithm: {algo}")


def write_manifest(manifest: SourceManifest, directory: Path):
    d = {
        "source_name": manifest.source_name,
        "source_version": manifest.source_version,
        "coverage_start": manifest.coverage_start.isoformat(),
        "coverage_end": manifest.coverage_end.isoformat() if manifest.coverage_end else None,
        "contract_count": manifest.contract_count,
        "day_count": manifest.day_count,
        "file_format": manifest.file_format,
        "checksum_algorithm": manifest.checksum_algorithm,
        "checksum": manifest.checksum,
        "ingestion_timestamp": manifest.ingestion_timestamp.isoformat(),
        "metadata": manifest.metadata,
    }
    path = directory / MANIFEST_FILENAME
    with open(path, "w") as f:
        json.dump(d, f, indent=2)
    return path


def read_manifest(directory: Path) -> SourceManifest:
    path = directory / MANIFEST_FILENAME
    with open(path) as f:
        d = json.load(f)
    return SourceManifest(
        source_name=d["source_name"],
        source_version=d["source_version"],
        coverage_start=date.fromisoformat(d["coverage_start"]),
        coverage_end=date.fromisoformat(d["coverage_end"]) if d.get("coverage_end") else None,
        contract_count=d["contract_count"],
        day_count=d["day_count"],
        file_format=d["file_format"],
        checksum_algorithm=d["checksum_algorithm"],
        checksum=d["checksum"],
        ingestion_timestamp=datetime.fromisoformat(d["ingestion_timestamp"]),
        metadata=d.get("metadata", {}),
    )
