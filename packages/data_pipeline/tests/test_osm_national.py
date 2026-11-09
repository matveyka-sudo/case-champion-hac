"""National OSM pass on a small synthetic .osm file."""

import json
from pathlib import Path

import pytest

pytest.importorskip("osmium")
pytest.importorskip("shapely")
pytest.importorskip("pyarrow")

from a2_data_pipeline import osm_national  # noqa: E402

SQUARE = """
  <node id="1" lat="54.70" lon="20.40" version="1"/>
  <node id="2" lat="54.70" lon="20.50" version="1"/>
  <node id="3" lat="54.80" lon="20.50" version="1"/>
  <node id="4" lat="54.80" lon="20.40" version="1"/>
  <way id="10" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/></way>
  <way id="11" version="1"><nd ref="3"/><nd ref="4"/><nd ref="1"/></way>
"""
CITY = f"""<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
{SQUARE}
  <node id="5" lat="54.75" lon="20.45" version="1">
    <tag k="amenity" v="bus_station"/><tag k="name" v="Автовокзал"/>
  </node>
  <node id="6" lat="54.71" lon="20.41" version="1"/>
  <node id="7" lat="54.71" lon="20.42" version="1"/>
  <node id="8" lat="54.72" lon="20.42" version="1"/>
  <way id="12" version="1">
    <nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="6"/>
    <tag k="landuse" v="harbour"/><tag k="name" v="Порт"/>
  </way>
  <way id="13" version="1"><nd ref="1"/><nd ref="2"/></way>
  <relation id="100" version="1">
    <member type="way" ref="10" role="outer"/><member type="way" ref="11" role="outer"/>
    <tag k="type" v="multipolygon"/><tag k="place" v="city"/><tag k="wikidata" v="Q10"/>
    <tag k="name" v="Синтетический"/>
  </relation>
  <relation id="101" version="1">
    <member type="way" ref="13" role="outer"/>
    <tag k="type" v="boundary"/><tag k="boundary" v="administrative"/><tag k="wikidata" v="Q20"/>
  </relation>
  <relation id="102" version="1">
    <member type="way" ref="10" role="outer"/><member type="way" ref="11" role="outer"/>
    <tag k="type" v="multipolygon"/><tag k="place" v="city"/><tag k="wikidata" v="Q99"/>
  </relation>
</osm>
"""


def test_outlines_stations_and_ports(tmp_path: Path) -> None:
    pbf = tmp_path / "country.osm"
    pbf.write_text(CITY, encoding="utf-8")
    out = tmp_path / "national"
    report = osm_national.extract_national(pbf, {"Q10", "Q20"}, out, source_dataset_id="syn.ru")
    # Q99 is not a directory city; the Q20 boundary does not close and is reported
    assert report["counts"] == {"city_boundaries": 1, "bus_stations": 1, "port_areas": 1}
    assert report["unclosed"] == ["relation/101"]
    outline = osm_national.load_table(out, "city_boundaries")[0]
    assert (outline["osm_ref"], outline["city_wikidata"]) == ("relation/100", "Q10")
    sha = osm_national.file_sha256(pbf)
    assert osm_national.national_is_current(out, {"Q10", "Q20"}, source_sha256=sha)
    assert not osm_national.national_is_current(out, {"Q10"}, source_sha256=sha)
    assert not osm_national.national_is_current(out, {"Q10", "Q20"}, source_sha256="0" * 64)
    meta = json.loads((out / "extract_meta.json").read_text())
    assert meta["source"]["dataset_id"] == "syn.ru"
    (out / "port_areas.parquet").unlink()
    assert not osm_national.national_is_current(out, {"Q10", "Q20"}, source_sha256=sha)
