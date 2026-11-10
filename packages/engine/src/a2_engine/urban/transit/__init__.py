"""Compatibility exports; implementations live in concrete submodules."""

from a2_engine.urban.transit.journeys import _identity_json as _identity_json
from a2_engine.urban.transit.journeys import check_urban_inputs as check_urban_inputs
from a2_engine.urban.transit.journeys import path_legs as path_legs
from a2_engine.urban.transit.models import UrbanTransitNetwork as UrbanTransitNetwork
from a2_engine.urban.transit.models import UrbanTransitResult as UrbanTransitResult
from a2_engine.urban.transit.search import search_urban_transit as search_urban_transit

__all__ = [
    "UrbanTransitNetwork",
    "UrbanTransitResult",
    "_identity_json",
    "check_urban_inputs",
    "path_legs",
    "search_urban_transit",
]
