"""Compatibility exports; implementations live in concrete submodules."""

from a2_engine.urban.districts.models import UrbanDistrict as UrbanDistrict
from a2_engine.urban.districts.models import UrbanDistrictCoverage as UrbanDistrictCoverage
from a2_engine.urban.districts.models import UrbanDistrictShare as UrbanDistrictShare
from a2_engine.urban.districts.models import UrbanOriginalCell as UrbanOriginalCell
from a2_engine.urban.districts.models import UrbanSharePrecision as UrbanSharePrecision

__all__ = [
    "UrbanOriginalCell",
    "UrbanDistrict",
    "UrbanDistrictShare",
    "UrbanSharePrecision",
    "UrbanDistrictCoverage",
]
