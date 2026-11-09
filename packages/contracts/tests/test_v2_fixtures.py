"""Published examples are synthetic contract artifacts, never production runs."""

import json
from pathlib import Path

import pytest
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import parse_analysis_request
from a2_contracts.v2.results import AnalysisResult

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v2"
KINDS = ("urban_passenger", "intercity_passenger", "urban_cargo", "intercity_cargo")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("suffix", ["request", "result"])
def test_all_eight_synthetic_fixtures_validate_through_actual_dtos(kind, suffix):
    raw = json.loads((FIXTURES / f"{kind}-{suffix}.json").read_text())
    assert raw["synthetic"] is True
    assert raw["description"]
    payload = raw["payload"]
    parsed = (
        parse_analysis_request(payload)
        if suffix == "request"
        else AnalysisResult.model_validate(payload)
    )
    assert parsed.analysis_kind == kind
    assert parsed.schema_version == "2"
    if suffix == "result":
        assert "synthetic_only" in parsed.limitations


def test_v2_namespace_does_not_replace_legacy_models():
    import a2_contracts
    import a2_contracts.v2 as v2

    assert v2.AcceptedData is not a2_contracts.AcceptedData
    assert v2.AnalysisRequest is not a2_contracts.AnalysisRequest
    assert v2.AnalysisResult is AnalysisResult


def test_synthetic_accepted_data_fixture_validates_as_a_snapshot():
    raw = json.loads((FIXTURES / "accepted-data.json").read_text())
    assert raw["synthetic"] is True
    assert raw["description"]
    parsed = AcceptedData.model_validate(raw["payload"])
    assert parsed.qa.acceptance_state == "accepted"
    assert parsed.schema_version == "2"
    assert parsed.population_cells[0].population == 0
