"""Compatibility exports; implementations live in concrete submodules."""

from a2_engine.urban.districts.calculation import _allocation as _allocation
from a2_engine.urban.districts.calculation import _decimal as _decimal
from a2_engine.urban.districts.calculation import _sum as _sum
from a2_engine.urban.districts.calculation import _sum_decimal as _sum_decimal
from a2_engine.urban.districts.calculation import _unique as _unique
from a2_engine.urban.districts.calculation import analyze_urban_districts as analyze_urban_districts
from a2_engine.urban.districts.models import UrbanCellAllocation as UrbanCellAllocation
from a2_engine.urban.districts.models import UrbanDistrictAnalysis as UrbanDistrictAnalysis
from a2_engine.urban.districts.models import UrbanDistrictComparison as UrbanDistrictComparison
from a2_engine.urban.districts.models import UrbanDistrictMetric as UrbanDistrictMetric
from a2_engine.urban.districts.models import UrbanDistrictModeResult as UrbanDistrictModeResult
from a2_engine.urban.districts.models import UrbanDistrictPopulation as UrbanDistrictPopulation
from a2_engine.urban.districts.models import UrbanDistrictRank as UrbanDistrictRank
from a2_engine.urban.districts.models import UrbanDistrictRanking as UrbanDistrictRanking
from a2_engine.urban.districts.models import UrbanDistrictValue as UrbanDistrictValue

__all__ = [
    "UrbanCellAllocation",
    "UrbanDistrictPopulation",
    "UrbanDistrictComparison",
    "UrbanDistrictValue",
    "UrbanDistrictRank",
    "UrbanDistrictRanking",
    "UrbanDistrictMetric",
    "UrbanDistrictModeResult",
    "UrbanDistrictAnalysis",
    "_decimal",
    "_sum_decimal",
    "_sum",
    "_unique",
    "_allocation",
    "analyze_urban_districts",
]
