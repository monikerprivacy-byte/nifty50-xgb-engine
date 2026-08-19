import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def tmp_data_dir() -> Generator[Path, None, None]:
    d = Path(tempfile.mkdtemp(prefix="nifty50_test_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)
