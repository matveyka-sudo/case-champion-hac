"""Eight scoped read-only tools. Domain services retain authoritative owner checks."""

import json
from datetime import date as Date
from typing import Annotated, Literal, Protocol, cast

from a2_contracts.v2.base import ContractModel, Identifier, Timestamp
from a2_contracts.v2.retrieval import EvidenceSearchRequest
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

ToolName = Literal[
    "get_analysis",
    "get_metric_details",
    "search_evidence",
    "get_source_status",
    "get_scenario_templates",
    "resolve_places",
    "get_intercity_departures",
    "get_calendar",
]
Query = Annotated[str, Field(min_length=1, max_length=500)]


class ToolRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Read-only tool or access scope rejected")


class AccessScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    principal_id: Identifier
    analysis_ids: frozenset[Identifier]
    territory_ids: frozenset[Identifier]
    source_ids: frozenset[Identifier]
    snapshot_ids: frozenset[Identifier]


class ToolBackend(Protocol):
    """Recheck ownership and return compact, privacy-filtered server DTOs.

    No raw catalogs, credentials or personal data. Core enforces payload length
    and scopes; it does not infer whether arbitrary source prose contains a name.
    """

    async def call(
        self, name: ToolName, args: dict[str, JsonValue], access_scope: AccessScope
    ) -> JsonValue: ...


class AnalysisArgs(ContractModel):
    analysis_id: Identifier


class MetricArgs(AnalysisArgs):
    metric_id: Identifier


class EvidenceArgs(EvidenceSearchRequest):
    pass


class SourceArgs(ContractModel):
    source_id: Identifier
    snapshot_id: Identifier


class PlaceArgs(ContractModel):
    query: Query


class DeparturesArgs(ContractModel):
    origin_id: Identifier
    destination_id: Identifier
    departure_at: Timestamp


class CalendarArgs(ContractModel):
    date: Date
    territory_id: Identifier


_MODELS: dict[ToolName, type[ContractModel]] = {
    "get_analysis": AnalysisArgs,
    "get_metric_details": MetricArgs,
    "search_evidence": EvidenceArgs,
    "get_source_status": SourceArgs,
    "get_scenario_templates": AnalysisArgs,
    "resolve_places": PlaceArgs,
    "get_intercity_departures": DeparturesArgs,
    "get_calendar": CalendarArgs,
}


def tool_definitions() -> list[dict[str, JsonValue]]:
    return [
        cast(
            dict[str, JsonValue],
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"Read scoped server data: {name}. Never mutates state.",
                    "parameters": model.model_json_schema(),
                },
            },
        )
        for name, model in _MODELS.items()
    ]


def _unique_pairs(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ToolRejected()
        result[key] = value
    return result


def validate_tool(
    name: str, arguments: str, scope: AccessScope
) -> tuple[ToolName, dict[str, JsonValue]]:
    if name not in _MODELS or len(arguments.encode("utf-8")) > 8000:
        raise ToolRejected()
    tool_name = name
    try:
        raw = json.loads(arguments, object_pairs_hook=_unique_pairs)
        model = _MODELS[tool_name].model_validate(raw)
    except (ValueError, ValidationError, RecursionError):
        raise ToolRejected() from None
    data = model.model_dump(mode="json")
    if isinstance(model, AnalysisArgs) and model.analysis_id not in scope.analysis_ids:
        raise ToolRejected()
    if isinstance(model, EvidenceArgs) and not set(model.territory_ids) <= scope.territory_ids:
        raise ToolRejected()
    if (
        isinstance(model, EvidenceArgs)
        and model.source_ids is not None
        and not set(model.source_ids) <= scope.source_ids
    ):
        raise ToolRejected()
    if isinstance(model, SourceArgs) and (
        model.source_id not in scope.source_ids or model.snapshot_id not in scope.snapshot_ids
    ):
        raise ToolRejected()
    if isinstance(model, CalendarArgs) and model.territory_id not in scope.territory_ids:
        raise ToolRejected()
    if isinstance(model, DeparturesArgs) and (
        model.origin_id not in scope.territory_ids
        or model.destination_id not in scope.territory_ids
        or model.origin_id == model.destination_id
    ):
        raise ToolRejected()
    return tool_name, data
