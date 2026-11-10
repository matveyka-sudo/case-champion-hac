"""A retained scenario estimate slot is distinct from an unproven observation."""

import pytest
from a2_assistant.narrative import _safety
from a2_contracts.v2.results import AnalysisResult
from test_assistant import assistant, explain_saved, reply, score_analysis


def observed_result(*, status, value, completeness, reason):
    raw = score_analysis(value=84.0, partial=True).model_dump(mode="json")
    raw["request"]["passenger_mode"] = "public_transport"
    raw["scores"][0].update(
        name="observed_route_accessibility",
        status=status,
        value=value,
        completeness=completeness,
        reason_code=reason,
    )
    return AnalysisResult.model_validate(raw)


@pytest.mark.parametrize(
    "status,value,completeness,reason",
    [
        ("available", 84.0, "partial", "MINIMUM_NOT_PROVEN"),
        ("available", 84.0, "full", "USE_PROVEN_CATEGORY_INDEX"),
    ],
)
def test_known_observation_retains_unproven_minimum_warning(status, value, completeness, reason):
    saved = observed_result(status=status, value=value, completeness=completeness, reason=reason)
    text = _safety(saved, saved.scores)
    assert "минимум не доказан" in text and "она не заменяет индекс категории" in text
    assert "используйте сохранённый индекс категории" not in text


@pytest.mark.parametrize(
    "status,completeness,reason",
    [
        ("unknown", "partial", "OBSERVED_ROUTE_UNAVAILABLE"),
        ("unknown", "partial", "USE_PROVEN_CATEGORY_INDEX"),
        ("unknown", "full", "USE_PROVEN_CATEGORY_INDEX"),
        ("not_applicable", "partial", "USE_PROVEN_CATEGORY_INDEX"),
        ("not_applicable", "full", "OTHER_REASON"),
    ],
)
def test_unknown_observation_does_not_assert_found_route_or_proof(status, completeness, reason):
    saved = observed_result(status=status, value=None, completeness=completeness, reason=reason)
    text = _safety(saved, saved.scores)
    assert "оценка одного найденного маршрута" not in text
    assert "По недоступной предварительной оценке нельзя подтвердить минимальное время" in text
    assert "Неизвестное значение не означает ноль" in text
    assert "используйте сохранённый индекс категории" not in text


@pytest.mark.asyncio
async def test_proven_auxiliary_slot_uses_canonical_category_without_false_unknown_warning():
    raw = observed_result(
        status="not_applicable", value=None, completeness="full", reason="USE_PROVEN_CATEGORY_INDEX"
    ).model_dump(mode="json")
    raw["scores"].append(
        raw["scores"][0]
        | {
            "score_id": "category",
            "name": "category_index",
            "status": "available",
            "value": 84.0,
            "reason_code": None,
            "ranking_eligible": True,
        }
    )
    saved = AnalysisResult.model_validate(raw)
    original = saved.model_dump_json()
    data = {
        "paragraph": "Предварительный показатель [[score:accessibility]]. [[coverage]]",
        "findings": [{"score_id": "accessibility", "explanation_code": "not_applicable"}],
        "evidence_ids": [],
    }
    agent, _, _, _ = assistant([reply(data)])
    result = await explain_saved(agent, saved, point="point")
    assert result.status == "generated"
    text = result.findings[0].text
    assert "используйте сохранённый индекс категории" in text
    assert "минимум не доказан" not in text and "Неизвестное значение" not in text
    assert result.findings[0].score_claims[0].value is None
    assert result.findings[0].evidence_refs == ["evidence"]
    assert saved.model_dump_json() == original
