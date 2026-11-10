"""TEST_ONLY saved facts; server wording makes no network or formula calls."""

from copy import deepcopy

import pytest
from a2_assistant import core
from a2_contracts.v2.results import AnalysisResult
from test_assistant import analysis, score_analysis


@pytest.mark.parametrize("value,partial", [(25.0, False), (0.497, True), (None, True)])
def test_saved_walk_formula_and_completeness_preserve_claims(value, partial):
    saved = score_analysis(value=value, partial=partial)
    original = saved.model_dump_json()
    answer = core.template_explanation(saved, "request", "DISABLED")
    text = core.render_explanation(answer, saved)
    assert "Индекс городской категории: I = 100 × 2^(−T/H)" in text
    assert "по классу площади города" in text
    assert "Чем дольше путь, тем ниже индекс" in text
    assert "отдельного разрешённого сценария" in text
    assert ("Предварительная оценка" in text) == partial
    assert ("а не нулевую доступность" in text) == (value is None)
    assert answer.status == "template" and answer.model_id is None
    assert answer.proposals == []
    assert answer.findings[0].score_claims[0].value == value
    assert saved.model_dump_json() == original


def test_generated_walk_wording_remains_fixed_and_exact_saved_time():
    saved = analysis()
    selection = core._ModelSelection.model_validate(
        {"findings": [{"metric_id": "travel-time", "explanation_code": "travel_time"}]}
    )
    built = core._build_content(selection, saved, frozenset())
    core._validate_content(built, saved, frozenset())
    assert built.findings[0].claims[0].value == 1800.0
    assert core._URBAN_FORMULA_NOTE in built.limitations
    assert built.proposals == []
    forged = built.model_copy(update={"limitations": ["H = 3600 секунд"]})
    with pytest.raises(ValueError, match="limitation_invalid"):
        core._validate_content(forged, saved, frozenset())


def test_walk_notes_cannot_be_copied_to_other_mode_or_missing_selected_facts():
    saved = analysis()
    payload = deepcopy(saved.model_dump(mode="json"))
    payload["request"]["passenger_mode"] = "public_transport"
    other = AnalysisResult.model_validate(payload)
    assert not core._urban_walk_notes(other)
    selection = core._ModelSelection.model_validate(
        {"findings": [{"metric_id": "travel-time", "explanation_code": "travel_time"}]}
    )
    built = core._build_content(selection, other, frozenset())
    built.limitations.append(core._URBAN_FORMULA_NOTE)
    with pytest.raises(ValueError, match="limitation_invalid"):
        core._validate_content(built, other, frozenset())
    context = core.ExplanationRequest(
        selection={"entity_kind": "sample_point", "entity_id": "TEST_ONLY-no-facts"}
    )
    answer = core.template_explanation(saved, "request", "NO_SELECTED_METRICS", context=context)
    assert not answer.findings and core._URBAN_FORMULA_NOTE not in answer.limitations


def test_saved_limitations_remain_bounded_with_readable_notes():
    saved = analysis().model_copy(update={"limitations": [f"TEST_ONLY-{i}" for i in range(20)]})
    answer = core.template_explanation(saved, "request", "DISABLED")
    assert len(answer.limitations) <= 20
    assert core._URBAN_FORMULA_NOTE in answer.limitations
