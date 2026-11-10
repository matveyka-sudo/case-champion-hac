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


def fence_file(
    tmp_path: Path, gate_tags: dict[str, str] | None, fence_tags: dict[str, str]
) -> Path:
    """A fence across the point's link (20.0045, 54.0045), optionally with a gate there."""

    def tags(values: dict[str, str]) -> str:
        return "".join(f'<tag k="{k}" v="{v}"/>' for k, v in values.items())

    gate = (
        f'<node id="3" lat="54.0045" lon="20.0045" version="1">{tags(gate_tags)}</node>'
        if gate_tags is not None
        else ""
    )
    refs = '<nd ref="1"/>' + ('<nd ref="3"/>' if gate_tags is not None else "") + '<nd ref="2"/>'
    path = tmp_path / "fence.osm"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><osm version="0.6">'
        '<node id="1" lat="54.0048" lon="20.0042" version="1"/>'
        '<node id="2" lat="54.0042" lon="20.0048" version="1"/>'
        f"{gate}"
        f'<way id="10" version="1">{refs}{tags(fence_tags)}</way>'
        "</osm>",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("gate", "fence", "traversable", "reasons"),
    [
        (None, {"barrier": "fence", "foot": "no"}, None, ["CROSSES_BARRIER"]),  # a closed fence
        ({"barrier": "gate", "foot": "yes"}, {"barrier": "fence"}, True, []),  # an open gate
        ({"barrier": "gate"}, {"barrier": "fence"}, None, ["GATE_ACCESS_UNKNOWN"]),  # unknown
        (
            {"barrier": "gate", "access": "private"},
            {"barrier": "fence"},
            None,
            ["CROSSES_CLOSED_GATE"],
        ),
        ({"barrier": "lift_gate", "access": "yes"}, {"barrier": "wall"}, True, []),
    ],
)
def test_barriers_and_gates(
    tmp_path: Path,
    gate: dict[str, str] | None,
    fence: dict[str, str],
    traversable: bool | None,
    reasons: list[str],
) -> None:
    data = bundle(tmp_path)
    barriers = supplements.barrier_layer(fence_file(tmp_path, gate, fence), None, CRS)
    connectors = supplements.build_connectors(data, layers(), CRS, EVIDENCE, barriers)
    point = next(c for c in connectors if c.physical_node_id == "physical:point:p-c1")
    assert (point.traversable, point.reasons) == (traversable, reasons)
    mall = next(c for c in connectors if c.physical_node_id == "physical:destination:mall-1")
    assert mall.traversable is True  # the fence is not on the mall's link


def test_closed_fence_is_not_confirmed_by_ml1(tmp_path: Path) -> None:
    data = bundle(tmp_path)
    barriers = supplements.barrier_layer(
        fence_file(tmp_path, None, {"barrier": "fence", "foot": "no"}), None, CRS
    )
    connectors = supplements.build_connectors(data, layers(), CRS, EVIDENCE, barriers)
    rows = collections(
        destinations=[
            destination("mall-1", "shopping_center", lon=20.006, lat=54.004),
            destination("far-1", "shopping_center", lon=20.03, lat=54.03),
        ],
        walk_connectors=connectors,
    )
    publish_bundle(manifest("fenced-v1"), rows, QA, root=tmp_path)
    out = supplements.walk_connectors(
        "fenced-v1",
        walking_speed_kmh=5.0,
        speed_evidence_refs=["build:test"],
        profile_version="valhalla-test",
        root=tmp_path,
    )
    point = next(r for r in out if r["physical_node_id"] == "physical:point:p-c1")
    assert point["traversable"] is None and point["seconds"] is None
    engine = pytest.importorskip("a2_engine.urban.valhalla.models")
    model = engine.WalkConnector(
        **{
            **point,
            "physical_location": engine.WalkCoordinate(**point["physical_location"]),
            "access_location": engine.WalkCoordinate(**point["access_location"]),
            "evidence_refs": tuple(point["evidence_refs"]),
        }
    )
    assert not model.confirmed


def test_gate_access_rules() -> None:
    assert supplements.gate_access({"barrier": "gate"}) == "unknown"
    assert supplements.gate_access({"barrier": "gate", "foot": "permissive"}) == "open"
    assert supplements.gate_access({"barrier": "gate", "access": "yes", "foot": "no"}) == "closed"
    assert supplements.gate_access({"barrier": "gate", "foot": "yes", "locked": "yes"}) == "closed"
    assert supplements.gate_access({"barrier": "gate", "access": "private"}) == "closed"
