"""Explicit exports for the intercity domain."""

from a2_engine.intercity.analysis import (
    AcceptedIntercityCar,
    BoardingReadiness,
    CapitalBoundary,
    IntercityAnalysisScope,
    IntercityChainResult,
    IntercityFrequency,
    IntercityModeResult,
    IntercityPassengerAnalysis,
    PhysicalDeparture,
    PhysicalService,
    TransportModeBinding,
    analyze_intercity_passenger,
)
from a2_engine.intercity.passenger import (
    CarResult,
    PassengerChain,
    PassengerResult,
    ReferenceTime,
    intercity_car_index,
    intercity_passenger_index,
)
from a2_engine.intercity.reference import ReferenceComparison, reference_time_from_result

__all__ = [
    "CarResult",
    "PassengerChain",
    "PassengerResult",
    "ReferenceTime",
    "intercity_car_index",
    "intercity_passenger_index",
    "ReferenceComparison",
    "reference_time_from_result",
]

__all__ += [
    "AcceptedIntercityCar",
    "BoardingReadiness",
    "CapitalBoundary",
    "IntercityAnalysisScope",
    "IntercityChainResult",
    "IntercityFrequency",
    "IntercityModeResult",
    "IntercityPassengerAnalysis",
    "PhysicalDeparture",
    "PhysicalService",
    "TransportModeBinding",
    "analyze_intercity_passenger",
]
