"""Read-only Kaliningrad audit of school quarantine and unknown walk links.

An audit is research evidence, never an accepted bundle. It does not infer public
ownership from an OSM name, a nearby municipal school or a geometrical overlap.
The original connector rules and tolerances are reused without modification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import osmium
import shapely  # type: ignore[import-untyped]
from pyproj import Transformer

from .geo import to_crs
from .klgd import ENCODING, parse_list
from .osm import ensure_extract, file_sha256
from .store import load_bundle
from .supplements import (
    GATE_TOLERANCE_M,
    GATES,
    LINEAR_BARRIERS,
    RAILWAYS,
    REASON,
    barrier_layer,
    gate_access,
    obstacle_layers,
)


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class SourceRows(osmium.SimpleHandler):
    """Copy OSM facts while handler objects and their locations remain valid."""

    def __init__(self, wanted: set[str], crs: str) -> None:
        super().__init__()
        self.wanted = wanted
        self.crs = crs
        self.transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        self.rows: dict[str, dict[str, Any]] = {}
        self.lines: dict[str, list[dict[str, Any]]] = {"barrier": [], "railway": []}
        self.gates: dict[int, dict[str, Any]] = {}

    def node(self, node: Any) -> None:
        ref = f"node/{node.id}"
        if ref not in self.wanted and node.tags.get("barrier") not in GATES:
            return
        tags = dict(node.tags)
        if ref in self.wanted or tags.get("barrier") in GATES:
            row = {
                "osm_ref": ref,
                "tags": tags,
                "coordinates": [node.location.lon, node.location.lat],
            }
            if ref in self.wanted:
                self.rows[ref] = row
            if tags.get("barrier") in GATES:
                self.gates[node.id] = row

    def way(self, way: Any) -> None:
        ref = f"way/{way.id}"
        barrier = way.tags.get("barrier") in LINEAR_BARRIERS
        railway = (
            way.tags.get("railway") in RAILWAYS
            and way.tags.get("tunnel", "no") == "no"
            and way.tags.get("bridge", "no") == "no"
        )
        if ref not in self.wanted and not barrier and not railway:
            return
        tags = dict(way.tags)
        points = [[n.lon, n.lat] for n in way.nodes if n.location.valid()]
        row = {"osm_ref": ref, "tags": tags, "node_refs": [n.ref for n in way.nodes]}
        row["coordinates"] = points
        if ref in self.wanted:
            self.rows[ref] = row
        if len(points) >= 2 and (barrier or railway):
            item = {
                "source": row,
                "geometry": shapely.transform(
                    shapely.LineString(points), self.transformer.transform, interleaved=False
                ),
            }
            if barrier:
                self.lines["barrier"].append(item)
            if railway:
                self.lines["railway"].append(item)

    def relation(self, relation: Any) -> None:
        ref = f"relation/{relation.id}"
        if ref in self.wanted:
            self.rows[ref] = {
                "osm_ref": ref,
                "tags": dict(relation.tags),
                "members": [
                    {"type": m.type, "ref": m.ref, "role": m.role} for m in relation.members
                ],
            }


def _source(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "osm_ref": row["osm_ref"],
        "url": "https://www.openstreetmap.org/" + row["osm_ref"],
        "tags": {
            k: v
            for k, v in row["tags"].items()
            if k not in {"phone", "email", "fax"} and not k.startswith("contact:")
        },
        "record_sha256": _hash(row),
    }


def audit(
    bundle_id: str,
    pbf: Path,
    extract_root: Path,
    official_list: Path,
) -> dict[str, Any]:
    data = load_bundle(bundle_id)
    if {t.territory_id for t in data.territories if t.level == "city"} != {"kaliningrad"}:
        raise ValueError("This audit's boundary IDs and CRS are specific to Kaliningrad")
    version = next(v for v in data.manifest.source_versions if v.source_id == "osm_geofabrik")
    actual_sha = file_sha256(pbf)
    if actual_sha != version.checksum.value:
        raise ValueError("Audit PBF does not match bundle source checksum")
    crs = "EPSG:32634"
    extract = ensure_extract(
        pbf,
        {331414, 331411, 331412, 331413},
        extract_root,
        source_dataset_id=version.dataset_id,
        expected_sha256=actual_sha,
    )
    layers = obstacle_layers(extract, pbf, None, crs)
    barriers = barrier_layer(pbf, None, crs)
    schools = [d for d in data.destinations if d.category == "school"]
    accepted = [d for d in schools if d.status == "accepted"]
    quarantined = [d for d in schools if d.status == "quarantined"]
    accepted_ids = {d.destination_id for d in accepted}
    links = [
        c
        for c in data.walk_connectors
        if c.traversable is None and (c.entity_kind == "point" or c.entity_id in accepted_ids)
    ]
    entities: dict[str, Any] = {p.point_id: p for p in data.sample_points}
    entities.update({d.destination_id: d for d in schools})
    nodes = {n.access_node_id: n for n in data.access_nodes}
    wanted = {d.osm_ref for d in quarantined if d.osm_ref}
    pending: list[tuple[Any, Any, set[str], dict[str, list[str]]]] = []
    for link in links:
        entity = entities[link.entity_id]
        access = nodes[link.access_node_id]
        start = to_crs(shapely.Point(entity.lon, entity.lat), "EPSG:4326", crs)
        end = to_crs(shapely.Point(access.lon, access.lat), "EPSG:4326", crs)
        segment = shapely.LineString([start, end])
        own_ref = getattr(entity, "building_ref", getattr(entity, "osm_ref", None))
        skip = {own_ref} if own_ref else set()
        skip |= {
            layers["building"].refs[int(i)]
            for i in layers["building"].tree.query(start, predicate="intersects")
        }
        crossed = {
            name: [
                layer.refs[int(i)]
                for i in layer.tree.query(segment, predicate="intersects")
                if name != "building" or layer.refs[int(i)] not in skip
            ]
            for name, layer in layers.items()
            if name != "railway"
        }
        wanted.update(ref for refs in crossed.values() for ref in refs)
        pending.append((link, segment, skip, crossed))
    raw = SourceRows(wanted, crs)
    raw.apply_file(str(pbf), locations=True)
    connector_rows = []
    for link, segment, skip, crossed in pending:
        obstacles = []
        reproduced = [REASON[name] for name, refs in crossed.items() if refs]
        for name, refs in crossed.items():
            for ref in refs:
                obstacles.append({"kind": name, **_source(raw.rows[ref])})
        for kind in ("railway", "barrier"):
            for item in raw.lines[kind]:
                if not segment.intersects(item["geometry"]):
                    continue
                source = item["source"]
                obstacle = {"kind": kind, **_source(source)}
                if kind == "railway":
                    if "CROSSES_RAILWAY" not in reproduced:
                        reproduced.append("CROSSES_RAILWAY")
                else:
                    crossing = segment.intersection(item["geometry"])
                    obstacle["gates_on_barrier"] = [
                        {
                            **_source(raw.gates[n]),
                            "access_state": gate_access(raw.gates[n]["tags"]),
                            "distance_to_crossing_m": round(
                                float(
                                    to_crs(
                                        shapely.Point(raw.gates[n]["coordinates"]), "EPSG:4326", crs
                                    ).distance(crossing)
                                ),
                                3,
                            ),
                        }
                        for n in source["node_refs"]
                        if n in raw.gates
                    ]
                obstacles.append(obstacle)
        reproduced.extend(barriers.reasons(segment))
        if round(float(segment.length), 1) > 500:
            reproduced.append("LONGER_THAN_500M")
        connector_rows.append(
            {
                **link.model_dump(mode="json"),
                "physical_coordinates": [
                    entities[link.entity_id].lon,
                    entities[link.entity_id].lat,
                ],
                "access_coordinates": [
                    nodes[link.access_node_id].lon,
                    nodes[link.access_node_id].lat,
                ],
                "skipped_own_or_holding_buildings": sorted(skip),
                "reproduced_reasons": sorted(set(reproduced)),
                "reason_set_matches": set(reproduced) == set(link.reasons),
                "obstacles": sorted(obstacles, key=lambda o: (o["kind"], o["osm_ref"])),
                "resolution": "unknown; requires a source-backed physical approach, no straight-line repair assumed",
            }
        )
    roster = parse_list(official_list.read_bytes().decode(ENCODING), "school")
    accepted_points = {
        d.destination_id: to_crs(shapely.Point(d.lon, d.lat), "EPSG:4326", crs) for d in accepted
    }
    school_rows = []
    for school in quarantined:
        source = raw.rows[str(school.osm_ref)]
        point = to_crs(shapely.Point(school.lon, school.lat), "EPSG:4326", crs)
        number = re.search(r"№\s*(\d+)", school.name or "")
        candidates = [
            asdict(r) for r in roster if number and re.search(r"№\s*" + number[1] + r"\b", r.name)
        ]
        nearest = sorted(accepted, key=lambda d: point.distance(accepted_points[d.destination_id]))[
            :3
        ]
        school_rows.append(
            {
                **school.model_dump(mode="json"),
                "osm_source": _source(source),
                "municipal_roster_number_candidates": candidates,
                "candidate_match_is_identity_proof": False,
                "nearest_accepted_schools": [
                    {
                        "destination_id": d.destination_id,
                        "name": d.name,
                        "osm_ref": d.osm_ref,
                        "point_distance_m": round(
                            float(point.distance(accepted_points[d.destination_id])), 2
                        ),
                    }
                    for d in nearest
                ],
                "review_status": "unresolved; retain original quarantine pending institution and physical-site identity evidence",
            }
        )
    excluded = [c for c in data.population_cells if c.status == "excluded"]
    return {
        "schema": "a2-school-quarantine-connector-audit-v1",
        "acceptance_state": "research_only",
        "bundle_id": bundle_id,
        "pbf_sha256": actual_sha,
        "official_municipal_roster_sha256": file_sha256(official_list),
        "official_municipal_roster_url": "https://www.klgd.ru/activity/education/institutions/schools/index.php?SHOWALL_1=1",
        "gate_tolerance_m_unchanged": GATE_TOLERANCE_M,
        "summary": {
            "quarantined_schools": len(school_rows),
            "unnamed_schools": sum(s.name is None for s in quarantined),
            "unknown_point_connectors": sum(c.entity_kind == "point" for c in links),
            "unknown_accepted_school_connectors": sum(
                c.entity_kind == "destination" for c in links
            ),
            "reason_sets_reproduced": sum(r["reason_set_matches"] for r in connector_rows),
            "connector_reason_counts": dict(
                sorted(Counter(r for c in links for r in c.reasons).items())
            ),
            "excluded_population_cells": len(excluded),
            "excluded_population": sum(c.population or 0 for c in excluded),
            "official_municipal_institutions": len(roster),
        },
        "coverage_decision": {
            "audit_publishes_bundles": False,
            "school_catalog_complete": False,
            "population_sample_complete": False,
            "reason": "Municipal institution list does not close regional/federal public-school physical-site roster; excluded populated cells and unknown physical approaches remain.",
        },
        "quarantined_schools": school_rows,
        "unknown_connectors": connector_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--extract-root", type=Path, required=True)
    parser.add_argument("--official-list", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.bundle_id, args.pbf, args.extract_root, args.official_list)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
