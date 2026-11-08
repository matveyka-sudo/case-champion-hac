"""Privacy-minimal, read-only adapters over authorized saved results and catalogs."""

import json
from datetime import date, datetime
from typing import cast

from a2_assistant.tools import AccessScope, ToolName, ToolRejected, validate_tool
from pydantic import JsonValue, TypeAdapter

from .analysis_service import AnalysisService
from .services import DomainError


class SavedTools:
    def __init__(self, service: AnalysisService) -> None:
        self.service = service

    async def call(
        self, name: ToolName, args: dict[str, JsonValue], access_scope: AccessScope
    ) -> JsonValue:
        value = await self._call(name, args, access_scope)
        return TypeAdapter(JsonValue).validate_python(value)

    async def _call(
        self, name: ToolName, args: dict[str, JsonValue], access_scope: AccessScope
    ) -> object:
        name, args = validate_tool(name, json.dumps(args), access_scope)
        # Recheck saved ownership on every call, including catalog/provider tools.
        saved = [
            await self.service.analysis(identifier, access_scope.principal_id)
            for identifier in sorted(access_scope.analysis_ids)
        ]
        if not saved:
            raise ToolRejected()
        allowed_territories = frozenset(
            t for result in saved for t in self._territories(result.request)
        )
        if not access_scope.territory_ids <= allowed_territories:
            raise ToolRejected()
        if not access_scope.snapshot_ids <= {result.snapshot_id for result in saved}:
            raise ToolRejected()
        if not access_scope.source_ids <= {
            ref.source_id for result in saved for ref in result.evidence_refs
        }:
            raise ToolRejected()
        if name in {"get_analysis", "get_metric_details", "get_scenario_templates"}:
            result = next((item for item in saved if item.analysis_id == args["analysis_id"]), None)
            if result is None:
                raise ToolRejected()
            if name == "get_scenario_templates":
                proposals = await self.service.proposals(
                    result.analysis_id, access_scope.principal_id
                )
                return [
                    {
                        "scenario_id": p.scenario_id,
                        "template_id": p.template_id,
                        "feasibility": p.feasibility,
                        "limitations": list(p.limitations),
                    }
                    for p in proposals
                ]
            if name == "get_metric_details":
                metric = next((m for m in result.metrics if m.metric_id == args["metric_id"]), None)
                if metric is None:
                    raise DomainError(404, "unknown_id", "Metric not found")
                return cast(JsonValue, metric.model_dump(mode="json"))
            return {
                "analysis_id": result.analysis_id,
                "status": result.status,
                "metrics": [cast(JsonValue, m.model_dump(mode="json")) for m in result.metrics],
                "scores": [cast(JsonValue, s.model_dump(mode="json")) for s in result.scores],
                "limitations": list(result.limitations),
                "result_hash": result.result_hash,
            }
        if name == "search_evidence":
            # Search stable identities only; raw source prose is never forwarded to the model.
            query = str(args["query"]).casefold()
            return [
                {"evidence_id": e.evidence_id, "source_id": e.source_id}
                for result in saved
                for e in result.evidence_refs
                if query in (e.evidence_id + " " + e.source_id).casefold()
            ][:50]
        if name == "resolve_places":
            return [
                cast(JsonValue, t.model_dump(mode="json"))
                for t in self.service.registry.territories(query=str(args["query"]))
                if t.territory_id in access_scope.territory_ids
            ][:20]
        if name == "get_source_status":
            snapshot_id, source_id = str(args["snapshot_id"]), str(args["source_id"])
            snapshot = self.service.registry.snapshots.get(snapshot_id)
            if snapshot is None or source_id not in snapshot.source_versions:
                raise DomainError(
                    503, "data_unavailable", "Accepted source snapshot is unavailable"
                )
            return {
                "source_id": source_id,
                "snapshot_id": snapshot_id,
                "source_version": snapshot.source_versions[source_id],
                "acceptance_state": snapshot.qa.acceptance_state,
                "evidence_ids": [
                    e.evidence_id for e in snapshot.evidence if e.source_id == source_id
                ],
            }
        if name == "get_calendar":
            requested_date = date.fromisoformat(str(args["date"]))
            territory_id = str(args["territory_id"])
            snapshots = []
            for snapshot in self.service.registry.snapshots.values():
                if (
                    snapshot.snapshot_id not in access_scope.snapshot_ids
                    or territory_id not in snapshot.geographic_scope
                ):
                    continue
                period = snapshot.valid_period
                if (
                    period.start is None
                    or period.end is None
                    or not period.start <= requested_date <= period.end
                ):
                    continue
                nodes = {
                    node.node_id
                    for node in snapshot.access_nodes
                    if node.territory_id == territory_id
                }
                snapshots.append(
                    {
                        "snapshot_id": snapshot.snapshot_id,
                        "calendar_version": snapshot.provenance.calendar_version,
                        "service_ids": [
                            s.service_id
                            for s in snapshot.scheduled_services
                            if requested_date in s.service_dates
                            and any(stop.node_id in nodes for stop in s.stop_times)
                        ][:100],
                    }
                )
            return {
                "territory_id": territory_id,
                "date": requested_date.isoformat(),
                "status": "accepted_calendar_only" if snapshots else "unknown",
                "completeness": "partial",
                "reason_code": None if snapshots else "DATE_OUTSIDE_ACCEPTED_COVERAGE",
                "snapshots": snapshots,
            }
        # Saved direct/mixed journeys are distinct. No new route search or provider inference.
        return {
            "status": "saved_only",
            "completeness": "partial",
            "departures": [
                {
                    "journey_id": journey.journey_id,
                    "departure_at": journey.departure_at.isoformat()
                    if journey.departure_at
                    else None,
                    "arrival_at": journey.arrival_at.isoformat() if journey.arrival_at else None,
                    "status": journey.status,
                }
                for result in saved
                if getattr(result.request, "origin_id", None) == args["origin_id"]
                and getattr(result.request, "destination_id", None) == args["destination_id"]
                and result.request.departure_at == datetime.fromisoformat(str(args["departure_at"]))
                for journey in result.journeys
            ][:100],
        }

    @staticmethod
    def _territories(request: object) -> frozenset[str]:
        from a2_contracts.v2.requests import parse_analysis_request

        from .analysis_service import territory_ids

        return territory_ids(parse_analysis_request(request))
