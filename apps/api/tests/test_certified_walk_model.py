"""TEST_ONLY metadata controls plus optional preserved offline native verification."""

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from a2_api.adapters import certified_walk_model as adapter
from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_api.core.registry import ProductRegistry
from a2_contracts.api import Job
from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import TerritoryOption
from a2_contracts.v2.base import VersionRefs
from a2_contracts.v2.requests import UrbanPassengerRequest
from a2_contracts.v2.results import AnalysisResult
from a2_data_pipeline.contract_v2 import load_accepted_data, registry_options
from a2_data_pipeline.store import load_bundle

ROOT = Path(__file__).parents[3]
SNAPSHOT = "kaliningrad-urban-2026-11-10.5"


def write(path, value):
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def metadata(tmp_path, monkeypatch):
    """A deliberately non-executable archive; never passed to a real solver."""
    snapshot = load_accepted_data(SNAPSHOT)
    archive = tmp_path / "TEST_ONLY-native"
    archive.mkdir()
    names = {
        "export.jsonl",
        "request.json",
        "valhalla.json",
        "walking-profile.json",
        "build-manifest.json",
        "source-bound-walk",
        "artifact.json",
        "replay.jsonl",
        "turn-plan.json",
        "turn-weights.jsonl",
        "turn-replay.jsonl",
    }
    files = {name: write(archive / name, {"TEST_ONLY": name}) for name in names}
    for module, name in (
        (adapter.source_bound, "source_bound.py"),
        (adapter.turn_certificate, "turn_certificate.py"),
    ):
        content = Path(module.__file__).read_bytes()
        (archive / name).write_bytes(content)
        files[name] = hashlib.sha256(content).hexdigest()
    source = {
        name: files[name]
        for name in (
            "export.jsonl",
            "request.json",
            "valhalla.json",
            "walking-profile.json",
            "build-manifest.json",
            "source-bound-walk",
            "artifact.json",
        )
    }
    native = {
        "native_run": "/TEST_ONLY-history-path-must-not-be-followed",
        "files_sha256": files,
        "closed_turn_certificate": {
            "source_binding": source,
            "source_scope_id": hashlib.sha256(
                json.dumps(source, sort_keys=True).encode()
            ).hexdigest(),
        },
        "result": {"network_minimum_proven": True, "index": 100},
    }
    native_hash = write(archive / "receipt.json", native)
    point = next(p for p in snapshot.sample_points if "e468-n6069-p" in p.point_id)
    destination = next(d for d in snapshot.destinations if "airport" in d.categories)
    binding = {
        "schema_version": "waydex-certified-walk-model/1",
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": canonical_hash(snapshot.model_dump(mode="json")),
        "city_id": "kaliningrad",
        "point_id": point.point_id,
        "destination_id": destination.destination_id,
        "service_date": "2026-11-10",
        "native_receipt": {"path": "TEST_ONLY-native/receipt.json", "sha256": native_hash},
    }
    for name in ("physical_access", "compatibility", "policy", "official_sources"):
        digest = write(tmp_path / ("TEST_ONLY-" + name + ".json"), {"TEST_ONLY": name})
        binding[name] = {"path": "TEST_ONLY-" + name + ".json", "sha256": digest}
    path = tmp_path / "TEST_ONLY-certified-model.json"
    digest = write(path, binding)
    monkeypatch.setenv("A2_CERTIFIED_WALK_MODEL_PATH", str(path))
    monkeypatch.setenv("A2_CERTIFIED_WALK_MODEL_SHA256", digest)
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + digest)
    return snapshot, binding, path


def request(**changes):
    return UrbanPassengerRequest(
        schema_version="2",
        analysis_kind="urban_passenger",
        city_id="kaliningrad",
        snapshot_id=SNAPSHOT,
        departure_at=datetime(2026, 11, 10, 9, tzinfo=ZoneInfo("Europe/Kaliningrad")),
        passenger_mode="walk",
        traffic_mode="static",
        categories=["airport"],
        priorities={"airport": "medium"},
        **changes,
    )


@pytest.mark.asyncio
async def test_preflight_only_checks_pinned_metadata_and_never_runs_solver(metadata, monkeypatch):
    snapshot, _, _ = metadata

    def forbidden(*args, **kwargs):
        raise AssertionError("Full certificate verification belongs in the spawned worker")

    monkeypatch.setattr(adapter, "_verify_native", forbidden)
    monkeypatch.setattr(adapter.source_bound, "solve_native_walk_relaxation", forbidden)
    await adapter.preflight(request(), snapshot)
    runtime = adapter.load_binding(ProductRegistry().config_hash)
    assert runtime.graph_fingerprint != runtime.profile_fingerprint
    assert runtime.binding.snapshot_id == SNAPSHOT


def test_changed_native_file_is_rejected_before_any_solver(metadata):
    _, binding, path = metadata
    archive = (path.parent / binding["native_receipt"]["path"]).parent
    (archive / "turn-weights.jsonl").write_text("TEST_ONLY changed weights")
    with pytest.raises(Exception, match="CERTIFIED_WALK_MODEL_INPUTS_UNAVAILABLE"):
        adapter.load_binding(ProductRegistry().config_hash)


def test_relative_proof_archive_relocates_without_following_historical_path(metadata, monkeypatch):
    _, _, path = metadata
    before = adapter.load_binding(ProductRegistry().config_hash)
    relocated = path.parent.parent / (path.parent.name + "-relocated")
    shutil.copytree(path.parent, relocated)
    monkeypatch.setenv("A2_CERTIFIED_WALK_MODEL_PATH", str(relocated / path.name))
    after = adapter.load_binding(ProductRegistry().config_hash)
    assert after.native_root == relocated / "TEST_ONLY-native"
    assert after.graph_fingerprint == before.graph_fingerprint
    assert after.profile_fingerprint == before.profile_fingerprint


@pytest.mark.asyncio
async def test_model_scope_rejects_other_day_and_changed_population(metadata):
    snapshot, _, _ = metadata
    later = request().model_copy(
        update={"departure_at": request().departure_at + timedelta(days=1)}
    )
    with pytest.raises(Exception, match="CATALOG_DATE_UNAVAILABLE"):
        await adapter.preflight(later, snapshot)
    changed = snapshot.model_dump(mode="json")
    changed["population_cells"][0]["population"] += 1
    with pytest.raises(Exception, match="OUTSIDE_CERTIFIED_WALK_MODEL_SCOPE"):
        await adapter.preflight(request(), type(snapshot).model_validate(changed))


def preserved_binding(tmp_path, monkeypatch):
    """Use only the reviewed archive when it is installed; never fetch or route."""
    archive = Path(
        "/Users/kirito/waydex-phase2-runtime/source-bound-walk/builds/closed-turn-v3-0b4724e549/runs/airport-network-final-20261110-v4"
    )
    physical = archive.parent / "airport-physical-final-20261110-v4/physical-receipt.json"
    policy = Path(
        "/Users/kirito/waydex-full-runtime/ml2/designated-access-2026-11-10/airport-canonical-access-policy-replay.json"
    )
    if not archive.is_dir() or not physical.is_file() or not policy.is_file():
        pytest.skip("Preserved real native proof is not installed; no network fallback")
    snapshot = load_accepted_data(SNAPSHOT)
    compatibility = (
        ROOT / "data/manifests/qa/kaliningrad-airport-native-snapshot-compatibility-2026-11-10.json"
    )
    official = ROOT / "data/manifests/research/kaliningrad-airport-official-sources-2026-11-10.json"
    compatible = json.loads(compatibility.read_text())
    access = json.loads(physical.read_text())
    artifact = json.loads((archive / "artifact.json").read_text())
    known = {row.evidence_id for row in snapshot.evidence}
    known.update(row["evidence_id"] for row in artifact["evidence_refs"])
    needed = {
        ref
        for side in access["endpoints"]
        for row in side["native_alternatives"]
        for ref in row["evidence_refs"]
    }
    needed.update(
        {
            "snapshot-compatibility:"
            + "794149d884066093bc61f985d899fd895b83b33f3ed0b13cc489a14118b670d0",
            "canonical-access-policy:"
            + "acb9a9ec103e81d8365ce987b38a092a4a47d53fe8991500a972d660a86b206b",
            "physical-access-proof:"
            + "0d0af39b4e536f14000bae235346e44c69c74a3cb6914af74261d7165c241203",
            "native-scope:" + access["source_scope_id"],
        }
    )
    origin = next(
        row
        for row in artifact["evidence_refs"]
        if row["evidence_id"].startswith("osm-geofabrik-pbf-")
    )
    evidence = [
        {
            **origin,
            "evidence_id": ref,
            "source_id": "TEST_ONLY-local-reviewed-proof",
            "section": "Locally preserved immutable "
            + ref
            + "; URL identifies originating data, not a receipt download.",
        }
        for ref in sorted(needed - known)
    ]
    value = dict(
        schema_version="waydex-certified-walk-model/1",
        snapshot_id=SNAPSHOT,
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        city_id="kaliningrad",
        point_id=compatible["selected_pair_invariants"]["point"]["point_id"],
        destination_id=compatible["selected_pair_invariants"]["destination"]["destination_id"],
        service_date="2026-11-10",
        native_receipt=dict(
            path=str(archive / "receipt.json"),
            sha256="e6cef13a2e3681f5cf0d337ae95c13ff292977e7a7ec978bb9fa13a38aca292b",
        ),
        physical_access=dict(
            path=str(physical),
            sha256="0d0af39b4e536f14000bae235346e44c69c74a3cb6914af74261d7165c241203",
        ),
        compatibility=dict(
            path=str(compatibility),
            sha256="794149d884066093bc61f985d899fd895b83b33f3ed0b13cc489a14118b670d0",
        ),
        policy=dict(
            path=str(policy),
            sha256="acb9a9ec103e81d8365ce987b38a092a4a47d53fe8991500a972d660a86b206b",
        ),
        official_sources=dict(
            path=str(official),
            sha256="c8c118ffa272525bc1a6a8eb76da63d26079f891f19270cbf4d79f655ac62918",
        ),
        evidence=evidence,
    )
    path = tmp_path / "TEST_ONLY-real-proof-binding.json"
    digest = write(path, value)
    monkeypatch.setenv("A2_CERTIFIED_WALK_MODEL_PATH", str(path))
    monkeypatch.setenv("A2_CERTIFIED_WALK_MODEL_SHA256", digest)
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + digest)
    return snapshot, digest


def claim_for(snapshot, digest):
    options = registry_options(load_bundle(snapshot.snapshot_id))
    registry = ProductRegistry(
        territories=[TerritoryOption.model_validate(row) for row in options["territories"]],
        snapshots=[snapshot],
        ready_kinds=["urban_passenger"],
    )
    normalized = registry.normalize(request())
    code = "TEST_ONLY-" + digest
    context = V2JobContext(
        schema_version="2",
        snapshot_id=SNAPSHOT,
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        versions=VersionRefs(
            snapshot_id=SNAPSHOT,
            graph_version=snapshot.provenance.graph_version,
            calendar_version=snapshot.provenance.calendar_version,
            population_version=snapshot.provenance.population_version,
            methodology_version=registry.methodology.version,
            config_hash=registry.config_hash,
            code_version=code,
        ),
        analysis_id="TEST_ONLY-certified-airport",
        normalized_request=normalized,
        access_scope="TEST_ONLY-owner",
    )
    return ClaimedJob(
        job=Job(
            job_id="TEST_ONLY-job",
            status="running",
            trace_id="TEST_ONLY-trace",
            bundle_id=SNAPSHOT,
            input_hash="TEST_ONLY",
            code_version=code,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            finished_at=None,
            run_id=None,
            error=None,
        ),
        kind="analysis",
        request=request(),
        context=context,
        cache_key="TEST_ONLY",
        claim_token="TEST_ONLY",
    )


def test_preserved_native_proof_recomputes_exact_model_and_projects_only_one_point(
    tmp_path, monkeypatch
):
    snapshot, digest = preserved_binding(tmp_path, monkeypatch)
    claim = claim_for(snapshot, digest)
    verified = []
    original = adapter.physical_certificate.verify_common_designated_access_minimum

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        verified.append(result)
        return result

    monkeypatch.setattr(
        adapter.physical_certificate, "verify_common_designated_access_minimum", observed
    )
    result = adapter.calculate(claim)
    assert len(verified) == 1
    assert verified[0].lower_seconds == Fraction("7939955842630578250721/576460752303423488")
    assert result == AnalysisResult.model_validate(result.model_dump(mode="json"))
    assert result.status == "partial" and "CANONICAL_ACCESS_MODEL" in result.limitations
    assert result.journeys == []
    numeric = [score for score in result.scores if score.value is not None]
    assert len(numeric) == 2
    assert {score.name for score in numeric} == {"category_index", "point_index"}
    assert all(
        score.value == pytest.approx(0.4971801994143471)
        and score.completeness == "partial"
        and not score.ranking_eligible
        for score in numeric
    )
    assert (
        len(
            [
                score
                for score in result.scores
                if score.name == "point_index" and score.value is None
            ]
        )
        == 215
    )
    assert next(score for score in result.scores if score.name == "territory_index").value is None
    point = next(
        feature
        for feature in result.map_data.features
        if feature.entity_id == numeric[0].subject.point_id
    )
    assert point.population == pytest.approx(661.234)
    time = next(
        metric
        for metric in result.metrics
        if metric.value and metric.name == "minimum_journey_seconds"
    )
    assert time.value == float(verified[0].lower_seconds) and time.journey_id is None
