"""Canonical DTO projection of public ML1 results; no domain formula lives here."""

from dataclasses import asdict
from datetime import timedelta
from typing import Any, Literal

from a2_contracts.common import canonical_hash
from a2_contracts.v2.evidence import EVIDENCE_GROUP_PREFIX, MAX_GROUP_MEMBERS, EvidenceGroup
from a2_contracts.v2.journeys import (
    BoundaryNodeSets,
    CountResult,
    JourneyResult,
    LastReturn,
    Leg,
    LineGeometry,
    ReferenceResult,
)
from a2_contracts.v2.map_data import AnalysisMapData
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
from a2_engine import MetricResult, category_index

from a2_api.core.jobs import V2JobContext


class Projection:
    def __init__(self, context: V2JobContext, evidence: list[EvidenceRef]) -> None:
        self.context = context
        self.evidence = evidence
        self.evidence_groups: list[EvidenceGroup] = []
        self.metrics: list[Metric] = []
        self.scores: list[Score] = []
        self.journeys: list[JourneyResult] = []
        self.references: list[ReferenceResult] = []
        self.counts: list[CountResult] = []
        self.last_returns: list[LastReturn] = []
        self.map_data = AnalysisMapData()
        self.limitations: set[str] = set()

    def shared_evidence(self, refs: tuple[str, ...]) -> tuple[str, ...]:
        """Factor a repeated catalogue before constructing thousands of DTO rows."""
        if len(refs) < 32:
            return refs
        identities = sorted(set(refs))
        result = []
        for start in range(0, len(identities), MAX_GROUP_MEMBERS):
            members = identities[start : start + MAX_GROUP_MEMBERS]
            if len(members) == 1:
                result.extend(members)
                continue
            group_id = EVIDENCE_GROUP_PREFIX + canonical_hash(members)
            if not any(group.group_id == group_id for group in self.evidence_groups):
                self.evidence_groups.append(EvidenceGroup(group_id=group_id, evidence_ids=members))
            result.append(group_id)
        return tuple(result)

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
            evidence_groups=self.evidence_groups,
            limitations=sorted(self.limitations),
            versions=self.context.versions,
            journeys=self.journeys,
            references=self.references,
            counts=self.counts,
            last_returns=self.last_returns,
            map_data=self.map_data,
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


def project_urban(
    out: Projection,
    analysis: Any,
    *,
    observed_score_ids: frozenset[str] | None = None,
    bounded: bool = False,
) -> None:
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
                    MetricResult(chosen.journey.total_seconds, "available", "MINIMUM_TIME_UNPROVEN")
                    if chosen is not None and not category.minimum_proven
                    else known(
                        chosen.journey.total_seconds if chosen else None,
                        "CONFIRMED_UNREACHABLE"
                        if category.minimum_proven
                        else "MINIMUM_TIME_UNPROVEN",
                    ),
                    s,
                    category.evidence_refs,
                    full=category.minimum_proven and not bounded,
                    journey_id=chosen.journey.candidate.journey_id if chosen else None,
                    time_kind="complete_chain",
                    population_basis=basis,
                    confirmed_absence=confirmed_absence,
                )
                if not bounded:
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
                observed_id = "observed-urban-accessibility-" + key
                # Analyses add estimates only for unproven minima. A scenario
                # keeps its saved presentation roster across proof transitions.
                if mode.passenger_mode == "public_transport" and (
                    not category.minimum_proven
                    if observed_score_ids is None
                    else observed_id in observed_score_ids
                ):
                    if category.minimum_proven:
                        observed = MetricResult(None, "not_applicable", "USE_PROVEN_CATEGORY_INDEX")
                    else:
                        estimate = category_index(
                            chosen.journey.total_seconds if chosen is not None else None,
                            analysis.h_seconds.value,
                        )
                        observed = MetricResult(
                            estimate.value,
                            estimate.status,
                            "MINIMUM_NOT_PROVEN"
                            if estimate.value is not None
                            else "OBSERVED_ROUTE_UNAVAILABLE"
                            if chosen is None
                            else estimate.reason_code,
                        )
                    out.score(
                        observed_id,
                        "observed_route_accessibility",
                        observed,
                        s,
                        sorted(set(category.evidence_refs) | set(analysis.area.source_refs)),
                        metrics=(raw, h),
                        full=category.minimum_proven,
                        ranking=False,
                    )
                    if not category.minimum_proven:
                        out.limitations.update(
                            {"OBSERVED_ROUTE_ESTIMATE", "MINIMUM_NOT_PROVEN", "NOT_A_CITY_RANKING"}
                        )
                out.limitations.update(category.limitations)
            point_score = out.score(
                "urban-point-" + p.point_id,
                "point_index",
                point.index,
                subject,
                p.source_refs,
                full=point.completeness == "full",
                scores=tuple(c_scores),
            )
            # Keep every point task visible, while the territorial dependency
            # graph follows the engine's original positive/unknown weights.
            if p.population != 0:
                point_scores.append(point_score)
        populations = [point.point.population for point in mode.points]
        population = sum(populations) if all(p is not None for p in populations) else None
        coverage = analysis.population_coverage
        population_full = (
            coverage.complete
            and coverage.excluded_population == 0
            and coverage.point_ids == frozenset(point.point.point_id for point in mode.points)
            and population is not None
        )
        basis = PopulationBasis(
            population=population,
            population_method="selected_original_cell_weights"
            if bounded
            else "accepted_original_cell_weights",
            year=None,
            coverage=1 if population_full or (bounded and population is not None) else 0,
            unit="persons",
            unknown_positive_weight=population is None if bounded else not population_full,
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


def project_car(
    out: Projection,
    result: Any,
    accepted: Any,
    reference: Any,
    *,
    geometry: LineGeometry | None = None,
) -> None:
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
            geometry=geometry,
            geometry_kind="actual" if geometry is not None else "none",
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


def _confirmed_missing_metrics(out: Projection, identities: tuple[str, ...]) -> None:
    """Project the public solver's proven absence into canonical raw tasks."""
    selected = set(identities)
    out.metrics[:] = [
        metric.model_copy(update={"reason_code": "NO_FEASIBLE_ROUTE"})
        if metric.metric_id in selected
        else metric
        for metric in out.metrics
    ]


def project_missing_public_timetable(out: Projection, reference: Any) -> None:
    """Represent absent source coverage, never confirmed absence of a service."""
    request = out.context.normalized_request
    assert isinstance(request, IntercityPassengerRequest)
    if not out.references:
        project_reference(out, reference)
    modes = request.passenger_modes or ["road", "rail", "air", "water"]
    selected: list[Literal["road", "rail", "air", "water", "mixed", "public_transport"]] = [
        mode for mode in modes if mode != "private_car"
    ]
    selected.append("public_transport")
    reason = "INTERCITY_SCHEDULES_UNAVAILABLE"
    raw = MetricResult(None, "unknown", reason)
    for mode in selected:
        subject = SubjectRef(
            kind="city_pair",
            origin_id=request.origin_id,
            destination_id=request.destination_id,
            mode=mode,
        )
        prefix = "intercity-" + mode
        path = out.metric(
            prefix + "-path", "path_seconds", raw, subject, (), time_kind="complete_chain"
        )
        wait = out.metric(prefix + "-wait", "initial_wait_seconds", raw, subject, ())
        out.metric(prefix + "-total", "total_seconds", raw, subject, (), time_kind="complete_chain")
        path_score = out.score(
            prefix + "-path-score",
            "path_index",
            raw,
            subject,
            (),
            metrics=(path,),
            kind="reference_ratio",
            reference_ids=(reference.reference_id,),
        )
        wait_score = out.score(
            prefix + "-wait-score",
            "waiting_index",
            raw,
            subject,
            (),
            metrics=(wait,),
            kind="initial_wait",
        )
        out.score(
            prefix,
            "passenger_index",
            raw,
            subject,
            (),
            scores=(path_score, wait_score),
            kind="aggregate",
        )
    out.limitations.add(reason)


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
        subject = SubjectRef(
            kind="city_pair",
            origin_id=request.origin_id,
            destination_id=request.destination_id,
            mode="public_transport" if group.category == "public" else group.category,
        )
        c = group.best_known
        prefix = "intercity-" + group.category
        if c is None:
            routes = [
                candidate
                for candidate in analysis.candidates
                if group.category == "public" or candidate.category == group.category
            ]
            absent = (
                analysis.timetable_complete
                and analysis.modes_complete
                and analysis.search.interrupted is False
                and not routes
            )
            reason = "NO_FEASIBLE_ROUTE" if absent else "INDEX_WINNER_UNAVAILABLE"
            raw = MetricResult(
                None,
                "not_applicable" if absent else "unknown",
                "CONFIRMED_UNREACHABLE" if absent else reason,
            )
            path_metric = out.metric(
                prefix + "-path",
                "path_seconds",
                raw,
                subject,
                analysis.evidence_refs,
                full=absent,
                time_kind="complete_chain",
            )
            wait_metric = out.metric(
                prefix + "-wait",
                "initial_wait_seconds",
                raw,
                subject,
                analysis.evidence_refs,
                full=absent,
            )
            out.metric(
                prefix + "-total",
                "total_seconds",
                raw,
                subject,
                analysis.evidence_refs,
                full=absent,
                time_kind="complete_chain",
            )
            if absent:
                _confirmed_missing_metrics(
                    out, (prefix + "-path", prefix + "-wait", prefix + "-total")
                )
            # A proven missing path has no path/wait ratio. Keep both tasks
            # represented without inventing numeric component indices.
            component = MetricResult(
                None,
                "not_applicable" if absent else "unknown",
                "INDEX_NOT_APPLICABLE_WITHOUT_ROUTE" if absent else reason,
            )
            path_score = out.score(
                prefix + "-path-score",
                "path_index",
                component,
                subject,
                analysis.evidence_refs,
                full=absent,
                metrics=(path_metric,),
                kind="reference_ratio",
                reference_ids=(reference.reference_id,),
            )
            wait_score = out.score(
                prefix + "-wait-score",
                "waiting_index",
                component,
                subject,
                analysis.evidence_refs,
                full=absent,
                metrics=(wait_metric,),
                kind="initial_wait",
            )
            out.score(
                prefix,
                "passenger_index",
                MetricResult(0, "available", reason)
                if absent
                else MetricResult(None, "unknown", reason),
                subject,
                analysis.evidence_refs,
                full=absent,
                scores=() if absent else (path_score, wait_score),
                kind="aggregate",
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
    shared_refs = out.shared_evidence(analysis.evidence_refs)
    h = out.metric(
        "cargo-h",
        "urban_h_seconds",
        analysis.h_seconds,
        territory,
        shared_refs,
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
            # Known road movement remains useful when compatibility or the
            # catalogue minimum is unknown. It is not access or delivery time
            # and must never become an input to an accessibility score.
            for observed in site.observed_routes:
                if observed.network_seconds is None:
                    continue
                node_id = observed.to_id if request.direction == "outbound" else observed.from_id
                out.metric(
                    "cargo-network-" + canonical_hash([prefix, observed.route_id])[:24],
                    "observed_truck_network_seconds",
                    MetricResult(
                        observed.network_seconds, "available", "TRUCK_NETWORK_ONLY_NOT_DELIVERY"
                    ),
                    SubjectRef(
                        kind="freight_site",
                        territory_id=request.city_id,
                        site_id=site.site_id,
                        node_id=node_id,
                        mode="road",
                    ),
                    observed.evidence_refs,
                    time_kind="movement",
                )
                out.limitations.add("TRUCK_NETWORK_ONLY_NOT_DELIVERY")
            raw = out.metric(
                "cargo-time-" + prefix,
                "minimum_truck_access_seconds",
                known(route.seconds if route else None, "MINIMUM_TIME_UNPROVEN"),
                subject,
                shared_refs,
                full=site.minimum_proven,
                time_kind="movement",
            )
            site_scores.append(
                out.score(
                    "cargo-site-" + prefix,
                    "cargo_site_index",
                    site.evaluation.index,
                    subject,
                    shared_refs,
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
            shared_refs,
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
            absent = group.optimum_proven
            reason = "NO_FEASIBLE_ROUTE" if absent else "DELIVERY_WINNER_UNKNOWN"
            missing_time = MetricResult(
                None,
                "not_applicable" if absent else "unknown",
                "CONFIRMED_UNREACHABLE" if absent else reason,
            )
            out.metric(
                prefix + "-time",
                "complete_delivery_seconds",
                missing_time,
                subject,
                analysis.evidence_refs,
                full=absent,
                time_kind="complete_chain",
            )
            out.metric(
                prefix + "-movement",
                "movement_seconds",
                missing_time,
                subject,
                analysis.evidence_refs,
                full=absent,
                time_kind="movement",
            )
            if absent:
                _confirmed_missing_metrics(out, (prefix + "-time", prefix + "-movement"))
            out.score(
                prefix,
                "delivery_index",
                MetricResult(0, "available", reason)
                if absent
                else MetricResult(None, "unknown", reason),
                subject,
                analysis.evidence_refs,
                full=absent,
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
