"""TEST_ONLY deployments over immutable real city data, with mocked routing only."""

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from a2_api.adapters import product, walk_batches, walk_runtime
from a2_api.adapters.product_runtime import FileRef, Profile, load_runtime
from a2_api.core.batches import BatchExecution, BatchPlan
from a2_api.core.config import RuntimeSettings
from a2_api.core.jobs import ClaimedJob, V2JobContext, batch_context_fingerprint, cache_fingerprint
from a2_api.core.registry import ProductRegistry
from a2_api.db.repositories.checkpoints import CheckpointRepository
from a2_api.db.repositories.jobs import JobRepository
from a2_contracts.api import Job
from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import VersionRefs
from a2_contracts.v2.requests import UrbanPassengerRequest
from a2_data_pipeline.contract_v2 import load_accepted_data
from a2_data_pipeline.store import load_bundle
from a2_data_pipeline.walk_projection import project_walk_connectors
from a2_engine.urban.valhalla.models import ValhallaBuild
from test_walk_batches import AcknowledgedChannel
from test_walk_pilot import transport

SNAPSHOTS = (
    "kaliningrad-urban-2026-11-10.2",
    "moscow-urban-2026-11-10.4",
    "novorossiysk-urban-2026-11-10.3",
)


def write(path, value):
    raw = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    path.write_bytes(raw)
    return {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()}


def city_profile(root, snapshot_id, categories=("school",)):
    snapshot = load_accepted_data(snapshot_id)
    bundle = load_bundle(snapshot_id)
    (city,) = snapshot.geographic_scope
    folder = root / city
    folder.mkdir()
    sources = [
        s
        for s in bundle.manifest.source_versions
        if bundle.manifest.graph_version.startswith(s.dataset_id + ":")
    ]
    (source,) = sources
    graph = dict(
        source_sha256=source.checksum.value,
        files=[dict(path="tiles/0/TEST_ONLY.gph", sha256="2" * 64, bytes=10)],
        image_id="TEST_ONLY-" + city,
        image_repo_digests=[],
        config_sha256="3" * 64,
    )
    graph_ref = write(folder / "graph.json", graph)
    build = dict(
        snapshot_id=snapshot_id,
        source_graph_version=snapshot.provenance.graph_version,
        build_id="TEST_ONLY-" + city,
        graph_version="TEST_ONLY-graph-" + graph_ref["sha256"],
        engine_version="3.5.1",
        profile_version="waydex-walk-static-5kmh-v1",
        pedestrian_options_json=json.dumps(dict(type="foot", walking_speed=5)),
        evidence_refs=["TEST_ONLY-runtime-proof"],
    )
    connectors = project_walk_connectors(
        bundle,
        walking_speed_kmh=5,
        speed_evidence_refs=build["evidence_refs"],
        profile_version=build["profile_version"],
    )
    by_physical = {c["physical_node_id"]: c for c in connectors}
    point = next(
        p
        for p in snapshot.sample_points
        if by_physical["physical:point:" + p.point_id]["traversable"] is True
    )
    destinations = [
        next(
            d
            for d in snapshot.destinations
            if category in d.categories
            and by_physical["physical:destination:" + d.destination_id]["traversable"] is True
        )
        for category in categories
    ]
    chosen = [
        by_physical["physical:point:" + point.point_id],
        *(by_physical["physical:destination:" + d.destination_id] for d in destinations),
    ]
    artifact = dict(
        schema_version="waydex-urban-walk-runtime/1",
        snapshot_id=snapshot_id,
        city_id=city,
        endpoint_url="http://" + city + ".test",
        point_ids=[point.point_id],
        destination_ids=sorted({d.destination_id for d in destinations}),
        build=build,
        connectors=chosen,
        correlations=[
            dict(
                access_node_id=c["access_node_id"],
                source_location=c["access_location"],
                correlated_location=c["access_location"],
                correlation_id="TEST_ONLY-" + c["access_node_id"],
                evidence_refs=build["evidence_refs"],
            )
            for c in {row["access_node_id"]: row for row in chosen}.values()
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
        status_expectation=dict(version="3.5.1", tileset_last_modified=123),
    )
    artifact_ref = write(folder / "artifact.json", artifact)
    approval = dict(
        schema_version="waydex-walk-batch-approval/1",
        snapshot_id=snapshot_id,
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        bundle_manifest_hash=canonical_hash(bundle.manifest.model_dump(mode="json")),
        runtime_artifact_hash=artifact_ref["sha256"],
        city_id=city,
        categories=sorted(categories),
        pairs=[
            dict(point_id=point.point_id, destination_id=destination)
            for destination in artifact["destination_ids"]
        ],
        routing_binding=dict(
            schema_version="waydex-routing-binding/1",
            graph_manifest=graph_ref,
            source_pbf_sha256=graph["source_sha256"],
            tile_manifest_sha256=canonical_hash(graph["files"]),
            build_image_digest=graph["image_id"],
            build_configuration_hash=graph["config_sha256"],
            runtime_build_id=build["build_id"],
            source_graph_version=build["source_graph_version"],
            endpoint_url=artifact["endpoint_url"],
            profile_fingerprint=ValhallaBuild(**build).profile_hash,
            immutable_deployment_attested=True,
            attested_by="TEST_ONLY-owner",
        ),
        batch_size=1,
        max_attempts=3,
        max_provider_calls=100,
        max_checkpoint_bytes=1024 * 1024,
        observation_retention_seconds=3600,
        valid_until=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    approval_ref = write(folder / "approval.json", approval)
    binding = dict(
        schema_version="waydex-urban-walk-binding/1",
        artifact=artifact_ref,
        approval=approval_ref,
        route_executor="valhalla_http_observation/1",
    )
    binding_ref = write(folder / "binding.json", binding)
    profile = Profile(
        profile_id="TEST_ONLY-" + city,
        analysis_kind="urban_passenger",
        city_id=city,
        snapshot_id=snapshot_id,
        snapshot_hash=approval["snapshot_hash"],
        passenger_modes=["walk"],
        categories=sorted(categories),
        traffic_modes=["static"],
        valid_from="2026-11-10T00:00:00Z",
        valid_to="2026-11-11T00:00:00Z",
        graph_fingerprint=graph_ref["sha256"],
        routing_profile_fingerprint=ValhallaBuild(**build).profile_hash,
        executor="walk_batches",
        max_route_pairs=len(approval["pairs"]),
        walk_binding=FileRef(path=city + "/binding.json", sha256=binding_ref["sha256"]),
    )
    snapshot_ref = write(root / (city + "-accepted.json"), snapshot.model_dump(mode="json"))
    snapshot_ref.update(snapshot_id=snapshot_id, content_hash=profile.snapshot_hash)
    territory = next(t for t in snapshot.territories if t.territory_id == city)
    return dict(
        snapshot=snapshot,
        profile=profile,
        folder=folder,
        artifact=artifact,
        approval=approval,
        binding=binding,
        snapshot_ref=snapshot_ref,
        territory=dict(
            territory_id=city, name=territory.name, level="city", timezone=territory.timezone
        ),
    )


def configure(root, monkeypatch, entries):
    manifest = dict(
        schema_version="waydex-product-runtime/1",
        snapshots=[e["snapshot_ref"] for e in entries],
        territories=[e["territory"] for e in entries],
        profiles=[e["profile"].model_dump(mode="json") for e in entries],
    )
    reference = write(root / "manifest.json", manifest)
    monkeypatch.setenv("A2_PRODUCT_RUNTIME_PATH", str(root / "manifest.json"))
    monkeypatch.setenv("A2_PRODUCT_RUNTIME_SHA256", reference["sha256"])
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + reference["sha256"])
    monkeypatch.setenv("A2_WALK_OBSERVATION_DIR", str(root))
    # Bound profiles must never consult these unrelated legacy locations.
    monkeypatch.setenv("A2_WALK_PILOT_ARTIFACT_PATH", "/TEST_ONLY/forbidden-global-artifact")
    monkeypatch.setenv("A2_WALK_BATCH_APPROVAL_PATH", "/TEST_ONLY/forbidden-global-approval")
    monkeypatch.delenv("A2_WALK_PILOT_EVIDENCE_DIR", raising=False)
    return manifest


def request_for(entry, categories=("school",)):
    return UrbanPassengerRequest(
        schema_version="2",
        analysis_kind="urban_passenger",
        city_id=entry["profile"].city_id,
        snapshot_id=entry["snapshot"].snapshot_id,
        departure_at="2026-11-10T09:00:00Z",
        passenger_mode="walk",
        traffic_mode="static",
        categories=list(categories),
        priorities={c: "medium" for c in categories},
    )


async def claim_for(entry, request=None):
    request = product.registry().normalize(request or request_for(entry))
    batch = await product.preflight(request, entry["snapshot"])
    registry = ProductRegistry()
    snapshot = entry["snapshot"]
    code = RuntimeSettings().code_version
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
        analysis_id="TEST_ONLY-" + request.city_id,
        normalized_request=request,
        access_scope="TEST_ONLY-owner",
        cache_valid_until=batch.valid_until,
    )
    execution = BatchExecution(
        **batch.model_dump(mode="python"),
        access_scope=context.access_scope,
        context_fingerprint=batch_context_fingerprint(context),
    )
    context = V2JobContext.model_validate(
        {**context.model_dump(mode="json"), "batch_execution": execution.model_dump(mode="json")}
    )
    now = datetime.now(UTC)
    return ClaimedJob(
        job=Job(
            job_id="TEST_ONLY-" + request.city_id,
            status="running",
            trace_id="TEST_ONLY",
            bundle_id=snapshot.snapshot_id,
            input_hash="TEST_ONLY",
            code_version=code,
            created_at=now,
            started_at=now,
            finished_at=None,
            run_id=None,
            error=None,
        ),
        kind="analysis",
        request=request,
        context=context,
        cache_key="TEST_ONLY",
        claim_token="TEST_ONLY",
    )


@pytest.fixture
def routed(monkeypatch):
    client_type = httpx.Client
    hosts = []
    mock = transport()

    def capture(request):
        hosts.append(request.url.host)
        return mock.handle_request(request)

    def client(*args, **kwargs):
        return client_type(*args, transport=httpx.MockTransport(capture), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    return hosts


@pytest.mark.asyncio
async def test_three_cities_share_dispatch_checkpoint_resume_and_methodology(
    tmp_path, monkeypatch, routed
):
    entries = [city_profile(tmp_path, identity) for identity in SNAPSHOTS]
    configure(tmp_path, monkeypatch, entries)
    runtime = load_runtime()
    assert len(runtime.walk_bindings) == len(runtime.profiles) == 3
    for bound in runtime.walk_bindings.values():
        assert bound.destination_names
        with pytest.raises(TypeError):
            bound.destination_names["TEST_ONLY-invented"] = "Invented name"
    identities = set()
    caches = set()
    previous_checkpoint = None
    for entry in entries:
        claim = await claim_for(entry)
        execution = claim.context.batch_execution
        identities.add(execution.identity)
        caches.add(cache_fingerprint("analysis", claim.request, claim.context))
        assert execution.schema_version == "waydex-walk-execution/2"
        assert execution.runtime_binding_hash == entry["profile"].walk_binding.sha256
        channel = AcknowledgedChannel(execution)
        if previous_checkpoint is not None:
            with pytest.raises(ValueError, match="EXECUTION_MISMATCH"):
                previous_checkpoint.validate_inventory(execution)
        before = len(routed)
        result = product.calculate(claim, channel)
        assert routed[before:] and set(routed[before:]) == {entry["profile"].city_id + ".test"}
        assert result.status == "partial" and result.journeys
        assert all(not s.ranking_eligible for s in result.scores)
        assert all(
            s.value is None
            for s in result.scores
            if s.name in {"category_index", "point_index", "territory_index"}
        )
        assert any(
            s.name == "observed_route_accessibility"
            and s.value is not None
            and s.completeness == "partial"
            and s.reason_code == "MINIMUM_NOT_PROVEN"
            for s in result.scores
        )
        area = next(
            t.area_km2
            for t in entry["snapshot"].territories
            if t.territory_id == entry["profile"].city_id
        )
        from a2_engine import load_urban_config, select_h_seconds

        expected = select_h_seconds(area, bands=load_urban_config().area_h_bands)
        assert (
            next(m.value for m in result.metrics if m.name == "urban_h_seconds") == expected.value
        )
        resumed = product.calculate(claim, AcknowledgedChannel(execution, channel.completed))
        assert resumed.result_hash == result.result_hash
        assert (
            len(routed) == before + 4 + 1
        )  # two pair calls, one batch status, final status; resume finalstatus
        previous_checkpoint = channel.completed[0]
    assert len(identities) == len(caches) == 3


@pytest.mark.parametrize(
    "attack",
    [
        "city",
        "snapshot",
        "graph",
        "graph_source",
        "category",
        "endpoint",
        "connector_seconds",
        "connector_state",
        "population",
    ],
)
def test_binding_rejects_cross_scope_and_same_id_source_mutations(tmp_path, monkeypatch, attack):
    entry = city_profile(tmp_path, SNAPSHOTS[0])
    configure(tmp_path, monkeypatch, [entry])
    runtime = load_runtime()
    bound = next(iter(runtime.walk_bindings.values()))
    snapshot = entry["snapshot"]
    scope = bound.scope
    if attack == "city":
        scope = replace(scope, city_id="moscow")
    elif attack == "snapshot":
        scope = replace(scope, snapshot_id="TEST_ONLY-other-snapshot")
    elif attack == "graph":
        scope = replace(scope, graph_fingerprint="0" * 64)
    elif attack == "graph_source":
        graph = json.loads((entry["folder"] / "graph.json").read_text())
        graph["source_sha256"] = "e867508d9240743dea215f130dcfe441a0a53980558b7d5002db91a6b87a57da"
        graph_ref = write(entry["folder"] / "graph.json", graph)
        scope = replace(scope, graph_fingerprint=graph_ref["sha256"])
        entry["artifact"]["build"]["graph_version"] = "TEST_ONLY-graph-" + graph_ref["sha256"]
        entry["approval"]["routing_binding"]["graph_manifest"] = graph_ref
        entry["approval"]["routing_binding"]["source_pbf_sha256"] = graph["source_sha256"]
    elif attack == "category":
        scope = replace(scope, categories=("airport",))
    elif attack == "population":
        value = snapshot.model_dump(mode="json")
        value["population_cells"][0]["population"] += 1
        snapshot = type(snapshot).model_validate(value)
        digest = canonical_hash(snapshot.model_dump(mode="json"))
        scope = replace(scope, snapshot_hash=digest)
        entry["approval"]["snapshot_hash"] = digest
    elif attack == "endpoint":
        entry["artifact"]["endpoint_url"] = "http://wrong-city.test"
    elif attack == "connector_seconds":
        entry["artifact"]["connectors"][0]["seconds"] += 1
    elif attack == "connector_state":
        entry["artifact"]["connectors"][0]["traversable"] = None
    if attack in {"population", "endpoint", "connector_seconds", "connector_state", "graph_source"}:
        artifact = write(entry["folder"] / "artifact.json", entry["artifact"])
        entry["approval"]["runtime_artifact_hash"] = artifact["sha256"]
        entry["binding"]["artifact"] = artifact
        entry["binding"]["approval"] = write(entry["folder"] / "approval.json", entry["approval"])
        ref = write(entry["folder"] / "binding.json", entry["binding"])
        reference = FileRef(path=entry["profile"].city_id + "/binding.json", sha256=ref["sha256"])
    else:
        reference = bound.reference
    with pytest.raises(ValueError, match="WALK_|CONNECTOR_"):
        walk_runtime.load_binding(bound.root, reference, snapshot=snapshot, scope=scope)


def test_validated_binding_rechecks_pinned_bytes_without_reparsing_sources(tmp_path, monkeypatch):
    from a2_api.core.errors import DomainError

    entry = city_profile(tmp_path, SNAPSHOTS[0])
    configure(tmp_path, monkeypatch, [entry])
    bound = next(iter(load_runtime().walk_bindings.values()))
    assert bound.inventory is not None

    def forbidden(*args, **kwargs):
        raise AssertionError("semantic source projection must not repeat within this job")

    monkeypatch.setattr(walk_runtime, "load_binding", forbidden)
    monkeypatch.setattr(walk_runtime, "load_bundle", forbidden)
    approval, digest, runtime = walk_batches.load_approval(bound=bound, snapshot=entry["snapshot"])
    assert approval is bound.approval and runtime is bound.runtime
    assert digest == bound.approval_sha256
    path = entry["folder"] / "artifact.json"
    original = path.read_bytes()
    path.write_bytes(b"x" + original[1:])
    with pytest.raises(DomainError, match="WALK_BATCH_APPROVAL_OR_BINDING_UNAVAILABLE"):
        walk_batches.load_approval(bound=bound, snapshot=entry["snapshot"])


@pytest.mark.asyncio
async def test_approved_category_subset_is_dynamic_and_foreign_category_is_unavailable(
    tmp_path, monkeypatch
):
    entry = city_profile(tmp_path, SNAPSHOTS[0], ("kindergarten", "school"))
    configure(tmp_path, monkeypatch, [entry])
    for category in ("kindergarten", "school"):
        claim = await claim_for(entry, request_for(entry, (category,)))
        assert len(claim.context.batch_execution.pairs) == 1
        assert {p.request.destination_id for p in claim.context.batch_execution.pairs} == {
            d.destination_id
            for d in entry["snapshot"].destinations
            if category in d.categories and d.destination_id in entry["artifact"]["destination_ids"]
        }
    with pytest.raises(Exception, match="RUNTIME_INPUTS_UNAVAILABLE_FOR_REQUEST"):
        await product.preflight(request_for(entry, ("airport",)), entry["snapshot"])
    with pytest.raises(ValueError, match="UNIQUE_AND_SORTED"):
        walk_batches.Approval.model_validate(
            {**entry["approval"], "categories": ["school", "school"]}
        )


@pytest.mark.asyncio
async def test_legacy_execution_serialization_is_unchanged_and_v2_requires_both_identities(
    tmp_path, monkeypatch
):
    entry = city_profile(tmp_path, SNAPSHOTS[0])
    configure(tmp_path, monkeypatch, [entry])
    claim = await claim_for(entry)
    data = BatchPlan.model_validate(
        claim.context.batch_execution.model_dump(
            mode="json", exclude={"access_scope", "context_fingerprint"}
        )
    ).model_dump(mode="json")
    for missing in ("runtime_binding_hash", "executor_fingerprint"):
        with pytest.raises(ValueError, match="BINDING_AND_EXECUTOR_REQUIRED"):
            BatchPlan.model_validate({key: value for key, value in data.items() if key != missing})
    data.pop("runtime_binding_hash")
    data.pop("executor_fingerprint")
    data["schema_version"] = "waydex-walk-execution/1"
    legacy = BatchPlan.model_validate(data)
    assert legacy.model_dump(mode="json") == data
    assert legacy.identity == canonical_hash(data)


@pytest.mark.asyncio
async def test_real_pg_generic_checkpoint_cancel_fence_and_explicit_resume(
    database, tmp_path, monkeypatch, routed
):
    pool, _, _ = database
    entry = city_profile(tmp_path, SNAPSHOTS[2])
    configure(tmp_path, monkeypatch, [entry])
    source = await claim_for(entry)
    repo = JobRepository(pool)
    scope = source.context.access_scope
    await repo.enqueue(
        "analysis", source.request, source.context, "TEST_ONLY-first", "TEST_ONLY", scope=scope
    )
    claim = await repo.claim("TEST_ONLY-token")
    progress = CheckpointRepository(repo)
    from a2_api.workers.calculation import calculate_batches

    result = await calculate_batches(
        claim, "test_walk_runtime:mocked_product_handler", 120, progress
    )
    execution = claim.context.batch_execution
    rows = await pool.fetch(
        "SELECT payload FROM od_checkpoints WHERE execution_identity=$1", execution.identity
    )
    from a2_api.core.batches import BatchCheckpoint

    (checkpoint,) = [BatchCheckpoint.model_validate_json(row["payload"]) for row in rows]
    await repo.cancel(claim.job.job_id, scope=scope)
    with pytest.raises(Exception, match="no longer active"):
        await progress.put(claim, checkpoint)
    await repo.enqueue(
        "analysis", source.request, source.context, "TEST_ONLY-resume", "TEST_ONLY", scope=scope
    )
    resumed = await repo.claim("TEST_ONLY-new-token")
    continued = await calculate_batches(
        resumed, "test_walk_runtime:mocked_product_handler", 120, progress
    )
    assert continued.result_hash == result.result_hash
    await repo.succeed(resumed, continued)
    assert (await repo.get_job(resumed.job.job_id, scope=scope)).status == "succeeded"


def mocked_product_handler(claim, channel):
    """TEST_ONLY child entrypoint: transport mocking must also live in the child."""
    original = httpx.Client

    def client(*args, **kwargs):
        return original(*args, transport=transport(), **kwargs)

    httpx.Client = client
    try:
        return product.calculate(claim, channel)
    finally:
        httpx.Client = original


def test_failed_moscow_profile_is_reported_without_borrowing_other_city_readiness(
    tmp_path, monkeypatch
):
    entries = [city_profile(tmp_path, identity) for identity in SNAPSHOTS]
    configure(tmp_path, monkeypatch, entries)
    (entries[1]["folder"] / "binding.json").write_text(
        "TEST_ONLY-invalid-https://secret.test/private-path"
    )
    runtime = load_runtime()
    assert set(runtime.profiles) == {"TEST_ONLY-kaliningrad", "TEST_ONLY-novorossiysk"}
    assert runtime.profile_failures == {
        "TEST_ONLY-moscow": "RUNTIME_DOCUMENT_HASH_OR_SIZE_MISMATCH"
    }
    assert len(product.registry().capabilities().urban_passenger_scopes) == 2


def test_cumulative_budget_counts_every_source_byte_and_rejects_one_byte_short(
    tmp_path, monkeypatch
):
    entry = city_profile(tmp_path, SNAPSHOTS[0])
    configure(tmp_path, monkeypatch, [entry])
    runtime = load_runtime()
    bound = next(iter(runtime.walk_bindings.values()))
    from a2_data_pipeline.paths import bundles_dir

    source_root = bundles_dir() / entry["snapshot"].snapshot_id
    manifest = json.loads((source_root / "manifest.json").read_text())
    size = sum(
        (entry["folder"] / name).stat().st_size
        for name in ("binding.json", "artifact.json", "approval.json", "graph.json")
    )
    size += (source_root / "manifest.json").stat().st_size
    size += sum((source_root / ref["path"]).stat().st_size for ref in manifest["files"].values())
    budget = [size]
    walk_runtime.load_binding(
        bound.root, bound.reference, snapshot=entry["snapshot"], scope=bound.scope, budget=budget
    )
    assert budget == [0]
    with pytest.raises(ValueError, match="SOURCE_BUNDLE_BYTE_BUDGET_EXCEEDED"):
        walk_runtime.load_binding(
            bound.root,
            bound.reference,
            snapshot=entry["snapshot"],
            scope=bound.scope,
            budget=[size - 1],
        )


def test_declared_category_without_any_executable_pair_stays_unavailable(tmp_path, monkeypatch):
    entry = city_profile(tmp_path, SNAPSHOTS[0])
    entry["artifact"]["correlations"] = []
    artifact_ref = write(entry["folder"] / "artifact.json", entry["artifact"])
    entry["binding"]["artifact"] = artifact_ref
    entry["approval"]["runtime_artifact_hash"] = artifact_ref["sha256"]
    entry["binding"]["approval"] = write(entry["folder"] / "approval.json", entry["approval"])
    ref = write(entry["folder"] / "binding.json", entry["binding"])
    entry["profile"] = entry["profile"].model_copy(
        update={
            "walk_binding": FileRef(
                path=entry["profile"].city_id + "/binding.json", sha256=ref["sha256"]
            )
        }
    )
    configure(tmp_path, monkeypatch, [entry])
    runtime = load_runtime()
    assert not runtime.profiles
    assert runtime.profile_failures == {
        "TEST_ONLY-kaliningrad": "WALK_APPROVED_CORRELATIONS_OR_CONNECTORS_UNAVAILABLE"
    }


def test_actual_three_city_startup_is_measured_in_a_fresh_process(tmp_path, monkeypatch):
    entries = [city_profile(tmp_path, identity) for identity in SNAPSHOTS]
    configure(tmp_path, monkeypatch, entries)
    script = """
import json,resource,sys,time
from a2_api.adapters.product_runtime import load_runtime
started=time.monotonic()
runtime=load_runtime()
peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({'profiles':len(runtime.profiles),'failures':runtime.profile_failures,
 'elapsed_seconds':time.monotonic()-started,'peak_rss_bytes':int(peak if sys.platform=='darwin' else peak*1024)}))
"""
    process = subprocess.run(
        [sys.executable, "-c", script],
        env=os.environ.copy(),
        cwd=Path(__file__).parents[3],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    measured = json.loads(process.stdout)
    assert measured["profiles"] == 3 and not measured["failures"]
    assert measured["peak_rss_bytes"] > 0
    (tmp_path / "startup-profile.json").write_text(json.dumps(measured, indent=2) + "\n")
    print("Measured three-city source verification: " + json.dumps(measured, sort_keys=True))


def test_registered_app_and_admitted_preflight_peak_is_measured(tmp_path, monkeypatch):
    entries = [city_profile(tmp_path, identity) for identity in SNAPSHOTS]
    configure(tmp_path, monkeypatch, entries)
    script = """
import asyncio,json,os,resource,sys,time
from a2_api.api.bootstrap import build_app
from a2_api.core.config import RuntimeSettings
from a2_api.adapters.product_runtime import load_runtime
from a2_api.adapters.walk_batches import load_approval
from a2_contracts.v2.requests import UrbanPassengerRequest
def peak():
 value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
 return int(value if sys.platform=='darwin' else value*1024)
started=time.monotonic()
app=build_app(RuntimeSettings(_env_file=None,database_url=None,assistant_hook=None,
 registry_hook='a2_api.adapters.product:registry',preflight_hook='a2_api.adapters.product:preflight',
 calculation_handler='a2_api.adapters.product:calculate',code_version=os.environ['A2_CODE_VERSION']))
registered=time.monotonic()-started
service=app.state.services
request=service.registry.normalize(UrbanPassengerRequest(schema_version='2',analysis_kind='urban_passenger',
 city_id='moscow',snapshot_id='moscow-urban-2026-11-10.4',departure_at='2026-11-10T09:00:00Z',
 passenger_mode='walk',traffic_mode='static',categories=['school'],priorities={'school':'medium'}))
snapshot=service.registry.resolve_snapshot(request)
started=time.monotonic()
plan=asyncio.run(service.preflight(request,snapshot))
preflight=time.monotonic()-started
app_peak=peak()
runtime=load_runtime()
bound=runtime.walk_bindings['TEST_ONLY-moscow']
started=time.monotonic()
load_approval(bound=bound,snapshot=runtime.snapshots[bound.scope.snapshot_id])
print(json.dumps({'registered_snapshots':len(service.registry.snapshots),'planned_pairs':len(plan.pairs),
 'app_registration_seconds':registered,'admitted_preflight_seconds':preflight,
 'retained_app_preflight_peak_rss_bytes':app_peak,
 'one_moscow_chunk_source_revalidation_seconds':time.monotonic()-started,
 'retained_app_runtime_and_revalidation_peak_rss_bytes':peak(),'database_lifespan_started':False,
 'provider_calls':0,'native_replays':0}))
"""
    process = subprocess.run(
        [sys.executable, "-c", script],
        env=os.environ.copy(),
        cwd=Path(__file__).parents[3],
        capture_output=True,
        text=True,
        check=True,
        timeout=90,
    )
    measured = json.loads(process.stdout)
    assert measured["registered_snapshots"] == 3 and measured["planned_pairs"] == 1
    assert measured["retained_app_preflight_peak_rss_bytes"] > 0
    (tmp_path / "registered-app-profile.json").write_text(json.dumps(measured, indent=2) + "\n")
    print("Measured retained application/preflight: " + json.dumps(measured, sort_keys=True))
