"""TEST_ONLY: known route observations cannot claim a proven minimum effect."""

import pytest
from a2_contracts.v2.scenarios import ScenarioResult
from pydantic import ValidationError
from test_v2_scenario_journeys import with_chain


def partial_minimum():
    raw = with_chain()
    for side in ("before", "after"):
        raw[side]["metrics"][0].update(
            name="minimum_journey_seconds",
            completeness="partial",
            reason_code="MINIMUM_TIME_UNPROVEN",
            journey_id="saved-before" if side == "before" else "new-chain",
            time_kind="complete_chain",
        )
    raw["after_journeys"][0].update(scope="urban", completeness="partial")
    raw["deltas"][0].update(
        delta=None, outcome="unknown", reason_code="SCENARIO_EFFECT_UNCONFIRMED"
    )
    raw["worsened_metric_ids"] = []
    raw["status"] = "partial"
    return raw


def test_partial_minimum_preserves_exact_observations_and_unknown_effect():
    raw = partial_minimum()
    parsed = ScenarioResult.model_validate(raw)
    assert parsed.before.metrics[0].value == raw["before"]["metrics"][0]["value"]
    assert parsed.after.metrics[0].value == raw["after"]["metrics"][0]["value"]
    assert parsed.deltas[0].delta is None and parsed.deltas[0].outcome == "unknown"
    assert ScenarioResult.model_validate_json(parsed.model_dump_json()) == parsed


@pytest.mark.parametrize(
    "attack",
    [
        "numeric",
        "missing_effect",
        "full",
        "before_evidence",
        "after_evidence",
        "before_journey",
        "after_journey",
        "journey_value",
        "unrelated_partial",
    ],
)
def test_unproven_minimum_exception_is_narrow_and_reference_closed(attack):
    raw = partial_minimum()
    if attack == "numeric":
        raw["deltas"][0].update(delta=-5.0, outcome="worsened", reason_code=None)
        raw["worsened_metric_ids"] = ["after-time"]
    elif attack == "missing_effect":
        raw["deltas"] = []
    elif attack == "full":
        for side in ("before", "after"):
            raw[side]["metrics"][0]["completeness"] = "full"
    elif attack in {"before_evidence", "after_evidence"}:
        raw[attack.split("_")[0]]["metrics"][0]["source_refs"] = []
    elif attack == "before_journey":
        raw["before"]["metrics"][0]["journey_id"] = None
    elif attack == "after_journey":
        raw["after_journeys"] = []
    elif attack == "journey_value":
        raw["after"]["metrics"][0]["value"] += 1
    else:
        for side in ("before", "after"):
            raw[side]["metrics"][0].update(name="total_seconds", reason_code=None)
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)
