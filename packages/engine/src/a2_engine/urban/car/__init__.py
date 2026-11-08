"""Compatibility exports; implementations live in concrete submodules."""

from a2_engine.urban.car.analysis import analyze_car_responses as analyze_car_responses
from a2_engine.urban.car.analysis import car_candidate as car_candidate
from a2_engine.urban.car.analysis import plan_car_routes as plan_car_routes
from a2_engine.urban.car.models import CarAnalysisResult as CarAnalysisResult
from a2_engine.urban.car.models import CarInputs as CarInputs
from a2_engine.urban.car.models import CarPlan as CarPlan

__all__ = [
    "CarInputs",
    "CarPlan",
    "CarAnalysisResult",
    "plan_car_routes",
    "car_candidate",
    "analyze_car_responses",
]
