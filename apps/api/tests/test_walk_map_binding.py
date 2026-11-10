"""TEST_ONLY optional pinned source geometry on the existing real walk binding."""

import pytest
from a2_api.adapters.product_runtime import FileRef, load_runtime
from a2_api.adapters.walk_map import attach_walk_map
from a2_api.adapters.walk_map_sources import validate_walk_map_features
from a2_contracts.common import canonical_hash
from a2_data_pipeline.store import load_bundle
from test_walk_map import calculate
from test_walk_pilot import configured as configured
from test_walk_runtime import SNAPSHOTS, city_profile, configure, write


def selection(snapshot):
    bundle = load_bundle(snapshot.snapshot_id)
    city = snapshot.geographic_scope[0]
    return dict(
        schema_version="waydex-walk-map-sources/1",
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        bundle_manifest_hash=canonical_hash(bundle.manifest.model_dump(mode="json")),
        city_id=city,
        cell_ids=[cell.cell_id for cell in snapshot.population_cells],
        territory_ids=[city],
    )


def test_optional_map_binding_loads_trusted_geometry_and_rejects_changed_bytes(
    tmp_path, monkeypatch
):
    entry = city_profile(tmp_path, SNAPSHOTS[0])
    reference = write(entry["folder"] / "map.json", selection(entry["snapshot"]))
    binding = write(
        entry["folder"] / "binding.json", entry["binding"] | {"map_features": reference}
    )
    entry["profile"] = entry["profile"].model_copy(
        update={
            "walk_binding": FileRef(
                path=entry["folder"].name + "/binding.json", sha256=binding["sha256"]
            )
        }
    )
    configure(tmp_path, monkeypatch, [entry])
    runtime = load_runtime()
    bound = runtime.walk_bindings[entry["profile"].profile_id]
    assert len(bound.map_features) == len(entry["snapshot"].population_cells) + 1
    assert all(feature.geometry_kind == "actual" for feature in bound.map_features)
    (entry["folder"] / "map.json").write_text("{}")
    assert not load_runtime().profiles


def test_attaching_source_polygons_preserves_metrics_population_and_rejects_mutation(configured):
    snapshot, calculated, before, names = calculate(configured)
    features = validate_walk_map_features(
        selection(snapshot), snapshot=snapshot, bundle=load_bundle(snapshot.snapshot_id)
    )
    result = attach_walk_map(before, snapshot, calculated, names, map_features=features)
    assert result.metrics == before.metrics and result.scores == before.scores
    cells = {cell.cell_id: cell for cell in snapshot.population_cells}
    mapped = [feature for feature in result.map_data.features if feature.entity_kind == "cell"]
    assert len(mapped) == len(cells)
    assert all(
        feature.geometry_kind == "actual"
        and feature.population == cells[feature.cell_id].population
        for feature in mapped
    )
    changed = features[0].model_copy(update={"population": 999999})
    with pytest.raises(ValueError, match="POPULATION_MISMATCH"):
        attach_walk_map(before, snapshot, calculated, names, map_features=[changed])
