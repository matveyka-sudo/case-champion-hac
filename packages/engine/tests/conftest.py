import json
from pathlib import Path
from typing import Any

import pytest
from a2_engine import (
    FreightConfig,
    PassengerConfig,
    UrbanConfig,
    load_freight_config,
    load_passenger_config,
    load_urban_config,
)

REPOSITORY = Path(__file__).resolve().parents[3]


@pytest.fixture
def config() -> UrbanConfig:
    return load_urban_config(REPOSITORY / "config/product.json")


@pytest.fixture
def passenger_config() -> PassengerConfig:
    return load_passenger_config(REPOSITORY / "config/product.json")


@pytest.fixture
def freight_config() -> FreightConfig:
    return load_freight_config(REPOSITORY / "config/product.json")


@pytest.fixture
def product() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((REPOSITORY / "config/product.json").read_text())
    return data
