"""Two reviewed municipal campuses joined by exact addresses and full containment.

The founder roster assigns the institution and campus address. An addressed OSM
building lies wholly inside the pinned school-site polygon. Neither proximity nor
the old OSM school/campus label establishes ownership. Approaches are unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import osmium
import shapely  # type: ignore[import-untyped]

from .bundle import BundleData, CategoryOwnership, Evidence, SourceVersion
from .contract_v2 import to_accepted_data_v2
from .klgd import ENCODING, parse_list
from .manifest import DatasetManifest, load_dataset_manifest, write_dataset_manifest
from .paths import data_dir, raw_dir
from .school_audit import _hash
from .store import (
    BUNDLE_COLLECTIONS,
    OPTIONAL_COLLECTIONS,
    _dump,
    load_bundle,
    publish_bundle,
    sha256_file,
)

BASE_BUNDLE = "kaliningrad-urban-2026-11-10.3"
NEXT_BUNDLE = "kaliningrad-urban-2026-11-10.4"
SOURCE_ID = "klgd_school_campuses"
DATASET_ID = "klgd_school_campuses.20261110"
ROSTER_SHA256 = "89808fa312acc81f470a39681c655e1c2f48aa345e6841b1ea2142efaaa54e2f"
PROTECTED = (
    "territories",
    "population_cells",
    "sample_points",
    "access_nodes",
    "walk_connectors",
    "freight_sites",
    "freight_nodes",
    "stop_frequencies",
)


@dataclass(frozen=True)
class Campus:
    institution_id: str
    institution_name: str
    destination_id: str
    site_way: int
    building_way: int
    street: str
    house: str
    founder_address_fragment: str
    site_record_sha256: str


CAMPUSES = (
    Campus(
        "4409878",
        "МАОУ СОШ № 12",
        "kaliningrad-school-way-90805889",
        90805889,
        90805877,
        "улица Багратиона",
        "107А",
        "ул. Багратиона, 107А (корпус 2)",
        "fdd87a4be88ed5f8f13298581d35a831d5d2faeb5f91d4f830796d9b4f0d39a3",
    ),
    Campus(
        "4409869",
        "МАОУ СОШ № 3",
        "kaliningrad-school-way-91240747",
        91240747,
        91240750,
        "Берёзовая улица",
        "2",
        "ул. Березовая, 2 (корпус 2)",
        "352cb0dbf96abe9e58c0125242def05d60a1019714576b4a40ad7aa5f61f532b",
    ),
)


def verify_geometry(
    site: dict[str, Any], building: dict[str, Any], street: str, house: str
) -> None:
    """Require a fully contained, exactly addressed building, with no buffer."""
    if site["tags"].get("amenity") != "school" or not building["tags"].get("building"):
        raise ValueError("SCHOOL_SITE_AND_PHYSICAL_BUILDING_REQUIRED")
    if (building["tags"].get("addr:street"), building["tags"].get("addr:housenumber")) != (
        street,
        house,
    ):
        raise ValueError("EXACT_FOUNDER_BUILDING_ADDRESS_REQUIRED")
    for row in (site, building):
        if (
            row["node_refs"][0] != row["node_refs"][-1]
            or row["coordinates"][0] != row["coordinates"][-1]
        ):
            raise ValueError("CLOSED_SOURCE_POLYGON_REQUIRED")
    campus, physical = (
        shapely.Polygon(site["coordinates"]),
        shapely.Polygon(building["coordinates"]),
    )
    if not campus.is_valid or not physical.is_valid or campus.is_empty or physical.is_empty:
        raise ValueError("VALID_SOURCE_POLYGONS_REQUIRED")
    if not campus.covers(physical):
        raise ValueError("WHOLE_BUILDING_MUST_BE_CONTAINED_NO_BUFFER")


class CampusRows(osmium.SimpleHandler):
    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[int, dict[str, Any]] = {}
        self.addressed: list[dict[str, Any]] = []

    def way(self, way: Any) -> None:
        wanted = {c.site_way for c in CAMPUSES} | {c.building_way for c in CAMPUSES}
        address_matches = any(
            (way.tags.get("addr:street"), way.tags.get("addr:housenumber")) == (c.street, c.house)
            for c in CAMPUSES
        )
        if way.id not in wanted and not address_matches:
            return
        row = {
            "osm_ref": f"way/{way.id}",
            "tags": dict(way.tags),
            "node_refs": [n.ref for n in way.nodes],
            "coordinates": [[n.lon, n.lat] for n in way.nodes],
        }
        if way.id in wanted:
            self.rows[way.id] = row
        if address_matches and way.tags.get("building"):
            self.addressed.append(row)


def verify_source_identities(
    base: BundleData, roster_sha256: str, pbf_sha256: str
) -> SourceVersion:
    if roster_sha256 != ROSTER_SHA256:
        raise ValueError("IMMUTABLE_FOUNDER_ROSTER_SHA256_MISMATCH")
    original = next(s for s in base.manifest.source_versions if s.source_id == "osm_geofabrik")
    if pbf_sha256 != original.checksum.value:
        raise ValueError("PINNED_BUNDLE_PBF_SHA256_MISMATCH")
    inherited = next(s for s in base.manifest.source_versions if s.source_id == "klgd_school_sites")
    if (
        inherited.dataset_id != "klgd_school_sites.20261110"
        or inherited.checksum.value != ROSTER_SHA256
    ):
        raise ValueError("FOUNDER_SOURCE_VERSION_MISMATCH")
    return original


def verified_campuses(official_list: Path, pbf: Path, base: BundleData) -> list[dict[str, Any]]:
    original = verify_source_identities(
        base, sha256_file(official_list).value, sha256_file(pbf).value
    )
    institutions = {
        r.institution_id: r
        for r in parse_list(official_list.read_bytes().decode(ENCODING), "school")
    }
    rows = CampusRows()
    rows.apply_file(str(pbf), locations=True)
    results = []
    for case in CAMPUSES:
        institution = institutions[case.institution_id]
        if (
            institution.name != case.institution_name
            or case.founder_address_fragment not in institution.address
        ):
            raise ValueError("FOUNDER_CAMPUS_IDENTITY_MISMATCH")
        site, building = rows.rows[case.site_way], rows.rows[case.building_way]
        if _hash(site) != case.site_record_sha256:
            raise ValueError("REVIEWED_SCHOOL_SITE_RECORD_MISMATCH")
        verify_geometry(site, building, case.street, case.house)
        footprint = shapely.Polygon(site["coordinates"])
        matches = [
            r["osm_ref"]
            for r in rows.addressed
            if r["tags"].get("addr:street") == case.street
            and r["tags"].get("addr:housenumber") == case.house
            and shapely.Polygon(r["coordinates"]).is_valid
            and footprint.covers(shapely.Polygon(r["coordinates"]))
        ]
        if matches != [f"way/{case.building_way}"]:
            raise ValueError("ONE_EXACT_ADDRESSED_BUILDING_PER_CAMPUS_REQUIRED")
        target = next(d for d in base.destinations if d.destination_id == case.destination_id)
        if (target.status, target.quarantine_reasons, target.osm_ref) != (
            "quarantined",
            ["OWNERSHIP_UNVERIFIED"],
            f"way/{case.site_way}",
        ):
            raise ValueError("UNEXPECTED_BASE_CAMPUS_QUARANTINE")
        if not footprint.covers(shapely.Point(target.lon, target.lat)):
            raise ValueError("EXISTING_PHYSICAL_LOCATION_OUTSIDE_SOURCE_CAMPUS")
        other_schools = [
            d.destination_id
            for d in base.destinations
            if d.category == "school"
            and d.status == "accepted"
            and footprint.covers(shapely.Point(d.lon, d.lat))
        ]
        if other_schools:
            raise ValueError("CAMPUS_ALREADY_REPRESENTED_BY_ACCEPTED_SCHOOL")
        results.append(
            {
                "destination_id": case.destination_id,
                "institution": asdict(institution),
                "campus": 2,
                "official_address_fragment": case.founder_address_fragment,
                "osm_original_name": target.name,
                "accepted_name": case.institution_name + ", корпус 2",
                "site_osm_ref": site["osm_ref"],
                "site_record_sha256": _hash(site),
                "address_building_osm_ref": building["osm_ref"],
                "address_building_record_sha256": _hash(building),
                "exact_osm_address": {"street": case.street, "house": case.house},
                "whole_building_contained": True,
                "geometry_buffer_m": 0,
                "other_accepted_schools_inside_site": other_schools,
                "raw_roster_sha256": ROSTER_SHA256,
                "pbf_sha256": original.checksum.value,
            }
        )
    if len({r["destination_id"] for r in results}) != len(results) or len(
        {r["site_osm_ref"] for r in results}
    ) != len(results):
        raise ValueError("DISTINCT_PHYSICAL_CAMPUSES_REQUIRED")
    return results


def revise(official_list: Path, provenance: Path, pbf: Path) -> Path:
    base = load_bundle(BASE_BUNDLE)
    records = verified_campuses(official_list, pbf, base)
    observed = json.loads(provenance.read_text())
    if observed["sha256"] != ROSTER_SHA256:
        raise ValueError("FOUNDER_PROVENANCE_SHA256_MISMATCH")
    retrieved = datetime.fromisoformat(observed["retrieved_at"])
    source = DatasetManifest(
        dataset_id=DATASET_ID,
        source_id=SOURCE_ID,
        source_url=observed["url"],
        retrieved_at=retrieved,
        publication_date=None,
        valid_from=None,
        valid_to=None,
        geography="Городской округ «Город Калининград»",
        license=load_dataset_manifest("klgd_school_sites.20261110").license,
        access_method="file",
        raw_path="klgd_education/schools-20261110.html",
        checksum=sha256_file(official_list),
        schema_mapping={
            "founder rows4409878/4409869 campus2 addresses": "municipal institution and physical-campus identity",
            "pinned addressed building fully within school-site polygon": "exact physical-site join; no proximity rule",
        },
        coverage="Only school12 campus2 Багратиона107А and school3 campus2 Березовая2; public-school catalogue remains partial",
        limitations=[
            "OSM school/campus labels are stale and are reconciled only for these two exact addresses",
            "No new entrance, connector, travel time or complete school inventory is verified",
        ],
        status="accepted",
        qa_report=f"data/bundles/{NEXT_BUNDLE}/qa_report.json",
    )
    raw = raw_dir() / source.raw_path
    raw.parent.mkdir(parents=True, exist_ok=True)
    if raw.exists() and sha256_file(raw) != source.checksum:
        raise ValueError("EXISTING_IMMUTABLE_RAW_DIFFERS")
    if not raw.exists():
        with tempfile.NamedTemporaryFile(dir=raw.parent) as staging:
            staging.write(official_list.read_bytes())
            staging.flush()
            os.link(staging.name, raw)
    raw.chmod(0o444)
    collections: dict[str, Any] = {
        name: getattr(base, name)
        for name in (*BUNDLE_COLLECTIONS, *OPTIONAL_COLLECTIONS)
        if name in BUNDLE_COLLECTIONS or getattr(base, name)
    }
    by_id = {r["destination_id"]: r for r in records}
    for record in records:
        record["evidence_id"] = (
            f"{SOURCE_ID}:20261110:{record['institution']['institution_id']}:campus2"
        )
        collections["evidence"] = [
            *collections["evidence"],
            Evidence(
                evidence_id=record["evidence_id"],
                source_id=SOURCE_ID,
                dataset_id=DATASET_ID,
                record_id=record["institution"]["institution_id"] + ":campus2",
                url=observed["url"],
                fetched_at=retrieved,
                content_hash=_hash(record),
                verification_state="verified",
            ),
        ]
    collections["destinations"] = [
        d.model_copy(
            update={
                "name": by_id[d.destination_id]["accepted_name"],
                "status": "accepted",
                "quarantine_reasons": [],
                "evidence_ids": sorted([*d.evidence_ids, by_id[d.destination_id]["evidence_id"]]),
            }
        )
        if d.destination_id in by_id
        else d
        for d in base.destinations
    ]
    collections["category_ownership"] = [
        CategoryOwnership(
            destination_id=o.destination_id,
            category="school",
            ownership="municipal",
            basis="registry",
            verification_state="verified",
            evidence_ids=[by_id[o.destination_id]["evidence_id"]],
        )
        if o.destination_id in by_id
        else o
        for o in base.category_ownership
    ]
    collections["evidence"] = sorted(collections["evidence"], key=lambda e: e.evidence_id)
    now = datetime.now(UTC)
    manifest = base.manifest.model_copy(
        update={
            "bundle_id": NEXT_BUNDLE,
            "created_at": now,
            "accepted_at": now,
            "accepted_by": "ML2 exact-address campus review",
            "files": {},
            "source_versions": [
                *base.manifest.source_versions,
                SourceVersion(
                    source_id=SOURCE_ID,
                    dataset_id=DATASET_ID,
                    retrieved_at=retrieved,
                    checksum=source.checksum,
                    license=source.license,
                ),
            ],
            "limitations": [
                *base.manifest.limitations,
                "Revision .4 accepts exactly two campus2 identities by founder address and pinned full-building containment; physical approaches and incomplete school coverage are unchanged",
            ],
        }
    )
    qa = json.loads(json.dumps(base.qa_report))
    counts = Counter((d.category, d.status) for d in collections["destinations"])
    qa.update(bundle_id=NEXT_BUNDLE, derived_from=BASE_BUNDLE, generated_at=now.isoformat())
    qa["summary"]["destinations"] = {
        category: {status: counts[category, status] for status in ("accepted", "quarantined")}
        for category in sorted({d.category for d in base.destinations})
    }
    qa["summary"]["campus_revision"] = {
        "records": records,
        "accepted_schools": counts["school", "accepted"],
        "quarantined_schools": counts["school", "quarantined"],
        "catalog_complete": False,
        "connectors_changed": 0,
        "population_or_grid_changed": False,
    }
    qa["checks"].extend(
        [
            {
                "check_id": "campus2_exact_founder_address_full_building_containment",
                "status": "pass",
                "detail": "Pinned source records, unique exact addressed building, whole polygon containment without buffer; source current institution overrides stale OSM labels",
            },
            {
                "check_id": "campus2_physical_sites_separate",
                "status": "pass",
                "detail": "Campus1 identities retained; no campus collapsed by institution name",
            },
        ]
    )
    import hashlib

    for name in PROTECTED:
        if hashlib.sha256(_dump(collections[name])).hexdigest() != base.manifest.files[name].sha256:
            raise ValueError("PROTECTED_COLLECTION_CHANGED_BEFORE_PUBLICATION")
    manifest_path = data_dir() / "manifests" / "datasets" / (DATASET_ID + ".json")
    if manifest_path.exists():
        if DatasetManifest.model_validate_json(manifest_path.read_bytes()) != source:
            raise ValueError("EXISTING_IMMUTABLE_SOURCE_MANIFEST_DIFFERS")
    else:
        write_dataset_manifest(source)
    target = publish_bundle(manifest, collections, qa)
    new = load_bundle(NEXT_BUNDLE)
    unchanged = {
        name: new.manifest.files[name].sha256 == base.manifest.files[name].sha256
        for name in PROTECTED
    }
    if not all(unchanged.values()):
        raise ValueError("PROTECTED_COLLECTION_CHANGED")
    verification = {
        "schema": "a2-campus-revision-verification-v1",
        "base_bundle_id": BASE_BUNDLE,
        "new_bundle_id": NEXT_BUNDLE,
        "source_dataset_id": DATASET_ID,
        "records": records,
        "unchanged_collection_sha256": {
            name: base.manifest.files[name].sha256 for name in PROTECTED
        },
        "base_manifest_sha256": sha256_file(
            data_dir() / "bundles" / BASE_BUNDLE / "manifest.json"
        ).value,
        "new_manifest_sha256": sha256_file(target / "manifest.json").value,
        "canonical_v2_payload_sha256": _hash(to_accepted_data_v2(new)),
        "index_ready": False,
    }
    (
        data_dir() / "manifests" / "qa" / "kaliningrad-school-campus-revision-2026-11-10.json"
    ).write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-list", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--pbf", type=Path, required=True)
    args = parser.parse_args()
    print(revise(args.official_list, args.provenance, args.pbf))


if __name__ == "__main__":
    main()
