"""Privacy-minimal, read-only adapters over authorized saved results and catalogs."""

import json
from datetime import date, datetime
from typing import cast

from a2_assistant.evidence import leaf_references
from a2_assistant.focus import focused_metrics, focused_scores
from a2_assistant.tools import AccessScope, ToolName, ToolRejected, validate_tool
from a2_contracts.common import canonical_hash
from a2_contracts.v2.assistant import ExplanationRequest, ExplanationSelection
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.evidence import EvidenceResolver, require_result_evidence
from a2_contracts.v2.results import Metric, Score
from a2_contracts.v2.retrieval import EvidenceSearchRequest, EvidenceSearchResult
from pydantic import JsonValue, TypeAdapter

from a2_api.core.errors import DomainError
from a2_api.db.repositories.evidence import EvidenceRepository
from a2_api.providers.departures import RaspDepartureGateway
from a2_api.services.analyses import AnalysisService


class SavedTools:
    def __init__(
        self,
        service: AnalysisService,
        *,
        evidence: EvidenceRepository | None = None,
        departures: RaspDepartureGateway | None = None,
    ) -> None:
        self.service = service
        self.evidence = evidence or (
            EvidenceRepository(service.repository.pool) if service.repository is not None else None
        )
        self.departures = departures

    async def call(
        self, name: ToolName, args: dict[str, JsonValue], access_scope: AccessScope
    ) -> JsonValue:
        value = TypeAdapter(JsonValue).validate_python(await self._call(name, args, access_scope))

        def size() -> int:
            return len(json.dumps({"untrusted_tool_data": value}, ensure_ascii=False).encode())

        if isinstance(value, dict) and size() > 11000:
            # Preserve a useful summary instead of failing the entire explanation on normal large runs.
            value["truncated"] = True
            for key in ("metrics", "scores", "departures"):
                items = value.get(key)
                if isinstance(items, list):
                    while len(items) > 1 and size() > 11000:
                        items.pop()
            provider = value.get("provider_observation")
            if isinstance(provider, dict):
                departures = provider.get("departures")
                if isinstance(departures, list):
                    while departures and size() > 11000:
                        departures.pop()
                        provider["truncated"] = True
        if size() > 11000:
            return {
                "status": "unknown",
                "reason_code": "TOOL_PAYLOAD_LIMIT",
                "completeness": "partial",
            }
        return value

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
        try:
            resolvers = {result.analysis_id: require_result_evidence(result) for result in saved}
        except ValueError:
            raise ToolRejected() from None
        allowed_territories = frozenset(
            t for result in saved for t in self._territories(result.request)
        )
        if not access_scope.territory_ids <= allowed_territories:
            raise ToolRejected()
        # A service without document corpora (older wiring, tests) behaves as before.
        available = getattr(self.service, "document_corpora", None) or {}
        corpora = {
            result.snapshot_id: corpus
            for result in saved
            if (corpus := available.get(result.snapshot_id)) is not None
        }
        allowed_snapshots = {result.snapshot_id for result in saved} | {
            corpus.snapshot_id for corpus in corpora.values()
        }
        if not access_scope.snapshot_ids <= allowed_snapshots:
            raise ToolRejected()
        snapshots: list[AcceptedData] = []
        for result in saved:
            snapshot = await self.service.accepted_snapshot(result, access_scope.principal_id)
            if snapshot is not None and snapshot.snapshot_id in access_scope.snapshot_ids:
                if all(item.snapshot_id != snapshot.snapshot_id for item in snapshots):
                    snapshots.append(snapshot)
                corpus = corpora.get(snapshot.snapshot_id)
                if corpus is not None and corpus.snapshot_id in access_scope.snapshot_ids:
                    if all(item.snapshot_id != corpus.snapshot_id for item in snapshots):
                        snapshots.append(corpus.snapshot)
        permitted_sources = {ref.source_id for result in saved for ref in result.evidence_refs}
        permitted_sources.update(
            e.source_id for snapshot in snapshots for e in snapshot.evidence if e.verified
        )
        if not access_scope.source_ids <= permitted_sources:
            raise ToolRejected()
        if name in {"get_analysis", "get_metric_details", "get_scenario_templates"}:
            selected = next(
                (item for item in saved if item.analysis_id == args["analysis_id"]), None
            )
            if selected is None:
                raise ToolRejected()
            result = selected
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
                return self._fact(metric, resolvers[result.analysis_id], limit=20)
            focus = (
                ExplanationRequest(selection=ExplanationSelection.model_validate(args["selection"]))
                if args.get("selection") is not None
                else None
            )
            selected_metrics = focused_metrics(result, focus)
            selected_scores = focused_scores(result, focus)
            if (
                focus is not None
                and focus.selection is not None
                and focus.selection.entity_kind != "territory"
            ):
                selected_scores.sort(
                    key=lambda score: (
                        score.subject.category is not None,
                        score.status != "available",
                    )
                )
                selected_metrics.sort(
                    key=lambda metric: (metric.journey_id is None, metric.status != "available")
                )
            return {
                "analysis_id": result.analysis_id,
                "status": result.status,
                "metrics": [
                    self._fact(m, resolvers[result.analysis_id]) for m in selected_metrics[:10]
                ],
                "scores": [
                    self._fact(s, resolvers[result.analysis_id]) for s in selected_scores[:10]
                ],
                "limitations": list(result.limitations[:20]),
                "truncated": len(selected_metrics) > 10
                or len(selected_scores) > 10
                or len(result.limitations) > 20,
                "result_hash": result.result_hash,
            }
        if name == "search_evidence":
            if self.evidence is None:
                return EvidenceSearchResult(
                    status="unknown", reason_code="EVIDENCE_INDEX_UNAVAILABLE", hits=[]
                ).model_dump(mode="json")
            request = EvidenceSearchRequest.model_validate(args)
            hashes = {
                snapshot.snapshot_id: canonical_hash(snapshot.model_dump(mode="json"))
                for snapshot in snapshots
            }
            return (
                await self.evidence.search(
                    request, snapshot_hashes=hashes, source_ids=access_scope.source_ids
                )
            ).model_dump(mode="json")
        if name == "resolve_places":
            places = [
                t
                for t in self.service.registry.catalog.values()
                if t.territory_id in access_scope.territory_ids
                and str(args["query"]).casefold() in t.name.casefold()
            ]
            return {
                "status": "available" if places else "unknown",
                "completeness": "partial",
                "reason_code": None if places else "PLACE_NOT_FOUND_IN_ACCEPTED_CATALOG",
                "places": [t.model_dump(mode="json") for t in places[:20]],
                "truncated": len(places) > 20,
            }
        if name == "get_source_status":
            snapshot_id, source_id = str(args["snapshot_id"]), str(args["source_id"])
            snapshot = next((item for item in snapshots if item.snapshot_id == snapshot_id), None)
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
            calendars = []
            truncated = False
            for snapshot in snapshots:
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
                services = [
                    s.service_id
                    for s in snapshot.scheduled_services
                    if requested_date in s.service_dates
                    and any(stop.node_id in nodes for stop in s.stop_times)
                ]
                truncated = truncated or len(services) > 20
                calendars.append(
                    {
                        "snapshot_id": snapshot.snapshot_id,
                        "calendar_version": snapshot.provenance.calendar_version,
                        "service_ids": services[:20],
                    }
                )
            return {
                "territory_id": territory_id,
                "date": requested_date.isoformat(),
                "status": "accepted_calendar_only" if calendars else "unknown",
                "completeness": "partial",
                "reason_code": None if calendars else "DATE_OUTSIDE_ACCEPTED_COVERAGE",
                "snapshots": calendars,
                "truncated": truncated,
            }
        # Provider observations never modify saved runs or accepted snapshots.
        observation = None
        if self.departures is not None:
            observation = await self.departures.get_departures(
                str(args["origin_id"]),
                str(args["destination_id"]),
                datetime.fromisoformat(str(args["departure_at"])),
                snapshots=snapshots,
                source_ids=access_scope.source_ids,
            )
        provider_payload = None if observation is None else observation.model_dump(mode="json")
        if provider_payload is not None and observation is not None:
            provider_payload["departures"] = [
                d.model_dump(mode="json") for d in observation.departures[:10]
            ]
            provider_payload["truncated"] = len(observation.departures) > 10
        return {
            "status": "saved_only" if observation is None else "saved_and_provider_observation",
            "completeness": "partial",
            "reason_code": "PROVIDER_NOT_CONFIGURED" if observation is None else None,
            "provider_observation": provider_payload,
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
            ][:10],
            "saved_results_only": True,
        }

    @staticmethod
    def _fact(
        fact: Metric | Score, resolver: EvidenceResolver, *, limit: int | None = None
    ) -> JsonValue:
        payload = fact.model_dump(mode="json")
        field = "source_refs" if isinstance(fact, Metric) else "evidence_refs"
        refs = fact.source_refs if isinstance(fact, Metric) else fact.evidence_refs
        leaves = leaf_references(refs, resolver)
        # Keep legacy get_analysis bytes; bound expanded groups before the existing tool byte cap.
        grouped = any(ref not in resolver.leaf_ids for ref in refs)
        if limit is not None or grouped:
            maximum = 20 if limit is None else limit
            payload[field] = leaves[:maximum]
            payload["references_truncated"] = len(leaves) > maximum
        return cast(JsonValue, payload)

    @staticmethod
    def _territories(request: object) -> frozenset[str]:
        from a2_contracts.v2.requests import parse_analysis_request

        from a2_api.core.scope import territory_ids

        return territory_ids(parse_analysis_request(request))
