"""Private, byte-hash-pinned scenario plans; never accepted from an HTTP client.

The runtime loader must use its bounded ``read_document`` and the same manifest
identity as transport inputs. Every document is for one exact normalized request.
Explicit dated execution, event, physical identity and resource bindings are kept
here because the public proposal deliberately exposes only fixed service changes.
"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import ContractModel, Identifier
from a2_contracts.v2.requests import AnalysisRequest
from a2_contracts.v2.results import EvidenceRef
from a2_engine import (
    CargoScenarioInput,
    IntercityScenarioInput,
    ScenarioFeasibilityInputs,
    ScenarioInputs,
    ScenarioOperation,
    ScenarioTimetable,
    UrbanScenarioInput,
    build_reference,
    load_cargo_delivery_rules,
    load_freight_config,
    load_intercity_passenger_rules,
    load_passenger_config,
    load_urban_config,
    load_urban_journey_rules,
    scenario_hash,
)
from pydantic import Field, field_serializer, model_validator

from a2_api.adapters.cargo_scenarios import CargoScenarioDocument
from a2_api.adapters.product_inputs import (
    DeliveryInputs,
    EngineInputs,
    IntercityInputs,
    UrbanInputs,
)
from a2_api.core.jobs import V2JobContext


def _json_value(value: object) -> object:
    """Serialize immutable domain mappings without depending on engine internals."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_value(item) for item in value), key=str)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, Fraction)):
        return str(value)
    return value


class ScenarioPlan(ContractModel):
    template_id: Identifier
    operation: ScenarioOperation
    feasibility: ScenarioFeasibilityInputs | None = None
    resource_constraints: tuple[Identifier, ...] = ()
    assumptions: tuple[Identifier, ...] = ()


class ScenarioDocument(ContractModel):
    schema_version: Literal["waydex-product-scenarios/1"]
    profile_id: Identifier
    snapshot_id: Identifier
    snapshot_hash: Identifier
    request_hash: Identifier
    normalized_request: AnalysisRequest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_inputs_hash: Identifier
    inputs: ScenarioInputs
    plans: tuple[ScenarioPlan, ...] = Field(min_length=1, max_length=10)
    evidence_refs: tuple[EvidenceRef, ...] = ()

    @field_serializer("inputs")
    def serialize_inputs(self, value: ScenarioInputs) -> object:
        return _json_value(value)

    @model_validator(mode="after")
    def distinct_plans(self) -> "ScenarioDocument":
        if self.normalized_request is not None and (
            self.normalized_request.snapshot_id != self.snapshot_id
            or self.normalized_request.methodology is None
            or canonical_hash(self.normalized_request.model_dump(mode="json")) != self.request_hash
        ):
            raise ValueError("SCENARIO_NORMALIZED_REQUEST_BINDING_MISMATCH")
        identities = [plan.operation.operation_id for plan in self.plans]
        if len(set(identities)) != len(identities):
            raise ValueError("DUPLICATE_SCENARIO_PLAN")
        if len({ref.evidence_id for ref in self.evidence_refs}) != len(self.evidence_refs):
            raise ValueError("DUPLICATE_SCENARIO_EVIDENCE")
        return self


ScenarioCatalogDocument = ScenarioDocument | CargoScenarioDocument


def parse_scenario_document(value: object) -> ScenarioCatalogDocument:
    if (
        isinstance(value, dict)
        and value.get("schema_version") == "waydex-product-cargo-scenarios/1"
    ):
        return CargoScenarioDocument.model_validate(value)
    return ScenarioDocument.model_validate(value)


def source_inputs_hash(inputs: EngineInputs) -> str:
    return scenario_hash(inputs.model_dump(mode="python"))


def document_matches_request(document: ScenarioCatalogDocument, request: AnalysisRequest) -> bool:
    """Match typed request scope while retaining the document's original hash.

    Offset spellings of the same aware instant have equal request semantics.
    The signed document still binds its own exact serialized request; legacy
    internal documents without a request retain their exact-hash comparison.
    """
    bound_request = document.normalized_request
    if bound_request is None:
        return document.request_hash == canonical_hash(request.model_dump(mode="json"))
    return (
        document.request_hash == canonical_hash(bound_request.model_dump(mode="json"))
        and bound_request == request
    )


def validate_source(
    document: ScenarioDocument, context: V2JobContext, inputs: EngineInputs, *, profile_id: str
) -> None:
    """Rebuild all calculation operands from the selected accepted transport facts.

    Only physical identities missing in UrbanInputs, and explicit cargo execution
    rosters/site mappings, may supplement those facts. Configs, observations,
    completeness, populations, thresholds and request scope cannot be overridden.
    Unregistered standalone truck/pipeline changes fail closed.
    """
    if (
        document.profile_id != profile_id
        or document.snapshot_id != context.snapshot_id
        or document.snapshot_hash != context.snapshot_hash
        or not document_matches_request(document, context.normalized_request)
        or document.source_inputs_hash != source_inputs_hash(inputs)
    ):
        raise ValueError("SCENARIO_SOURCE_BINDING_MISMATCH")
    request = context.normalized_request
    facts = document.inputs
    if isinstance(inputs, UrbanInputs):
        if request.analysis_kind != "urban_passenger" or inputs.timetable is None:
            raise ValueError("SCENARIO_DATED_TIMETABLE_REQUIRED")
        if len(facts.urban) != 1 or len(facts.timetables) != 1:
            raise ValueError("SCENARIO_SINGLE_SOURCE_SCOPE_REQUIRED")
        job, dataset = facts.urban[0], facts.timetables[0]
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
        expected = ScenarioInputs(
            timetables=(
                ScenarioTimetable(
                    dataset.network_id,
                    inputs.timetable,
                    dataset.transport_modes,
                    dataset.physical_services,
                ),
            ),
            urban=(
                UrbanScenarioInput(
                    job.job_id,
                    dataset.network_id,
                    urban_context,
                    inputs.points,
                    inputs.destinations,
                    tuple(
                        replace(row, scope=replace(row.scope, context=urban_context))
                        for row in inputs.coverage
                    ),
                    inputs.area,
                    tuple((str(k), str(v)) for k, v in request.priorities.items()),
                    (request.passenger_mode,),
                    replace(inputs.population_coverage, context=urban_context),
                    load_urban_config(),
                    load_urban_journey_rules(),
                    thresholds=inputs.thresholds,
                    fixed_candidates=tuple(candidates),
                    resource_limit=inputs.resource_limit,
                ),
            ),
        )
    elif isinstance(inputs, IntercityInputs):
        if request.analysis_kind != "intercity_passenger" or request.passenger_modes == [
            "private_car"
        ]:
            raise ValueError("SCENARIO_MUTABLE_PUBLIC_TRANSPORT_REQUIRED")
        if len(facts.intercity) != 1 or len(facts.timetables) != 1:
            raise ValueError("SCENARIO_SINGLE_SOURCE_SCOPE_REQUIRED")
        intercity_job, dataset = facts.intercity[0], facts.timetables[0]
        reference_scope = inputs.reference_scope
        if request.origin_node_ids is not None:
            reference_scope = replace(
                reference_scope,
                origin_node_ids=tuple(request.origin_node_ids),
                destination_node_ids=tuple(request.destination_node_ids or ()),
            )
        scope = replace(inputs.scope, comparison_scope=reference_scope)
        if request.passenger_modes is not None:
            scope = replace(
                scope,
                applicable_modes=tuple(
                    mode for mode in scope.applicable_modes if mode in request.passenger_modes
                ),
            )
        car = (
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
        expected = ScenarioInputs(
            timetables=(
                ScenarioTimetable(
                    dataset.network_id,
                    inputs.timetable,
                    inputs.transport_modes,
                    inputs.physical_services,
                ),
            ),
            intercity=(
                IntercityScenarioInput(
                    intercity_job.job_id,
                    dataset.network_id,
                    scope,
                    request.departure_at,
                    build_reference(inputs.reference_graph, reference_scope),
                    load_passenger_config(),
                    load_intercity_passenger_rules(),
                    inputs.frequency_start_at,
                    inputs.frequency_end_at,
                    inputs.readiness,
                    car,
                    inputs.resource_limit,
                ),
            ),
        )
    elif isinstance(inputs, DeliveryInputs):
        if request.analysis_kind != "intercity_cargo" or len(facts.cargo) != 1:
            raise ValueError("SCENARIO_SINGLE_SOURCE_SCOPE_REQUIRED")
        cargo_job = facts.cargo[0]
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
                    mode for mode in delivery_scope.allowed_modes if mode in request.cargo_modes
                ),
            )
        expected = ScenarioInputs(
            cargo=(
                CargoScenarioInput(
                    cargo_job.job_id,
                    delivery_scope,
                    request.departure_at,
                    inputs.profile,
                    inputs.states,
                    inputs.actions,
                    inputs.readiness,
                    cargo_job.executions,
                    load_freight_config(),
                    load_cargo_delivery_rules(),
                    cargo_job.physical_sites,
                    inputs.resource_limit,
                ),
            )
        )
        if not cargo_job.executions:
            raise ValueError("SCENARIO_CARGO_EXECUTION_BINDINGS_REQUIRED")
    else:
        raise ValueError("SCENARIO_MUTABLE_TRANSPORT_MODEL_UNAVAILABLE")
    if expected != facts:
        raise ValueError("SCENARIO_SOURCE_OPERANDS_CHANGED")
    for plan in document.plans:
        operation = plan.operation
        if operation.timing_model != "whole_execution":
            raise ValueError("SCENARIO_WHOLE_EXECUTION_REQUIRED")
        if operation.target_kind == "timetable":
            target_dataset = next(
                (row for row in facts.timetables if row.network_id == operation.target_id), None
            )
            covered = (
                {run_id for row in target_dataset.physical_services for run_id in row.run_ids}
                if target_dataset
                else set()
            )
            if not {operation.execution_id, operation.incoming_execution_id} <= covered:
                raise ValueError("SCENARIO_PHYSICAL_EXECUTION_BINDINGS_REQUIRED")
