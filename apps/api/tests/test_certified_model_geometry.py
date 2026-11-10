"""Source binding controls and one preserved offline 97-edge geometry replay."""

import copy
import hashlib
import json
import os
import shutil
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from a2_api.adapters import certified_walk_model as adapter
from a2_api.core.registry import ProductRegistry
from a2_contracts.v2.results import AnalysisResult
from test_certified_walk_model import claim_for, request, write
from test_certified_walk_model import metadata as base_metadata  # noqa: F401

RUNTIME = Path(
    os.environ.get(
        "A2_TEST_CERTIFIED_MODEL_RUNTIME_PATH",
        "/Users/kirito/waydex-full-runtime/certified-walk-model-v1",
    )
)
GEOMETRY = Path(
    os.environ.get(
        "A2_TEST_CERTIFIED_GEOMETRY_RUN_PATH",
        "/Users/kirito/waydex-phase2-runtime/source-bound-walk/geometry-builds/"
        "airport-directed-v2-20261110/runs/airport-geometry-final-20261110-v1",
    )
)
RECEIPT_SHA = "89dcd9238cf1289e243030611303bb2a964b819a978e51aba9bf83a2045a4eab"
EXACT_SECONDS = Fraction("7939955842630578250721/576460752303423488")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pin(path, binding, monkeypatch):
    digest = write(path, binding)
    monkeypatch.setenv("A2_CERTIFIED_WALK_MODEL_PATH", str(path))
    monkeypatch.setenv("A2_CERTIFIED_WALK_MODEL_SHA256", digest)
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + digest)
    return digest


@pytest.fixture
def geometry_metadata(base_metadata, monkeypatch):  # noqa: F811
    """TEST_ONLY archives exercise metadata checks; they cannot execute a route."""
    snapshot, binding, path = base_metadata
    native_path = path.parent / binding["native_receipt"]["path"]
    native = json.loads(native_path.read_text())
    native["files_sha256"]["turn-candidate.json"] = write(
        native_path.parent / "turn-candidate.json", {"TEST_ONLY": "candidate"}
    )
    binding["native_receipt"]["sha256"] = write(native_path, native)
    original_digest = pin(path, binding, monkeypatch)
    original_runtime = adapter.load_binding(ProductRegistry().config_hash)
    archive = path.parent / "geometry"
    archive.mkdir()
    shutil.copyfile(native_path, archive / "native-receipt.json")
    shutil.copyfile(path, archive / "proof-binding.json")
    shutil.copyfile(
        path.parent / binding["physical_access"]["path"], archive / "physical-receipt.json"
    )
    shutil.copyfile(adapter.geometry_engine.__file__, archive / "geometry.py")
    for name in ("model-receipt.json", "export.jsonl", "plan.json"):
        write(archive / name, {"TEST_ONLY": name})
    (archive / "headers").mkdir()
    for index in range(25):
        write(archive / "headers" / str(index), {"TEST_ONLY": index})
    files = {
        str(file.relative_to(archive)): sha(file) for file in archive.rglob("*") if file.is_file()
    }
    assert len(files) == 32
    receipt = {
        "schema": "waydex-certified-walk-geometry/1",
        "geometry_only": True,
        "promotes_minimum_or_coverage": False,
        "files_sha256": files,
        "source_files_sha256": native["files_sha256"],
        "native_receipt_sha256": binding["native_receipt"]["sha256"],
        "physical_receipt_sha256": binding["physical_access"]["sha256"],
        "proof_binding_sha256": original_digest,
        "model_receipt_sha256": files["model-receipt.json"],
        "source_scope_id": native["closed_turn_certificate"]["source_scope_id"],
        "source_build_manifest_sha256": native["files_sha256"]["build-manifest.json"],
        "candidate_sha256": native["files_sha256"]["turn-candidate.json"],
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": binding["snapshot_hash"],
    }
    receipt_sha = write(archive / "receipt.json", receipt)
    snapshot_sha = write(path.parent / "accepted-data.json", snapshot.model_dump(mode="json"))
    supplement = {
        "schema_version": "waydex-certified-walk-geometry-binding/1",
        "receipt": {"path": "geometry/receipt.json", "sha256": receipt_sha},
        "original_snapshot": {"path": "accepted-data.json", "sha256": snapshot_sha},
    }
    binding["geometry"] = {
        "path": "geometry-supplement.json",
        "sha256": write(path.parent / "geometry-supplement.json", supplement),
    }
    pin(path, binding, monkeypatch)
    return snapshot, binding, path, receipt, supplement, original_runtime


def repin(geometry_metadata, monkeypatch):
    _, binding, path, receipt, supplement, _ = geometry_metadata
    supplement["receipt"]["sha256"] = write(path.parent / "geometry/receipt.json", receipt)
    binding["geometry"]["sha256"] = write(path.parent / "geometry-supplement.json", supplement)
    pin(path, binding, monkeypatch)


def test_optional_geometry_preserves_legacy_core_and_relocates(geometry_metadata, monkeypatch):
    _, binding, path, _, _, before = geometry_metadata
    assert "geometry" not in before.binding.model_dump(mode="json")
    with_geometry = adapter.load_binding(ProductRegistry().config_hash)
    assert with_geometry.geometry is not None
    assert (path.parent / "accepted-data.json").stat().st_size > adapter.MAX_METADATA_BYTES
    assert with_geometry.graph_fingerprint != before.graph_fingerprint
    assert with_geometry.profile_fingerprint != before.profile_fingerprint
    assert with_geometry.binding.model_dump(mode="json", exclude={"geometry"}) == dict(
        before.binding.model_dump(mode="json")
    )
    relocated = path.parent.parent / (path.parent.name + "-relocated")
    shutil.copytree(path.parent, relocated)
    monkeypatch.setenv("A2_CERTIFIED_WALK_MODEL_PATH", str(relocated / path.name))
    after = adapter.load_binding(ProductRegistry().config_hash)
    assert after.graph_fingerprint == with_geometry.graph_fingerprint
    assert after.profile_fingerprint == with_geometry.profile_fingerprint
    binding.pop("geometry")
    pin(path, binding, monkeypatch)
    legacy = adapter.load_binding(ProductRegistry().config_hash)
    assert legacy.graph_fingerprint == before.graph_fingerprint
    assert legacy.profile_fingerprint == before.profile_fingerprint


@pytest.mark.asyncio
async def test_geometry_preflight_has_no_solver_or_provider_io(geometry_metadata, monkeypatch):
    snapshot, _, _, _, _, _ = geometry_metadata

    def forbidden(*args, **kwargs):
        raise AssertionError("Geometry/native replay belongs in the calculation child")

    monkeypatch.setattr(adapter, "_verify_native", forbidden)
    monkeypatch.setattr(adapter.geometry_engine, "load_certified_walk_geometry", forbidden)
    await adapter.preflight(request(), snapshot)


def test_geometry_inventory_has_a_cumulative_byte_budget(geometry_metadata, monkeypatch):
    monkeypatch.setattr(adapter, "MAX_GEOMETRY_TOTAL_BYTES", 1)
    with pytest.raises(Exception, match="CERTIFIED_WALK_MODEL_INPUTS_UNAVAILABLE"):
        adapter.load_binding(ProductRegistry().config_hash)


@pytest.mark.parametrize(
    "attack",
    [
        "inventory_hash",
        "source_inventory",
        "source_scope",
        "native_receipt",
        "core_binding",
        "snapshot_file_hash",
        "snapshot_semantics",
        "snapshot_identity",
        "inventory_escape",
        "verifier",
    ],
)
def test_source_or_snapshot_mutation_cannot_authorize_geometry(
    geometry_metadata, monkeypatch, attack
):
    _, binding, path, receipt, supplement, _ = geometry_metadata
    archive = path.parent / "geometry"
    if attack == "inventory_hash":
        (archive / "export.jsonl").write_text("TEST_ONLY changed")
    elif attack == "source_inventory":
        receipt["source_files_sha256"]["artifact.json"] = "0" * 64
    elif attack == "source_scope":
        receipt["source_scope_id"] = "0" * 64
    elif attack == "native_receipt":
        receipt["native_receipt_sha256"] = "0" * 64
    elif attack == "core_binding":
        binding["service_date"] = "2026-11-11"
    elif attack == "snapshot_file_hash":
        supplement["original_snapshot"]["sha256"] = "0" * 64
    elif attack in {"snapshot_semantics", "snapshot_identity"}:
        snapshot_path = path.parent / "accepted-data.json"
        value = json.loads(snapshot_path.read_text())
        if attack == "snapshot_semantics":
            value["population_cells"][0]["population"] += 1
        else:
            value["snapshot_id"] = "TEST_ONLY-other-snapshot"
        supplement["original_snapshot"]["sha256"] = write(snapshot_path, value)
    elif attack == "inventory_escape":
        receipt["files_sha256"]["../TEST_ONLY-physical_access.json"] = receipt["files_sha256"].pop(
            "headers/0"
        )
    elif attack == "verifier":
        receipt["files_sha256"]["geometry.py"] = write(
            archive / "geometry.py", {"TEST_ONLY": "wrong verifier"}
        )
    repin(geometry_metadata, monkeypatch)
    with pytest.raises(Exception, match="CERTIFIED_WALK_MODEL_INPUTS_UNAVAILABLE"):
        adapter.load_binding(ProductRegistry().config_hash)


def preserved_geometry(tmp_path, monkeypatch):
    if not RUNTIME.is_dir() or not GEOMETRY.is_dir():
        reason = (
            f"Preserved source runtime required at {RUNTIME}; geometry run required at {GEOMETRY}. "
            "Restore reviewed artifacts and set A2_TEST_CERTIFIED_MODEL_RUNTIME_PATH and "
            "A2_TEST_CERTIFIED_GEOMETRY_RUN_PATH; no network fallback."
        )
        if os.environ.get("A2_TEST_CERTIFIED_MODEL_RUNTIME_PATH") or os.environ.get(
            "A2_TEST_CERTIFIED_GEOMETRY_RUN_PATH"
        ):
            pytest.fail(reason)
        pytest.skip(reason)
    assert sha(GEOMETRY / "receipt.json") == RECEIPT_SHA
    for name in ("native", "physical", "metadata"):
        (tmp_path / name).symlink_to(RUNTIME / name, target_is_directory=True)
    (tmp_path / "geometry").symlink_to(GEOMETRY, target_is_directory=True)
    shutil.copyfile(RUNTIME / "accepted-data.json", tmp_path / "accepted-data.json")
    binding = json.loads((RUNTIME / "proof-binding.json").read_text())
    supplement = adapter.GeometrySupplement.model_validate(
        {
            "schema_version": "waydex-certified-walk-geometry-binding/1",
            "receipt": {"path": "geometry/receipt.json", "sha256": RECEIPT_SHA},
            "original_snapshot": {
                "path": "accepted-data.json",
                "sha256": "f616122d81a297917d49fa64bec39a9876c20ef385469a3f4a7cbdf9b271b82d",
            },
        }
    )
    binding["geometry"] = {
        "path": "geometry-supplement.json",
        "sha256": write(tmp_path / "geometry-supplement.json", supplement.model_dump(mode="json")),
    }
    digest = pin(tmp_path / "proof-binding.json", binding, monkeypatch)
    snapshot = adapter.AcceptedData.model_validate_json(
        (tmp_path / "accepted-data.json").read_bytes()
    )
    return snapshot, digest


def test_preserved_geometry_reuses_one_graph_and_preserves_exact_score_operand(
    tmp_path, monkeypatch
):
    snapshot, digest = preserved_geometry(tmp_path, monkeypatch)
    graph_loads = []
    geometries = []
    load_graph = adapter.source_bound.load_native_walk_export
    load_geometry = adapter.geometry_engine.load_certified_walk_geometry

    def observe_graph(*args, **kwargs):
        graph = load_graph(*args, **kwargs)
        graph_loads.append(graph.content_sha256)
        return graph

    def observe_geometry(content, **kwargs):
        geometry = load_geometry(content, **kwargs)
        geometries.append(geometry)
        # Reuse the one verified graph for independent malformed-shape controls.
        rows = [json.loads(row) for row in content.splitlines()]
        first = next(i for i, row in enumerate(rows) if row["kind"] == "geometry_edge")
        for attack in ("sequence", "reverse_flag", "reverse_shape"):
            changed = copy.deepcopy(rows)
            edge = changed[first]
            if attack == "sequence":
                edge["ordinal"] += 1
            elif attack == "reverse_flag":
                edge["forward"] = not edge["forward"]
            else:
                edge["shape_bits"].reverse()
            raw = "\n".join(json.dumps(row) for row in changed)
            with pytest.raises(ValueError, match="GEOMETRY_"):
                load_geometry(
                    raw, **{**kwargs, "expected_sha256": hashlib.sha256(raw.encode()).hexdigest()}
                )
        with pytest.raises(ValueError, match="GEOMETRY_EXPORT_CHANGED"):
            load_geometry(content, **{**kwargs, "expected_sha256": "0" * 64})
        candidate = kwargs["candidate"]
        with pytest.raises(ValueError, match="GEOMETRY_CERTIFIED_SEQUENCE_OR_SOURCE_CHANGED"):
            load_geometry(
                content,
                **{
                    **kwargs,
                    "candidate": replace(
                        candidate, traversals=tuple(reversed(candidate.traversals))
                    ),
                },
            )
        return geometry

    monkeypatch.setattr(adapter.source_bound, "load_native_walk_export", observe_graph)
    monkeypatch.setattr(adapter.geometry_engine, "load_certified_walk_geometry", observe_geometry)
    result = adapter.calculate(claim_for(snapshot, digest))
    assert len(graph_loads) == len(geometries) == 1
    assert len(geometries[0].edge_ids) == 97
    assert len(geometries[0].physical_coordinates) == 392
    assert result == AnalysisResult.model_validate(result.model_dump(mode="json"))
    assert result.status == "partial"
    assert {
        "CANONICAL_ACCESS_MODEL",
        "PINNED_TILE_SHAPE_6_DECIMAL_ENCODING",
        "MODEL_TIMELINE_MICROSECOND_PRECISION",
    } <= set(result.limitations)
    assert "CERTIFIED_MODEL_DURATION_ONLY_NO_ROUTE_GEOMETRY" not in result.limitations
    exact = next(
        metric
        for metric in result.metrics
        if metric.name == "minimum_journey_seconds" and metric.value is not None
    )
    assert exact.value == float(EXACT_SECONDS) and exact.journey_id is None
    timeline = next(metric for metric in result.metrics if metric.name == "total_seconds")
    (journey,) = result.journeys
    assert timeline.journey_id == journey.journey_id
    assert timeline.value == journey.total_seconds == 13773.627798
    assert timeline.completeness == journey.completeness == "partial"
    assert abs(Fraction(str(timeline.value)) - EXACT_SECONDS) <= Fraction(1, 2_000_000)
    assert journey.total_seconds == (journey.arrival_at - journey.departure_at).total_seconds()
    (leg,) = journey.legs
    assert leg.mode == leg.kind == "walk" and leg.service_id is None
    assert leg.duration_seconds == journey.total_seconds
    assert leg.duration_seconds == (leg.end_at - leg.start_at).total_seconds()
    assert journey.geometry_kind == "actual" and len(journey.geometry.coordinates) == 392
    assert journey.geometry.coordinates == list(geometries[0].physical_coordinates)
    numeric = [score for score in result.scores if score.value is not None]
    assert len(numeric) == 2
    assert all(
        score.value == 0.4971801994143471
        and not score.ranking_eligible
        and score.completeness == "partial"
        for score in numeric
    )
    assert all(
        timeline.metric_id not in {component.id for component in score.components}
        for score in result.scores
    )
    (category,) = [score for score in numeric if score.name == "category_index"]
    assert exact.metric_id in {component.id for component in category.components}
    assert next(score for score in result.scores if score.name == "territory_index").value is None
    assert (
        sum(score.name == "point_index" and score.value is None for score in result.scores) == 215
    )
    point = next(
        feature for feature in result.map_data.features if feature.entity_id == journey.point_id
    )
    destination = next(
        feature
        for feature in result.map_data.features
        if feature.entity_id == journey.destination_object_id
    )
    assert point.population == 661.234
    assert "Храброво" in destination.name
    assert journey.geometry.coordinates[0] == point.geometry.coordinates
    assert journey.geometry.coordinates[-1] == destination.geometry.coordinates
    geometry_evidence = [
        row
        for row in result.evidence_refs
        if row.evidence_id.startswith("certified-walk-geometry-")
    ]
    assert len(geometry_evidence) == 3
    assert all("not this local proof artifact" in row.section for row in geometry_evidence)
    assert all("download.geofabrik.de" in str(row.url) for row in geometry_evidence)
