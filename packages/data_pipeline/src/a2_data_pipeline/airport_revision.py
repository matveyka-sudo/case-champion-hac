"""Reviewed current official airport roster; immutable one-day coverage revision.

Only factual identifiers and provenance are redistributed. Captured documents
remain local. The source review closes the officially city-serving passenger
airport roster on the observed date, independently of the extraction radius.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import HttpUrl

from .bundle import BundleData, CoverageRecord, Evidence, SourceVersion
from .contract_v2 import load_accepted_data, to_accepted_data_v2
from .manifest import DatasetManifest, License, load_dataset_manifest, write_dataset_manifest
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

BASE_BUNDLE = "kaliningrad-urban-2026-11-10.4"
NEXT_BUNDLE = "kaliningrad-urban-2026-11-10.5"
OBSERVED = date(2026, 11, 10)
AIRPORT = "kaliningrad-airport-way-59178160"
POINT = "kaliningrad-utm34n-1km-v1-e468-n6069-p"
PROTECTED = tuple(
    name
    for name in (*BUNDLE_COLLECTIONS, *OPTIONAL_COLLECTIONS)
    if name not in {"evidence", "destinations"}
)
SOURCE_MANIFEST = "manifests/research/kaliningrad-airport-official-sources-2026-11-10.json"
REVIEW_MANIFEST_SHA256 = "c8c118ffa272525bc1a6a8eb76da63d26079f891f19270cbf4d79f655ac62918"

# observation -> (source ID, exact record, approved factual role)
SOURCES = {
    "tic-airport-roster": (
        "klgd_tic_airports",
        "main-article:first-paragraph",
        "Khrabrovo is the only passenger airport in the region; direct Kaliningrad service",
    ),
    "tic-charter-2026": (
        "klgd_tic_charter",
        "pdf:page2:section1.5",
        "Regional Ministry of Culture and Tourism is the TIC founder",
    ),
    "favt-civil-airfields-pdf-20261109": (
        "favt_registry",
        "pdf:page1:row8",
        "Kaliningrad(Khrabrovo), registration109 dated02.02.2015, operatorAOAirportKhrabrovo",
    ),
    "favt-federal-airports": (
        "favt_airport_designation",
        "federal-airport-table:row24",
        "Khrabrovo is the airport of the regional administrative center Kaliningrad",
    ),
    "khrabrovo-current-board-web-extraction": (
        "khrabrovo_observed_board",
        "web-extraction:lines49-114:20261110",
        "Operator board observed10Nov2026 reports scheduled passenger flightsSU6334andSU1005",
    ),
}


def verified_observations(capture_root: Path) -> list[dict[str, Any]]:
    manifest_path = data_dir() / SOURCE_MANIFEST
    if sha256_file(manifest_path).value != REVIEW_MANIFEST_SHA256:
        raise ValueError("INDEPENDENTLY_REVIEWED_SOURCE_MANIFEST_CHANGED")
    manifest = json.loads(manifest_path.read_bytes())
    rows = [r for r in manifest["sources"] if r["source_observation_id"] in SOURCES]
    if {r["source_observation_id"] for r in rows} != set(SOURCES):
        raise ValueError("OFFICIAL_ROSTER_AUTHORITY_DESIGNATION_AND_OPERATION_REQUIRED")
    for row in rows:
        filename = row["local_artifact"]
        if Path(filename).name != filename:
            raise ValueError("LOCAL_CAPTURE_FILENAME_REQUIRED")
        checksum = sha256_file(capture_root / filename)
        if checksum.value != row["sha256"] or checksum.size_bytes != row["size_bytes"]:
            raise ValueError("REVIEWED_OFFICIAL_CAPTURE_SHA256_OR_SIZE_MISMATCH")
        retrieved = datetime.fromisoformat(row["retrieved_at"])
        if retrieved.tzinfo is None or retrieved.date() != OBSERVED:
            raise ValueError("OBSERVED_SOURCE_DATE_MISMATCH")
    return sorted(rows, key=lambda row: row["source_observation_id"])


def restricted_coverage(base: BundleData, evidence_ids: list[str]) -> list[CoverageRecord]:
    old = [
        r
        for r in base.manifest.coverage
        if r.collection == "destinations" and r.category == "airport"
    ]
    if len(old) != 1 or (old[0].valid_from, old[0].valid_to) != (
        date(2026, 11, 1),
        date(2026, 11, 30),
    ):
        raise ValueError("EXPECTED_LEGACY_AIRPORT_PERIOD_REQUIRED")
    previous = old[0]
    remaining = [r for r in base.manifest.coverage if r is not previous]
    uncertainty = (
        "Исходные кандидаты OSM/OurAirports сохранены; полный официальный перечень "
        "обслуживающих Калининград пассажирских аэропортов подтверждён только наблюдением "
        "10.11.2026, полнота за эту дату не переносится на другие даты"
    )
    return [
        *remaining,
        previous.model_copy(
            update={
                "valid_to": OBSERVED - timedelta(days=1),
                "status": "partial",
                "reason": uncertainty,
            }
        ),
        previous.model_copy(
            update={
                "valid_from": OBSERVED,
                "valid_to": OBSERVED,
                "status": "complete",
                "reason": "Официально обслуживающие Калининград пассажирские аэропорты: Храброво; единственный пассажирский аэропорт области по региональному ТИЦ, городское назначение по Росавиации; только наблюдение10.11.2026, не радиус60км",
                "evidence_ids": sorted(set(previous.evidence_ids + evidence_ids)),
            }
        ),
        previous.model_copy(
            update={
                "valid_from": OBSERVED + timedelta(days=1),
                "status": "partial",
                "reason": uncertainty,
            }
        ),
    ]


def factual_license(url: str, source_id: str) -> License:
    return License(
        name="Factual identifiers and observed roster/operation facts only; source documents not redistributed",
        url=HttpUrl(url),
        status="permitted",
        attribution=source_id,
    )


def revise(capture_root: Path) -> Path:
    base = load_bundle(BASE_BUNDLE)
    observations = verified_observations(capture_root)
    airport = next(d for d in base.destinations if d.destination_id == AIRPORT)
    if (airport.name, airport.serving_city_id, airport.osm_ref, airport.status) != (
        "Калининград (Храброво)",
        "kaliningrad",
        "way/59178160",
        "accepted",
    ) or "ourairports:umkk" not in airport.evidence_ids:
        raise ValueError("REVIEWED_OFFICIAL_AIRPORT_IDENTITY_MISMATCH")
    if any(s.source_id in {v[0] for v in SOURCES.values()} for s in base.manifest.source_versions):
        raise ValueError("ORIGINAL_SOURCE_VERSIONS_MUST_NOT_BE_REPLACED")
    collections: dict[str, Any] = {
        name: getattr(base, name)
        for name in (*BUNDLE_COLLECTIONS, *OPTIONAL_COLLECTIONS)
        if name in BUNDLE_COLLECTIONS or getattr(base, name)
    }
    versions, added_evidence, datasets = [], [], []
    for row in observations:
        source_id, record_id, role = SOURCES[row["source_observation_id"]]
        dataset_id = f"{source_id}.20261110"
        evidence_id = f"{source_id}:20261110:kaliningrad"
        source_path = capture_root / row["local_artifact"]
        source = DatasetManifest(
            dataset_id=dataset_id,
            source_id=source_id,
            source_url=row["url"],
            retrieved_at=datetime.fromisoformat(row["retrieved_at"]),
            publication_date=date(2026, 11, 9) if source_id == "favt_registry" else None,
            valid_from=OBSERVED,
            valid_to=OBSERVED,
            geography="Калининград: официально обслуживающие город пассажирские аэропорты",
            license=factual_license(row["url"], source_id),
            access_method="manual",
            raw_path=f"airport-official-20261110/{source_path.name}",
            checksum=sha256_file(source_path),
            schema_mapping={record_id: role},
            coverage="Observed current official city-serving passenger-airport roster,10Nov2026 only",
            limitations=[
                "No historical or future completeness is inferred from an undated publication",
                "No raw source documents redistributed in Git; accepted outputs contain factual identifiers/provenance",
                "Operator observation is web text extraction, not raw HTTP; it may be cached"
                if source_id == "khrabrovo_observed_board"
                else "No distance cutoff used as completeness proof",
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
                staging.write(source_path.read_bytes())
                staging.flush()
                os.link(staging.name, raw)
        raw.chmod(0o444)
        datasets.append(source)
        versions.append(
            SourceVersion(
                source_id=source_id,
                dataset_id=dataset_id,
                retrieved_at=source.retrieved_at,
                checksum=source.checksum,
                license=source.license,
                publication_date=source.publication_date,
            )
        )
        added_evidence.append(
            Evidence(
                evidence_id=evidence_id,
                source_id=source_id,
                dataset_id=dataset_id,
                record_id=record_id,
                url=row["url"],
                fetched_at=source.retrieved_at,
                content_hash=row["sha256"],
                verification_state="verified",
                valid_from=OBSERVED,
                valid_to=OBSERVED,
            )
        )
    evidence_ids = [e.evidence_id for e in added_evidence]
    collections["evidence"] = sorted([*base.evidence, *added_evidence], key=lambda e: e.evidence_id)
    collections["destinations"] = [
        d.model_copy(update={"evidence_ids": sorted(set(d.evidence_ids + evidence_ids))})
        if d.destination_id == AIRPORT
        else d
        for d in base.destinations
    ]
    now = datetime.now(UTC)
    manifest = base.manifest.model_copy(
        update={
            "bundle_id": NEXT_BUNDLE,
            "created_at": now,
            "accepted_at": now,
            "accepted_by": "ML2 independent official-airport-source review",
            "files": {},
            "source_versions": [*base.manifest.source_versions, *versions],
            "coverage": restricted_coverage(base, evidence_ids),
            "limitations": [
                *base.manifest.limitations,
                "Revision .5 replaces inherited airport completeness01–30Nov with full only10Nov2026 and partial01–09/11–30Nov; officially city-serving passenger airports, independent of60km cutoff",
                "New official airport evidence is observed10Nov2026 only; all school identities, physical coordinates, population, grid and approaches remain unchanged; no product-index readiness implied",
            ],
        }
    )
    qa = json.loads(json.dumps(base.qa_report))
    qa.update(bundle_id=NEXT_BUNDLE, derived_from=BASE_BUNDLE, generated_at=now.isoformat())
    qa["summary"]["airport_revision"] = {
        "official_destination_id": AIRPORT,
        "observed_date": OBSERVED.isoformat(),
        "category_count": 1,
        "evidence_ids": evidence_ids,
        "scope": "officially city-serving passenger airports; includes outside municipal boundary",
        "reviewed_source_manifest_sha256": REVIEW_MANIFEST_SHA256,
        "school_counts": {"accepted": 79, "quarantined": 51},
        "index_ready": False,
    }
    qa["checks"].extend(
        [
            {
                "check_id": "official_airport_roster_source_hashes",
                "status": "pass",
                "detail": "Independently reviewed source captures verified by SHA256 and size; factual airport identity joins retained UMKK/OSM identity",
            },
            {
                "check_id": "airport_full_only_observed_date",
                "status": "pass",
                "detail": "Disjoint partial01–09Nov/full10Nov/partial11–30Nov; all five official evidence periods10Nov only",
            },
            {
                "check_id": "airport_revision_preserves_physical_inputs",
                "status": "pass",
                "detail": "All point/population/grid/access/connector and school rows unchanged; airport evidence references only appended",
            },
        ]
    )
    for name in PROTECTED:
        if (
            name in base.manifest.files
            and hashlib.sha256(_dump(collections.get(name, []))).hexdigest()
            != base.manifest.files[name].sha256
        ):
            raise ValueError("PROTECTED_COLLECTION_CHANGED")
    for source in datasets:
        path = data_dir() / "manifests" / "datasets" / f"{source.dataset_id}.json"
        if path.exists():
            if load_dataset_manifest(source.dataset_id) != source:
                raise ValueError("EXISTING_IMMUTABLE_SOURCE_MANIFEST_DIFFERS")
        else:
            write_dataset_manifest(source)
    target = publish_bundle(manifest, collections, qa)
    load_accepted_data(NEXT_BUNDLE)
    return target


def compatibility(original_id: str, native_artifact: Path) -> dict[str, Any]:
    """Bind unchanged pair inputs to a derivative without relabelling native artifacts."""
    original, current = load_bundle(original_id), load_bundle(NEXT_BUNDLE)
    artifact = json.loads(native_artifact.read_bytes())
    if artifact["bundle_id"] != original_id or artifact["build"]["snapshot_id"] != original_id:
        raise ValueError("ORIGINAL_NATIVE_SNAPSHOT_IDENTITY_REQUIRED")
    if original.manifest.graph_version != current.manifest.graph_version:
        raise ValueError("DERIVATIVE_GRAPH_CHANGED")
    old_versions = {s.source_id: s for s in original.manifest.source_versions}
    new_versions = {s.source_id: s for s in current.manifest.source_versions}
    if any(new_versions.get(k) != v for k, v in old_versions.items()):
        raise ValueError("ORIGINAL_SOURCE_VERSIONS_CHANGED")
    hashes = {}
    for name in (n for n in PROTECTED if n != "category_ownership"):
        if name not in original.manifest.files:
            continue
        if original.manifest.files[name].sha256 != current.manifest.files[name].sha256:
            raise ValueError("NATIVE_INPUT_COLLECTION_CHANGED")
        hashes[name] = current.manifest.files[name].sha256
    endpoints = []
    for name, key, wanted in (
        ("sample_points", "point_id", POINT),
        ("destinations", "destination_id", AIRPORT),
    ):
        a = next(v for v in getattr(original, name) if getattr(v, key) == wanted)
        b = next(v for v in getattr(current, name) if getattr(v, key) == wanted)
        if a.model_dump(exclude={"evidence_ids"}) != b.model_dump(
            exclude={"evidence_ids"}
        ) or not set(a.evidence_ids).issubset(b.evidence_ids):
            raise ValueError("NATIVE_PAIR_PHYSICAL_INPUT_CHANGED")
        endpoints.append(
            {
                "entity_id": wanted,
                "original_record_sha256": _hash(a.model_dump(mode="json")),
                "derivative_record_sha256": _hash(b.model_dump(mode="json")),
                "geometry_and_access_fields_unchanged": True,
            }
        )
    # Hash the canonical validated model, including canonical defaults. This is
    # the engine codec's ASCII JSON convention, distinct from ML2's _hash.
    current_canonical = load_accepted_data(NEXT_BUNDLE).model_dump(mode="json")
    original_canonical = load_accepted_data(original_id).model_dump(mode="json")

    def content_hash(payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            ).encode()
        ).hexdigest()

    point = next(p for p in current.sample_points if p.point_id == POINT)
    cell = next(c for c in current.population_cells if c.cell_id == point.cell_id)
    airport = next(d for d in current.destinations if d.destination_id == AIRPORT)
    city = next(t for t in current.territories if t.territory_id == "kaliningrad")
    proof_root = native_artifact.parent
    proof_hashes = {
        "source_physical_receipt_sha256": sha256_file(
            proof_root / "point-airport-receipt.json"
        ).value,
        "canonical_access_policy_replay_sha256": sha256_file(
            proof_root / "airport-canonical-access-policy-replay.json"
        ).value,
    }
    if proof_hashes != {
        "source_physical_receipt_sha256": "0d0af39b4e536f14000bae235346e44c69c74a3cb6914af74261d7165c241203",
        "canonical_access_policy_replay_sha256": "acb9a9ec103e81d8365ce987b38a092a4a47d53fe8991500a972d660a86b206b",
    }:
        raise ValueError("INDEPENDENT_NATIVE_PHYSICAL_SOURCE_BINDING_CHANGED")
    physical = json.loads((proof_root / "point-airport-receipt.json").read_bytes())
    if (
        physical["artifact_sha256"] != sha256_file(native_artifact).value
        or physical["snapshot_id"] != original_id
        or physical["source_graph_version"] != original.manifest.graph_version
        or artifact["build"]["source_graph_version"] != original.manifest.graph_version
        or physical["profile_version"] != artifact["build"]["profile_version"]
    ):
        raise ValueError("NATIVE_ARTIFACT_PHYSICAL_RECEIPT_IDENTITY_MISMATCH")
    return {
        "schema": "a2-native-snapshot-compatibility-v1",
        "scope": "one original physical point/airport pair and unchanged routing-source/model inputs",
        "original_snapshot_id": original_id,
        "derivative_snapshot_id": NEXT_BUNDLE,
        "source_snapshot_id": original_id,
        "target_snapshot_id": NEXT_BUNDLE,
        "target_canonical_content_sha256": content_hash(current_canonical),
        "source_canonical_content_sha256": content_hash(original_canonical),
        "content_hash_convention": "SHA256(json.dumps(canonical model_dump(mode=json),sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode())",
        "original_manifest_sha256": sha256_file(
            data_dir() / "bundles" / original_id / "manifest.json"
        ).value,
        "derivative_manifest_sha256": sha256_file(
            data_dir() / "bundles" / NEXT_BUNDLE / "manifest.json"
        ).value,
        "original_canonical_payload_sha256": _hash(to_accepted_data_v2(original)),
        "derivative_canonical_payload_sha256": _hash(to_accepted_data_v2(current)),
        "native_artifact_sha256": sha256_file(native_artifact).value,
        **proof_hashes,
        "official_closure_source_manifest_sha256": REVIEW_MANIFEST_SHA256,
        "official_closure_source_refs": [
            f"{source[0]}:20261110:kaliningrad" for source in SOURCES.values()
        ],
        "native_build_identity_preserved": artifact["build"],
        "graph_version": current.manifest.graph_version,
        "osm_source_version": new_versions["osm_geofabrik"].model_dump(mode="json"),
        "unchanged_collection_sha256": hashes,
        "endpoints": endpoints,
        "selected_pair_invariants": {
            "point": point.model_dump(mode="json"),
            "destination": airport.model_dump(mode="json"),
            "access_nodes": [
                n.model_dump(mode="json")
                for n in current.access_nodes
                if n.access_node_id in {point.access_node_id, airport.access_node_id}
            ],
            "original_population_cell": cell.model_dump(mode="json"),
            "administrative_city": city.model_dump(mode="json"),
        },
        "official_roster_full_only_date": OBSERVED.isoformat(),
        "all_real_entrances_claimed": False,
        "index_ready": False,
        "native_artifact_relabelled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--native-artifact", type=Path)
    args = parser.parse_args()
    print(revise(args.capture_root))
    if args.native_artifact:
        report = compatibility("kaliningrad-urban-2026-11-10.3", args.native_artifact)
        path = (
            data_dir()
            / "manifests"
            / "qa"
            / "kaliningrad-airport-native-snapshot-compatibility-2026-11-10.json"
        )
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(path)


if __name__ == "__main__":
    main()
