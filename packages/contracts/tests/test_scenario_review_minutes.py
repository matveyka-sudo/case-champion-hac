"""Independent review control: improvement B must not conceal worsening A (minutes)."""

import pytest
from a2_contracts.v2.scenarios import ScenarioResult
from pydantic import ValidationError
from test_scenario_transitions import transition_result


def minutes_control():
    raw = transition_result()
    for side in ("before", "after"):
        for metric in raw[side]["metrics"]:
            metric["unit"] = "minutes"
    for effect in raw["deltas"]:
        effect["unit"] = "minutes"
    return raw


def test_both_minute_effects_remain_visible():
    saved = ScenarioResult.model_validate(minutes_control())
    assert [effect.delta for effect in saved.deltas] == [-5.0, 20.0]
    assert saved.worsened_metric_ids == ["A"]


@pytest.mark.parametrize(
    "hide", ["omit", "delete", "unknown", "no-route", "lost", "new", "id", "semantic", "duplicate"]
)
def test_minute_worsening_cannot_be_concealed(hide):
    raw = minutes_control()
    if hide == "duplicate":
        raw["deltas"].append(dict(raw["deltas"][0], effect_id="duplicate-A"))
    else:
        raw["deltas"] = raw["deltas"][1:]
        raw["worsened_metric_ids"] = []
        if hide == "delete":
            raw["after"]["metrics"] = raw["after"]["metrics"][1:]
        elif hide in ("unknown", "no-route"):
            raw["after"]["metrics"][0].update(
                value=None,
                status="unknown" if hide == "unknown" else "not_applicable",
                reason_code="DATA_UNAVAILABLE" if hide == "unknown" else "NO_FEASIBLE_ROUTE",
            )
        elif hide in ("lost", "new"):
            raw["deltas"].append(
                dict(
                    raw["deltas"][0],
                    effect_id="false-A-event",
                    before_id="A",
                    after_id="A",
                    delta=None,
                    outcome="lost_connection" if hide == "lost" else "new_connection",
                    reason_code="FALSE_EVENT",
                )
            )
        elif hide == "id":
            raw["after"]["metrics"][0]["metric_id"] = "unrelated-A"
        elif hide == "semantic":
            raw["after"]["metrics"][0]["subject"]["mode"] = "walk"
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


def test_score_calculation_semantics_cannot_change_silently_under_stable_id():
    from test_v2_results import score

    raw = minutes_control()
    old = score()
    old.update(components=[{"kind": "metric", "id": "A"}], evidence_refs=["e-1"])
    raw["before"]["scores"] = [old]
    raw["after"]["scores"] = [dict(old, calculation_kind="initial_wait")]
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)
