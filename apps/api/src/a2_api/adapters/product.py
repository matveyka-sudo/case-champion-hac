"""Unified production hooks. HTTP preflight never runs a route calculation."""

import asyncio
from dataclasses import replace
from zoneinfo import ZoneInfo

from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import (
    AnalysisRequest,
    IntercityCargoRequest,
    IntercityPassengerRequest,
    UrbanCargoRequest,
    UrbanPassengerRequest,
)
from a2_contracts.v2.results import AnalysisResult, EvidenceRef, SubjectRef
from a2_contracts.v2.scenarios import ScenarioResult
from a2_engine import (
    MetricResult,
    ReferenceComparison,
    UrbanTransitNetwork,
    analyze_intercity_cargo,
    analyze_intercity_passenger,
    analyze_pipeline_connections,
    analyze_urban_candidates,
    analyze_urban_cargo,
    build_reference,
    intercity_car_index,
    load_cargo_delivery_rules,
    load_freight_config,
    load_intercity_passenger_rules,
    load_passenger_config,
    load_urban_config,
    load_urban_journey_rules,
    reference_time_from_result,
    search_urban_transit,
)

from a2_api.adapters.product_inputs import (
    DeliveryInputs,
    IntercityInputs,
    PipelineInputs,
    UrbanCargoInputs,
    UrbanInputs,
    candidate_is_static,
)
from a2_api.adapters.product_projection import (
    Projection,
    project_car,
    project_delivery,
    project_intercity,
    project_pipeline,
    project_urban,
    project_urban_cargo,
)
from a2_api.adapters.product_runtime import load_runtime, unavailable
from a2_api.adapters.product_runtime import registry as registry
from a2_api.core.batches import BatchPlan
from a2_api.core.config import RuntimeSettings
from a2_api.core.errors import DomainError
from a2_api.core.jobs import CalculationOutput, ClaimedJob, V2JobContext
from a2_api.core.registry import ProductRegistry
from a2_api.core.runtime_admission import run_heavy
from a2_api.workers.checkpoint_channel import CheckpointChannel


async def preflight(request: AnalysisRequest, snapshot: AcceptedData) -> BatchPlan | None:
    # One reservation covers every validation stage. The owning thread waits for
    # the inner async adapter and its executor to finish even if HTTP times out.
    return await run_heavy(lambda: asyncio.run(_preflight(request, snapshot)))


async def _preflight(request: AnalysisRequest, snapshot: AcceptedData) -> BatchPlan | None:
    runtime = await asyncio.to_thread(load_runtime)
    if runtime.digest not in (RuntimeSettings().code_version or ""):
        raise DomainError(409, "incompatible_versions", "Product runtime code identity differs")
    profile, inputs = runtime.select(request, snapshot)
    if profile.executor == "walk_pilot":
        from a2_api.adapters import walk_pilot

        await walk_pilot.preflight(request, snapshot)
    elif profile.executor == "walk_batches":
        from a2_api.adapters import walk_batches

        return await walk_batches.preflight(
            request, snapshot, bound=runtime.walk_bindings.get(profile.profile_id)
        )
    elif profile.executor == "certified_walk_model":
        from a2_api.adapters import certified_walk_model

        await certified_walk_model.preflight(request, snapshot)
    elif inputs is None:
        raise unavailable("RUNTIME_TRANSPORT_INPUTS_REQUIRED")
    return None


def calculate(
    claim: ClaimedJob, channel: CheckpointChannel | None = None
) -> AnalysisResult | CalculationOutput | ScenarioResult:
    if claim.kind not in {"analysis", "scenario"} or not isinstance(claim.context, V2JobContext):
        raise DomainError(
            422, "unsupported_scenario", "Product handler requires a canonical calculation job"
        )
    context = claim.context
    runtime = load_runtime()
    snapshot = runtime.snapshots.get(context.snapshot_id)
    current = ProductRegistry()
    if (
        snapshot is None
        or runtime.digest not in context.versions.code_version
        or canonical_hash(snapshot.model_dump(mode="json")) != context.snapshot_hash
        or context.versions.config_hash != current.config_hash
        or context.versions.methodology_version != current.methodology.version
        or context.versions.graph_version != snapshot.provenance.graph_version
        or context.versions.calendar_version != snapshot.provenance.calendar_version
        or context.versions.population_version != snapshot.provenance.population_version
    ):
        raise DomainError(409, "incompatible_versions", "Queued product inputs or runtime changed")
    request = context.normalized_request
    profile, inputs = runtime.select(request, snapshot)
    if claim.kind == "scenario":
        if profile.executor != "accepted_inputs" or inputs is None:
            raise DomainError(
                422, "unsupported_scenario", "SCENARIO_MUTABLE_TRANSPORT_MODEL_UNAVAILABLE"
            )
        from a2_api.adapters.product_scenario_runtime import calculate_saved

        return calculate_saved(
            claim,
            inputs,
            profile_id=profile.profile_id,
            documents=runtime.scenario_documents.get(profile.profile_id, ()),
        )
    if profile.executor == "walk_pilot":
        from a2_api.adapters import walk_pilot

        return walk_pilot.calculate(claim)
    if profile.executor == "walk_batches":
        if channel is None:
            raise unavailable("FENCED_CHECKPOINT_CHANNEL_REQUIRED")
        from a2_api.adapters import walk_batches

        return walk_batches.calculate(
            claim, channel, bound=runtime.walk_bindings.get(profile.profile_id), snapshot=snapshot
        )
    if profile.executor == "certified_walk_model":
        from a2_api.adapters import certified_walk_model

        return certified_walk_model.calculate(claim)
    evidence = {
        row.evidence_id: EvidenceRef(
            evidence_id=row.evidence_id,
            source_id=row.source_id,
            url=row.url,
            retrieved_at=row.retrieved_at,
            observed_at=None,
            section="accepted-snapshot",
        )
        for row in snapshot.evidence
    }
    for row in profile.evidence:
        if row.evidence_id in evidence and evidence[row.evidence_id] != row:
            raise DomainError(409, "incompatible_versions", "Runtime evidence identity collision")
        evidence[row.evidence_id] = row
    out = Projection(context, list(evidence.values()))
    if isinstance(inputs, UrbanInputs):
        assert isinstance(request, UrbanPassengerRequest)
        config, rules = load_urban_config(), load_urban_journey_rules()
        urban_context = replace(inputs.context, requested_departure_at=request.departure_at)
        population = replace(inputs.population_coverage, context=urban_context)
        coverage = tuple(
            replace(row, scope=replace(row.scope, context=urban_context)) for row in inputs.coverage
        )
        candidates = []
        for candidate in inputs.candidates:
            if (
                candidate.passenger_mode != request.passenger_mode
                or candidate.category not in request.categories
            ):
                continue
            if not candidate_is_static(candidate):
                raise unavailable("SCHEDULED_CANDIDATES_REQUIRE_DATED_TIMETABLE_RECOMPUTATION")
            offset = request.departure_at - candidate.departure_at
            candidates.append(
                replace(
                    candidate,
                    context=urban_context,
                    departure_at=candidate.departure_at + offset,
                    arrival_at=candidate.arrival_at + offset,
                    legs=tuple(
                        replace(leg, start_at=leg.start_at + offset, end_at=leg.end_at + offset)
                        for leg in candidate.legs
                    ),
                )
            )
        if request.passenger_mode in ("walk", "public_transport") and inputs.timetable is not None:
            network = UrbanTransitNetwork(
                urban_context, inputs.timetable, inputs.timetable.scope.evidence_refs
            )
            searched: dict[tuple[str, str], bool] = {}
            for point in inputs.points:
                for category in request.categories:
                    results = []
                    missing_boundary = False
                    for destination in inputs.destinations:
                        if (
                            category not in destination.categories
                            or not destination.in_service_scope
                        ):
                            continue
                        if (
                            point.origin_node_id not in inputs.timetable.scope.node_ids
                            or (destination.physical_node_id or destination.access_node_id)
                            not in inputs.timetable.scope.node_ids
                        ):
                            missing_boundary = True
                            out.limitations.add("PHYSICAL_BOUNDARY_CONNECTOR_UNAVAILABLE")
                            continue
                        results.append(
                            search_urban_transit(
                                network,
                                point,
                                destination,
                                category=category,
                                rules=rules,
                                passenger_mode=request.passenger_mode,
                                resource_limit=inputs.resource_limit,
                            )
                        )
                    searched[(point.point_id, category)] = not missing_boundary and all(
                        row.search_complete for row in results
                    )
                    candidates.extend(
                        row.best_known.candidate for row in results if row.best_known is not None
                    )
            coverage = tuple(
                replace(
                    row,
                    search_complete=searched.get((row.scope.point_id, row.scope.category), False),
                )
                for row in coverage
            )
            local_day = request.departure_at.astimezone(
                ZoneInfo(inputs.timetable.scope.timezone)
            ).date()
            out.metric(
                "accepted-dated-service-runs",
                "accepted_dated_service_runs",
                MetricResult(
                    float(sum(run.service_date == local_day for run in inputs.timetable.runs)),
                    "available",
                ),
                SubjectRef(
                    kind="territory", territory_id=request.city_id, mode=request.passenger_mode
                ),
                inputs.timetable.scope.evidence_refs,
                unit="runs",
                full=False,
            )
        elif request.passenger_mode == "public_transport":
            raise unavailable("DATED_TIMETABLE_REQUIRED_FOR_PUBLIC_TRANSPORT")
        analysis = analyze_urban_candidates(
            urban_context,
            inputs.points,
            inputs.destinations,
            tuple(candidates),
            coverage,
            area=inputs.area,
            selected_categories={str(k): str(v) for k, v in request.priorities.items()},
            modes=(request.passenger_mode,),
            population_coverage=population,
            config=config,
            rules=rules,
            thresholds=inputs.thresholds,
        )
        project_urban(out, analysis)
    elif isinstance(inputs, IntercityInputs):
        assert isinstance(request, IntercityPassengerRequest)
        reference_scope = inputs.reference_scope
        if request.origin_node_ids is not None:
            reference_scope = replace(
                reference_scope,
                origin_node_ids=tuple(request.origin_node_ids),
                destination_node_ids=tuple(request.destination_node_ids or ()),
            )
        scope = replace(inputs.scope, comparison_scope=reference_scope)
        if request.passenger_modes is not None and request.passenger_modes != ["private_car"]:
            applicable = tuple(
                mode for mode in scope.applicable_modes if mode in request.passenger_modes
            )
            scope = replace(scope, applicable_modes=applicable)
        reference = build_reference(inputs.reference_graph, reference_scope)
        accepted_car = (
            inputs.car
            if (
                (request.passenger_modes is None or "private_car" in request.passenger_modes)
                and inputs.car_departure_at == request.departure_at
                and inputs.car is not None
                and inputs.car.traffic_mode == request.traffic_mode
                and (
                    request.origin_node_ids is None
                    or (
                        inputs.car.origin_node_id in request.origin_node_ids
                        and inputs.car.destination_node_id in (request.destination_node_ids or ())
                    )
                )
            )
            else None
        )
        if request.passenger_modes == ["private_car"]:
            assert accepted_car is not None
            passenger_config = load_passenger_config()
            scalar = reference_time_from_result(
                reference,
                ReferenceComparison(
                    accepted_car.comparison_scope,
                    accepted_car.origin_node_id,
                    accepted_car.destination_node_id,
                    accepted_car.traffic_mode,
                ),
                config=passenger_config,
            )
            car_result = intercity_car_index(
                accepted_car.chain_id, accepted_car.path_seconds, scalar, config=passenger_config
            )
            project_car(out, car_result, accepted_car, reference)
            return out.finish()
        passenger_analysis = analyze_intercity_passenger(
            scope,
            request.departure_at,
            inputs.timetable,
            inputs.transport_modes,
            inputs.physical_services,
            reference,
            config=load_passenger_config(),
            rules=load_intercity_passenger_rules(),
            frequency_start_at=inputs.frequency_start_at,
            frequency_end_at=inputs.frequency_end_at,
            readiness=inputs.readiness,
            car=accepted_car,
            resource_limit=inputs.resource_limit,
        )
        project_intercity(out, passenger_analysis, reference, accepted_car)
        if (
            request.passenger_modes is not None
            and "private_car" in request.passenger_modes
            and accepted_car is None
        ):
            out.score(
                "private-car-index",
                "private_car_index",
                MetricResult(None, "unknown", "ACCEPTED_CAR_OBSERVATION_DEPARTURE_UNAVAILABLE"),
                SubjectRef(
                    kind="city_pair",
                    origin_id=request.origin_id,
                    destination_id=request.destination_id,
                    mode="private_car",
                ),
                (),
                kind="reference_ratio",
            )
    elif isinstance(inputs, UrbanCargoInputs):
        assert isinstance(request, UrbanCargoRequest)
        if not profile.static_observations:
            raise unavailable("ACCEPTED_STATIC_TRUCK_OBSERVATION_PROOF_REQUIRED")
        routes = tuple(replace(route, requested_at=request.departure_at) for route in inputs.routes)
        selected = tuple(
            request.freight_node_types or sorted({node.analysis_type for node in inputs.nodes})
        )
        urban_cargo_analysis = analyze_urban_cargo(
            inputs.profile,
            request.departure_at,
            inputs.sites,
            inputs.nodes,
            routes,
            inputs.receipts,
            inputs.coverage,
            selected,
            inputs.area_km2,
            config=load_freight_config(),
        )
        project_urban_cargo(out, urban_cargo_analysis)
    elif isinstance(inputs, DeliveryInputs):
        assert isinstance(request, IntercityCargoRequest)
        delivery_scope = inputs.scope
        if request.origin_node_ids is not None:
            delivery_scope = replace(
                delivery_scope,
                origin_node_ids=tuple(request.origin_node_ids),
                destination_node_ids=tuple(request.destination_node_ids or ()),
            )
        if request.cargo_modes is not None:
            delivery_scope = replace(
                delivery_scope,
                allowed_modes=tuple(
                    m for m in delivery_scope.allowed_modes if m in request.cargo_modes
                ),
            )
        delivery_analysis = analyze_intercity_cargo(
            delivery_scope,
            request.departure_at,
            inputs.profile,
            inputs.states,
            inputs.actions,
            inputs.readiness,
            config=load_freight_config(),
            rules=load_cargo_delivery_rules(),
            resource_limit=inputs.resource_limit,
        )
        project_delivery(out, delivery_analysis)
    elif isinstance(inputs, PipelineInputs):
        assert isinstance(request, IntercityCargoRequest)
        pipeline_scope = inputs.scope
        if request.origin_node_ids is not None:
            pipeline_scope = replace(
                pipeline_scope,
                origin_node_ids=tuple(request.origin_node_ids),
                destination_node_ids=tuple(request.destination_node_ids or ()),
            )
        pipeline_analysis = analyze_pipeline_connections(
            pipeline_scope, inputs.observations, config=load_freight_config()
        )
        project_pipeline(out, pipeline_analysis)
    else:
        raise unavailable("RUNTIME_TRANSPORT_INPUTS_REQUIRED")
    from a2_api.adapters.product_scenario_runtime import finish_analysis

    return finish_analysis(
        claim,
        out.finish(),
        inputs,
        profile_id=profile.profile_id,
        documents=runtime.scenario_documents.get(profile.profile_id, ()),
        configured=bool(profile.scenarios),
    )
