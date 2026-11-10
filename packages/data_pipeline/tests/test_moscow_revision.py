"""Candidate radius/service flags cannot establish full Moscow airport coverage."""

import json
from datetime import date

from a2_data_pipeline import moscow_revision as revision
from a2_data_pipeline.contract_v2 import load_accepted_data
from a2_data_pipeline.paths import data_dir
from a2_data_pipeline.store import load_bundle, sha256_file


def test_revision_preserves_every_factual_collection_and_other_coverage() -> None:
    old, new = load_bundle(revision.BASE_BUNDLE), load_bundle(revision.NEXT_BUNDLE)
    assert (
        sha256_file(data_dir() / "bundles" / revision.BASE_BUNDLE / "manifest.json").value
        == revision.BASE_MANIFEST_SHA256
    )
    for name in revision.PROTECTED:
        if name in old.manifest.files:
            assert new.manifest.files[name].sha256 == old.manifest.files[name].sha256
            assert getattr(new, name) == getattr(old, name)
    for field in (
        "grids",
        "graph_version",
        "road_graph_version",
        "source_versions",
        "timetables",
        "valid_from",
        "valid_to",
        "geographic_scope",
        "timezone",
    ):
        assert getattr(new.manifest, field) == getattr(old.manifest, field)
    assert [c for c in new.manifest.coverage if c.category != "airport"] == [
        c for c in old.manifest.coverage if c.category != "airport"
    ]
    assert sum(d.category == "airport" and d.status == "accepted" for d in new.destinations) == 4
    assert sum(d.category == "airport" and d.status == "quarantined" for d in new.destinations) == 1
    assert len(new.sample_points) == 2361 and len(new.population_cells) == 2931


def test_historical_iata_ourairports_and_radius_are_insufficient_for_full_scope() -> None:
    old = load_bundle(revision.BASE_BUNDLE)
    before = next(c for c in old.manifest.coverage if c.category == "airport")
    assert before.status == "complete" and "60" in before.reason
    assert any(ref.startswith("ourairports:") for ref in before.evidence_ids)
    after = next(c for c in revision.corrected_coverage(old) if c.category == "airport")
    assert after.evidence_ids == before.evidence_ids
    assert after.status == "partial" and after.confirmed_absence is False
    assert after.valid_from == date(2026, 11, 1) and after.valid_to == date(2026, 11, 30)
    canonical = load_accepted_data(revision.NEXT_BUNDLE)
    scopes = [c for c in canonical.coverage_by_mode_date_category if "airport" in c.categories]
    assert len(scopes) == 1
    assert scopes[0].completeness == "partial"
    assert scopes[0].valid_period.start == date(2026, 11, 1)
    assert scopes[0].valid_period.end == date(2026, 11, 30)
    assert not any(c.completeness == "full" for c in canonical.coverage_by_mode_date_category)


def test_correction_audit_binds_sources_and_disclaims_minimum_readiness() -> None:
    report = json.loads((data_dir() / revision.QA_MANIFEST).read_bytes())
    old, new = load_bundle(revision.BASE_BUNDLE), load_bundle(revision.NEXT_BUNDLE)
    assert report == revision.correction_report(old)
    assert report["source_manifest_sha256"] == revision.BASE_MANIFEST_SHA256
    assert report["source_versions_unchanged"] == [
        s.model_dump(mode="json") for s in new.manifest.source_versions
    ]
    assert report["category_minimum_proven"] is report["index_ready"] is False
    assert report["new_accepted_objects"] == report["raw_source_retrievals"] == 0
    assert report["all_other_category_coverage_unchanged"] is True
