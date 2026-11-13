"""Saved limitations are mandatory: service notes never push them out (BUG-006)."""

import pytest
from a2_assistant import core
from a2_assistant.core import render_explanation, template_explanation
from a2_contracts.v2.results import AnalysisResult
from test_assistant import score_analysis
from test_freeform import answer

SCORE = {"score_id": "accessibility"}
LAST = "MANDATORY_FINAL_WARNING"


def with_limitations(count, *, value=25.0):
    raw = score_analysis(value=value, partial=True).model_dump(mode="json")
    raw["limitations"] = [*(f"SAVED_LIMITATION_{i}" for i in range(count - 1)), LAST]
    return AnalysisResult.model_validate(raw)


async def generated(saved, clause="coverage"):
    result, *_ = await answer(
        saved,
        "Что ограничивает этот индекс?",
        "coverage",
        responses=[__import__("test_assistant").reply({"clause_id": clause, "findings": [SCORE]})],
    )
    assert result.status == "generated"
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [25.0, None], ids=["partial", "unknown"])
@pytest.mark.parametrize("count", [1, 19, 20])
async def test_every_saved_limitation_survives_in_generated_and_template_answers(count, value):
    saved = with_limitations(count, value=value)
    for result in (await generated(saved), template_explanation(saved, "request", "TIMEOUT")):
        assert result.limitations[:count] == saved.limitations
        assert len(result.limitations) <= 20
        # The mandatory warning at the end of the saved list is not displaced.
        assert LAST in result.limitations
        assert render_explanation(result, saved)


@pytest.mark.asyncio
async def test_service_notes_only_fill_the_free_places():
    nineteen, full = with_limitations(19), with_limitations(20)
    assert "Объяснение ограничено сохранёнными данными." in (await generated(nineteen)).limitations
    result = await generated(full)
    assert result.limitations == full.limitations
    template = template_explanation(full, "request", "TIMEOUT")
    assert template.limitations == full.limitations and template.status == "template"


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [21, 40])
async def test_a_longer_saved_list_is_cut_with_an_explicit_note(count):
    saved = with_limitations(count)
    note = core._limitations_note(saved)
    assert note is not None and f"19 из {count}" in note
    for result in (await generated(saved), template_explanation(saved, "request", "TIMEOUT")):
        assert len(result.limitations) == 20
        assert result.limitations[:19] == saved.limitations[:19]
        assert result.limitations[-1] == note
        # The public contract cannot hold the rest: the loss is stated, not hidden.
        assert LAST not in result.limitations
        assert render_explanation(result, saved)


def test_no_note_and_no_duplicates_when_everything_fits():
    saved = with_limitations(3)
    assert core._limitations_note(saved) is None
    own = ["Объяснение ограничено сохранёнными данными.", LAST, LAST]
    rows = core._with_saved_limitations(saved, own)
    assert rows == [*saved.limitations, "Объяснение ограничено сохранёнными данными."]
