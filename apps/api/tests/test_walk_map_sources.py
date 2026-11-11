"""Real immutable polygon projection; no routes or numerical indices fabricated."""

from copy import deepcopy

import pytest
from a2_api.adapters.walk_map_sources import validate_walk_map_features
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_data_pipeline.contract_v2 import to_accepted_data_v2
from a2_data_pipeline.store import load_bundle


@pytest.fixture(scope="module")
def source():
    bundle = load_bundle("moscow-urban-2026-11-10.4")
    snapshot = AcceptedData.model_validate(to_accepted_data_v2(bundle))
    document = {
        "schema_version": "waydex-walk-map-sources/1",
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": canonical_hash(snapshot.model_dump(mode="json")),
        "bundle_manifest_hash": canonical_hash(bundle.manifest.model_dump(mode="json")),
        "city_id": "moscow",
        "cell_ids": [cell.cell_id for cell in snapshot.population_cells],
        "territory_ids": [territory.territory_id for territory in bundle.territories],
    }
    return bundle, snapshot, document


def test_real_polygons_preserve_source_population_and_excluded_cells(source):
    bundle, snapshot, document = source
    features = validate_walk_map_features(document, snapshot=snapshot, bundle=bundle)
    cells = {row.entity_id: row for row in features if row.entity_kind == "cell"}
    territories = [row for row in features if row.entity_kind == "territory"]
    assert len(cells) == 2931 and len(territories) == 13
    assert {row.geometry.type for row in cells.values()} == {"Polygon", "MultiPolygon"}
    for cell in snapshot.population_cells:
        actual = cells[cell.cell_id]
        assert actual.population == cell.population
        assert actual.reason_code == cell.exclusion_reason
        assert actual.geometry_kind == "actual"
    source_cells = {row.cell_id: row for row in bundle.population_cells}
    assert all(
        row.geometry.model_dump(mode="json") == source_cells[key].geometry
        for key, row in cells.items()
    )
    assert any(row.reason_code and row.population > 0 for row in cells.values())
    assert all(row.population is None for row in territories)


@pytest.mark.parametrize(
    "attack", ["snapshot", "manifest", "city", "duplicate", "foreign", "coordinates"]
)
def test_wrong_source_and_injected_geometry_rejected(source, attack):
    bundle, snapshot, original = source
    document = deepcopy(original)
    if attack == "snapshot":
        document["snapshot_hash"] = "wrong"
    elif attack == "manifest":
        document["bundle_manifest_hash"] = "wrong"
    elif attack == "city":
        document["city_id"] = "novorossiysk"
    elif attack == "duplicate":
        document["cell_ids"].append(document["cell_ids"][0])
    elif attack == "foreign":
        document["cell_ids"].append("TEST_ONLY_foreign_cell")
    else:
        document["geometry"] = {"type": "Polygon", "coordinates": []}
    with pytest.raises(ValueError):
        validate_walk_map_features(document, snapshot=snapshot, bundle=bundle)


def test_accepted_population_cannot_be_overwritten_from_a_different_projection(source):
    bundle, snapshot, original = source
    cell = snapshot.population_cells[0]
    changed = snapshot.model_copy(
        update={
            "population_cells": [
                cell.model_copy(update={"population": 999.0}),
                *snapshot.population_cells[1:],
            ]
        }
    )
    document = {**original, "snapshot_hash": canonical_hash(changed.model_dump(mode="json"))}
    with pytest.raises(ValueError, match="CELL_SOURCE_MISMATCH"):
        validate_walk_map_features(document, snapshot=changed, bundle=bundle)
