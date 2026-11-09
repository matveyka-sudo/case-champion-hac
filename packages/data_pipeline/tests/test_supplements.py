"""Walk connector rules on a synthetic bundle and synthetic obstacle layers."""

from pathlib import Path
from typing import Any

import pytest

shapely = pytest.importorskip("shapely")
pytest.importorskip("pyproj")
pytest.importorskip("pyarrow")
pytest.importorskip("osmium")

from a2_data_pipeline import supplements  # noqa: E402
from a2_data_pipeline.bundle import WalkConnector  # noqa: E402
from a2_data_pipeline.geo import to_crs  # noqa: E402
from a2_data_pipeline.store import load_bundle, publish_bundle  # noqa: E402
from synthetic_bundle import EVIDENCE, QA, collections, destination, manifest  # noqa: E402

CRS = "EPSG:32634"


def utm(geometry: Any) -> Any:
    return to_crs(geometry, "EPSG:4326", CRS)


def layers(**extra: list[tuple[str, Any]]) -> dict[str, supplements.Layer]:
    result = {}
    for name in ("building", "water", "railway", "motorway"):
        rows = extra.get(name, [])
        result[name] = supplements.Layer([utm(g) for _, g in rows], [r for r, _ in rows])
    return result


def bundle(tmp_path: Path, **parts: Any) -> Any:
    rows = collections(
        destinations=[
            destination("mall-1", "shopping_center", lon=20.006, lat=54.004),
            destination("far-1", "shopping_center", lon=20.03, lat=54.03),
        ],
        **parts,
    )
    publish_bundle(manifest(), rows, QA, root=tmp_path)
    return load_bundle("synthetic-test-v1", root=tmp_path)


def test_connector_crossings_and_own_building(tmp_path: Path) -> None:
    data = bundle(tmp_path)
    own = shapely.box(20.0048, 54.0048, 20.0052, 54.0052)  # holds the point, its own building
    pond = shapely.box(20.0049, 54.0039, 20.0051, 54.0041)  # lies across the mall's link only
    connectors = supplements.build_connectors(
        data, layers(building=[("way/2", own)], water=[("way/99", pond)]), CRS, EVIDENCE
    )
    by_id = {c.physical_node_id: c for c in connectors}
    point = by_id["physical:point:p-c1"]
    assert (point.traversable, point.reasons) == (True, [])  # its own building is skipped
    mall = by_id["physical:destination:mall-1"]
    assert (mall.traversable, mall.reasons) == (None, ["CROSSES_WATER"])
    far = by_id["physical:destination:far-1"]
    assert far.traversable is None and "LONGER_THAN_500M" in far.reasons
    assert 120 < point.length_m < 140


def test_connector_model_rules() -> None:
    fields = {
        "physical_node_id": "physical:point:p1",
        "entity_kind": "point",
        "entity_id": "p1",
        "access_node_id": "walk:way:1:0.0",
        "length_m": 10.0,
        "evidence_ids": [EVIDENCE],
    }
    WalkConnector(**fields, traversable=True, reasons=[])
    with pytest.raises(ValueError, match="reasons"):
        WalkConnector(**fields, traversable=None, reasons=[])
    with pytest.raises(ValueError):
        WalkConnector(**fields, traversable=False, reasons=["X"])  # never impassable
    with pytest.raises(ValueError, match="physical"):
        WalkConnector(
            **(fields | {"physical_node_id": "physical:destination:p1"}),
            traversable=True,
            reasons=[],
        )


def test_connectors_for_ml1_use_the_profile_speed(tmp_path: Path) -> None:
    data = bundle(tmp_path)
    connectors = supplements.build_connectors(data, layers(), CRS, EVIDENCE)
    rows = collections(
        destinations=[
            destination("mall-1", "shopping_center"),
            destination("far-1", "shopping_center", lon=20.03, lat=54.03),
        ],
        walk_connectors=connectors,
    )
    publish_bundle(manifest("with-connectors-v1"), rows, QA, root=tmp_path)
    with pytest.raises(ValueError, match="walking speed"):
        supplements.walk_connectors(
            "with-connectors-v1",
            walking_speed_kmh=5.0,
            speed_evidence_refs=[],
            profile_version="p",
            root=tmp_path,
        )
    out = supplements.walk_connectors(
        "with-connectors-v1",
        walking_speed_kmh=3.6,
        speed_evidence_refs=["build:test"],
        profile_version="valhalla-test",
        root=tmp_path,
    )
    by_id = {r["physical_node_id"]: r for r in out}
    point = by_id["physical:point:p-c1"]
    length = next(c.length_m for c in connectors if c.entity_id == "p-c1")
    assert point["seconds"] == pytest.approx(length, abs=0.1)  # 3.6 km/h = 1 m/s
    assert by_id["physical:destination:far-1"]["seconds"] is None  # unknown stays unknown
    engine = pytest.importorskip("a2_engine.urban.valhalla.models")
    model = engine.WalkConnector(
        **{
            **point,
            "physical_location": engine.WalkCoordinate(**point["physical_location"]),
            "access_location": engine.WalkCoordinate(**point["access_location"]),
            "evidence_refs": tuple(point["evidence_refs"]),
        }
    )
    assert model.confirmed
