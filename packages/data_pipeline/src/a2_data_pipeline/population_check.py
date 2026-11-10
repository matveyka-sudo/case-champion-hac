"""Cross-check of bundle population with Rosstat (rule set ``population-rosstat-check-v1``).

A bundle's population is a GHSL model estimate. Rosstat's municipal tables are not
reachable from this environment (TLS on a Russian CA), so the official figure is
taken from Wikidata (CC0) statements whose reference is a Rosstat publication:
P248 «stated in» an item published (P123) by Rosstat (Q2624680).

The territory is the one the bundle's boundary relation names in its own OSM
``wikidata`` tag, read from the raw PBF the bundle was built from (its sha256 is
checked against the dataset manifest): no matching by name. A boundary item
without a Rosstat statement stays unknown; another item (the city inside an
okrug) is never used in its place.

The result is a QA report, not a correction: published bundles stay as they are.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import territories
from .manifest import load_dataset_manifest
from .paths import data_dir, raw_dir
from .store import load_bundle, sha256_file

RULES_VERSION = "population-rosstat-check-v1"


def file_sha256(path: Path) -> str:
    return sha256_file(path).value


ROSSTAT = "Q2624680"
# a model estimate within 10 % of the official figure is plausible for an index weight
TOLERANCE = 0.10
QUERY = """SELECT ?o ?pop ?date ?method ?stated ?statedLabel ?publisher WHERE {
  VALUES ?o { ITEMS }
  ?o p:P1082 ?st . ?st ps:P1082 ?pop ; wikibase:rank ?rank .
  FILTER(?rank != wikibase:DeprecatedRank)
  OPTIONAL { ?st pq:P585 ?date }
  OPTIONAL { ?st pq:P459 ?method }
  OPTIONAL { ?st prov:wasDerivedFrom ?ref . ?ref pr:P248 ?stated .
             OPTIONAL { ?stated wdt:P123 ?publisher } }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "ru" } }"""


def _boundary(bundle_id: str) -> tuple[str, int, Path, str]:
    """Dataset id, relation id, raw PBF and its verified sha256 of a bundle's city boundary."""

    data = load_bundle(bundle_id)
    city = next(t for t in data.territories if t.level == "city")
    dataset_id, ref = city.boundary_version.split(":", 1)
    kind, relation = ref.split("/")
    if kind != "relation":
        raise ValueError(f"{bundle_id}: boundary {ref} is not a relation")
    manifest = load_dataset_manifest(dataset_id)
    pbf = raw_dir() / manifest.raw_path
    sha = file_sha256(pbf)
    if sha != manifest.checksum.value:
        raise ValueError(f"{dataset_id}: raw sha256 differs from the manifest")
    return dataset_id, int(relation), pbf, sha


def boundary_wikidata(pbf: Path, relation_id: int) -> str | None:
    """The ``wikidata`` tag of one relation, read straight from the PBF."""

    import osmium

    processor = osmium.FileProcessor(str(pbf), osmium.osm.RELATION).with_filter(
        osmium.filter.IdFilter([relation_id])
    )
    for relation in processor:
        if relation.id == relation_id:
            value = relation.tags.get("wikidata")
            return str(value) if value else None
    return None


def boundary_items(bundle_ids: list[str]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for bundle_id in bundle_ids:
        dataset_id, relation, pbf, sha = _boundary(bundle_id)
        items[bundle_id] = {
            "osm_dataset_id": dataset_id,
            "osm_sha256": sha,
            "boundary_relation": relation,
            "wikidata_id": boundary_wikidata(pbf, relation),
        }
    return items


def fetch(target: Path, qids: list[str]) -> Path:
    query = QUERY.replace("ITEMS", " ".join(f"wd:{q}" for q in sorted(set(qids))))
    return territories.fetch(target, {"population": query})


def rosstat_figures(raw_zip: Path) -> dict[str, dict[str, Any]]:
    """Latest population per item whose reference is a Rosstat publication."""

    with zipfile.ZipFile(raw_zip) as archive:
        rows = territories._bindings(archive, "population")
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("publisher", {}).get("value", "").rsplit("/", 1)[-1] != ROSSTAT:
            continue
        value = row["pop"]["value"]
        if not value.replace(".", "", 1).isdigit():  # «unknown value» is a blank node
            continue
        qid = territories._qid(row["o"]["value"])
        figure = {
            "population": int(float(value)),
            "date": row.get("date", {}).get("value", "")[:10] or None,
            "publication_id": territories._qid(row["stated"]["value"]),
            "publication": row.get("statedLabel", {}).get("value"),
            "method_id": territories._qid(row["method"]["value"]) if "method" in row else None,
        }
        current = best.get(qid)
        key = (figure["date"] or "", figure["publication_id"])
        if current is None or key > (current["date"] or "", current["publication_id"]):
            best[qid] = figure
    return best


def crosscheck(bundle_ids: list[str], dataset_id: str) -> dict[str, Any]:
    manifest = load_dataset_manifest(dataset_id)
    raw_zip = raw_dir() / manifest.raw_path
    if file_sha256(raw_zip) != manifest.checksum.value:
        raise ValueError(f"{dataset_id}: raw sha256 differs from the manifest")
    figures = rosstat_figures(raw_zip)
    items = boundary_items(bundle_ids)
    results = []
    for bundle_id in bundle_ids:
        data = load_bundle(bundle_id)
        cells = data.population_cells
        known = [c.population for c in cells if c.population is not None]
        model = round(sum(known))
        item = items[bundle_id]
        official = figures.get(item["wikidata_id"] or "")
        ratio: float | None = None
        reason: str | None
        if official is None:
            status, reason = "unknown", "NO_ROSSTAT_STATEMENT_FOR_BOUNDARY_ITEM"
        else:
            ratio = round(model / official["population"], 4)
            within = abs(ratio - 1) <= TOLERANCE
            status, reason = ("pass", None) if within else ("warn", "MODEL_DIFFERS_FROM_ROSSTAT")
        results.append(
            {
                "bundle_id": bundle_id,
                "city_id": next(t.territory_id for t in data.territories if t.level == "city"),
                **item,
                "model": {
                    "population": model,
                    "methods": sorted({c.population_method for c in cells if c.population_method}),
                    "years": sorted({c.population_year for c in cells if c.population_year}),
                    "cells_unknown": len(cells) - len(known),
                },
                "rosstat": official,
                "ratio_model_to_rosstat": ratio,
                "status": status,
                "reason": reason,
            }
        )
    return {
        "rules": RULES_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "tolerance": TOLERANCE,
        "source": {
            "dataset_id": dataset_id,
            "raw_sha256": manifest.checksum.value,
            "retrieved_at": manifest.retrieved_at.isoformat(),
            "license": manifest.license.name,
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_cmd = sub.add_parser("fetch")
    fetch_cmd.add_argument("raw_name")
    fetch_cmd.add_argument("bundle_ids", nargs="+")
    check_cmd = sub.add_parser("check")
    check_cmd.add_argument("dataset_id")
    check_cmd.add_argument("report_name")
    check_cmd.add_argument("bundle_ids", nargs="+")
    args = parser.parse_args()
    if args.command == "fetch":
        qids = [
            i["wikidata_id"] for i in boundary_items(args.bundle_ids).values() if i["wikidata_id"]
        ]
        print(fetch(raw_dir() / territories.SOURCE_ID / args.raw_name, qids))
        return
    report = crosscheck(args.bundle_ids, args.dataset_id)
    target = data_dir() / "manifests" / "qa" / args.report_name
    if target.exists():
        raise FileExistsError(target)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)


__all__ = ["boundary_wikidata", "crosscheck", "rosstat_figures"]

if __name__ == "__main__":
    main()
