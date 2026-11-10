"""Immutable corrections for source-proven false public-ownership acceptance.

The observed bulk registry is not substituted for the historical source. Only
exact individual EduOrg hashes close the affected historical factual records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import obrnadzor
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

AUDIT = "manifests/research/ownership-classifier-audit-2026-11-10.json"
AUDIT_SHA256 = "8b6ad468c0f91ec9508615f8ffeca0faae83c7374ed91f7af80efd2eca0f029d"
QA = "manifests/qa/cross-city-ownership-correction-2026-11-10.json"
REVISIONS = {
    "moscow": ("moscow-urban-2026-11-10.3", "moscow-urban-2026-11-10.4"),
    "novorossiysk": ("novorossiysk-urban-2026-11-10.2", "novorossiysk-urban-2026-11-10.3"),
}
COLLECTIONS = (*BUNDLE_COLLECTIONS, *OPTIONAL_COLLECTIONS)


def reviewed_audit() -> dict[str, Any]:
    path = data_dir() / AUDIT
    if sha256_file(path).value != AUDIT_SHA256:
        raise ValueError("REVIEWED_OWNERSHIP_AUDIT_CHANGED")
    audit: dict[str, Any] = json.loads(path.read_bytes())
    source_path = Path(obrnadzor.__file__)
    if sha256_file(source_path).value != audit["classifier_source_sha256"]:
        raise ValueError("VERSIONED_OWNERSHIP_CLASSIFIER_CHANGED")
    return audit


def publish_city(city_id: str, audit: dict[str, Any]) -> dict[str, Any]:
    before_id, after_id = REVISIONS[city_id]
    before_root = data_dir() / "bundles" / before_id
    summary = next(c for c in audit["city_summaries"] if c["city_id"] == city_id)
    if sha256_file(before_root / "manifest.json").value != summary["manifest_sha256"]:
        raise ValueError("PINNED_OWNERSHIP_BASE_CHANGED")
    base = load_bundle(before_id)
    # Raw dictionaries preserve older optional defaults and unchanged byte hashes.
    collections = {
        name: json.loads((before_root / base.manifest.files[name].path).read_bytes())
        for name in COLLECTIONS
        if name in base.manifest.files
    }
    uncertain = set(audit["identity_join_uncertain_destination_ids"])
    affected: dict[str, dict[str, Any]] = {}
    evidence = {e.evidence_id: e for e in base.evidence}
    for org in audit["affected_orgs"]:
        if org["city_id"] != city_id:
            continue
        if not org["historical_record_fields_match_observed_exactly"] or (
            org["record_hash_historical_expected"] != [org["record_hash_observed"]]
        ):
            raise ValueError("EXACT_HISTORICAL_INDIVIDUAL_RECORD_JOIN_REQUIRED")
        if obrnadzor.ownership(org["full_name"]) != "private":
            raise ValueError("SOURCE_PROVEN_PRIVATE_LEGAL_FORM_REQUIRED")
        for entry in org["destinations"]:
            source = evidence[entry["evidence_id"]]
            if (
                source.source_id != "obrnadzor_accreditation"
                or source.record_id.lower() != org["org_id"]
                or source.content_hash != org["record_hash_observed"]
            ):
                raise ValueError("ACCEPTED_EVIDENCE_RECORD_HASH_OR_ID_CHANGED")
            affected[entry["destination_id"]] = org
    expected_count = 5 if city_id == "moscow" else 1
    if len(affected) != expected_count:
        raise ValueError("EXACT_REVIEWED_DESTINATION_ROSTER_REQUIRED")
    changed = set()
    for row in collections["destinations"]:
        identity = row["destination_id"]
        if identity not in affected:
            continue
        if row["status"] != "accepted" or row["quarantine_reasons"]:
            raise ValueError("EXPECTED_ORIGINAL_PUBLIC_ACCEPTANCE_CHANGED")
        row["status"] = "quarantined"
        row["quarantine_reasons"] = (
            ["SOURCE_IDENTITY_JOIN_REQUIRES_REVIEW", "LINKED_REGISTRY_RECORD_NOT_PUBLIC"]
            if identity in uncertain
            else ["NOT_PUBLIC_BY_REGISTRY"]
        )
        changed.add(identity)
    if changed != set(affected):
        raise ValueError("REVIEWED_DESTINATION_NOT_FOUND")
    for row in collections["category_ownership"]:
        if row["destination_id"] in affected:
            identity_uncertain = row["destination_id"] in uncertain
            row["ownership"] = "unknown" if identity_uncertain else "private"
            row["verification_state"] = "unknown" if identity_uncertain else "verified"
    protected = {}
    for name, rows in collections.items():
        if name in {"destinations", "category_ownership"}:
            continue
        if hashlib.sha256(_dump(rows)).hexdigest() != base.manifest.files[name].sha256:
            raise ValueError("PROTECTED_OWNERSHIP_REVISION_COLLECTION_CHANGED")
        protected[name] = base.manifest.files[name].sha256
    unresolved = [r for r in audit["unresolved_records"] if r["city_id"] == city_id]
    report = {
        "source_snapshot_id": before_id,
        "target_snapshot_id": after_id,
        "source_manifest_sha256": summary["manifest_sha256"],
        "audit_path": AUDIT,
        "audit_sha256": AUDIT_SHA256,
        "classifier_commit": audit["classifier_commit"],
        "classifier_source_sha256": audit["classifier_source_sha256"],
        "observed_source_archive_sha256": audit["source_observation"]["sha256"],
        "historical_source_archive_sha256_unchanged": audit["historical_archive_sha256"],
        "changed_destination_ids": sorted(affected),
        "changed_ownership_unknown_physical_identity": sorted(set(affected) & uncertain),
        "quarantined_public_acceptance_count": len(affected),
        "protected_collection_sha256": protected,
        "unresolved_current_records_preserved": unresolved,
        "source_versions_coverage_geometry_population_and_connectors_unchanged": True,
        "new_promotions": 0,
        "index_ready": False,
    }
    now = datetime.now(UTC)
    manifest = base.manifest.model_copy(
        update={
            "bundle_id": after_id,
            "created_at": now,
            "accepted_at": now,
            "accepted_by": "ML2 exact-record ownership correction; no new source promotion",
            "files": {},
            "limitations": [
                *base.manifest.limitations,
                f"Ownership correction: {len(affected)} originally accepted public objects quarantined using exact historical individual registry hashes; legal-form classifier fc7e4da. Raw observed registry is not substituted for the historical source version.",
                "Uncertain RosNOU physical institutional identity is not assigned the linked registry institution's private ownership."
                if city_id == "moscow"
                else "No new municipal/state institution, full category roster or route minimum is established by this revision.",
                "PK50 org15a60264-0f9e-6fd8-205d-dd9f4619f6e2 current record hash differs: two historical accepted objects preserved without a new10Nov2026 ownership/completeness/index admission."
                if unresolved
                else "All original coverage scopes are preserved; no new full-category or index claim.",
            ],
        }
    )
    qa = json.loads(json.dumps(base.qa_report))
    qa.update(bundle_id=after_id, derived_from=before_id, generated_at=now.isoformat())
    qa["summary"]["ownership_correction"] = report
    counts: dict[str, Counter[str]] = {}
    for row in collections["destinations"]:
        counts.setdefault(row["category"], Counter())[row["status"]] += 1
    qa["summary"]["destinations_by_category"] = {
        category: {state: values[state] for state in ["accepted", "quarantined"]}
        for category, values in counts.items()
    }
    qa["checks"].append(
        {
            "check_id": "ownership_corrected_from_exact_historical_record_hashes",
            "status": "pass",
            "detail": report,
        }
    )
    if unresolved:
        qa["warnings"].append("current_registry_record_changed_historical_inputs_preserved")
        qa["checks"].append(
            {
                "check_id": "current_registry_record_changed_historical_inputs_preserved",
                "status": "warning",
                "detail": unresolved,
            }
        )
    target = publish_bundle(manifest, collections, qa)
    accepted = load_accepted_data(after_id)
    if set(affected) & {d.destination_id for d in accepted.destinations}:
        raise ValueError("QUARANTINED_OBJECT_LEAKED_TO_CANONICAL_ACCEPTED_LIST")
    return {**report, "manifest_sha256": sha256_file(target / "manifest.json").value}


def publish() -> list[dict[str, Any]]:
    audit = reviewed_audit()
    reports = [publish_city(city, audit) for city in REVISIONS]
    with (data_dir() / QA).open("x", encoding="utf-8") as stream:
        json.dump(reports, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return reports


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(publish(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
