"""Compatibility exports; implementations live in concrete submodules."""

from a2_engine.urban.tau.calculation import _as_seconds as _as_seconds
from a2_engine.urban.tau.calculation import _slice_result as _slice_result
from a2_engine.urban.tau.calculation import _week_result as _week_result
from a2_engine.urban.tau.calculation import calculate_urban_tau as calculate_urban_tau
from a2_engine.urban.tau.calendar import _local_instant as _local_instant
from a2_engine.urban.tau.models import UrbanTauResult as UrbanTauResult
from a2_engine.urban.tau.models import UrbanTauSliceResult as UrbanTauSliceResult
from a2_engine.urban.tau.models import UrbanTauWeekResult as UrbanTauWeekResult

__all__ = [
    "UrbanTauSliceResult",
    "UrbanTauWeekResult",
    "UrbanTauResult",
    "_local_instant",
    "_as_seconds",
    "_slice_result",
    "_week_result",
    "calculate_urban_tau",
]
