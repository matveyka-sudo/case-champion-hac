"""Accepted end-to-end directed connections; deliberately no arc composition."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from a2_engine.pipeline.utilization import PipelineBasis, PipelineResult, pipeline_utilization
from a2_engine.shared.config import FreightConfig
from a2_engine.shared.normalized import checked_id, content_hash, ids, utc
from a2_engine.shared.results import MetricResult, finite_number


@dataclass(frozen=True)
class PipelineContext:
    snapshot_id: str
    graph_version: str
    registry_version: str

    def __post_init__(self) -> None:
        for value in (self.snapshot_id, self.graph_version, self.registry_version):
            checked_id(value)


@dataclass(frozen=True)
class PipelineEndpoint:
    node_id: str
    territory_id: str
    product: str
    context: PipelineContext
    role: Literal["intake", "delivery"]
    valid_from: datetime
    valid_to: datetime
    state: Literal["confirmed", "forbidden", "unknown"]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.node_id, self.territory_id, self.product):
            checked_id(value)
        if (
            not isinstance(self.context, PipelineContext)
            or self.role not in ("intake", "delivery")
            or self.state not in ("confirmed", "forbidden", "unknown")
        ):
            raise ValueError("INVALID_PIPELINE_ENDPOINT")
        for key in ("valid_from", "valid_to"):
            object.__setattr__(self, key, utc(getattr(self, key)))
        if self.valid_from >= self.valid_to:
            raise ValueError("INVALID_PIPELINE_ENDPOINT_PERIOD")
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))


@dataclass(frozen=True)
class PipelineConnection:
    connection_id: str
    product: str
    context: PipelineContext
    origin: PipelineEndpoint
    destination: PipelineEndpoint
    state: Literal["confirmed", "forbidden", "unknown"]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        checked_id(self.connection_id)
        checked_id(self.product)
        if not isinstance(self.context, PipelineContext) or self.state not in (
            "confirmed",
            "forbidden",
            "unknown",
        ):
            raise ValueError("INVALID_PIPELINE_CONNECTION")
        if (
            self.origin.role != "intake"
            or self.destination.role != "delivery"
            or self.origin.node_id == self.destination.node_id
        ):
            raise ValueError("DIRECTED_PIPELINE_ENDPOINTS_REQUIRED")
        if any(
            e.context != self.context or e.product != self.product
            for e in (self.origin, self.destination)
        ):
            raise ValueError("PIPELINE_ENDPOINT_CONTEXT_MISMATCH")
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))

    @property
    def direction(self) -> str:
        return self.origin.node_id + "->" + self.destination.node_id


@dataclass(frozen=True)
class PipelineMeasurement:
    value: float | None
    basis: PipelineBasis
    context: PipelineContext
    period_start_at: datetime
    period_end_at: datetime
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.basis, PipelineBasis) or not isinstance(
            self.context, PipelineContext
        ):
            raise ValueError("PIPELINE_MEASUREMENT_BASIS_REQUIRED")
        if self.value is not None:
            object.__setattr__(self, "value", finite_number(self.value, "pipeline quantity"))
        for key in ("period_start_at", "period_end_at"):
            object.__setattr__(self, key, utc(getattr(self, key)))
        if self.period_start_at >= self.period_end_at:
            raise ValueError("INVALID_PIPELINE_QUANTITY_PERIOD")
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))


@dataclass(frozen=True)
class PipelineObservation:
    connection: PipelineConnection
    volume: PipelineMeasurement
    capacity: PipelineMeasurement


@dataclass(frozen=True)
class PipelineAnalysisScope:
    context: PipelineContext
    origin_territory_id: str
    destination_territory_id: str
    origin_node_ids: tuple[str, ...]
    destination_node_ids: tuple[str, ...]
    product: str
    period: str
    period_start_at: datetime
    period_end_at: datetime
    coverage_complete: bool
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (
            self.origin_territory_id,
            self.destination_territory_id,
            self.product,
            self.period,
        ):
            checked_id(value)
        if (
            not isinstance(self.context, PipelineContext)
            or type(self.coverage_complete) is not bool
        ):
            raise ValueError("INVALID_PIPELINE_SCOPE")
        for key in ("origin_node_ids", "destination_node_ids", "evidence_refs"):
            object.__setattr__(self, key, ids(getattr(self, key)))
        if set(self.origin_node_ids) & set(self.destination_node_ids):
            raise ValueError("OVERLAPPING_PIPELINE_BOUNDARIES")
        for key in ("period_start_at", "period_end_at"):
            object.__setattr__(self, key, utc(getattr(self, key)))
        if self.period_start_at >= self.period_end_at:
            raise ValueError("INVALID_PIPELINE_SCOPE_PERIOD")


@dataclass(frozen=True)
class PipelineConnectionAnalysis:
    observation: PipelineObservation
    state: Literal["connected", "forbidden", "unknown"]
    utilization: PipelineResult
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PipelineConnectionsAnalysis:
    analysis_id: str
    scope: PipelineAnalysisScope
    connections: tuple[PipelineConnectionAnalysis, ...]
    state: Literal["connected", "confirmed_absent", "unknown"]
    connectivity_complete: bool
    excluded_connection_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    evidence_refs: tuple[str, ...]


def analyze_pipeline_connections(
    scope: PipelineAnalysisScope,
    observations: tuple[PipelineObservation, ...],
    *,
    config: FreightConfig,
) -> PipelineConnectionsAnalysis:
    if scope.product not in config.pipeline_products:
        raise ValueError("UNKNOWN_PIPELINE_PRODUCT")
    ids(tuple(o.connection.connection_id for o in observations), required=False)
    rows = []
    excluded = []
    evidence = set(scope.evidence_refs)
    for observation in sorted(observations, key=lambda o: o.connection.connection_id):
        connection = observation.connection
        if connection.context != scope.context:
            raise ValueError("PIPELINE_SNAPSHOT_OR_GRAPH_MISMATCH")
        if (
            connection.product != scope.product
            or connection.origin.node_id not in scope.origin_node_ids
            or connection.destination.node_id not in scope.destination_node_ids
            or connection.origin.territory_id != scope.origin_territory_id
            or connection.destination.territory_id != scope.destination_territory_id
        ):
            excluded.append(connection.connection_id)
            continue
        endpoints = (connection.origin, connection.destination)
        evidence.update(connection.evidence_refs)
        for endpoint in endpoints:
            evidence.update(endpoint.evidence_refs)
        applicable = all(
            e.valid_from <= scope.period_start_at and scope.period_end_at <= e.valid_to
            for e in endpoints
        )
        states = (connection.state, connection.origin.state, connection.destination.state)
        status: Literal["connected", "forbidden", "unknown"] = "unknown"
        if applicable:
            status = (
                "forbidden"
                if "forbidden" in states
                else "connected"
                if set(states) == {"confirmed"}
                else "unknown"
            )
        volume, capacity = observation.volume, observation.capacity
        # Q validation is always performed even for incomparable metadata.
        if capacity.value is not None:
            finite_number(capacity.value, "pipeline capacity", positive=True)
        comparable = all(
            measurement.context == scope.context
            and measurement.basis.connection_id == connection.connection_id
            and measurement.basis.product == scope.product
            and measurement.basis.direction == connection.direction
            and measurement.basis.period == scope.period
            and measurement.period_start_at == scope.period_start_at
            and measurement.period_end_at == scope.period_end_at
            for measurement in (volume, capacity)
        )
        if comparable:
            utilization = pipeline_utilization(
                volume.value,
                capacity.value,
                volume_basis=volume.basis,
                capacity_basis=capacity.basis,
                connection_confirmed=status == "connected",
                config=config,
            )
        else:
            utilization = PipelineResult(
                MetricResult(None, "unknown", "INCOMPARABLE_PIPELINE_DATA"), ()
            )
        reasons = (
            ()
            if status == "connected"
            else (
                "PIPELINE_CONNECTION_FORBIDDEN"
                if status == "forbidden"
                else "PIPELINE_CONNECTION_UNCONFIRMED",
            )
        )
        evidence.update(volume.evidence_refs + capacity.evidence_refs)
        rows.append(PipelineConnectionAnalysis(observation, status, utilization, reasons))
    complete = scope.coverage_complete and all(row.state != "unknown" for row in rows)
    state: Literal["connected", "confirmed_absent", "unknown"] = (
        "connected"
        if any(r.state == "connected" for r in rows)
        else "confirmed_absent"
        if complete
        else "unknown"
    )
    identity = content_hash(
        (
            "accepted-pipeline-connections-v1",
            scope,
            tuple(sorted(observations, key=lambda o: o.connection.connection_id)),
            tuple(sorted(config.pipeline_products)),
        )
    )
    return PipelineConnectionsAnalysis(
        "pipeline-analysis:" + identity,
        scope,
        tuple(rows),
        state,
        complete,
        tuple(sorted(excluded)),
        (
            "ACCEPTED_END_TO_END_CONNECTIONS_ONLY",
            "NO_CONNECTION_COMPOSITION",
            "NO_AGGREGATE_UTILIZATION",
        ),
        tuple(sorted(evidence)),
    )
