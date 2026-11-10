"""Explicit synthetic-demo catalog construction over existing public ML1 inputs.

This operator-side helper does not fetch data, change a runtime, or publish a
proposal. The normal worker still verifies the immutable before result, dated
operation and feasibility before exposing any saved proposal to Alice or the UI.
"""

from dataclasses import replace

from a2_contracts.common import canonical_hash
from a2_engine import (
    PhysicalService,
    ScenarioInputs,
    ScenarioOperation,
    ScenarioTimetable,
    TransportModeBinding,
    UrbanScenarioInput,
    create_scenario_baseline,
    load_urban_config,
    load_urban_journey_rules,
    scenario_hash,
)

from a2_api.adapters.product_inputs import UrbanInputs
from a2_api.adapters.product_scenario_inputs import (
    ScenarioDocument,
    ScenarioPlan,
    source_inputs_hash,
    validate_source,
)
from a2_api.core.jobs import V2JobContext


def build_demo_scenarios(
    context: V2JobContext,
    inputs: UrbanInputs,
    *,
    profile_id: str,
    incoming_run_id: str,
    outgoing_run_id: str,
    shift_seconds: tuple[int, ...],
    transport_modes: tuple[TransportModeBinding, ...],
    physical_services: tuple[PhysicalService, ...],
    evidence_refs: tuple[str, ...],
) -> ScenarioDocument:
    """Bind selected synthetic timetable shifts to one exact normalized request.

    Call only for a separately labelled DEMO snapshot/profile. Both runs, actual
    stop events and their accepted transfer rule must already exist in inputs.
    No arbitrary route, missing resource or positive impact is inferred here.
    """
    request = context.normalized_request
    if (
        request.analysis_kind != "urban_passenger"
        or request.passenger_mode != "public_transport"
        or inputs.timetable is None
        or not 1 <= len(shift_seconds) <= 10
        or len(set(shift_seconds)) != len(shift_seconds)
        or not evidence_refs
    ):
        raise ValueError("DEMO_SCENARIO_EXPLICIT_TRANSIT_INPUTS_REQUIRED")
    urban_context = replace(inputs.context, requested_departure_at=request.departure_at)
    candidates = []
    for candidate in inputs.candidates:
        if (
            candidate.passenger_mode != request.passenger_mode
            or candidate.category not in request.categories
        ):
            continue
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
    job = UrbanScenarioInput(
        "DEMO-urban",
        "DEMO-network",
        urban_context,
        inputs.points,
        inputs.destinations,
        tuple(
            replace(row, scope=replace(row.scope, context=urban_context)) for row in inputs.coverage
        ),
        inputs.area,
        tuple((str(key), str(value)) for key, value in request.priorities.items()),
        (request.passenger_mode,),
        replace(inputs.population_coverage, context=urban_context),
        load_urban_config(),
        load_urban_journey_rules(),
        thresholds=inputs.thresholds,
        fixed_candidates=tuple(candidates),
        resource_limit=inputs.resource_limit,
    )
    source = ScenarioInputs(
        timetables=(
            ScenarioTimetable("DEMO-network", inputs.timetable, transport_modes, physical_services),
        ),
        urban=(job,),
    )
    runs = {row.run_id: row for row in inputs.timetable.runs}
    if (
        incoming_run_id not in runs
        or outgoing_run_id not in runs
        or incoming_run_id == outgoing_run_id
    ):
        raise ValueError("DEMO_SCENARIO_DATED_RUN_PAIR_REQUIRED")
    incoming, outgoing = runs[incoming_run_id], runs[outgoing_run_id]
    frozen = create_scenario_baseline(source)
    document = ScenarioDocument(
        schema_version="waydex-product-scenarios/1",
        profile_id=profile_id,
        snapshot_id=context.snapshot_id,
        snapshot_hash=context.snapshot_hash,
        request_hash=canonical_hash(request.model_dump(mode="json")),
        normalized_request=request,
        source_inputs_hash=source_inputs_hash(inputs),
        inputs=source,
        plans=tuple(
            ScenarioPlan(
                template_id="coordinate_transfer_v1",
                operation=ScenarioOperation(
                    frozen.baseline_id,
                    "DEMO-shift-" + str(shift),
                    "coordinate_transfer",
                    "timetable",
                    "DEMO-network",
                    outgoing.run_id,
                    outgoing.service_date,
                    str(outgoing.events[0].sequence),
                    scenario_hash(outgoing),
                    shift,
                    incoming.run_id,
                    str(incoming.events[-1].sequence),
                    evidence_refs,
                ),
                assumptions=("DEMO_SYNTHETIC_TRANSPORT_MODEL", "NOT_A_REAL_CITY_RANKING"),
            )
            for shift in shift_seconds
        ),
    )
    validate_source(document, context, inputs, profile_id=profile_id)
    return document
