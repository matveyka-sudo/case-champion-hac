"""Unused definitions may shrink; all saved facts and reference edges stay exact."""

import pytest
from a2_api.adapters import product, walk_map, walk_runtime
from a2_api.adapters.result_evidence import compact_result_evidence
from a2_api.adapters.walk_observed import project_observed_walk
from a2_contracts.common import canonical_hash
from a2_contracts.v2.map_data import AnalysisMapData, MapFeature, PointGeometry
from pydantic import ValidationError
from test_evidence_group_packing import leaf_facts
from test_walk_batches import AcknowledgedChannel
from test_walk_observed import setup
from test_walk_runtime import SNAPSHOTS, city_profile, claim_for, configure
from test_walk_runtime import routed as routed


def test_compaction_keeps_nested_map_leg_metric_score_evidence_and_exact_facts():
    out, calculated = setup()
    project_observed_walk(out, calculated)
    result = out.finish()
    extra = result.evidence_refs[0].model_copy(update={"evidence_id": "TEST_ONLY-map-only-proof"})
    unused = extra.model_copy(update={"evidence_id": "TEST_ONLY-unused-proof"})
    feature = MapFeature(
        entity_kind="sample_point",
        entity_id=calculated.plan.inputs.points[0].point_id,
        point_id=calculated.plan.inputs.points[0].point_id,
        territory_id="kaliningrad",
        geometry=PointGeometry(type="Point", coordinates=(20, 54)),
        geometry_kind="actual",
        source_refs=[extra.evidence_id],
    )
    result = type(result).model_validate(
        result.model_dump(mode="json")
        | {
            "evidence_refs": [
                row.model_dump(mode="json") for row in [*result.evidence_refs, extra, unused]
            ],
            "map_data": AnalysisMapData(
                status="available", completeness="partial", features=[feature]
            ).model_dump(mode="json"),
        }
    )
    before = result.model_dump(mode="json", exclude={"evidence_refs", "result_hash"})
    compacted = compact_result_evidence(result)
    assert compacted.model_dump(mode="json", exclude={"evidence_refs", "result_hash"}) == before
    ids = {row.evidence_id for row in compacted.evidence_refs}
    assert extra.evidence_id in ids and unused.evidence_id not in ids
    # Independent explicit traversal of every current canonical evidence edge.
    refs = {ref for row in compacted.metrics for ref in row.source_refs}
    refs |= {ref for row in compacted.scores for ref in row.evidence_refs}
    refs |= {ref for row in compacted.map_data.features for ref in row.source_refs}
    refs |= {ref for row in compacted.references for ref in row.calibration_evidence}
    refs |= {ref for row in compacted.counts for ref in row.evidence_refs}
    refs |= {ref for row in compacted.last_returns for ref in row.evidence_refs}
    for journey in compacted.journeys:
        refs.update(journey.evidence_refs)
        if journey.geometry:
            refs.update(journey.geometry.evidence_refs)
        for leg in journey.legs:
            refs.update(leg.evidence_refs)
            if leg.geometry:
                refs.update(leg.geometry.evidence_refs)
    assert refs <= ids
    assert compacted.result_hash == canonical_hash(
        compacted.model_dump(mode="json", exclude={"result_hash"})
    )
    assert compact_result_evidence(compacted) is compacted


def test_evidence_used_only_by_geometry_calibration_count_or_return_survives():
    out, calculated = setup()
    result = out.finish()
    payload = result.model_dump(mode="json")
    labels = ("journey-geometry", "leg-geometry", "calibration", "count", "last-return")
    ids = {label: "TEST_ONLY-" + label + "-only-proof" for label in labels}
    for identity in [*ids.values(), "TEST_ONLY-unused-proof"]:
        payload["evidence_refs"].append(payload["evidence_refs"][0] | {"evidence_id": identity})
    geometry = {"type": "LineString", "coordinates": [[20, 54], [20.1, 54.1]]}
    payload["journeys"][0]["geometry"] = geometry | {"evidence_refs": [ids["journey-geometry"]]}
    payload["journeys"][0]["geometry_kind"] = "schematic"
    payload["journeys"][0]["legs"][0]["geometry"] = geometry | {
        "evidence_refs": [ids["leg-geometry"]]
    }
    payload["references"].append(
        dict(
            reference_id="TEST_ONLY-reference",
            seconds=None,
            status="unknown",
            completeness="partial",
            reason_code="TEST_ONLY",
            profile_version="edge-calibrated-v1",
            graph_version=result.versions.graph_version,
            boundary_node_sets=dict(
                origin_node_ids=["TEST_ONLY-a"], destination_node_ids=["TEST_ONLY-b"]
            ),
            edges=[],
            necessary_operations=[],
            calibration_evidence=[ids["calibration"]],
        )
    )
    payload["metrics"].append(
        payload["metrics"][0]
        | dict(
            metric_id="TEST_ONLY-count",
            name="TEST_ONLY-count",
            unit="objects",
            value=None,
            status="unknown",
            reason_code="TEST_ONLY",
            source_refs=[],
        )
    )
    payload["counts"].append(
        dict(
            metric_id="TEST_ONLY-count",
            threshold_seconds=None,
            value=None,
            status="unknown",
            reason_code="TEST_ONLY",
            dependency_job_id=None,
            evidence_refs=[ids["count"]],
        )
    )
    payload["last_returns"].append(
        dict(
            point_id=calculated.plan.inputs.points[0].point_id,
            destination_object_id=None,
            journey_id=None,
            status="not_applicable",
            reason_code="TEST_ONLY-walk-only",
            service_date="2026-11-10",
            timezone="Europe/Kaliningrad",
            leave_at=None,
            first_departure_at=None,
            arrival_at=None,
            evidence_refs=[ids["last-return"]],
        )
    )
    result = type(result).model_validate(payload)
    compacted = compact_result_evidence(result)
    retained = {row.evidence_id for row in compacted.evidence_refs}
    assert set(ids.values()) <= retained
    assert "TEST_ONLY-unused-proof" not in retained
    assert compacted.model_dump(mode="json", exclude={"evidence_refs", "result_hash"}) == (
        result.model_dump(mode="json", exclude={"evidence_refs", "result_hash"})
    )


def test_invalid_original_reference_is_rejected_instead_of_disappearing():
    out, _ = setup()
    result = out.finish()
    invalid = result.model_copy(update={"evidence_refs": []})
    with pytest.raises(ValidationError, match="evidence"):
        compact_result_evidence(invalid)


@pytest.mark.asyncio
async def test_bound_walk_compacts_only_after_map_attachment(tmp_path, monkeypatch, routed):
    entry = city_profile(tmp_path, SNAPSHOTS[0])
    configure(tmp_path, monkeypatch, [entry])
    original_attach = walk_map.attach_walk_map
    before = []

    def attach(*args, **kwargs):
        mapped = original_attach(*args, **kwargs)
        payload = mapped.model_dump(mode="json")
        extra = payload["evidence_refs"][0] | {"evidence_id": "TEST_ONLY-map-only-proof"}
        payload["evidence_refs"].extend([extra, extra | {"evidence_id": "TEST_ONLY-unused-proof"}])
        payload["map_data"]["features"][0]["source_refs"].append(extra["evidence_id"])
        payload["result_hash"] = canonical_hash(
            {key: value for key, value in payload.items() if key != "result_hash"}
        )
        mapped = type(mapped).model_validate(payload)
        before.append(mapped)
        return mapped

    monkeypatch.setattr(walk_map, "attach_walk_map", attach)
    claim = await claim_for(entry)
    result = product.calculate(claim, AcknowledgedChannel(claim.context.batch_execution))
    assert len(before) == 1
    actual_facts = leaf_facts(result)
    expected_facts = leaf_facts(before[0])
    actual_facts.pop("evidence_refs")
    expected_facts.pop("evidence_refs")
    assert actual_facts == expected_facts
    ids = {row.evidence_id for row in result.evidence_refs}
    assert "TEST_ONLY-map-only-proof" in ids
    assert "TEST_ONLY-unused-proof" not in ids
    assert len(result.evidence_refs) < len(before[0].evidence_refs)
    assert result.result_hash == canonical_hash(
        result.model_dump(mode="json", exclude={"result_hash"})
    )


@pytest.mark.parametrize(
    "module",
    [
        "a2_api.adapters.result_evidence",
        "a2_api.adapters.evidence_groups",
        "a2_contracts.v2.evidence",
        "a2_contracts.v2.results",
        "a2_contracts.v2.scenarios",
        "a2_engine.urban.valhalla.codec",
        "a2_engine.urban.valhalla.models",
        "a2_engine.urban.valhalla._grisu2",
    ],
)
def test_transitive_execution_source_is_in_executor_fingerprint(tmp_path, monkeypatch, module):
    from types import SimpleNamespace

    source = tmp_path / "TEST_ONLY-result-evidence.py"
    source.write_text("TEST_ONLY first implementation")
    original_import = walk_runtime.importlib.import_module
    seen = []

    def imported(name):
        seen.append(name)
        if name == module:
            return SimpleNamespace(__file__=str(source))
        return original_import(name)

    monkeypatch.setattr(walk_runtime.importlib, "import_module", imported)
    before = walk_runtime.executor_fingerprint()
    source.write_text("TEST_ONLY changed implementation")
    assert walk_runtime.executor_fingerprint() != before
    assert module in seen
