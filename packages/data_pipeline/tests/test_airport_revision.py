"""The reviewed airport closure is dated; all physical and school inputs persist."""

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from a2_data_pipeline import airport_revision as revision
from a2_data_pipeline.bundle import Evidence
from a2_data_pipeline.contract_v2 import load_accepted_data
from a2_data_pipeline.manifest import load_dataset_manifest, load_registry
from a2_data_pipeline.paths import data_dir
from a2_data_pipeline.store import load_bundle, sha256_file


def test_revision_changes_only_airport_evidence_and_dated_coverage() -> None:
    old, new = load_bundle(revision.BASE_BUNDLE), load_bundle(revision.NEXT_BUNDLE)
    for name in revision.PROTECTED:
        if name in old.manifest.files:
            assert new.manifest.files[name].sha256 == old.manifest.files[name].sha256
            assert getattr(new, name) == getattr(old, name)
    assert new.manifest.grids == old.manifest.grids
    assert new.manifest.graph_version == old.manifest.graph_version
    assert new.manifest.road_graph_version == old.manifest.road_graph_version
    assert (
        new.manifest.source_versions[: len(old.manifest.source_versions)]
        == old.manifest.source_versions
    )
    changed = []
    for before, after in zip(old.destinations, new.destinations, strict=True):
        assert before.model_dump(exclude={"evidence_ids"}) == after.model_dump(
            exclude={"evidence_ids"}
        )
        if before != after:
            changed.append(before.destination_id)
            assert set(before.evidence_ids).issubset(after.evidence_ids)
    assert changed == [revision.AIRPORT]
    assert [d for d in old.destinations if d.category == "school"] == [
        d for d in new.destinations if d.category == "school"
    ]
    assert sum(d.category == "school" and d.status == "accepted" for d in new.destinations) == 79
    assert sum(d.category == "school" and d.status == "quarantined" for d in new.destinations) == 51
    assert len(new.sample_points) == 216
    assert (
        sha256_file(data_dir() / "bundles" / revision.BASE_BUNDLE / "manifest.json").value
        == "d43a87096cd039485cba522bd7e2cd0cc33046474b931ff68c75a6ff9d212f6a"
    )
    assert (
        sha256_file(
            data_dir() / "bundles" / "kaliningrad-urban-2026-11-10.3" / "manifest.json"
        ).value
        == "b174039c6c2d1feb33a46063418e5975afd5bc58095c20e8ff65f73434e25fa1"
    )
    assert [r for r in new.manifest.coverage if r.category != "airport"] == [
        r for r in old.manifest.coverage if r.category != "airport"
    ]


def test_exactly_one_airport_scope_per_day_and_full_only_observed_date() -> None:
    accepted = load_accepted_data(revision.NEXT_BUNDLE)
    scopes = [s for s in accepted.coverage_by_mode_date_category if "airport" in s.categories]
    assert len(scopes) == 3
    for day in range(1, 31):
        at = date(2026, 11, day)
        matches = [s for s in scopes if s.valid_period.start <= at <= s.valid_period.end]
        assert len(matches) == 1
        assert matches[0].completeness == ("full" if at == revision.OBSERVED else "partial")
        assert matches[0].modes == ["walk"]
    assert all(s.valid_period.start is not None and s.valid_period.end is not None for s in scopes)
    assert not any(
        "school" in s.categories and s.completeness == "full"
        for s in accepted.coverage_by_mode_date_category
    )


def test_official_evidence_dates_hashes_and_source_versions_are_bound() -> None:
    base, current = (
        load_accepted_data(revision.BASE_BUNDLE),
        load_accepted_data(revision.NEXT_BUNDLE),
    )
    by_id = {e.evidence_id: e for e in current.evidence}
    assert all(by_id[e.evidence_id] == e for e in base.evidence)
    observations = json.loads((data_dir() / revision.SOURCE_MANIFEST).read_bytes())["sources"]
    by_source = {
        revision.SOURCES[r["source_observation_id"]][0]: r
        for r in observations
        if r["source_observation_id"] in revision.SOURCES
    }
    added = [e for e in current.evidence if e.source_id in by_source]
    assert len(added) == 5
    for e in added:
        row = by_source[e.source_id]
        dataset = load_dataset_manifest(e.source_version)
        registry = load_registry().get(e.source_id)
        assert e.verified is True and e.content_hash == dataset.checksum.value == row["sha256"]
        assert str(e.url) == row["url"]
        assert e.valid_period.start == e.valid_period.end == revision.OBSERVED
        assert dataset.valid_from == dataset.valid_to == revision.OBSERVED
        assert dataset.checksum.size_bytes == row["size_bytes"]
        assert dataset.status == registry.status == "accepted"
        assert dataset.dataset_id in registry.datasets
    for source_id, version in base.source_versions.items():
        assert current.source_versions[source_id] == version


def test_compatibility_content_digest_and_original_native_identity_are_explicit() -> None:
    path = (
        data_dir()
        / "manifests/qa/kaliningrad-airport-native-snapshot-compatibility-2026-11-10.json"
    )
    receipt = json.loads(path.read_bytes())
    canonical = load_accepted_data(revision.NEXT_BUNDLE).model_dump(mode="json")
    content_hash = hashlib.sha256(
        json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode()
    ).hexdigest()
    assert receipt["target_canonical_content_sha256"] == content_hash
    assert (
        receipt["source_snapshot_id"]
        == receipt["native_build_identity_preserved"]["snapshot_id"]
        == "kaliningrad-urban-2026-11-10.3"
    )
    assert receipt["target_snapshot_id"] == revision.NEXT_BUNDLE
    assert receipt["native_artifact_relabelled"] is receipt["index_ready"] is False
    assert receipt["official_roster_full_only_date"] == "2026-11-10"
    assert (
        receipt["canonical_access_policy_replay_sha256"]
        == "acb9a9ec103e81d8365ce987b38a092a4a47d53fe8991500a972d660a86b206b"
    )
    assert (
        receipt["derivative_manifest_sha256"]
        == sha256_file(data_dir() / "bundles" / revision.NEXT_BUNDLE / "manifest.json").value
    )
    assert receipt["selected_pair_invariants"]["original_population_cell"]["population"] > 0
    assert receipt["selected_pair_invariants"]["administrative_city"]["area_km2"] > 100


@pytest.mark.parametrize("mutation", ["graph", "source", "point"])
def test_compatibility_rejects_changed_routing_or_physical_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    old, current = load_bundle("kaliningrad-urban-2026-11-10.3"), load_bundle(revision.NEXT_BUNDLE)
    if mutation == "graph":
        current = current.model_copy(
            update={"manifest": current.manifest.model_copy(update={"graph_version": "changed"})}
        )
    elif mutation == "source":
        versions = list(current.manifest.source_versions)
        versions[0] = versions[0].model_copy(update={"dataset_id": "changed"})
        current = current.model_copy(
            update={"manifest": current.manifest.model_copy(update={"source_versions": versions})}
        )
    else:
        current = current.model_copy(
            update={
                "sample_points": [
                    p.model_copy(update={"lon": p.lon + 0.01})
                    if p.point_id == revision.POINT
                    else p
                    for p in current.sample_points
                ]
            }
        )
    artifact = tmp_path / "artifact.json"
    artifact.write_text(
        json.dumps(
            {"bundle_id": old.manifest.bundle_id, "build": {"snapshot_id": old.manifest.bundle_id}}
        )
    )
    monkeypatch.setattr(
        revision,
        "load_bundle",
        lambda bundle_id: current if bundle_id == revision.NEXT_BUNDLE else old,
    )
    with pytest.raises(ValueError, match="CHANGED"):
        revision.compatibility(old.manifest.bundle_id, artifact)


def test_reviewed_source_manifest_cannot_be_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / revision.SOURCE_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    monkeypatch.setenv("A2_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="REVIEWED_SOURCE_MANIFEST_CHANGED"):
        revision.verified_observations(tmp_path)


def test_reviewed_capture_corruption_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = (data_dir() / revision.SOURCE_MANIFEST).read_bytes()
    rows = json.loads(payload)["sources"]
    manifest = tmp_path / revision.SOURCE_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(payload)
    for row in rows:
        if row["source_observation_id"] in revision.SOURCES:
            (tmp_path / row["local_artifact"]).write_bytes(b"corrupt")
    monkeypatch.setenv("A2_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="CAPTURE_SHA256_OR_SIZE_MISMATCH"):
        revision.verified_observations(tmp_path)


def test_reversed_evidence_period_is_rejected() -> None:
    evidence: dict[str, Any] = load_bundle(revision.NEXT_BUNDLE).evidence[0].model_dump()
    evidence.update(valid_from=date(2026, 11, 11), valid_to=date(2026, 11, 10))
    with pytest.raises(ValueError, match="valid_to precedes valid_from"):
        Evidence.model_validate(evidence)
