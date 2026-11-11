"""Rosstat cross-check rules on synthetic SPARQL answers and a synthetic OSM file."""

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from a2_data_pipeline import population_check

WD = "http://www.wikidata.org/entity/"


def uri(qid: str) -> dict[str, str]:
    return {"type": "uri", "value": WD + qid}


def lit(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def statement(qid: str, pop: str, date: str, publisher: str | None, stated: str = "Q900"):
    row: dict[str, Any] = {
        "o": uri(qid),
        "pop": lit(pop),
        "date": lit(f"{date}T00:00:00Z"),
        "stated": uri(stated),
        "statedLabel": lit("Численность населения по муниципальным образованиям"),
    }
    if publisher:
        row["publisher"] = uri(publisher)
    return row


ROWS = [
    statement("Q10", "1000", "2024-01-01", "Q2624680", "Q901"),
    statement("Q10", "1100", "2025-01-01", "Q2624680", "Q902"),  # the latest Rosstat figure
    statement("Q10", "9999", "2026-01-01", "Q12345"),  # a later figure of another publisher
    statement("Q10", "_:blank", "2026-06-01", "Q2624680"),  # «unknown value»
    statement("Q20", "500", "2025-01-01", None),  # no Rosstat reference at all
    statement("Q30", "2000", "2025-01-01", "Q2624680"),
]


def raw(tmp_path: Path) -> Path:
    path = tmp_path / "population.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("population.json", json.dumps({"results": {"bindings": ROWS}}))
    return path


def test_latest_rosstat_statement_wins(tmp_path: Path) -> None:
    figures = population_check.rosstat_figures(raw(tmp_path))
    assert figures["Q10"]["population"] == 1100
    assert (figures["Q10"]["date"], figures["Q10"]["publication_id"]) == ("2025-01-01", "Q902")
    assert "Q20" not in figures  # a figure without a Rosstat reference is not official


def test_statuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw_zip = raw(tmp_path)
    monkeypatch.setattr(population_check, "raw_dir", lambda: tmp_path)
    monkeypatch.setattr(
        population_check,
        "load_dataset_manifest",
        lambda _id: SimpleNamespace(
            raw_path=raw_zip.name,
            checksum=SimpleNamespace(value=population_check.file_sha256(raw_zip)),
            retrieved_at=population_check.datetime(2026, 11, 9, tzinfo=population_check.UTC),
            license=SimpleNamespace(name="CC0-1.0"),
        ),
    )
    items = {"a": "Q10", "b": "Q20", "c": "Q30"}
    monkeypatch.setattr(
        population_check,
        "boundary_items",
        lambda ids: {i: {"boundary_relation": 1, "wikidata_id": items[i]} for i in ids},
    )
    model = {"a": 1050.0, "b": 480.0, "c": 1500.0}

    def bundle(bundle_id: str) -> SimpleNamespace:
        cells = [
            SimpleNamespace(
                population=model[bundle_id], population_method="ghsl", population_year=2025
            ),
            SimpleNamespace(population=None, population_method=None, population_year=None),
        ]
        return SimpleNamespace(
            population_cells=cells,
            territories=[SimpleNamespace(level="city", territory_id=f"city-{bundle_id}")],
        )

    monkeypatch.setattr(population_check, "load_bundle", bundle)
    report = population_check.crosscheck(["a", "b", "c"], "wd.syn")
    by_id = {r["bundle_id"]: r for r in report["results"]}
    assert (by_id["a"]["status"], by_id["a"]["ratio_model_to_rosstat"]) == ("pass", 0.9545)
    assert by_id["a"]["model"]["cells_unknown"] == 1
    # no Rosstat statement for the boundary item: unknown, never another item's figure
    assert (by_id["b"]["status"], by_id["b"]["rosstat"]) == ("unknown", None)
    assert (by_id["c"]["status"], by_id["c"]["reason"]) == ("warn", "MODEL_DIFFERS_FROM_ROSSTAT")


def test_boundary_tag_is_read_from_the_pbf(tmp_path: Path) -> None:
    pytest.importorskip("osmium")
    pbf = tmp_path / "city.osm"
    pbf.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="1" lat="54.7" lon="20.4" version="1"/>
  <node id="2" lat="54.7" lon="20.5" version="1"/>
  <way id="10" version="1"><nd ref="1"/><nd ref="2"/></way>
  <relation id="7" version="1">
    <member type="way" ref="10" role="outer"/>
    <tag k="type" v="boundary"/><tag k="wikidata" v="Q27554528"/>
  </relation>
  <relation id="8" version="1"><member type="way" ref="10" role="outer"/></relation>
</osm>
""",
        encoding="utf-8",
    )
    assert population_check.boundary_wikidata(pbf, 7) == "Q27554528"
    assert population_check.boundary_wikidata(pbf, 8) is None
