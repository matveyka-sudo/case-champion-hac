"""Real revision regression plus synthetic negative source-identity controls."""

import json
from pathlib import Path

import pytest
from a2_data_pipeline import school_revision
from a2_data_pipeline.contract_v2 import load_accepted_data
from a2_data_pipeline.paths import data_dir
from a2_data_pipeline.store import load_bundle, sha256_file


def test_revision_preserves_existing_sites_population_and_unknown_approaches() -> None:
    old = load_bundle(school_revision.BASE_BUNDLE)
    new = load_bundle(school_revision.NEXT_BUNDLE)
    before = {d.destination_id: d for d in old.destinations}
    after = {d.destination_id: d for d in new.destinations}
    assert before.keys() == after.keys()
    changed = {key for key in before if before[key] != after[key]}
    assert changed == {school_revision.DESTINATION_ID}
    for key, old_site in before.items():
        assert (old_site.lon, old_site.lat, old_site.access_node_id) == (
            after[key].lon,
            after[key].lat,
            after[key].access_node_id,
        )
        if old_site.status == "accepted":
            assert old_site == after[key]
    assert old.sample_points == new.sample_points
    assert old.population_cells == new.population_cells
    assert old.access_nodes == new.access_nodes
    assert old.walk_connectors == new.walk_connectors
    assert old.manifest.coverage == new.manifest.coverage
    assert new.manifest.source_versions[:-1] == old.manifest.source_versions
    assert len({s.source_id for s in new.manifest.source_versions}) == len(
        new.manifest.source_versions
    )
    ownership = next(
        o for o in new.category_ownership if o.destination_id == school_revision.DESTINATION_ID
    )
    assert (ownership.ownership, ownership.basis, ownership.verification_state) == (
        "municipal",
        "registry",
        "verified",
    )
    assert (
        len([d for d in new.destinations if d.category == "school" and d.status == "accepted"])
        == 77
    )
    assert new.qa_report["summary"]["school_revision"]["selected_endpoints"] == 293
    assert new.qa_report["summary"]["school_revision"]["traversable_endpoints"] == 229
    assert new.qa_report["summary"]["school_revision"]["unknown_point_connectors"] == 26
    assert new.qa_report["summary"]["school_revision"]["unknown_accepted_school_connectors"] == 38
    accepted = load_accepted_data(school_revision.NEXT_BUNDLE)
    assert accepted is not None


def test_changed_founder_bytes_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "changed.html"
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Founder-list bytes differ"):
        school_revision.verified_site(source, tmp_path / "absent.json")


def test_audit_accounts_for_every_base_quarantine_and_unknown_endpoint() -> None:
    old = load_bundle(school_revision.BASE_BUNDLE)
    report = json.loads(
        (data_dir() / "manifests/qa/kaliningrad-school-audit-2026-11-10.json").read_text()
    )
    assert {r["destination_id"] for r in report["quarantined_schools"]} == {
        d.destination_id
        for d in old.destinations
        if d.category == "school" and d.status == "quarantined"
    }
    schools = {
        d.destination_id
        for d in old.destinations
        if d.category == "school" and d.status == "accepted"
    }
    original = {
        c.physical_node_id: set(c.reasons)
        for c in old.walk_connectors
        if c.traversable is None and (c.entity_kind == "point" or c.entity_id in schools)
    }
    audited = {c["physical_node_id"]: c for c in report["unknown_connectors"]}
    assert original.keys() == audited.keys()
    assert len(original) == 64
    for key, reasons in original.items():
        assert reasons == set(audited[key]["reproduced_reasons"])
        assert audited[key]["reason_set_matches"] is True
        for obstacle in audited[key]["obstacles"]:
            assert obstacle["osm_ref"].startswith(("way/", "relation/"))
            assert len(obstacle["record_sha256"]) == 64
    closed = next(c for c in audited.values() if c["reasons"] == ["CROSSES_CLOSED_GATE"])
    gate = closed["obstacles"][0]["gates_on_barrier"][0]
    assert gate["osm_ref"] == "node/3577400375"
    assert gate["tags"]["foot"] == "no"
    assert gate["distance_to_crossing_m"] == 0


def test_school_number_without_exact_site_address_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Synthetic source negative control: the institution is right, the site is wrong.
    source = tmp_path / "synthetic-founder-list.html"
    source.write_bytes(
        (
            '<tr><td><a href="detail.php?ID=4418524">'
            "МАОУ гимназия № 40 им. Ю.А. Гагарина</a></td>"
            "<td>Ленинградский<br>ул. Ю. Маточкина, 4 (корпус 1)</td></tr>"
        ).encode("cp1251")
    )
    monkeypatch.setattr(school_revision, "LIST_SHA256", sha256_file(source).value)
    audit = data_dir() / "manifests/qa/kaliningrad-school-audit-2026-11-10.json"
    with pytest.raises(ValueError, match="does not confirm the second physical site"):
        school_revision.verified_site(source, audit)
