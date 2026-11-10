"""Publish a bounded source correction, without promoting school completeness.

The reviewed founder-list address identifies gymnasium 40's second physical site.
The corresponding OSM site and its existing connector retain their coordinates.
This revision closes one ownership/identity gap, not the public-school catalogue.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .bundle import CategoryOwnership, Evidence, SourceVersion
from .klgd import ENCODING, parse_list
from .manifest import DatasetManifest, load_dataset_manifest, write_dataset_manifest
from .paths import raw_dir
from .school_audit import _hash
from .store import (
    BUNDLE_COLLECTIONS,
    OPTIONAL_COLLECTIONS,
    load_bundle,
    publish_bundle,
    sha256_file,
)

BASE_BUNDLE = "kaliningrad-urban-2026-11-10.2"
NEXT_BUNDLE = "kaliningrad-urban-2026-11-10.3"
LIST_SHA256 = "89808fa312acc81f470a39681c655e1c2f48aa345e6841b1ea2142efaaa54e2f"
OSM_RECORD_SHA256 = "b84828979b75978def7b804b4100b0143046d3a2d3dbacb1697295b79eda0162"
DESTINATION_ID = "kaliningrad-school-way-1055458688"
DATASET_ID = "klgd_school_sites.20261110"


def verified_site(official_list: Path, audit_report: Path) -> dict[str, Any]:
    """Fail closed unless both immutable source records are the reviewed records."""

    if sha256_file(official_list).value != LIST_SHA256:
        raise ValueError("Founder-list bytes differ from reviewed immutable source")
    report = json.loads(audit_report.read_text())
    base = load_bundle(BASE_BUNDLE)
    expected = next(v for v in base.manifest.source_versions if v.source_id == "osm_geofabrik")
    if report["bundle_id"] != BASE_BUNDLE or report["pbf_sha256"] != expected.checksum.value:
        raise ValueError("Audit report is not bound to the reviewed base/PBF")
    row = next(r for r in report["quarantined_schools"] if r["destination_id"] == DESTINATION_ID)
    if row["osm_source"]["record_sha256"] != OSM_RECORD_SHA256:
        raise ValueError("OSM identity/geometry record differs from reviewed source")
    institution = next(
        r
        for r in parse_list(official_list.read_bytes().decode(ENCODING), "school")
        if r.institution_id == "4418524"
    )
    if institution.name != "МАОУ гимназия № 40 им. Ю.А. Гагарина":
        raise ValueError("Unexpected founder institution identity")
    if "пер. Рассветный, 5 (корпус 2)" not in institution.address:
        raise ValueError("Founder list does not confirm the second physical site")
    tags = row["osm_source"]["tags"]
    if tags.get("addr:street") != "Рассветный переулок" or tags.get("addr:housenumber") != "5":
        raise ValueError("OSM site does not have the reviewed exact address")
    return {
        "institution": asdict(institution),
        "physical_site": {"address": "г. Калининград, пер. Рассветный, 5", "campus": 2},
        "osm_record_sha256": OSM_RECORD_SHA256,
        "destination_id": DESTINATION_ID,
    }


def revise(official_list: Path, provenance: Path, audit_report: Path) -> Path:
    record = verified_site(official_list, audit_report)
    observed = json.loads(provenance.read_text())
    if observed["sha256"] != LIST_SHA256:
        raise ValueError("Source provenance checksum does not match")
    retrieved = datetime.fromisoformat(observed["retrieved_at"])
    old_source = load_dataset_manifest("klgd_education.20261108")
    target = raw_dir() / "klgd_education" / "schools-20261110.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and sha256_file(target).value != LIST_SHA256:
        raise ValueError("Refusing to replace existing immutable raw input")
    if not target.exists():
        target.write_bytes(official_list.read_bytes())
    target.chmod(0o444)
    source = DatasetManifest(
        dataset_id=DATASET_ID,
        source_id="klgd_school_sites",
        source_url=observed["url"],
        retrieved_at=retrieved,
        publication_date=None,
        valid_from=None,
        valid_to=None,
        geography="Городской округ «Город Калининград»",
        license=old_source.license,
        access_method="file",
        raw_path=str(target.relative_to(raw_dir())),
        checksum=sha256_file(target),
        schema_mapping={
            "school founder-list row 4418524": "institution identity and second-campus address"
        },
        coverage="Reviewed facts for gymnasium 40, second campus; not a complete public-school physical-site catalogue",
        limitations=[
            "Municipal founder only; regional/federal schools are outside this list",
            "Physical coordinates remain OSM-derived; no entrance or approach is newly verified",
            "Other founder list/map discrepancies remain unresolved",
        ],
        status="accepted",
        qa_report=f"data/bundles/{NEXT_BUNDLE}/qa_report.json",
    )
    base = load_bundle(BASE_BUNDLE)
    site = next(d for d in base.destinations if d.destination_id == DESTINATION_ID)
    if site.status != "quarantined" or site.quarantine_reasons != ["OWNERSHIP_UNVERIFIED"]:
        raise ValueError("Unexpected base quarantine state")
    evidence_id = "klgd_school_sites:20261110:4418524:campus2"
    evidence = Evidence(
        evidence_id=evidence_id,
        source_id=source.source_id,
        dataset_id=DATASET_ID,
        record_id="4418524:campus2",
        url=record["institution"]["detail_url"],
        fetched_at=retrieved,
        content_hash=_hash(record),
        verification_state="verified",
    )
    collections: dict[str, Any] = {
        name: getattr(base, name)
        for name in (*BUNDLE_COLLECTIONS, *OPTIONAL_COLLECTIONS)
        if name in BUNDLE_COLLECTIONS or getattr(base, name)
    }
    collections["destinations"] = [
        d.model_copy(
            update={
                "status": "accepted",
                "quarantine_reasons": [],
                "evidence_ids": sorted([*d.evidence_ids, evidence_id]),
            }
        )
        if d.destination_id == DESTINATION_ID
        else d
        for d in base.destinations
    ]
    collections["category_ownership"] = [
        CategoryOwnership(
            destination_id=DESTINATION_ID,
            category="school",
            ownership="municipal",
            basis="registry",
            verification_state="verified",
            evidence_ids=[evidence_id],
        )
        if o.destination_id == DESTINATION_ID
        else o
        for o in base.category_ownership
    ]
    collections["evidence"] = sorted([*base.evidence, evidence], key=lambda e: e.evidence_id)
    now = datetime.now(UTC)
    manifest = base.manifest.model_copy(
        update={
            "bundle_id": NEXT_BUNDLE,
            "created_at": now,
            "accepted_at": now,
            "accepted_by": "ML2 Phase2 source review",
            "files": {},
            "source_versions": [
                *base.manifest.source_versions,
                SourceVersion(
                    source_id=source.source_id,
                    dataset_id=source.dataset_id,
                    retrieved_at=retrieved,
                    checksum=source.checksum,
                    license=source.license,
                ),
            ],
            "limitations": [
                *base.manifest.limitations,
                "School revision .3 verifies only gymnasium40 campus2 ownership and exact roster/address identity; school coverage remains partial, 53 OSM school candidates remain quarantined; founder list/map conflicts and 64 previously unknown approaches remain unresolved",
            ],
        }
    )
    qa = dict(base.qa_report)
    qa.update(
        {"bundle_id": NEXT_BUNDLE, "derived_from": BASE_BUNDLE, "generated_at": now.isoformat()}
    )
    qa["summary"] = dict(qa["summary"])
    school_ids = {
        d.destination_id
        for d in collections["destinations"]
        if d.category == "school" and d.status == "accepted"
    }
    endpoints = [
        c for c in base.walk_connectors if c.entity_kind == "point" or c.entity_id in school_ids
    ]
    qa["summary"]["school_revision"] = {
        "accepted_schools": len(school_ids),
        "quarantined_schools": sum(
            d.category == "school" and d.status == "quarantined"
            for d in collections["destinations"]
        ),
        "selected_endpoints": len(endpoints),
        "traversable_endpoints": sum(c.traversable is True for c in endpoints),
        "unknown_point_connectors": sum(
            c.traversable is None and c.entity_kind == "point" for c in endpoints
        ),
        "unknown_accepted_school_connectors": sum(
            c.traversable is None and c.entity_kind == "destination" for c in endpoints
        ),
        "record": record,
        "record_sha256": evidence.content_hash,
        "source_sha256": LIST_SHA256,
        "catalog_complete": False,
    }
    counts = Counter((d.category, d.status) for d in collections["destinations"])
    qa["summary"]["destinations"] = {
        category: {status: counts[category, status] for status in ("accepted", "quarantined")}
        for category in sorted({d.category for d in base.destinations})
    }
    qa["checks"] = [
        *qa["checks"],
        {
            "check_id": "gymnasium40_campus2_founder_address_identity",
            "status": "pass",
            "detail": "Municipal founder explicitly lists campus2 at Рассветный5; pinned OSM site has exact address; existing physical coordinates and connector preserved",
            "record_sha256": evidence.content_hash,
        },
    ]
    result = publish_bundle(manifest, collections, qa)
    write_dataset_manifest(source)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-list", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--audit-report", type=Path, required=True)
    args = parser.parse_args()
    print(revise(args.official_list, args.provenance, args.audit_report))


if __name__ == "__main__":
    main()
