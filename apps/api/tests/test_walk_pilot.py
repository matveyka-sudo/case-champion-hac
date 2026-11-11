"""Real accepted geography with explicitly mocked TEST-ONLY routing observations."""

import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest
from a2_api.adapters import walk_pilot as pilot
from a2_api.core.errors import DomainError
from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_contracts.api import Job
from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import VersionRefs
from a2_contracts.v2.requests import UrbanPassengerRequest
from a2_data_pipeline.contract_v2 import load_accepted_data
from a2_data_pipeline.store import load_bundle

POINT = "kaliningrad-utm34n-1km-v1-e467-n6062-p"
SCHOOL = "kaliningrad-school-klgd_education-4409878"
BUNDLE_V2 = "kaliningrad-urban-2026-11-10.2"
BUNDLE_V3 = "kaliningrad-urban-2026-11-10.3"


@pytest.fixture
def configured(tmp_path, monkeypatch, request):
    bundle_id = getattr(request, "param", BUNDLE_V2)
    snapshot = load_accepted_data(bundle_id)
    bundle = load_bundle(bundle_id)
    nodes = {n.node_id: n for n in snapshot.access_nodes}
    endpoints = {
        **{f"physical:point:{p.point_id}": p for p in snapshot.sample_points},
        **{f"physical:destination:{d.destination_id}": d for d in snapshot.destinations},
    }
    connectors = []
    for row in bundle.walk_connectors:
        if row.physical_node_id not in endpoints:
            continue
        endpoint = endpoints[row.physical_node_id]
        node = nodes[row.access_node_id]
        connectors.append(
            dict(
                snapshot_id=snapshot.snapshot_id,
                source_graph_version=snapshot.provenance.graph_version,
                physical_node_id=row.physical_node_id,
                access_node_id=row.access_node_id,
                physical_location=endpoint.location.model_dump(),
                access_location=node.location.model_dump(),
                seconds=round(row.length_m / (5 / 3.6), 1) if row.traversable else None,
                traversable=row.traversable,
                time_basis="TEST_ONLY_MODEL_5_KMH",
                evidence_refs=["TEST_ONLY-runtime-proof", *row.evidence_ids],
            )
        )
    chosen = [endpoints[f"physical:point:{POINT}"], endpoints[f"physical:destination:{SCHOOL}"]]
    raw = dict(
        status_expectation=dict(version="3.5.1", tileset_last_modified=123),
        bundle_id=snapshot.snapshot_id,
        endpoint_url="http://test-valhalla",
        point_ids=[POINT],
        destination_ids=[SCHOOL],
        build=dict(
            snapshot_id=snapshot.snapshot_id,
            source_graph_version=snapshot.provenance.graph_version,
            build_id="TEST_ONLY-build",
            graph_version="TEST_ONLY-built-graph",
            engine_version="3.5.1",
            profile_version="waydex-walk-static-5kmh-v1",
            pedestrian_options_json=json.dumps(dict(type="foot", walking_speed=5)),
            evidence_refs=["TEST_ONLY-runtime-proof"],
        ),
        connectors=connectors,
        correlations=[
            dict(
                access_node_id=row.access_node_id,
                source_location=nodes[row.access_node_id].location.model_dump(),
                correlated_location=nodes[row.access_node_id].location.model_dump(),
                correlation_id="TEST_ONLY-" + row.access_node_id,
                evidence_refs=["TEST_ONLY-runtime-proof"],
            )
            for row in chosen
        ],
        evidence_refs=[
            dict(
                evidence_id="TEST_ONLY-runtime-proof",
                source_id="TEST_ONLY-source",
                url="https://example.test/TEST_ONLY",
                retrieved_at="2026-11-10T00:00:00Z",
                observed_at=None,
                section="TEST_ONLY",
            )
        ],
    )
    path = tmp_path / "TEST_ONLY-runtime.json"
    path.write_text(json.dumps(raw))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setenv("A2_WALK_PILOT_ARTIFACT_PATH", str(path))
    monkeypatch.setenv("A2_WALK_PILOT_ARTIFACT_SHA256", digest)
    monkeypatch.setenv("A2_WALK_PILOT_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-walk-" + digest)
    return raw, path, snapshot


def request():
    return UrbanPassengerRequest.model_validate(
        dict(
            schema_version="2",
            analysis_kind="urban_passenger",
            city_id="kaliningrad",
            passenger_mode="walk",
            categories=["school"],
            priorities={"school": "medium"},
            departure_at="2026-11-10T09:00:00+02:00",
            traffic_mode="static",
        )
    )


def claim_for(snapshot):
    registry = pilot.registry()
    normalized = registry.normalize(request()).model_copy(
        update={"snapshot_id": snapshot.snapshot_id}
    )
    code = pilot.RuntimeSettings().code_version
    context = V2JobContext(
        schema_version="2",
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        versions=VersionRefs(
            snapshot_id=snapshot.snapshot_id,
            graph_version=snapshot.provenance.graph_version,
            calendar_version=snapshot.provenance.calendar_version,
            population_version=snapshot.provenance.population_version,
            methodology_version=registry.methodology.version,
            config_hash=registry.config_hash,
            code_version=code,
        ),
        analysis_id="TEST_ONLY-analysis",
        normalized_request=normalized,
        access_scope="TEST_ONLY-owner",
    )
    now = datetime.now(UTC)
    return ClaimedJob(
        job=Job(
            job_id="TEST_ONLY-job",
            status="running",
            trace_id="TEST_ONLY-trace",
            bundle_id=snapshot.snapshot_id,
            input_hash="TEST_ONLY-hash",
            code_version=code,
            created_at=now,
            started_at=now,
            finished_at=None,
            run_id=None,
            error=None,
        ),
        kind="analysis",
        request=request(),
        context=context,
        cache_key="TEST_ONLY-cache",
        claim_token="TEST_ONLY-token",
    )


def route_body(payload):
    summary = dict(time=120.0, length=0.15, has_ferry=False)
    return dict(
        id=payload["id"],
        trip=dict(
            status=0,
            units="kilometers",
            locations=payload["locations"],
            summary=summary,
            legs=[
                dict(
                    summary=summary,
                    maneuvers=[
                        dict(
                            travel_mode="pedestrian",
                            travel_type="foot",
                            type=1,
                            time=120.0,
                            length=0.15,
                        )
                    ],
                )
            ],
        ),
    )


def transport(
    *,
    shifted=False,
    timeout=False,
    ferry=False,
    no_route=False,
    malformed_edge=False,
    stale_status=False,
    malformed_body=False,
    route_change=None,
):
    def response(body):
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(body).encode()))

    def respond(req):
        payload = json.loads(req.content)
        if req.url.path == "/status":
            return response(
                dict(
                    version="3.5.1",
                    tileset_last_modified=124 if stale_status else 123,
                    has_tiles=True,
                )
            )
        if req.url.path == "/locate":
            return response(
                [
                    dict(
                        edges=(
                            [
                                dict(
                                    correlated_lon=row["lon"] + (0.001 if shifted else 0),
                                    correlated_lat=row["lat"],
                                )
                            ]
                            + ([{}] if malformed_edge else [])
                        )
                    )
                    for row in payload["locations"]
                ],
            )
        if timeout:
            raise httpx.ReadTimeout("TEST_ONLY timeout")
        body = [] if malformed_body else {"error_code": 442} if no_route else route_body(payload)
        if ferry:
            body["trip"]["summary"]["has_ferry"] = True
        if route_change is not None:
            path, value = route_change
            target = body
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
        return response(body)

    return httpx.MockTransport(respond)


def mock_client(monkeypatch, **options):
    real = httpx.Client
    monkeypatch.setattr(
        pilot.httpx, "Client", lambda **kwargs: real(transport=transport(**options), **kwargs)
    )


@pytest.mark.parametrize("configured", [BUNDLE_V2, BUNDLE_V3], indirect=True)
def test_real_accepted_data_registry_and_hash_bound_partial_result(configured, monkeypatch):
    _, path, snapshot = configured
    claim = claim_for(snapshot)
    mock_client(monkeypatch)
    result = pilot.calculate(claim)
    assert result.status == "partial"
    assert result.versions.graph_version == snapshot.provenance.graph_version
    assert result.versions.graph_version != "TEST_ONLY-built-graph"
    assert len(result.journeys) == 1
    journey = result.journeys[0]
    assert journey.total_seconds > 120
    assert len(journey.legs) == 3
    assert journey.completeness == "partial" and journey.geometry is None
    assert {ref for leg in journey.legs for ref in leg.evidence_refs} <= set(journey.evidence_refs)
    assert all(s.value is None and not s.ranking_eligible for s in result.scores)
    assert {"PILOT_SAMPLE_ONLY", "MINIMUM_TIME_UNPROVEN", "PARTIAL_SCHOOL_CATALOG"} <= set(
        result.limitations
    )
    assert len(list(path.parent.glob("*.json"))) == 4
    assert len(result.map_data.features) == 2
    point = next(row for row in snapshot.sample_points if row.point_id == POINT)
    cell = next(row for row in snapshot.population_cells if row.cell_id == point.cell_id)
    saved_point = next(row for row in result.map_data.features if row.entity_kind == "sample_point")
    assert set(cell.source_refs) <= set(saved_point.source_refs)
    assert saved_point.population == cell.population
    assert saved_point.population_year == cell.population_year
    assert saved_point.population_method == cell.population_method
    # No dangling cell link: this deliberately sampled map has no cell feature.
    assert saved_point.cell_id is None
    saved_school = next(row for row in result.map_data.features if row.entity_kind == "destination")
    accepted_school = next(
        row
        for row in load_bundle(snapshot.snapshot_id).destinations
        if row.destination_id == SCHOOL
    )
    assert accepted_school.name == "МАОУ СОШ № 12"
    assert saved_school.name == accepted_school.name
    assert saved_school.entity_id == SCHOOL
    assert saved_school.source_refs == next(
        row.source_refs for row in snapshot.destinations if row.destination_id == SCHOOL
    )
    assert result.snapshot_id == snapshot.snapshot_id
    assert len([row for row in snapshot.destinations if "school" in row.categories]) == (
        76 if snapshot.snapshot_id == BUNDLE_V2 else 77
    )
    assert result.result_hash == canonical_hash(
        result.model_dump(mode="json", exclude={"result_hash"})
    )


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"shifted": True}, "ACTUAL_ENDPOINTS_UNCONFIRMED"),
        ({"timeout": True}, "PROVIDER_TIMEOUT"),
        ({"ferry": True}, "PATH_MODE_UNCONFIRMED"),
        ({"no_route": True}, "PROVIDER_ERROR_442"),
        ({"malformed_edge": True}, "ACTUAL_ENDPOINTS_UNCONFIRMED"),
        ({"malformed_body": True}, "INVALID_RESPONSE"),
    ],
)
def test_unproven_or_failed_provider_is_unknown_never_zero(
    configured, monkeypatch, options, reason
):
    _, _, snapshot = configured
    claim = claim_for(snapshot)
    mock_client(monkeypatch, **options)
    result = pilot.calculate(claim)
    assert result.status == "partial"
    assert result.journeys == []
    assert all(s.value is None and not s.ranking_eligible for s in result.scores)
    # Raw known H remains valid; route failures do not convert into zero travel time.
    assert [m.name for m in result.metrics] == ["urban_h_seconds"]
    assert reason in result.limitations


@pytest.mark.parametrize(
    "path,value",
    [
        (("trip",), None),
        (("trip",), []),
        (("trip", "summary"), None),
        (("trip", "summary"), []),
        (("trip", "locations"), None),
        (("trip", "locations"), {}),
        (("trip", "locations", 0), None),
        (("trip", "legs"), None),
        (("trip", "legs"), {}),
        (("trip", "legs"), "TEST_ONLY-malformed"),
        (("trip", "legs", 0), None),
        (("trip", "legs", 0), []),
        (("trip", "legs", 0, "summary"), None),
        (("trip", "legs", 0, "maneuvers"), None),
        (("trip", "legs", 0, "maneuvers"), {}),
        (("trip", "legs", 0, "maneuvers"), "TEST_ONLY-malformed"),
        (("trip", "legs", 0, "maneuvers", 0), None),
        (("trip", "legs", 0, "maneuvers", 0), []),
    ],
)
def test_malformed_nested_route_is_unknown_through_calculate(configured, monkeypatch, path, value):
    _, _, snapshot = configured
    mock_client(monkeypatch, route_change=(path, value))
    result = pilot.calculate(claim_for(snapshot))
    assert result.status == "partial"
    assert result.journeys == []
    assert all(score.value is None and not score.ranking_eligible for score in result.scores)
    assert [metric.name for metric in result.metrics] == ["urban_h_seconds"]
    assert result.metrics[0].value is not None and result.metrics[0].value > 0
    assert "PROVIDER_HTTP_ERROR" in result.limitations
    assert all("UNREACHABLE" not in reason for reason in result.limitations)


def test_changed_artifact_rejected(configured):
    _, path, _ = configured
    path.write_text(path.read_text() + " ")
    with pytest.raises(DomainError, match="runtime artifact"):
        pilot.load_runtime()


def repin(raw, path, monkeypatch):
    path.write_text(json.dumps(raw))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setenv("A2_WALK_PILOT_ARTIFACT_SHA256", digest)
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-walk-" + digest)


@pytest.mark.parametrize("configured", [BUNDLE_V2, BUNDLE_V3], indirect=True)
def test_explicit_bundle_school_bound_and_single_point(configured, monkeypatch):
    raw, path, snapshot = configured
    schools = [row.destination_id for row in snapshot.destinations if "school" in row.categories]
    raw["destination_ids"] = schools
    repin(raw, path, monkeypatch)
    # Readiness still needs per-pair correlation/connector proofs; this is not execution.
    assert pilot.load_runtime().destination_ids == tuple(schools)
    raw["destination_ids"] = [*schools, "TEST_ONLY-over-limit"]
    repin(raw, path, monkeypatch)
    with pytest.raises(DomainError, match="runtime artifact"):
        pilot.load_runtime()
    raw["destination_ids"] = [SCHOOL]
    raw["point_ids"] = [POINT, POINT]
    repin(raw, path, monkeypatch)
    with pytest.raises(DomainError, match="runtime artifact"):
        pilot.load_runtime()


@pytest.mark.parametrize("bundle_id", ["kaliningrad-urban-2026-11-10.4", "TEST_ONLY-unknown", ""])
def test_unknown_bundle_rejected_before_data_lookup(configured, monkeypatch, bundle_id):
    raw, path, _ = configured
    raw["bundle_id"] = bundle_id
    repin(raw, path, monkeypatch)

    def unexpected_lookup(identity):
        pytest.fail(f"Unsupported bundle triggered data lookup: {identity}")

    monkeypatch.setattr(pilot, "load_accepted_data", unexpected_lookup)
    with pytest.raises(DomainError, match="runtime artifact"):
        pilot.load_runtime()


@pytest.mark.parametrize("configured", [BUNDLE_V2, BUNDLE_V3], indirect=True)
def test_missing_allowed_bundle_has_no_snapshot_fallback(configured, monkeypatch):
    raw, _, _ = configured
    lookups = []

    def missing(identity):
        lookups.append(identity)
        raise KeyError("TEST_ONLY unavailable accepted bundle")

    monkeypatch.setattr(pilot, "load_accepted_data", missing)
    with pytest.raises(DomainError, match="runtime artifact"):
        pilot.load_runtime()
    assert lookups == [raw["bundle_id"]]


@pytest.mark.parametrize("configured", [BUNDLE_V2, BUNDLE_V3], indirect=True)
def test_unexpected_school_inventory_growth_exceeds_reviewed_bound(configured, monkeypatch):
    _, _, snapshot = configured
    school = next(row for row in snapshot.destinations if "school" in row.categories)
    # Deliberately TEST_ONLY altered loader output: no source-data mutation/publication.
    enlarged = snapshot.model_copy(
        update={
            "destinations": [
                *snapshot.destinations,
                school.model_copy(update={"destination_id": "TEST_ONLY-unreviewed-school"}),
            ]
        }
    )
    monkeypatch.setattr(pilot, "load_accepted_data", lambda identity: enlarged)
    with pytest.raises(DomainError, match="runtime artifact"):
        pilot.load_runtime()


@pytest.mark.parametrize("configured", [BUNDLE_V2, BUNDLE_V3], indirect=True)
def test_allowed_bundle_does_not_accept_other_snapshot_build(configured, monkeypatch):
    raw, path, snapshot = configured
    raw["build"]["snapshot_id"] = BUNDLE_V2 if snapshot.snapshot_id == BUNDLE_V3 else BUNDLE_V3
    repin(raw, path, monkeypatch)
    with pytest.raises(DomainError, match="runtime artifact"):
        pilot.load_runtime()


def test_revision_retains_same_pilot_physical_endpoints():
    old, new = (load_accepted_data(identity) for identity in (BUNDLE_V2, BUNDLE_V3))
    assert old.sample_points == new.sample_points
    old_point = next(row for row in old.sample_points if row.point_id == POINT)
    new_point = next(row for row in new.sample_points if row.point_id == POINT)
    old_school = next(row for row in old.destinations if row.destination_id == SCHOOL)
    new_school = next(row for row in new.destinations if row.destination_id == SCHOOL)
    assert old_point == new_point and old_school == new_school
    assert old.provenance.graph_version == new.provenance.graph_version
    assert canonical_hash(old.model_dump(mode="json")) != canonical_hash(
        new.model_dump(mode="json")
    )
    assert load_bundle(BUNDLE_V2).walk_connectors == load_bundle(BUNDLE_V3).walk_connectors


@pytest.mark.parametrize("configured", [BUNDLE_V3], indirect=True)
@pytest.mark.asyncio
async def test_new_runtime_rejects_queued_and_preflight_old_snapshot(configured, monkeypatch):
    _, _, snapshot = configured
    old = load_accepted_data(BUNDLE_V2)

    def unexpected_http(*args, **kwargs):
        pytest.fail("TEST_ONLY mismatched snapshot reached provider I/O")

    monkeypatch.setattr(pilot, "_post", unexpected_http)
    with pytest.raises(DomainError, match="fingerprint"):
        await pilot.preflight(request(), old)
    claim = claim_for(snapshot)
    claim.context.snapshot_id = BUNDLE_V2
    with pytest.raises(DomainError, match="Queued walk inputs"):
        pilot.calculate(claim)
    claim = claim_for(snapshot)
    claim.context.snapshot_hash = canonical_hash(old.model_dump(mode="json"))
    with pytest.raises(DomainError, match="Queued walk inputs"):
        pilot.calculate(claim)
    claim = claim_for(snapshot)
    claim.context.versions.code_version = "TEST_ONLY-code-without-artifact-fingerprint"
    with pytest.raises(DomainError, match="Queued walk inputs"):
        pilot.calculate(claim)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_graph_version", "TEST_ONLY-wrong-source"),
        ("profile_version", "TEST_ONLY-wrong-profile"),
        ("pedestrian_options_json", '{"type":"foot","walking_speed":4}'),
        ("evidence_refs", ["TEST_ONLY-unknown-proof"]),
    ],
)
def test_hash_pinning_does_not_accept_inconsistent_build(configured, monkeypatch, field, value):
    raw, path, _ = configured
    raw["build"][field] = value
    path.write_text(json.dumps(raw))
    monkeypatch.setenv(
        "A2_WALK_PILOT_ARTIFACT_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    with pytest.raises(DomainError, match="runtime artifact"):
        pilot.load_runtime()


@pytest.mark.parametrize(
    "changes",
    [
        {"city_id": "moscow"},
        {"passenger_mode": "private_car"},
        {"categories": ["kindergarten"], "priorities": {"kindergarten": "medium"}},
        {"area_id": "kaliningrad-relation-331411"},
        {"traffic_mode": "live"},
    ],
)
def test_runtime_rejects_outside_pilot_scope(configured, changes):
    with pytest.raises(DomainError, match="supports Kaliningrad"):
        pilot._scope(request().model_copy(update=changes))


@pytest.mark.asyncio
async def test_preflight_requires_code_fingerprint(configured, monkeypatch):
    _, _, snapshot = configured
    mock_client(monkeypatch)
    await pilot.preflight(request(), snapshot)
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-unbound-code")
    with pytest.raises(DomainError, match="fingerprint"):
        await pilot.preflight(request(), snapshot)


@pytest.mark.asyncio
async def test_replaced_routing_tileset_fails_preflight(configured, monkeypatch):
    _, _, snapshot = configured
    mock_client(monkeypatch, stale_status=True)
    with pytest.raises(DomainError, match="pinned build status"):
        await pilot.preflight(request(), snapshot)


@pytest.mark.asyncio
async def test_missing_connector_proof_is_unavailable_before_enqueue(configured, monkeypatch):
    raw, path, snapshot = configured
    raw["connectors"] = []
    path.write_text(json.dumps(raw))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setenv("A2_WALK_PILOT_ARTIFACT_SHA256", digest)
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + digest)
    with pytest.raises(DomainError, match="lack confirmed"):
        await pilot.preflight(request(), snapshot)


@pytest.mark.parametrize("compressed,oversized", [(True, False), (False, True)])
def test_unbounded_or_compressed_response_cannot_publish(configured, compressed, oversized):
    runtime = pilot.load_runtime()

    def respond(req):
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"} if compressed else {},
            stream=httpx.ByteStream(b"x" * (pilot.MAX_RESPONSE_BYTES + 1) if oversized else b"{}"),
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DomainError):
            pilot._post(client, runtime, "/route", {})


def test_context_snapshot_hash_is_checked_before_http(configured):
    _, _, snapshot = configured
    claim = claim_for(snapshot)
    claim.context.snapshot_hash = "TEST_ONLY-changed"
    with pytest.raises(DomainError, match="Queued walk inputs"):
        pilot.calculate(claim)


@pytest.mark.asyncio
async def test_partial_real_catalog_publishes_atomically_and_stays_scoped(
    configured, monkeypatch, database
):
    from a2_api.db.repositories.jobs import JobRepository
    from a2_api.services.analyses import AnalysisService

    _, _, snapshot = configured
    pool, _, _ = database
    repository = JobRepository(pool)
    code = pilot.RuntimeSettings().code_version
    service = AnalysisService(
        pilot.registry(),
        repository,
        code_version=code,
        engine_enabled=True,
        preflight=pilot.preflight,
    )
    mock_client(monkeypatch)
    accepted = await service.analyse(
        request(), "TEST_ONLY-key", "TEST_ONLY-trace", "TEST_ONLY-owner"
    )
    claim = await repository.claim("TEST_ONLY-token")
    assert claim is not None
    result = pilot.calculate(claim)
    async with repository.worker_lock() as acquired:
        assert acquired
        await repository.succeed(claim, result)
    saved = await service.analysis(claim.context.analysis_id, "TEST_ONLY-owner")
    assert saved.result_hash == result.result_hash
    assert (await service.job(accepted.job_id, "TEST_ONLY-owner")).status == "succeeded"
    assert saved.status == "partial" and all(s.value is None for s in saved.scores)
    with pytest.raises(DomainError):
        await service.analysis(saved.analysis_id, "TEST_ONLY-other")
