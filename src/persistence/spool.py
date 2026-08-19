import logging
import os
import pickle
import shutil
from pathlib import Path
from typing import Iterator, Optional

from .envelope import MarketEventEnvelope

logger = logging.getLogger(__name__)


class SpoolManager:
    def __init__(self, spool_dir: str | Path, max_spool_bytes: int = 500 * 1024 * 1024):
        self._dir = Path(spool_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_spool_bytes
        self._seq: int = 0
        self._next_seq()

    def _next_seq(self) -> int:
        existing = [int(f.stem) for f in self._dir.iterdir() if f.suffix == ".env" and f.stem.isdigit()]
        self._seq = (max(existing) if existing else 0) + 1
        return self._seq

    def spool(self, envelope: MarketEventEnvelope) -> bool:
        path = self._dir / f"{self._seq:012d}.env"
        self._seq += 1
        try:
            data = pickle.dumps(envelope)
            if data is None:
                return False
            path.write_bytes(data)
            return True
        except (OSError, pickle.PicklingError) as e:
            logger.error(f"Spool write failed for {envelope.event_id}: {e}")
            return False

    def replay(self) -> Iterator[MarketEventEnvelope]:
        files = sorted(self._dir.iterdir())
        for f in files:
            if f.suffix != ".env":
                continue
            try:
                data = f.read_bytes()
                env = pickle.loads(data)
                yield env
            except (OSError, pickle.UnpicklingError, Exception) as e:
                logger.warning(f"Spool read error {f.name}: {e}")

    def purge(self, max_age_seconds: Optional[float] = None):
        for f in self._dir.iterdir():
            if f.suffix != ".env":
                continue
            if max_age_seconds is not None:
                age = f.stat().st_mtime
                import time
                if time.time() - age < max_age_seconds:
                    continue
            try:
                f.unlink()
            except OSError as e:
                logger.warning(f"Spool purge failed {f.name}: {e}")

    def count(self) -> int:
        return sum(1 for f in self._dir.iterdir() if f.suffix == ".env")

    @property
    def total_bytes(self) -> int:
        return sum(f.stat().st_size for f in self._dir.iterdir() if f.suffix == ".env")

    def clear(self):
        for f in list(self._dir.iterdir()):
            if f.suffix == ".env":
                try:
                    f.unlink()
                except OSError as e:
                    logger.warning(f"Spool clear failed {f.name}: {e}")
