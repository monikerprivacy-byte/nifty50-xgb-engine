"""Legacy v1 golden fixture tests — DEPRECATED.

These tests use the old v1 binary format and are superseded by
test_v2_decoder.py. They are preserved for documentation only.

Run v2 decoder tests instead:
    python3 -m pytest tests/providers/test_v2_decoder.py -v
"""

from __future__ import annotations

import pytest

pytest.skip("Legacy v1 tests — use test_v2_decoder.py instead", allow_module_level=True)
