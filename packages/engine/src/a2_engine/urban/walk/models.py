"""walk / models — canonical urban implementation."""

from dataclasses import dataclass

from a2_engine.urban.analysis.models import UrbanAnalysis
from a2_engine.urban.models import (
    UrbanArea,
    UrbanContext,
    UrbanDestination,
    UrbanPoint,
    UrbanPopulationCoverage,
)
from a2_engine.urban.valhalla.models import (
    ValhallaBuild,
    ValhallaCorrelation,
    WalkConnector,
    WalkCoordinate,
    WalkRouteRequest,
    WalkRouteResult,
)


@dataclass(frozen=True)
class WalkEndpoint:
    entity_id: str
    physical_node_id: str
    access_node_id: str
    location: WalkCoordinate
    access_location: WalkCoordinate
    connector: WalkConnector | None
    correlation: ValhallaCorrelation | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class WalkCatalog:
    category: str
    complete: bool
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class WalkPlanIssue:
    point_id: str
    destination_id: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class WalkInputs:
    canonical_payload_hash: str
    source_graph_version: str
    context: UrbanContext
    build: ValhallaBuild | None
    points: tuple[UrbanPoint, ...]
    destinations: tuple[UrbanDestination, ...]
    point_endpoints: tuple[WalkEndpoint, ...]
    destination_endpoints: tuple[WalkEndpoint, ...]
    catalogs: tuple[WalkCatalog, ...]
    area: UrbanArea
    population_coverage: UrbanPopulationCoverage
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class WalkRoutePlan:
    inputs: WalkInputs
    point_ids: tuple[str, ...]
    categories: tuple[str, ...]
    requests: tuple[WalkRouteRequest, ...]
    issues: tuple[WalkPlanIssue, ...]


@dataclass(frozen=True)
class WalkAnalysisResult:
    plan: WalkRoutePlan
    routes: tuple[WalkRouteResult, ...]
    analysis: UrbanAnalysis
