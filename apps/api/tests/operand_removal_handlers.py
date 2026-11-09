"""Adversarial synthetic spawned calculation outputs, never production adapters."""

import json
from pathlib import Path

from a2_contracts.v2.results import AnalysisResult


def control(claim):
    raw = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "packages/contracts/fixtures/v2/required-dependencies-synthetic.json"
        ).read_text()
    )["payload"]
    raw["analysis_id"] = claim.context.analysis_id
    if claim.context.normalized_request.analysis_kind == "urban_passenger":
        raw.update(analysis_kind="urban_passenger", journeys=[], references=[])
        raw["request"] = claim.context.normalized_request.model_dump(mode="json")
        raw["metrics"] = [raw["metrics"][0]]
        raw["metrics"][0].update(
            journey_id=None,
            subject=dict(kind="sample_point", point_id="point-1", category="school", mode="walk"),
        )
        basis = dict(
            population=100,
            population_method="synthetic",
            year=2026,
            coverage=1,
            unit="persons",
            unknown_positive_weight=False,
        )
        raw["metrics"][0]["population_basis"] = dict(basis)
        raw["scores"] = [raw["scores"][0]]
        raw["scores"][0].update(
            subject=dict(kind="territory", territory_id="city-1", mode="walk"),
            aggregation_basis=basis,
            ranking_eligible=False,
        )
        raw["scores"][0].pop("calculation_kind", None)
    return AnalysisResult.model_validate(raw)


def stripped_reference(claim):
    saved = control(claim)
    saved.references[0].completeness = "partial"
    saved.journeys[0].reference_id = None
    for score in saved.scores:
        score.reference_ids = []
    return saved


def stripped_journey(claim):
    saved = stripped_reference(claim)
    saved.metrics[0].journey_id = None
    return saved


def missing_reference(claim):
    saved = stripped_reference(claim)
    saved.references = []
    return saved


def missing_basis(claim):
    saved = control(claim)
    saved.metrics[0].population_basis.coverage = 0.5
    saved.metrics[0].population_basis.unknown_positive_weight = True
    saved.scores[0].aggregation_basis = None
    return saved


def wrong_basis(claim):
    from a2_contracts.v2.results import SiteBasis

    saved = missing_basis(claim)
    saved.scores[0].aggregation_basis = SiteBasis(
        site_count=1, weighting="equal_physical_sites", coverage=1
    )
    return saved
