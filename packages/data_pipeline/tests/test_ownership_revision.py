"""Exact source corrections remove false public eligibility without changing geometry."""

import json

import pytest
from a2_data_pipeline import ownership_revision as revision
from a2_data_pipeline.contract_v2 import load_accepted_data
from a2_data_pipeline.paths import data_dir
from a2_data_pipeline.store import load_bundle, sha256_file


@pytest.mark.parametrize("city", ["moscow", "novorossiysk"])
def test_corrections_preserve_unaffected_records_and_all_physical_inputs(city: str) -> None:
    audit = revision.reviewed_audit()
    before_id, after_id = revision.REVISIONS[city]
    old, new = load_bundle(before_id), load_bundle(after_id)
    changed = {
        d["destination_id"]
        for org in audit["affected_orgs"]
        if org["city_id"] == city
        for d in org["destinations"]
    }
    source = next(c for c in audit["city_summaries"] if c["city_id"] == city)
    assert (
        sha256_file(data_dir() / "bundles" / before_id / "manifest.json").value
        == source["manifest_sha256"]
    )
    for name in revision.COLLECTIONS:
        if name in old.manifest.files and name not in {"destinations", "category_ownership"}:
            assert new.manifest.files[name].sha256 == old.manifest.files[name].sha256
            assert getattr(old, name) == getattr(new, name)
    for field in [
        "grids",
        "graph_version",
        "road_graph_version",
        "source_versions",
        "coverage",
        "timetables",
        "valid_from",
        "valid_to",
    ]:
        assert getattr(old.manifest, field) == getattr(new.manifest, field)
    for a, b in zip(old.destinations, new.destinations, strict=True):
        if a.destination_id in changed:
            assert a.status == "accepted" and b.status == "quarantined"
            assert a.model_dump(exclude={"status", "quarantine_reasons"}) == b.model_dump(
                exclude={"status", "quarantine_reasons"}
            )
        else:
            assert a == b
    for a, b in zip(old.category_ownership, new.category_ownership, strict=True):
        if a.destination_id not in changed:
            assert a == b
    old_ids = {d.destination_id for d in load_accepted_data(before_id).destinations}
    new_ids = {d.destination_id for d in load_accepted_data(after_id).destinations}
    assert old_ids - new_ids == changed
    assert not new_ids - old_ids


def test_private_sources_are_exact_record_joins_and_uncertain_building_stays_unknown() -> None:
    audit = revision.reviewed_audit()
    assert audit["source_observation"]["historical_hash_matches"] is False
    assert audit["historical_archive_sha256"] != audit["source_observation"]["sha256"]
    assert sum(len(r["destinations"]) for r in audit["affected_orgs"]) == 6
    for org in audit["affected_orgs"]:
        assert org["historical_record_fields_match_observed_exactly"] is True
        assert org["record_hash_historical_expected"] == [org["record_hash_observed"]]
        data = load_bundle(revision.REVISIONS[org["city_id"]][1])
        owners = {o.destination_id: o for o in data.category_ownership}
        evidence = {e.evidence_id: e for e in data.evidence}
        for entry in org["destinations"]:
            assert evidence[entry["evidence_id"]].content_hash == org["record_hash_observed"]
            owner = owners[entry["destination_id"]]
            if entry["destination_id"] in audit["identity_join_uncertain_destination_ids"]:
                assert owner.ownership == owner.verification_state == "unknown"
                destination = next(
                    d for d in data.destinations if d.destination_id == entry["destination_id"]
                )
                assert destination.quarantine_reasons == [
                    "SOURCE_IDENTITY_JOIN_REQUIRES_REVIEW",
                    "LINKED_REGISTRY_RECORD_NOT_PUBLIC",
                ]
            else:
                assert owner.ownership == "private" and owner.verification_state == "verified"


def test_changed_current_record_preserves_two_historical_objects_without_new_admission() -> None:
    audit = revision.reviewed_audit()
    assert len(audit["unresolved_records"]) == 1
    row = audit["unresolved_records"][0]
    assert row["org_id"] == "15a60264-0f9e-6fd8-205d-dd9f4619f6e2"
    assert row["historical_record_fields_match_observed_exactly"] is False
    before_id, after_id = revision.REVISIONS["moscow"]
    old, new = load_bundle(before_id), load_bundle(after_id)
    for entry in row["destinations"]:
        a = next(d for d in old.destinations if d.destination_id == entry["destination_id"])
        b = next(d for d in new.destinations if d.destination_id == entry["destination_id"])
        assert a == b and b.status == "accepted"
    assert (
        "current_registry_record_changed_historical_inputs_preserved" in new.qa_report["warnings"]
    )
    assert not any(
        c.completeness == "full"
        for c in load_accepted_data(after_id).coverage_by_mode_date_category
    )


def test_qa_reports_all_source_rows_and_preserves_kaliningrad_bundle() -> None:
    reports = json.loads((data_dir() / revision.QA).read_bytes())
    assert [r["quarantined_public_acceptance_count"] for r in reports] == [5, 1]
    for report in reports:
        data = load_bundle(report["target_snapshot_id"])
        counts = data.qa_report["summary"]["destinations_by_category"]
        for category, row in counts.items():
            assert row["accepted"] == sum(
                d.category == category and d.status == "accepted" for d in data.destinations
            )
            assert row["quarantined"] == sum(
                d.category == category and d.status == "quarantined" for d in data.destinations
            )
        assert report["index_ready"] is False and report["new_promotions"] == 0
    assert (
        sha256_file(data_dir() / "bundles/kaliningrad-urban-2026-11-10.5/manifest.json").value
        == "bf26c9b75d1b424f5c13ea2461b14d8619a812b207d219707ea0c2790062c32c"
    )
