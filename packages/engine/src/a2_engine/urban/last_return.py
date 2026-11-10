"""Compatibility exports; implementations live in concrete submodules."""

from a2_engine.urban.transit.last_return import _midnight as _midnight
from a2_engine.urban.transit.last_return import search_urban_last_return as search_urban_last_return
from a2_engine.urban.transit.models import UrbanLastReturnResult as UrbanLastReturnResult
from a2_engine.urban.transit.models import UrbanReturnJourney as UrbanReturnJourney

__all__ = ["UrbanReturnJourney", "UrbanLastReturnResult", "_midnight", "search_urban_last_return"]
