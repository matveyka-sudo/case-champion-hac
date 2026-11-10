"""Canonical final-contract imports; legacy exports remain in a2_contracts."""

from .api import (
    AreaOption,
    Capabilities,
    ErrorDetail,
    ErrorEnvelope,
    JobAccepted,
    JobView,
    TerritoryOption,
)
from .base import MethodologyRef, VersionRefs
from .dataset import AcceptedData, Evidence, GeometryRef, RegistryEvidence
from .evidence import EvidenceGroup, EvidenceResolver
from .journeys import CountResult, JourneyResult, LastReturn, Leg, ReferenceResult
from .requests import (
    AnalysisRequest,
    CargoProfile,
    DeliveryCargoProfile,
    IntercityCargoRequest,
    IntercityPassengerRequest,
    PipelineCargoProfile,
    UrbanCargoRequest,
    UrbanPassengerRequest,
    parse_analysis_request,
)
from .results import AnalysisResult, EvidenceRef, Metric, Score, SubjectRef
from .scenarios import ScenarioProposal, ScenarioResult, ScenarioRunOptions, ScenarioRunRequest

__all__ = [
    "AcceptedData",
    "AnalysisRequest",
    "AnalysisResult",
    "AreaOption",
    "Capabilities",
    "CargoProfile",
    "CountResult",
    "DeliveryCargoProfile",
    "ErrorDetail",
    "ErrorEnvelope",
    "Evidence",
    "EvidenceRef",
    "EvidenceGroup",
    "EvidenceResolver",
    "GeometryRef",
    "IntercityCargoRequest",
    "IntercityPassengerRequest",
    "JobAccepted",
    "JobView",
    "JourneyResult",
    "LastReturn",
    "Leg",
    "MethodologyRef",
    "Metric",
    "PipelineCargoProfile",
    "ReferenceResult",
    "RegistryEvidence",
    "ScenarioProposal",
    "ScenarioResult",
    "ScenarioRunOptions",
    "ScenarioRunRequest",
    "Score",
    "SubjectRef",
    "TerritoryOption",
    "UrbanCargoRequest",
    "UrbanPassengerRequest",
    "VersionRefs",
    "parse_analysis_request",
]
