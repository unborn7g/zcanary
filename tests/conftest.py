import json
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def load(rel: str):
    return json.loads((FIX / rel).read_text())["result"]


@pytest.fixture
def fx():
    return load
