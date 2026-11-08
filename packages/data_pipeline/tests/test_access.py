"""Walk network rules and access node snapping on synthetic geometry."""

from pathlib import Path

import pytest

shapely = pytest.importorskip("shapely")
pytest.importorskip("pyproj")
pytest.importorskip("rasterio")
pytest.importorskip("osmium")

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
from a2_data_pipeline.access import load_walk_network, snap  # noqa: E402
from a2_data_pipeline.geo import to_crs  # noqa: E402
from a2_data_pipeline.osm import walkable  # noqa: E402
from shapely.geometry import LineString, Point  # noqa: E402

CRS = "EPSG:32634"
X0, Y0 = 500_000, 6_060_000


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"highway": "footway"}, True),
        ({"highway": "residential"}, True),
        ({"highway": "motorway"}, False),
        ({"highway": "trunk"}, False),
        ({"highway": "trunk", "sidewalk": "both"}, True),
        ({"highway": "trunk", "foot": "yes"}, True),
        ({"highway": "primary", "foot": "use_sidepath"}, False),
        ({"highway": "service", "access": "private"}, False),
        ({"highway": "service", "access": "private", "foot": "yes"}, True),
        ({"highway": "construction"}, False),
        ({"building": "yes"}, False),
    ],
)
def test_walkable(tags: dict[str, str], expected: bool) -> None:
    assert walkable(tags) is expected


def utm_line(*points: tuple[float, float]) -> LineString:
    return to_crs(LineString([(X0 + x, Y0 + y) for x, y in points]), CRS, "EPSG:4326")


def write_network(directory: Path, ways: list[tuple[str, list[int], LineString]]) -> None:
    rows = [
        {
            "osm_ref": ref,
            "highway": "footway",
            "node_refs": nodes,
            "wkb": shapely.to_wkb(line),
            "content_hash": f"{index:064d}",
        }
        for index, (ref, nodes, line) in enumerate(ways)
    ]
    pq.write_table(pa.Table.from_pylist(rows), directory / "walk_ways.parquet")


def utm_point(x: float, y: float) -> Point:
    return Point(X0 + x, Y0 + y)


def test_snap_uses_main_component_and_breaks_ties(tmp_path: Path) -> None:
    write_network(
        tmp_path,
        [
            # main component: two connected ways of 2 km in total
            ("way/20", [1, 2], utm_line((0, 0), (1000, 0))),
            ("way/10", [2, 3], utm_line((1000, 0), (2000, 0))),
            # isolated short fragment right next to the first point
            ("way/5", [8, 9], utm_line((0, 95), (50, 95))),
            # main-component way parallel to way/20 at the same distance from (500, 100)
            ("way/30", [3, 4], utm_line((2000, 0), (2000, 200), (0, 200))),
        ],
    )
    network = load_walk_network(tmp_path, 32634, "synthetic")
    assert network.stats["components"] == 2
    assert "way/5" not in network.refs
    near_fragment, tie = snap([utm_point(10, 100), utm_point(500, 100)], network)
    assert near_fragment.way_ref == "way/20"
    assert near_fragment.distance_m == pytest.approx(100, abs=0.05)
    # (500, 100) is 100 m from way/20 and from way/30: the smaller id wins
    assert tie.way_ref == "way/20"
    assert tie.node_id == f"walk:way:20:{tie.offset_m:.1f}"
    assert tie.offset_m == pytest.approx(500, abs=0.5)
    again = snap([utm_point(500, 100)], network)[0]
    assert again == tie
