"""Correct unsupported Moscow airport completeness without changing accepted facts.

OurAirports scheduled-service flags and the extraction radius identify candidates.
They do not close the roster of every passenger airport serving Moscow. No new
official roster, transport proof, or raw dataset is introduced by this revision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .bundle import BundleData, CoverageRecord
from .contract_v2 import load_accepted_data
from .paths import data_dir
from .store import (
    BUNDLE_COLLECTIONS,
    OPTIONAL_COLLECTIONS,
    _dump,
    load_bundle,
    publish_bundle,
    sha256_file,
)

BASE_BUNDLE = "moscow-urban-2026-11-10.2"
NEXT_BUNDLE = "moscow-urban-2026-11-10.3"
BASE_MANIFEST_SHA256 = "84df02d264702906319b5db9f82235c1f3b221a5dcf27edb9facc6d52d950fa5"
QA_MANIFEST = "manifests/qa/moscow-airport-coverage-correction-2026-11-10.json"
CORRECTION_REASON = (
    "IATA и регулярное сообщение OurAirports в радиусе60км подтверждают кандидатов, "
    "но не полный официальный перечень пассажирских аэропортов, обслуживающих Москву, "
    "включая объекты за границей и за радиусом выгрузки. "
    "Официальное исчерпывающее основание и его применимость к датам01–30.11.2026 "
    "не подтверждены; полнота категории, ближайший объект по всей категории и индекс unknown."
)
PROTECTED = (*BUNDLE_COLLECTIONS, *OPTIONAL_COLLECTIONS)


def corrected_coverage(base: BundleData) -> list[CoverageRecord]:
    """Downgrade the old candidate scope; no supplied candidate can make it full."""
    if base.manifest.bundle_id != BASE_BUNDLE:
        raise ValueError("PINNED_MOSCOW_BASE_REQUIRED")
    airport = [
        row
        for row in base.manifest.coverage
        if row.collection == "destinations" and row.category == "airport"
    ]
    if len(airport) != 1 or airport[0].city_id != "moscow":
        raise ValueError("ONE_MOSCOW_AIRPORT_COVERAGE_SCOPE_REQUIRED")
    return [
        row.model_copy(
            update={"status": "partial", "confirmed_absence": False, "reason": CORRECTION_REASON}
        )
        if row is airport[0]
        else row
        for row in base.manifest.coverage
    ]


def correction_report(base: BundleData) -> dict[str, Any]:
    return {
        "schema": "waydex-moscow-airport-coverage-correction/1",
        "source_snapshot_id": BASE_BUNDLE,
        "target_snapshot_id": NEXT_BUNDLE,
        "source_manifest_sha256": BASE_MANIFEST_SHA256,
        "reason": CORRECTION_REASON,
        "source_roles": {
            "osm": "Physical candidate identity/location and deterministic access projection",
            "ourairports": "Candidate IATA and scheduled-service flag, not a city-serving roster",
            "60km_radius": "Engineering candidate extraction policy, not an absence proof",
        },
        "airport_candidate_ids_preserved": [
            d.destination_id for d in base.destinations if d.category == "airport"
        ],
        "airport_candidate_statuses_preserved": {
            d.destination_id: d.status for d in base.destinations if d.category == "airport"
        },
        "original_airport_coverage": [
            row.model_dump(mode="json")
            for row in base.manifest.coverage
            if row.category == "airport"
        ],
        "corrected_airport_coverage": [
            row.model_dump(mode="json")
            for row in corrected_coverage(base)
            if row.category == "airport"
        ],
        "unchanged_collection_sha256": {
            name: base.manifest.files[name].sha256
            for name in PROTECTED
            if name in base.manifest.files
        },
        "source_versions_unchanged": [
            s.model_dump(mode="json") for s in base.manifest.source_versions
        ],
        "all_other_category_coverage_unchanged": True,
        "original_bundle_unchanged": True,
        "raw_source_retrievals": 0,
        "new_accepted_objects": 0,
        "category_minimum_proven": False,
        "index_ready": False,
        "needed_before_full": [
            "Authoritative exhaustive city-serving passenger-airport roster, including outside the boundary",
            "Official destination identity joins and dated scheduled-passenger applicability",
            "Separately reviewed complete model routing/physical proofs for any numerical index",
        ],
    }


def publish() -> Path:
    base_path = data_dir() / "bundles" / BASE_BUNDLE / "manifest.json"
    if sha256_file(base_path).value != BASE_MANIFEST_SHA256:
        raise ValueError("PINNED_MOSCOW_BASE_MANIFEST_CHANGED")
    base = load_bundle(BASE_BUNDLE)
    # Preserve the original JSON rows byte-for-byte. Loading them into today's
    # producer models may add later optional defaults (for example evidence
    # validity fields), which would change historical collection hashes.
    collections = {
        name: json.loads((base_path.parent / base.manifest.files[name].path).read_bytes())
        for name in PROTECTED
        if name in base.manifest.files
    }
    for name, rows in collections.items():
        if hashlib.sha256(_dump(rows)).hexdigest() != base.manifest.files[name].sha256:
            raise ValueError("PROTECTED_MOSCOW_COLLECTION_CHANGED")
    report = correction_report(base)
    now = datetime.now(UTC)
    manifest = base.manifest.model_copy(
        update={
            "bundle_id": NEXT_BUNDLE,
            "created_at": now,
            "accepted_at": now,
            "accepted_by": "ML2 conservative airport coverage correction; no new source acceptance",
            "files": {},
            "coverage": corrected_coverage(base),
            "limitations": [
                *base.manifest.limitations,
                "Revision .3: airport roster partial for01–30Nov2026. OurAirports/IATA and60km proximity do not prove an exhaustive officially city-serving roster; no minimum/index readiness follows.",
                "All destination identities/statuses, source versions, physical geometry, population, grids, approaches, other category coverage and timetable references are unchanged.",
            ],
        }
    )
    qa = json.loads(json.dumps(base.qa_report))
    qa.update(bundle_id=NEXT_BUNDLE, derived_from=BASE_BUNDLE, generated_at=now.isoformat())
    check_id = "airport_city_serving_catalogue_not_proven"
    qa["warnings"].append(check_id)
    qa["checks"].append({"check_id": check_id, "status": "warning", "detail": CORRECTION_REASON})
    qa["summary"]["airport_coverage_correction"] = report
    target = publish_bundle(manifest, collections, qa)
    load_accepted_data(NEXT_BUNDLE)
    path = data_dir() / QA_MANIFEST
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return target


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(publish())


if __name__ == "__main__":
    main()
