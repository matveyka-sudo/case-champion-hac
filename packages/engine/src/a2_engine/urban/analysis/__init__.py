"""Compatibility exports; implementations live in concrete submodules."""

from a2_engine.urban.analysis.calculation import _category_result as _category_result
from a2_engine.urban.analysis.calculation import (
    analyze_urban_candidates as analyze_urban_candidates,
)
from a2_engine.urban.analysis.models import UrbanAnalysis as UrbanAnalysis
from a2_engine.urban.analysis.models import UrbanCategoryResult as UrbanCategoryResult
from a2_engine.urban.analysis.models import UrbanPointResult as UrbanPointResult
from a2_engine.urban.analysis.models import UrbanTerritoryResult as UrbanTerritoryResult
from a2_engine.urban.analysis.validation import _check_candidate as _check_candidate
from a2_engine.urban.validation import _check_points as _check_points

__all__ = [
    "UrbanCategoryResult",
    "UrbanPointResult",
    "UrbanTerritoryResult",
    "UrbanAnalysis",
    "_check_candidate",
    "_category_result",
    "analyze_urban_candidates",
    "_check_points",
]
