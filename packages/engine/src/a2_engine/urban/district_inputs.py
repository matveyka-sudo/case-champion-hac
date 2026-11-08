"""Compatibility exports; implementations live in concrete submodules."""

from a2_engine.urban.districts.inputs import _table as _table
from a2_engine.urban.districts.inputs import (
    prepare_urban_district_inputs as prepare_urban_district_inputs,
)
from a2_engine.urban.districts.models import UrbanDistrictInputs as UrbanDistrictInputs

__all__ = ["UrbanDistrictInputs", "_table", "prepare_urban_district_inputs"]
