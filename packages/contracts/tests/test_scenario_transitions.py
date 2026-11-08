"""Synthetic task identities and explicit, nonnumeric connectivity transitions."""

import pytest
from a2_contracts.v2.scenarios import ScenarioResult
from pydantic import ValidationError
from test_v2_scenarios import metric, result


def transition_result():
    raw = result()
    raw["before"]["metrics"] = [metric("A", 30.0), metric("B", 60.0)]
    raw["after"]["metrics"] = [metric("A", 35.0), metric("B", 40.0)]
    for side in ("before", "after"):
        raw[side]["metrics"][1]["subject"]["destination_id"] = "destination-B"
    raw["deltas"][0].update(before_id="A", after_id="A")
    raw["deltas"].append(
        dict(
            raw["deltas"][0],
            effect_id="B",
            before_id="B",
            after_id="B",
            delta=20.0,
            outcome="improved",
        )
    )
    raw["worsened_metric_ids"] = ["A"]
    return raw


def only_improvement(raw):
    raw["deltas"] = raw["deltas"][1:]
    raw["worsened_metric_ids"] = []
    return raw


def test_full_control_preserves_both_distinct_task_effects():
    assert ScenarioResult.model_validate(transition_result()).worsened_metric_ids == ["A"]


@pytest.mark.parametrize(
    "conceal",
    ["numeric", "deleted", "no-route", "unknown", "units", "mode", "category", "territory"],
)
def test_omitting_a_task_effect_cannot_hide_change_or_disappearance(conceal):
    raw = only_improvement(transition_result())
    after = raw["after"]["metrics"][0]
    if conceal == "deleted":
        raw["after"]["metrics"].pop(0)
    elif conceal in ("no-route", "unknown"):
        after.update(
            value=None,
            status="not_applicable" if conceal == "no-route" else "unknown",
            reason_code="NO_FEASIBLE_ROUTE" if conceal == "no-route" else "DATA_UNAVAILABLE",
        )
    elif conceal == "units":
        after["unit"] = "minutes"
    elif conceal == "mode":
        after["subject"]["mode"] = "walk"
    elif conceal == "category":
        after["subject"]["category"] = "school"
    elif conceal == "territory":
        after["subject"]["territory_id"] = "different-territory"
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


def test_confirmed_lost_connection_is_explicit_without_numeric_saving():
    raw = transition_result()
    raw["after"]["metrics"][0].update(
        value=None, status="not_applicable", reason_code="NO_FEASIBLE_ROUTE"
    )
    raw["deltas"][0].update(delta=None, outcome="lost_connection", reason_code="LOST_CONNECTION")
    saved = ScenarioResult.model_validate(raw)
    assert saved.deltas[0].delta is None and saved.worsened_metric_ids == ["A"]


@pytest.mark.parametrize("direction", ["known-unknown", "unknown-known"])
def test_uncertain_transition_requires_explicit_unknown_and_partial(direction):
    raw = transition_result()
    side = "after" if direction == "known-unknown" else "before"
    raw[side]["metrics"][0].update(value=None, status="unknown", reason_code="DATA_UNAVAILABLE")
    raw["status"] = "partial"
    raw["deltas"][0].update(delta=None, outcome="unknown", reason_code="DATA_UNAVAILABLE")
    raw["worsened_metric_ids"] = []
    assert ScenarioResult.model_validate(raw).status == "partial"
    raw["deltas"][0]["outcome"] = (
        "new_connection" if direction == "unknown-known" else "lost_connection"
    )
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


def test_changed_journey_is_allowed_for_the_same_task():
    raw = transition_result()
    raw["before"]["metrics"][0]["journey_id"] = "old-journey"
    raw["after"]["metrics"][0]["journey_id"] = "new-journey"
    assert ScenarioResult.model_validate(raw).status == "available"


def test_unchanged_task_needs_no_effect():
    raw = transition_result()
    raw["after"]["metrics"][0]["value"] = 30.0
    assert ScenarioResult.model_validate(only_improvement(raw)).status == "available"


@pytest.mark.parametrize("outcome", ["unknown", "lost_connection"])
def test_confirmed_loss_cannot_be_downgraded_to_unreported_unknown(outcome):
    raw = transition_result()
    raw["after"]["metrics"][0].update(
        value=None, status="not_applicable", reason_code="NO_FEASIBLE_ROUTE"
    )
    raw["status"] = "partial"
    raw["deltas"][0].update(delta=None, outcome=outcome, reason_code="LOST_CONNECTION")
    raw["worsened_metric_ids"] = []
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


def test_deleted_task_is_not_replaced_by_new_semantics_or_dangling_effect():
    raw = transition_result()
    raw["after"]["metrics"].pop(0)
    raw["status"] = "partial"
    raw["deltas"][0].update(
        after_id=None, delta=None, outcome="unknown", reason_code="MISSING_AFTER"
    )
    raw["worsened_metric_ids"] = []
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


@pytest.mark.parametrize("semantic", ["unit", "mode", "category", "population"])
def test_changed_stable_task_semantics_require_visible_unknown_partial(semantic):
    raw = transition_result()
    after = raw["after"]["metrics"][0]
    if semantic == "unit":
        after["unit"] = "minutes"
    elif semantic in ("mode", "category"):
        after["subject"][semantic] = "walk" if semantic == "mode" else "school"
    else:
        after["population_basis"] = dict(
            population=100.0,
            population_method="synthetic",
            year=2026,
            coverage=1.0,
            unit="persons",
            unknown_positive_weight=False,
        )
    raw["status"] = "partial"
    raw["deltas"][0].update(delta=None, outcome="unknown", reason_code="INCOMPARABLE_TASK")
    raw["worsened_metric_ids"] = []
    assert ScenarioResult.model_validate(raw).status == "partial"
    raw["status"] = "available"
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


def test_additional_unknown_task_is_allowed_in_honest_partial_result():
    raw = transition_result()
    raw["status"] = "partial"
    raw["after"]["metrics"].append(metric("new-unresolved-task", None))
    assert ScenarioResult.model_validate(raw).status == "partial"
    raw["status"] = "available"
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)
