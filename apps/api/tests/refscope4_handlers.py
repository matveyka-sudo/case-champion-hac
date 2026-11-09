"""Synthetic reference-scope cases for isolated calculation/publication regressions."""

import json
from copy import deepcopy
from pathlib import Path

from a2_contracts.v2.results import AnalysisResult


def raw_case(mode):
    raw = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "packages/contracts/fixtures/v2/required-dependencies-synthetic.json"
        ).read_text()
    )["payload"]
    raw["request"].update(origin_node_ids=["demo-a"], destination_node_ids=["demo-b"])
    raw["scores"][0]["reference_ids"] = ["reference"]
    if mode in {"fixed_control", "foreign", "reverse", "superset", "partial", "missing_binding"}:
        raw["metrics"][0]["journey_id"] = None
    if mode in {
        "missing_binding",
        "journey_control",
        "foreign_unfixed",
        "singleton_journey_control",
        "unlinked_multi_scope",
    }:
        raw["request"].update(origin_node_ids=None, destination_node_ids=None)
    if mode in {"foreign", "reverse", "superset", "foreign_unfixed"}:
        replacement = deepcopy(raw["references"][0])
        replacement["reference_id"] = "another-pair-reference"
        replacement["boundary_node_sets"] = dict(
            origin_node_ids=["demo-b"]
            if mode == "reverse"
            else ["demo-a", "foreign-origin"]
            if mode == "superset"
            else ["foreign-origin"],
            destination_node_ids=["demo-a"]
            if mode == "reverse"
            else ["demo-b"]
            if mode == "superset"
            else ["foreign-destination"],
        )
        raw["references"].append(replacement)
        raw["scores"][0]["reference_ids"] = ["another-pair-reference"]
        if mode != "foreign_unfixed":
            raw["references"][0]["completeness"] = "partial"
    if mode in {"fixed_broad_control", "narrower"}:
        raw["request"].update(
            origin_node_ids=["demo-a", "demo-a2"], destination_node_ids=["demo-b", "demo-b2"]
        )
        if mode == "fixed_broad_control":
            raw["references"][0]["boundary_node_sets"] = dict(
                origin_node_ids=["demo-a", "demo-a2"], destination_node_ids=["demo-b", "demo-b2"]
            )
    if mode in {"singleton_journey_control", "unlinked_multi_scope"}:
        raw["journeys"][0]["reference_id"] = None
        if mode == "unlinked_multi_scope":
            raw["references"][0]["boundary_node_sets"]["origin_node_ids"].append("unproven-node")
    if mode == "fixed_foreign_journey":
        foreign = deepcopy(raw["journeys"][0])
        foreign.update(
            journey_id="foreign-path",
            reference_id=None,
            origin_node_id="foreign-origin",
            destination_node_id="foreign-destination",
        )
        foreign["legs"][0].update(from_node_id="foreign-origin", to_node_id="foreign-destination")
        raw["journeys"].append(foreign)
        raw["metrics"][0]["journey_id"] = "foreign-path"
    if mode == "partial":
        raw["references"][0]["completeness"] = "partial"
    if mode == "city_ids_control":
        raw["request"].update(origin_id="origin-city", destination_id="destination-city")
        for item in [*raw["metrics"], *raw["scores"]]:
            item["subject"].update(origin_id="origin-city", destination_id="destination-city")
    if mode == "missing_path":
        raw["scores"] = raw["scores"][1:]
        raw["scores"][-1]["components"] = [dict(kind="score", id="wait-index")]
    if mode == "partial_value":
        raw = raw_case("foreign")
        raw["status"] = "partial"
        for item in raw["scores"]:
            item.update(completeness="partial", ranking_eligible=False)
    return raw


def calculate(claim):
    raw = raw_case(claim.context.snapshot_hash)
    raw["analysis_id"] = claim.context.analysis_id
    return AnalysisResult.model_validate(raw)
