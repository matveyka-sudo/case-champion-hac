"""Canonical DTO projection of public ML1 results; no domain formula lives here."""

from dataclasses import asdict
from datetime import timedelta
from typing import Any, Literal

from a2_contracts.common import canonical_hash
from a2_contracts.v2.journeys import (
    BoundaryNodeSets,
    CountResult,
    JourneyResult,
    Leg,
    ReferenceResult,
)
from a2_contracts.v2.requests import (
    IntercityCargoRequest,
    IntercityPassengerRequest,
    UrbanCargoRequest,
    UrbanPassengerRequest,
)
from a2_contracts.v2.results import (
    AnalysisResult,
    ComponentRef,
    EvidenceRef,
    Metric,
    PopulationBasis,
    Score,
    SiteBasis,
    SubjectRef,
)
from a2_engine import MetricResult

from a2_api.core.jobs import V2JobContext


class Projection:
    def __init__(self, context: V2JobContext, evidence: list[EvidenceRef]) -> None:
        self.context = context
        self.evidence = evidence
        self.metrics: list[Metric] = []
        self.scores: list[Score] = []
        self.journeys: list[JourneyResult] = []
        self.references: list[ReferenceResult] = []
        self.counts: list[CountResult] = []
        self.limitations: set[str] = set()

    def metric(
        self,
        identity: str,
        name: str,
        result: MetricResult,
        subject: SubjectRef,
        refs: tuple[str, ...] | list[str],
        *,
        unit: str = "seconds",
        full: bool = False,
        journey_id: str | None = None,
        time_kind: Literal["movement", "complete_chain"] | None = None,
        population_basis: PopulationBasis | None = None,
        confirmed_absence: bool = False,
    ) -> str:
        self.metrics.append(
            Metric(
                metric_id=identity,
                name=name,
                value=result.value,
                unit=unit,
                status="not_applicable" if confirmed_absence else result.status,
                reason_code="NO_FEASIBLE_ROUTE" if confirmed_absence else result.reason_code,
                source_refs=list(refs),
                subject=subject,
                completeness="full" if full else "partial",
                journey_id=journey_id,
                time_kind=time_kind,
                population_basis=population_basis,
            )
        )
        return identity

    def score(
        self,
        identity: str,
        name: str,
        result: MetricResult,
        subject: SubjectRef,
        refs: tuple[str, ...] | list[str],
        *,
        full: bool = False,
        metrics: tuple[str, ...] = (),
        scores: tuple[str, ...] = (),
        basis: PopulationBasis | SiteBasis | None = None,
        kind: Literal["reference_ratio", "initial_wait", "aggregate", "delivery_deadline"]
        | None = None,
        reference_ids: tuple[str, ...] = (),
        ranking: bool = False,
    ) -> str:
        self.scores.append(
            Score(
                score_id=identity,
                name=name,
                value=result.value,
                status=result.status,
                completeness="full" if full else "partial",
                methodology_version=self.context.versions.methodology_version,
                reason_code=result.reason_code,
                subject=subject,
                evidence_refs=list(refs),
                components=[ComponentRef(kind="metric", id=i) for i in metrics]
                + [ComponentRef(kind="score", id=i) for i in scores],
                ranking_eligible=ranking and full and result.value is not None,
                aggregation_basis=basis,
                calculation_kind=kind,
                reference_ids=list(reference_ids),
            )
        )
        return identity

    def finish(self) -> AnalysisResult:
        request = self.context.normalized_request
        items: list[Metric | Score] = [*self.metrics, *self.scores]
        known = any(item.value is not None for item in items)
        complete = all(
            item.status in ("available", "not_applicable") and item.completeness == "full"
            for item in items
        )
        result = AnalysisResult(
            schema_version="2",
            analysis_id=self.context.analysis_id,
            analysis_kind=request.analysis_kind,
            request=request,
            snapshot_id=self.context.snapshot_id,
            methodology_version=self.context.versions.methodology_version,
            config_hash=self.context.versions.config_hash,
            traffic_mode=request.traffic_mode,
            status="available" if complete else "partial" if known else "unknown",
            metrics=self.metrics,
            scores=self.scores,
            evidence_refs=self.evidence,
            limitations=sorted(self.limitations),
            versions=self.context.versions,
            journeys=self.journeys,
            references=self.references,
            counts=self.counts,
            last_returns=[],
            result_hash="pending-publication",
        )
        return result.model_copy(
            update={
                "result_hash": canonical_hash(
                    result.model_dump(mode="json", exclude={"result_hash"})
                )
            }
        )


def known(value: float | None, reason: str = "DATA_UNAVAILABLE") -> MetricResult:
    return (
        MetricResult(float(value), "available")
        if value is not None
        else MetricResult(None, "unknown", reason)
    )


def project_urban(out: Projection, analysis: Any) -> None:
    request = out.context.normalized_request
    assert isinstance(request, UrbanPassengerRequest)
    territory = SubjectRef(
        kind="territory", territory_id=request.city_id, mode=request.passenger_mode
    )
    h = out.metric(
        "urban-h",
        "urban_h_seconds",
        analysis.h_seconds,
        territory,
        analysis.area.source_refs,
        full=analysis.h_seconds.value is not None,
    )
    full_journeys = {
        item.best.journey.candidate.journey_id
        for mode in analysis.modes
        for point in mode.points
        for item in point.categories
        if item.best is not None and item.minimum_proven
    }
    for timing in analysis.journeys:
        if not timing.admissible:
            continue
        c = timing.candidate
        if any(j.journey_id == c.journey_id for j in out.journeys):
            continue
        out.journeys.append(
            JourneyResult(
                journey_id=c.journey_id,
                scope="urban",
                origin_node_id=c.origin_node_id,
                destination_node_id=c.destination_node_id,
                requested_departure_at=request.departure_at,
                departure_at=c.departure_at,
                arrival_at=c.arrival_at,
                total_seconds=timing.total_seconds,
                initial_wait_seconds=timing.initial_wait_seconds,
                path_seconds=timing.total_seconds,
                total_wait_seconds=timing.total_wait_seconds,
                legs=[Leg.model_validate(asdict(leg)) for leg in c.legs],
                geometry=None,
                geometry_kind="none",
                status="available",
                reason_code=None,
                snapshot_id=out.context.snapshot_id,
                completeness="full" if c.journey_id in full_journeys else "partial",
                traffic_mode=request.traffic_mode,
                time_kind="complete_chain",
                reference_id=None,
                destination_object_id=c.destination_id,
                point_id=c.point_id,
                evidence_refs=list(c.evidence_refs),
            )
        )
    for mode in analysis.modes:
        point_scores = []
        for point in mode.points:
            c_scores = []
            p = point.point
            subject = SubjectRef(
                kind="sample_point",
                territory_id=request.city_id,
                point_id=p.point_id,
                mode=mode.passenger_mode,
            )
            basis = PopulationBasis(
                population=p.population,
                population_method=p.population_method or "unknown",
                year=p.population_year,
                coverage=1 if p.population is not None else 0,
                unit="persons",
                unknown_positive_weight=p.population is None,
            )
            for category in point.categories:
                key = canonical_hash([p.point_id, category.category, mode.passenger_mode])[:24]
                s = subject.model_copy(update={"category": category.category})
                chosen = category.best
                confirmed_absence = (
                    chosen is None
                    and category.minimum_proven
                    and category.index.value == 0
                    and category.index.reason_code == "NO_FEASIBLE_ROUTE"
                )
                raw = out.metric(
                    "urban-time-" + key,
                    "minimum_journey_seconds",
                    known(
                        chosen.journey.total_seconds if chosen else None,
                        "CONFIRMED_UNREACHABLE"
                        if category.minimum_proven
                        else "MINIMUM_TIME_UNPROVEN",
                    ),
                    s,
                    category.evidence_refs,
                    full=category.minimum_proven,
                    journey_id=chosen.journey.candidate.journey_id if chosen else None,
                    time_kind="complete_chain",
                    population_basis=basis,
                    confirmed_absence=confirmed_absence,
                )
                count_id = out.metric(
                    "urban-count-" + key,
                    "accessible_object_count",
                    category.count,
                    s,
                    category.evidence_refs,
                    unit="objects",
                    full=category.completeness == "full",
                )
                out.counts.append(
                    CountResult(
                        metric_id=count_id,
                        threshold_seconds=category.threshold.seconds
                        if category.threshold
                        else None,
                        value=int(category.count.value)
                        if category.count.value is not None
                        else None,
                        status=category.count.status,
                        reason_code=category.count.reason_code,
                        dependency_job_id=None,
                        evidence_refs=list(category.evidence_refs),
                    )
                )
                c_scores.append(
                    out.score(
                        "urban-category-" + key,
                        "category_index",
                        category.index,
                        s,
                        category.evidence_refs,
                        full=category.completeness == "full",
                        metrics=() if confirmed_absence else (raw, h),
                    )
                )
                out.limitations.update(category.limitations)
            point_scores.append(
                out.score(
                    "urban-point-" + p.point_id,
                    "point_index",
                    point.index,
                    subject,
                    p.source_refs,
                    full=point.completeness == "full",
                    scores=tuple(c_scores),
                )
            )
        populations = [point.point.population for point in mode.points]
        population = sum(populations) if all(p is not None for p in populations) else None
        coverage = analysis.population_coverage
        basis = PopulationBasis(
            population=population,
            population_method="accepted_original_cell_weights",
            year=None,
            coverage=1 if coverage.complete else 0,
            unit="persons",
            unknown_positive_weight=population is None or not coverage.complete,
        )
        out.score(
            "urban-territory-" + mode.passenger_mode,
            "territory_index",
            mode.index,
            territory,
            mode.evidence_refs,
            full=mode.completeness == "full",
            scores=tuple(point_scores),
            basis=basis,
            ranking=mode.ranking_eligible,
        )
    out.limitations.update(analysis.limitations)


def project_reference(out: Projection, reference: Any) -> None:
    out.references.append(
        ReferenceResult(
            reference_id=reference.reference_id,
            seconds=reference.seconds,
            status="available" if reference.seconds is not None else "unknown",
            completeness=reference.completeness,
            reason_code=None if reference.seconds is not None else "REFERENCE_UNAVAILABLE",
            profile_version=reference.scope.network.profile_version,
            graph_version=out.context.versions.graph_version,
            boundary_node_sets=BoundaryNodeSets(
                origin_node_ids=list(reference.scope.origin_node_ids),
                destination_node_ids=list(reference.scope.destination_node_ids),
            ),
            edges=list(reference.path.edge_ids) if reference.path else [],
            necessary_operations=list(reference.path.operation_ids) if reference.path else [],
            calibration_evidence=list(reference.evidence_refs),
        )
    )


def project_car(out: Projection, result: Any, accepted: Any, reference: Any) -> None:
    request = out.context.normalized_request
    assert isinstance(request, IntercityPassengerRequest)
    if not out.references:
        project_reference(out, reference)
    subject = SubjectRef(
        kind="city_pair",
        origin_id=request.origin_id,
        destination_id=request.destination_id,
        mode="private_car",
    )
    arrival = request.departure_at + timedelta(seconds=accepted.path_seconds)
    full = accepted.complete and result.completeness == "full"
    out.journeys.append(
        JourneyResult(
            journey_id=accepted.chain_id,
            scope="intercity",
            origin_node_id=accepted.origin_node_id,
            destination_node_id=accepted.destination_node_id,
            requested_departure_at=request.departure_at,
            departure_at=request.departure_at,
            arrival_at=arrival,
            total_seconds=accepted.path_seconds,
            initial_wait_seconds=0,
            path_seconds=accepted.path_seconds,
            total_wait_seconds=0,
            legs=[
                Leg(
                    mode="private_car",
                    kind="ride",
                    from_node_id=accepted.origin_node_id,
                    to_node_id=accepted.destination_node_id,
                    start_at=request.departure_at,
                    end_at=arrival,
                    duration_seconds=accepted.path_seconds,
                    service_id=None,
                    evidence_refs=list(accepted.evidence_refs),
                )
            ],
            geometry=None,
            geometry_kind="none",
            status="available",
            reason_code=None,
            snapshot_id=out.context.snapshot_id,
            completeness="full" if accepted.complete else "partial",
            traffic_mode=request.traffic_mode,
            time_kind="complete_chain",
            reference_id=reference.reference_id,
            destination_object_id=None,
            evidence_refs=list(accepted.evidence_refs),
        )
    )
    raw = out.metric(
        "private-car-path",
        "private_car_path_seconds",
        known(accepted.path_seconds),
        subject,
        accepted.evidence_refs,
        full=accepted.complete,
        journey_id=accepted.chain_id,
        time_kind="complete_chain",
    )
    out.score(
        "private-car-index",
        "private_car_index",
        result.path_index,
        subject,
        accepted.evidence_refs,
        full=full,
        metrics=(raw,),
        kind="reference_ratio",
        reference_ids=(reference.reference_id,),
        ranking=full,
    )
    out.limitations.update(result.diagnostics)


def project_intercity(
    out: Projection, analysis: Any, reference: Any, accepted_car: Any = None
) -> None:
    request = out.context.normalized_request
    assert isinstance(request, IntercityPassengerRequest)
    project_reference(out, reference)
    mode_map = {binding.transport_type: binding.mode for binding in analysis.transport_modes}
    mode_map.update({"physical:" + mode: mode for mode in mode_map.values()})
    for candidate in analysis.candidates:
        path = candidate.route.path
        legs = [
            Leg(
                mode="walk" if step.kind == "walk" else mode_map[step.transport_type],
                kind=step.kind,
                from_node_id=step.from_node_id,
                to_node_id=step.to_node_id,
                start_at=step.start_at,
                end_at=step.end_at,
                duration_seconds=step.duration_seconds,
                service_id=step.run_id,
                evidence_refs=list(step.evidence_refs),
            )
            for step in path.steps
        ]
        first_departure = next(step.start_at for step in path.steps if step.kind == "ride")
        out.journeys.append(
            JourneyResult(
                journey_id=candidate.route.chain_id,
                scope="intercity",
                origin_node_id=path.origin_node_id,
                destination_node_id=path.destination_node_id,
                requested_departure_at=request.departure_at,
                departure_at=first_departure,
                arrival_at=path.arrival_at,
                total_seconds=candidate.total_seconds,
                initial_wait_seconds=candidate.initial_wait_seconds,
                path_seconds=candidate.path_seconds,
                total_wait_seconds=candidate.initial_wait_seconds + candidate.transfer_wait_seconds,
                legs=legs,
                geometry=None,
                geometry_kind="none",
                status="available",
                reason_code=None,
                snapshot_id=out.context.snapshot_id,
                completeness="full" if candidate.chain_complete else "partial",
                traffic_mode=request.traffic_mode,
                time_kind="complete_chain",
                reference_id=reference.reference_id,
                destination_object_id=None,
                evidence_refs=list(analysis.evidence_refs),
            )
        )
    for group in (*analysis.by_mode, analysis.public):
        if request.passenger_modes is not None and group.category not in (
            *request.passenger_modes,
            "public",
        ):
            continue
        subject = SubjectRef(
            kind="city_pair",
            origin_id=request.origin_id,
            destination_id=request.destination_id,
            mode="public_transport" if group.category == "public" else group.category,
        )
        c = group.best_known
        prefix = "intercity-" + group.category
        if c is None:
            out.score(
                prefix,
                "passenger_index",
                MetricResult(None, "unknown", "INDEX_WINNER_UNAVAILABLE"),
                subject,
                analysis.evidence_refs,
            )
            continue
        full = group.optimum_proven and c.chain_complete and c.evaluation.completeness == "full"
        path_metric = out.metric(
            prefix + "-path",
            "path_seconds",
            known(c.path_seconds),
            subject,
            analysis.evidence_refs,
            full=c.chain_complete,
            journey_id=c.route.chain_id,
            time_kind="complete_chain",
        )
        wait_metric = out.metric(
            prefix + "-wait",
            "initial_wait_seconds",
            known(c.initial_wait_seconds),
            subject,
            analysis.evidence_refs,
            full=c.chain_complete,
            journey_id=c.route.chain_id,
        )
        out.metric(
            prefix + "-total",
            "total_seconds",
            known(c.total_seconds),
            subject,
            analysis.evidence_refs,
            full=c.chain_complete,
            journey_id=c.route.chain_id,
            time_kind="complete_chain",
        )
        path_score = out.score(
            prefix + "-path-score",
            "path_index",
            c.evaluation.path_index,
            subject,
            analysis.evidence_refs,
            full=full,
            metrics=(path_metric,),
            kind="reference_ratio",
            reference_ids=(reference.reference_id,),
        )
        wait_score = out.score(
            prefix + "-wait-score",
            "waiting_index",
            c.evaluation.waiting_index,
            subject,
            analysis.evidence_refs,
            full=full,
            metrics=(wait_metric,),
            kind="initial_wait",
        )
        out.score(
            prefix,
            "passenger_index",
            c.evaluation.index,
            subject,
            analysis.evidence_refs,
            full=full,
            scores=(path_score, wait_score),
            kind="aggregate",
            ranking=analysis.full_ranking_eligible,
        )
    for frequency in analysis.frequency:
        if request.passenger_modes is not None and frequency.category not in (
            *request.passenger_modes,
            "public",
        ):
            continue
        subject = SubjectRef(
            kind="city_pair", origin_id=request.origin_id, destination_id=request.destination_id
        )
        out.metric(
            "departures-" + frequency.category,
            "unique_physical_departures",
            known(frequency.count),
            subject,
            analysis.evidence_refs,
            unit="departures",
            full=frequency.complete,
        )
    out.limitations.update(analysis.reasons)
    if analysis.car is not None and accepted_car is not None:
        project_car(out, analysis.car, accepted_car, reference)


def project_urban_cargo(out: Projection, analysis: Any) -> None:
    request = out.context.normalized_request
    assert isinstance(request, UrbanCargoRequest)
    territory = SubjectRef(kind="territory", territory_id=request.city_id, mode="road")
    h = out.metric(
        "cargo-h",
        "urban_h_seconds",
        analysis.h_seconds,
        territory,
        analysis.evidence_refs,
        full=analysis.h_seconds.value is not None,
    )
    for group in analysis.by_type:
        site_scores = []
        for site in group.sites:
            subject = SubjectRef(
                kind="freight_site",
                territory_id=request.city_id,
                site_id=site.site_id,
                node_id=site.selected_node_id,
                mode="road",
            )
            prefix = canonical_hash([group.scope.node_type, site.site_id, group.scope.direction])[
                :24
            ]
            route = site.best_known_route
            raw = out.metric(
                "cargo-time-" + prefix,
                "minimum_truck_access_seconds",
                known(route.seconds if route else None, "MINIMUM_TIME_UNPROVEN"),
                subject,
                analysis.evidence_refs,
                full=site.minimum_proven,
                time_kind="movement",
            )
            site_scores.append(
                out.score(
                    "cargo-site-" + prefix,
                    "cargo_site_index",
                    site.evaluation.index,
                    subject,
                    analysis.evidence_refs,
                    full=site.minimum_proven,
                    metrics=(raw, h),
                )
            )
            out.limitations.update(site.reasons)
        basis = SiteBasis(
            site_count=len(analysis.coverage.expected_site_ids),
            weighting="equal_physical_sites",
            coverage=1 if analysis.coverage.roster_complete else 0,
        )
        out.score(
            "cargo-territory-" + group.scope.node_type,
            "cargo_access_index",
            group.index,
            territory,
            analysis.evidence_refs,
            full=group.full_ranking_eligible,
            scores=tuple(site_scores),
            basis=basis,
            ranking=group.full_ranking_eligible,
        )


def project_delivery(out: Projection, analysis: Any) -> None:
    request = out.context.normalized_request
    assert isinstance(request, IntercityCargoRequest)
    for group in (*analysis.by_mode, analysis.overall):
        subject = SubjectRef(
            kind="city_pair",
            origin_id=request.origin_id,
            destination_id=request.destination_id,
            mode="mixed" if group.category == "overall" else group.category,
        )
        prefix = "delivery-" + group.category
        path = group.best_known
        if path is None:
            out.metric(
                prefix + "-time",
                "complete_delivery_seconds",
                known(None, "DELIVERY_WINNER_UNKNOWN"),
                subject,
                analysis.evidence_refs,
                time_kind="complete_chain",
            )
            out.score(
                prefix,
                "delivery_index",
                known(None, "DELIVERY_WINNER_UNKNOWN"),
                subject,
                analysis.evidence_refs,
                kind="delivery_deadline",
            )
            continue
        raw = out.metric(
            prefix + "-time",
            "complete_delivery_seconds",
            path.evaluation.total_seconds,
            subject,
            analysis.evidence_refs,
            full=group.optimum_proven,
            time_kind="complete_chain",
        )
        out.metric(
            prefix + "-movement",
            "movement_seconds",
            path.evaluation.movement_seconds,
            subject,
            analysis.evidence_refs,
            full=analysis.data_complete,
            time_kind="movement",
        )
        out.score(
            prefix,
            "delivery_index",
            path.evaluation.index,
            subject,
            analysis.evidence_refs,
            full=group.optimum_proven,
            metrics=(raw,),
            kind="delivery_deadline",
            ranking=analysis.full_ranking_eligible,
        )
    for path in analysis.incomplete_paths:
        subject = SubjectRef(
            kind="city_pair", origin_id=request.origin_id, destination_id=request.destination_id
        )
        out.metric(
            "incomplete-" + path.path_id,
            "known_movement_seconds",
            known(path.known_movement_seconds),
            subject,
            analysis.evidence_refs,
            time_kind="movement",
        )
    out.limitations.update(analysis.reasons)


def project_pipeline(out: Projection, analysis: Any) -> None:
    request = out.context.normalized_request
    assert isinstance(request, IntercityCargoRequest)
    subject = SubjectRef(
        kind="pipeline",
        origin_id=request.origin_id,
        destination_id=request.destination_id,
        mode="pipeline",
    )
    if not analysis.connections:
        out.metric(
            "pipeline-connectivity",
            "pipeline_connection_confirmed",
            known(None, "PIPELINE_CONNECTION_UNCONFIRMED"),
            subject,
            analysis.evidence_refs,
            unit="connections",
        )
    for row in analysis.connections:
        identity = row.observation.connection.connection_id
        out.metric(
            "pipeline-utilization-" + identity,
            "pipeline_utilization_percent",
            row.utilization.utilization_percent,
            subject,
            analysis.evidence_refs,
            unit="percent",
            full=row.state == "connected" and analysis.connectivity_complete,
        )
        out.limitations.update(row.reasons)
        out.limitations.update(row.utilization.diagnostics)
    out.limitations.update(analysis.limitations)
