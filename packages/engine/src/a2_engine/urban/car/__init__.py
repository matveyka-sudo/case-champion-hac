"""Compatibility exports; implementations live in concrete submodules."""

from a2_engine.urban.car.analysis import analyze_car_responses as analyze_car_responses
from a2_engine.urban.car.analysis import car_candidate as car_candidate
from a2_engine.urban.car.analysis import plan_car_routes as plan_car_routes
from a2_engine.urban.car.models import CarAnalysisResult as CarAnalysisResult
from a2_engine.urban.car.models import CarInputs as CarInputs
from a2_engine.urban.car.models import CarPlan as CarPlan
from a2_engine.urban.car.traffic import TrafficCarAnalysisResult as TrafficCarAnalysisResult
from a2_engine.urban.car.traffic import TrafficCarInputs as TrafficCarInputs
from a2_engine.urban.car.traffic import analyze_traffic_car as analyze_traffic_car
from a2_engine.urban.car.traffic import traffic_car_requests as traffic_car_requests

__all__ = [
    "CarInputs",
    "CarPlan",
    "CarAnalysisResult",
    "plan_car_routes",
    "car_candidate",
    "analyze_car_responses",
]

__all__ += [
    "TrafficCarInputs",
    "TrafficCarAnalysisResult",
    "traffic_car_requests",
    "analyze_traffic_car",
]
