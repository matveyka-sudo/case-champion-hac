"""car / models — canonical urban implementation."""

from dataclasses import dataclass

from a2_engine.routing.road import RoadEndpoint, RoadRequest, RoadResult
from a2_engine.urban.analysis.models import UrbanAnalysis
from a2_engine.urban.models import (
    UrbanArea,
    UrbanContext,
    UrbanDestination,
    UrbanPoint,
    UrbanPopulationCoverage,
)


@dataclass(frozen=True)
class CarInputs:
    context: UrbanContext
    points: tuple[UrbanPoint, ...]
    destinations: tuple[UrbanDestination, ...]
    entries: tuple[tuple[str, RoadEndpoint], ...]
    exits: tuple[tuple[str, RoadEndpoint], ...]
    area: UrbanArea
    population: UrbanPopulationCoverage
    # category, accepted full catalog flag, evidence
    catalogs: tuple[tuple[str, bool, tuple[str, ...]], ...]
    timezone: str

    def __post_init__(self) -> None:
        for name in ("points", "destinations", "entries", "exits", "catalogs"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if len({p.point_id for p in self.points}) != len(self.points) or len(
            {d.destination_id for d in self.destinations}
        ) != len(self.destinations):
            raise ValueError("DUPLICATE_CAR_ENTITY")
        if self.context.traffic_mode != "static" or self.population.context != self.context:
            raise ValueError("CAR_STATIC_CONTEXT_REQUIRED")
        for rows, entities, entry in (
            (self.entries, self.points, True),
            (self.exits, self.destinations, False),
        ):
            ids = [key for key, _ in rows]
            expected = {
                p.point_id if isinstance(p, UrbanPoint) else p.destination_id for p in entities
            }
            if len(ids) != len(set(ids)) or set(ids) - expected:
                raise ValueError("DUPLICATE_OR_FOREIGN_CAR_ENDPOINT")
            for key, endpoint in rows:
                build = endpoint.build
                if (
                    build.costing != "auto"
                    or build.snapshot_id != self.context.versions.snapshot_id
                    or build.graph_version != self.context.versions.graph_version
                    or self.context.versions.traffic_version != "static:" + build.build_id
                ):
                    raise ValueError("CAR_BUILD_CONTEXT_MISMATCH")
                entity = next(
                    p
                    for p in entities
                    if (p.point_id if isinstance(p, UrbanPoint) else p.destination_id) == key
                )
                physical = (
                    entity.origin_node_id
                    if isinstance(entity, UrbanPoint)
                    else (entity.physical_node_id or entity.access_node_id)
                )
                if (
                    endpoint.physical_node_id != physical
                    or endpoint.access_node_id != entity.access_node_id
                    or endpoint.direction != ("entry" if entry else "exit")
                ):
                    raise ValueError("CAR_PHYSICAL_BOUNDARY_MISMATCH")
        builds = {e.build for _, e in (*self.entries, *self.exits)}
        if len(builds) > 1 or len({c[0] for c in self.catalogs}) != len(self.catalogs):
            raise ValueError("MIXED_CAR_PROFILE_OR_DUPLICATE_CATALOG")
        for _, complete, evidence in self.catalogs:
            if type(complete) is not bool or (complete and not evidence):
                raise ValueError("CATALOG_PROOF_REQUIRED")


@dataclass(frozen=True)
class CarPlan:
    inputs: CarInputs
    categories: tuple[str, ...]
    requests: tuple[RoadRequest, ...]
    issues: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class CarAnalysisResult:
    plan: CarPlan
    routes: tuple[RoadResult, ...]
    analysis: UrbanAnalysis
