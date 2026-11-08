"""Tool adapters consume real canonical saved DTOs with synthetic contents."""

import json
from pathlib import Path

import pytest
from a2_api.analysis_service import AnalysisService
from a2_api.services import DomainError
from a2_api.tool_services import SavedTools
from a2_assistant import AccessScope
from a2_assistant.tools import ToolRejected
from a2_contracts.v2.results import AnalysisResult
from v2_handlers import registry


class ScopedSavedService(AnalysisService):
    """Storage test double; production access checks are separately tested on PostgreSQL."""

    def __init__(self):
        super().__init__(registry())
        path = (
            Path(__file__).resolve().parents[3]
            / "packages/contracts/fixtures/v2/urban_passenger-result.json"
        )
        payload = json.loads(path.read_text())["payload"]
        payload = json.loads(json.dumps(payload).replace("demo-source", "source-1"))
        payload["request"]["city_id"] = "city-1"
        payload["snapshot_id"] = payload["request"]["snapshot_id"] = payload["versions"][
            "snapshot_id"
        ] = "synthetic-snapshot"
        self.saved = AnalysisResult.model_validate(payload)
        self.reads = 0

    async def analysis(self, analysis_id, scope):
        self.reads += 1
        if scope != "owner" or analysis_id != self.saved.analysis_id:
            raise DomainError(404, "unknown_id", "Analysis not found")
        return self.saved

    async def accepted_snapshot(self, result, scope):
        await self.analysis(result.analysis_id, scope)
        return self.registry.snapshots.get(result.snapshot_id)

    async def proposals(self, analysis_id, scope):
        await self.analysis(analysis_id, scope)
        return []


def scope(service, principal="owner"):
    return AccessScope(
        principal_id=principal,
        analysis_ids=frozenset({service.saved.analysis_id}),
        territory_ids=frozenset({"city-1"}),
        source_ids=frozenset({"source-1"}),
        snapshot_ids=frozenset({"synthetic-snapshot"}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,args",
    [
        ("get_analysis", {"analysis_id": "demo-urban_passenger"}),
        ("get_metric_details", {"analysis_id": "demo-urban_passenger", "metric_id": "demo-time"}),
        ("get_scenario_templates", {"analysis_id": "demo-urban_passenger"}),
        ("search_evidence", {"query": "source-1", "territory_ids": ["city-1"]}),
        ("resolve_places", {"query": "Москва"}),
        ("get_source_status", {"source_id": "source-1", "snapshot_id": "synthetic-snapshot"}),
        ("get_calendar", {"date": "2026-11-09", "territory_id": "city-1"}),
    ],
)
async def test_tools_recheck_saved_ownership_and_return_structured_values(name, args):
    service = ScopedSavedService()
    tools = SavedTools(service)
    result = await tools.call(name, args, scope(service))
    assert result is not None
    assert service.reads >= 1
    serialized = json.dumps(result)
    assert "accepted_by" not in serialized
    assert "source_versions" not in serialized
    with pytest.raises(DomainError) as denied:
        await tools.call(name, args, scope(service, "stranger"))
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_tools_cannot_enlarge_scope_or_run_scenario():
    service = ScopedSavedService()
    tools = SavedTools(service)
    with pytest.raises(ToolRejected):
        await tools.call(
            "get_calendar", {"date": "2026-11-09", "territory_id": "other"}, scope(service)
        )
    with pytest.raises(ToolRejected):
        await tools.call("run_scenario", {}, scope(service))


@pytest.mark.asyncio
async def test_calendar_never_leaks_services_wholly_outside_authorized_territory():
    from a2_contracts.v2.dataset import AcceptedData

    service = ScopedSavedService()
    snapshot = service.registry.snapshots["synthetic-snapshot"].model_dump(mode="json")
    snapshot["geographic_scope"].append("other")
    snapshot["territories"].append(snapshot["territories"][0] | {"territory_id": "other"})
    for node in list(snapshot["access_nodes"]):
        snapshot["access_nodes"].append(
            node | {"node_id": "other-" + node["node_id"], "territory_id": "other"}
        )
    other_service = snapshot["scheduled_services"][0].copy()
    other_service.update(
        service_id="private-other-service",
        trip_id="other-trip",
        stop_times=[t | {"node_id": "other-" + t["node_id"]} for t in other_service["stop_times"]],
    )
    snapshot["scheduled_services"].append(other_service)
    service.registry.snapshots["synthetic-snapshot"] = AcceptedData.model_validate(snapshot)
    result = await SavedTools(service).call(
        "get_calendar", {"date": "2026-11-09", "territory_id": "city-1"}, scope(service)
    )
    assert "private-other-service" not in json.dumps(result)
    assert "service-1" in json.dumps(result)


@pytest.mark.asyncio
async def test_calendar_outside_accepted_period_is_explicit_unknown():
    service = ScopedSavedService()
    result = await SavedTools(service).call(
        "get_calendar", {"date": "2100-01-01", "territory_id": "city-1"}, scope(service)
    )
    assert result["status"] == "unknown"
    assert result["reason_code"] == "DATE_OUTSIDE_ACCEPTED_COVERAGE"
    assert result["snapshots"] == []


@pytest.mark.asyncio
async def test_saved_departures_match_equal_instants_with_different_offsets():
    from a2_contracts.v2.requests import parse_analysis_request

    service = ScopedSavedService()
    payload = service.saved.model_dump(mode="json")
    payload["analysis_kind"] = "intercity_passenger"
    payload["request"] = parse_analysis_request(
        dict(
            schema_version="2",
            analysis_kind="intercity_passenger",
            territory_level="city",
            origin_id="demo-a",
            destination_id="demo-b",
            departure_at="2026-11-09T08:15:00+03:00",
            traffic_mode="static",
            methodology=payload["request"]["methodology"],
            snapshot_id=payload["snapshot_id"],
        )
    ).model_dump(mode="json")
    payload["journeys"] = [
        dict(
            journey_id="synthetic-journey",
            scope="intercity",
            origin_node_id="node-a",
            destination_node_id="node-b",
            requested_departure_at="2026-11-09T08:15:00+03:00",
            departure_at="2026-11-09T08:15:00+03:00",
            arrival_at="2026-11-09T08:45:00+03:00",
            total_seconds=1800,
            initial_wait_seconds=0,
            path_seconds=1800,
            total_wait_seconds=0,
            time_kind="movement",
            status="available",
            reason_code=None,
            snapshot_id=payload["snapshot_id"],
            completeness="full",
            traffic_mode="static",
            reference_id=None,
            geometry=None,
            geometry_kind="none",
            evidence_refs=["demo-evidence"],
            destination_object_id=None,
            legs=[
                dict(
                    mode="rail",
                    kind="ride",
                    from_node_id="node-a",
                    to_node_id="node-b",
                    start_at="2026-11-09T08:15:00+03:00",
                    end_at="2026-11-09T08:45:00+03:00",
                    duration_seconds=1800,
                    service_id="synthetic-service",
                    evidence_refs=["demo-evidence"],
                )
            ],
        )
    ]
    service.saved = AnalysisResult.model_validate(payload)
    access = scope(service).model_copy(update={"territory_ids": frozenset({"demo-a", "demo-b"})})
    result = await SavedTools(service).call(
        "get_intercity_departures",
        dict(origin_id="demo-a", destination_id="demo-b", departure_at="2026-11-09T07:15:00+02:00"),
        access,
    )
    assert len(result["departures"]) == 1
    assert result["completeness"] == "partial"


@pytest.mark.asyncio
async def test_evidence_filters_are_validated_before_any_server_access():
    service = ScopedSavedService()
    with pytest.raises(ToolRejected):
        await SavedTools(service).call(
            "search_evidence",
            dict(query="пересадка", territory_ids=["city-1"], source_ids=["not-authorized"]),
            scope(service),
        )
    assert service.reads == 0


@pytest.mark.asyncio
async def test_missing_retrieval_index_is_explicit_unknown_not_id_only_fallback():
    service = ScopedSavedService()
    result = await SavedTools(service).call(
        "search_evidence", dict(query="source-1", territory_ids=["city-1"]), scope(service)
    )
    assert result["status"] == "unknown" and result["reason_code"] == "EVIDENCE_INDEX_UNAVAILABLE"
    assert result["hits"] == []


@pytest.mark.asyncio
async def test_place_tool_describes_incomplete_catalog_and_absent_matches():
    service = ScopedSavedService()
    known = await SavedTools(service).call("resolve_places", {"query": "Москва"}, scope(service))
    assert known["status"] == "available" and known["completeness"] == "partial"
    assert [p["territory_id"] for p in known["places"]] == ["city-1"]
    missing = await SavedTools(service).call(
        "resolve_places", {"query": "unknown-place"}, scope(service)
    )
    assert missing["status"] == "unknown" and missing["places"] == []
    assert missing["reason_code"] == "PLACE_NOT_FOUND_IN_ACCEPTED_CATALOG"


@pytest.mark.asyncio
async def test_departure_tool_separates_provider_observation_from_saved_journeys():
    from a2_api.providers.departures import DepartureObservation, DepartureQuery

    class Gateway:
        def __init__(self):
            self.calls = 0

        async def get_departures(
            self, origin_id, destination_id, departure_at, *, snapshots, source_ids
        ):
            self.calls += 1
            return DepartureObservation(
                status="unknown",
                query=DepartureQuery(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    departure_at=departure_at,
                    transport_types=("train",),
                ),
                reason_codes=["PLACE_MAPPING_MISSING"],
            )

    service = ScopedSavedService()
    gateway = Gateway()
    # Territory ownership is still enforced before any gateway call.
    with pytest.raises(ToolRejected):
        await SavedTools(service, departures=gateway).call(
            "get_intercity_departures",
            dict(
                origin_id="city-1",
                destination_id="foreign",
                departure_at="2026-11-09T08:15:00+03:00",
            ),
            scope(service),
        )
    assert gateway.calls == 0


@pytest.mark.asyncio
async def test_departure_tool_integrates_real_gateway_through_mock_http_without_accepting_response():
    import httpx
    from a2_api.providers.departures import RaspDepartureGateway
    from a2_api.providers.rasp import RaspProvider
    from test_departure_gateway import START, bindings, snapshot
    from test_rasp_provider import page

    service = ScopedSavedService()
    raw = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "packages/contracts/fixtures/v2/intercity_passenger-result.json"
        ).read_text()
    )["payload"]
    raw = json.loads(json.dumps(raw).replace("demo-source", "source-1"))
    raw["request"].update(
        origin_id="city-1",
        destination_id="city-2",
        departure_at=START.isoformat(),
        snapshot_id="synthetic-snapshot",
    )
    raw["snapshot_id"] = raw["versions"]["snapshot_id"] = "synthetic-snapshot"
    service.saved = AnalysisResult.model_validate(raw)
    service.registry.snapshots["synthetic-snapshot"] = snapshot()
    before = service.saved.model_dump_json()
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=page([], request=request))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        gateway = RaspDepartureGateway(
            RaspProvider(client, api_key="synthetic", requests_per_second=1000),
            bindings(snapshot()),
            transport_types=("train",),
        )
        access = scope(service).model_copy(
            update={"territory_ids": frozenset({"city-1", "city-2"})}
        )
        response = await SavedTools(service, departures=gateway).call(
            "get_intercity_departures",
            dict(origin_id="city-1", destination_id="city-2", departure_at=START.isoformat()),
            access,
        )
    assert requests
    assert response["departures"] == []
    observation = response["provider_observation"]
    assert observation["accepted_snapshot"] is False
    assert observation["coverage"] == observation["completeness"] == "partial"
    assert "EMPTY_DIRECT_RESPONSE_NOT_NO_ROUTE" in observation["reason_codes"]
    assert service.saved.model_dump_json() == before


@pytest.mark.asyncio
async def test_calendar_truncation_only_counts_services_in_authorized_territory():
    from a2_contracts.v2.dataset import AcceptedData

    service = ScopedSavedService()
    raw = service.registry.snapshots["synthetic-snapshot"].model_dump(mode="json")
    original = raw["scheduled_services"][0]
    raw["scheduled_services"] = [
        original | {"service_id": f"synthetic-service-{i}", "trip_id": f"synthetic-trip-{i}"}
        for i in range(25)
    ]
    service.registry.snapshots["synthetic-snapshot"] = AcceptedData.model_validate(raw)
    result = await SavedTools(service).call(
        "get_calendar", {"date": "2026-11-09", "territory_id": "city-1"}, scope(service)
    )
    assert result["truncated"] is True
    assert len(result["snapshots"][0]["service_ids"]) == 20


@pytest.mark.asyncio
async def test_valid_large_saved_analysis_is_compact_instead_of_breaking_assistant_budget():
    service = ScopedSavedService()
    metric = service.saved.metrics[0]
    service.saved.metrics = [
        metric.model_copy(
            update={"metric_id": f"synthetic-{i}", "name": "Синтетическое время " * 25}
        )
        for i in range(20)
    ]
    response = await SavedTools(service).call(
        "get_analysis", {"analysis_id": service.saved.analysis_id}, scope(service)
    )
    assert len(json.dumps({"untrusted_tool_data": response}, ensure_ascii=False).encode()) <= 12000
    assert response["metrics"]
    assert response["truncated"] is True
    assert len(service.saved.metrics) == 20
