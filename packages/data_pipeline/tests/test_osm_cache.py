"""The OSM extract cache is bound to the source PBF, the rules and its table files."""

import json
from pathlib import Path

import pytest

pytest.importorskip("osmium")

from a2_data_pipeline import osm  # noqa: E402

BOUNDARY = 1
# a square city boundary with one building; .osm XML is read by the same osmium handler
CITY = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="1" lat="54.70" lon="20.40" version="1"/>
  <node id="2" lat="54.70" lon="20.50" version="1"/>
  <node id="3" lat="54.80" lon="20.50" version="1"/>
  <node id="4" lat="54.80" lon="20.40" version="1"/>
  <node id="5" lat="54.750" lon="20.450" version="1"/>
  <node id="6" lat="54.750" lon="20.451" version="1"/>
  <node id="7" lat="54.751" lon="20.451" version="1"/>
  <node id="8" lat="54.751" lon="20.450" version="1"/>
  <way id="10" version="1">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>
  </way>
  <way id="11" version="1">
    <nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/>
    <tag k="building" v="{building}"/>
  </way>
  <relation id="1" version="1">
    <member type="way" ref="10" role="outer"/>
    <tag k="type" v="boundary"/>
    <tag k="boundary" v="administrative"/>
    <tag k="admin_level" v="6"/>
    <tag k="name" v="Синтетический"/>
  </relation>
</osm>
"""


def write_pbf(path: Path, building: str = "yes") -> Path:
    path.write_text(CITY.format(building=building), encoding="utf-8")
    return path


@pytest.fixture
def extracted(tmp_path: Path) -> tuple[Path, str]:
    pbf = write_pbf(tmp_path / "city-a.osm")
    out = tmp_path / "osm"
    osm.extract(pbf, {BOUNDARY}, out, source_dataset_id="syn.city-a")
    return out, osm.file_sha256(pbf)


def test_meta_binds_the_extract_to_its_source(extracted: tuple[Path, str]) -> None:
    out, sha = extracted
    meta = json.loads((out / "extract_meta.json").read_text())
    assert meta["source"]["dataset_id"] == "syn.city-a"
    assert meta["source"]["sha256"] == sha
    assert meta["walk_rules"] == osm.WALK_RULES_VERSION
    assert meta["road_rules"] == osm.ROAD_RULES_VERSION
    assert meta["tables"] == list(osm.TABLES)
    assert all((out / f"{name}.parquet").is_file() for name in osm.TABLES)


def test_matching_extract_is_current(extracted: tuple[Path, str]) -> None:
    out, sha = extracted
    assert osm.extract_is_current(out, {BOUNDARY}, source_sha256=sha)


def test_same_city_other_pbf_is_not_current(tmp_path: Path, extracted: tuple[Path, str]) -> None:
    out, sha = extracted
    other = write_pbf(tmp_path / "city-b.osm", building="house")  # same boundaries and boxes
    other_sha = osm.file_sha256(other)
    assert other_sha != sha
    assert not osm.extract_is_current(out, {BOUNDARY}, source_sha256=other_sha)


def test_missing_table_is_not_current(extracted: tuple[Path, str]) -> None:
    out, sha = extracted
    (out / "stops.parquet").unlink()
    assert not osm.extract_is_current(out, {BOUNDARY}, source_sha256=sha)


def test_other_rules_or_boxes_are_not_current(
    extracted: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    out, sha = extracted
    assert not osm.extract_is_current(out, {BOUNDARY}, (20.0, 54.0, 21.0, 55.0), source_sha256=sha)
    monkeypatch.setattr(osm, "ROAD_RULES_VERSION", "osm-road-network-v2")
    assert not osm.extract_is_current(out, {BOUNDARY}, source_sha256=sha)


def test_meta_without_source_is_not_current(extracted: tuple[Path, str]) -> None:
    """An extract written before the source binding (EXTRACT_VERSION 9) is stale."""

    out, sha = extracted
    meta = json.loads((out / "extract_meta.json").read_text())
    del meta["source"], meta["tables"]
    (out / "extract_meta.json").write_text(json.dumps(meta))
    assert not osm.extract_is_current(out, {BOUNDARY}, source_sha256=sha)


def test_extract_refuses_an_unexpected_pbf(tmp_path: Path, extracted: tuple[Path, str]) -> None:
    out, sha = extracted
    other = write_pbf(tmp_path / "city-b.osm", building="house")
    with pytest.raises(ValueError, match="sha256"):
        osm.extract(other, {BOUNDARY}, out, source_dataset_id="syn.city-b", expected_sha256=sha)
    # the cached extract of city-a is untouched by the refused run
    assert osm.extract_is_current(out, {BOUNDARY}, source_sha256=sha)
