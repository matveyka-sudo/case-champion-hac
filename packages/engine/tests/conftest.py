import json
from pathlib import Path
from typing import Any

import pytest
from a2_engine import UrbanConfig, load_urban_config

REPOSITORY = Path(__file__).resolve().parents[3]


@pytest.fixture
def config() -> UrbanConfig:
    return load_urban_config(REPOSITORY / "config/product.json")


@pytest.fixture
def product() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((REPOSITORY / "config/product.json").read_text())
    return data
