"""Trusted spawned handlers for synthetic dependency publication regressions only."""

import json
from pathlib import Path

from a2_contracts.v2.results import AnalysisResult


def result(claim):
    raw = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "packages/contracts/fixtures/v2/required-dependencies-synthetic.json"
        ).read_text()
    )["payload"]
    raw["analysis_id"] = claim.context.analysis_id
    return AnalysisResult.model_validate(raw)


def partial_reference(claim):
    saved = result(claim)
    saved.references[0].completeness = "partial"
    return saved


def unknown_reference(claim):
    saved = result(claim)
    ref = saved.references[0]
    ref.status, ref.seconds, ref.reason_code, ref.completeness = (
        "unknown",
        None,
        "REFERENCE_MISSING",
        "partial",
    )
    return saved


def partial_journey(claim):
    saved = result(claim)
    saved.journeys[0].completeness = "partial"
    return saved


def partial_population(claim):
    from a2_contracts.v2.results import PopulationBasis

    saved = result(claim)
    saved.scores[0].ranking_eligible = False
    saved.scores[0].aggregation_basis = PopulationBasis(
        population=100,
        population_method="synthetic",
        year=2026,
        coverage=0.5,
        unit="persons",
        unknown_positive_weight=True,
    )
    return saved
