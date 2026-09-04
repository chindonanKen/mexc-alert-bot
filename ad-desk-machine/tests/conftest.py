from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("MACHINE_TOKEN", "test-machine-token")
os.environ.setdefault("MACHINE_LOOP", "0")


@pytest.fixture(autouse=True)
def _reset_runtime():
    from machine.engine import reset_runtime

    reset_runtime()
    yield
    reset_runtime()
