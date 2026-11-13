"""The longest-leg wording follows the real Leg.kind values of the v2 contract (BUG-007)."""

import json
from pathlib import Path
from typing import get_args

import pytest
from a2_assistant.core import render_explanation
from a2_assistant.grounding.narrative import _LEG_KINDS
from a2_contracts.v2.journeys import Leg
from a2_contracts.v2.results import AnalysisResult
from test_assistant import reply
from test_freeform import answer

ROOT = Path(__file__).resolve().parents[3]
KINDS = get_args(Leg.model_fields["kind"].annotation)
WORDING = {
    "walk": "пеший участок",
    "ride": "поездка",
    "wait_initial": "ожидание первого отправления",
    "wait_transfer": "ожидание при пересадке",
    "transfer_operation": "пересадка",
    "handling": "обработка",
}


def saved_with_longest(kind):
    path = ROOT / "packages/contracts/fixtures/v2/intercity_passenger-result.json"
    saved = AnalysisResult.model_validate(json.loads(path.read_text())["payload"])
    metric = saved.metrics[0]
    journey = next(item for item in saved.journeys if item.journey_id == metric.journey_id)
    longest = journey.legs[0]
    longest.kind = kind
    longest.geometry = None
    longest.duration_seconds = 1 + max(leg.duration_seconds for leg in journey.legs)
    return saved, metric


def test_every_contract_kind_has_its_own_wording():
    assert set(KINDS) == set(_LEG_KINDS) == set(WORDING)
    assert len(set(_LEG_KINDS.values())) == len(KINDS)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_longest_leg_is_named_by_its_real_kind(kind):
    saved, metric = saved_with_longest(kind)
    result, *_ = await answer(
        saved,
        "Какой участок маршрута занимает больше всего времени?",
        "journey",
        responses=[reply({"clause_id": "journey", "findings": [{"metric_id": metric.metric_id}]})],
    )
    assert result.status == "generated"
    text = result.findings[0].text
    assert f"наибольшую продолжительность имеет {WORDING[kind]}." in text
    assert "участок транспортной цепочки" not in text
    # Wording is a description of the saved chain, never a cause or a forecast.
    assert "не прогноз задержек" in text
    assert result.findings[0].claims[0].value == metric.value
    assert render_explanation(result, saved)
