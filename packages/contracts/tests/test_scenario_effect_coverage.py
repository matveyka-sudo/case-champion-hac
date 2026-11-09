"""Synthetic regressions for PR #8: omitted effects must not hide worsening."""

import pytest
from a2_contracts.v2.scenarios import ScenarioResult
from pydantic import ValidationError
from test_v2_scenarios import metric, result


def hidden_worsening():
    raw = result()
    raw["before"]["metrics"].append(metric("before-B", 60.0))
    raw["after"]["metrics"].append(metric("after-B", 40.0))
    raw["deltas"] = [
        dict(
            raw["deltas"][0],
            effect_id="effect-B",
            before_id="before-B",
            after_id="after-B",
            delta=20.0,
            outcome="improved",
        )
    ]
    raw["worsened_metric_ids"] = []
    return raw


def test_missing_worsening_is_rejected_even_when_another_route_improves():
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(hidden_worsening())


def complete_pair():
    raw = hidden_worsening()
    raw["deltas"].insert(0, result()["deltas"][0])
    raw["worsened_metric_ids"] = ["after-time"]
    return raw


def score(identifier, value):
    return dict(
        score_id=identifier,
        name="accessibility",
        value=value,
        status="available",
        completeness="full",
        methodology_version="a2-methodology-v3",
        reason_code=None,
        components=[],
        evidence_refs=["e-1"],
        subject=dict(kind="territory", territory_id="city-1", mode="walk"),
        ranking_eligible=False,
        aggregation_basis=dict(
            population=100,
            population_method="synthetic",
            year=2026,
            coverage=1.0,
            unit="persons",
            unknown_positive_weight=False,
        ),
    )


def test_complete_effects_keep_both_improvement_and_worsening():
    saved = ScenarioResult.model_validate(complete_pair())
    assert [d.outcome for d in saved.deltas] == ["worsened", "improved"]
    assert saved.worsened_metric_ids == ["after-time"]


@pytest.mark.parametrize("values", [(60.0, 40.0), (40.0, 60.0)])
def test_every_changed_index_also_requires_its_effect(values):
    raw = result()
    raw["before"]["scores"] = [score("index", values[0])]
    raw["after"]["scores"] = [score("index", values[1])]
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


def test_index_worsening_is_visible_as_an_explicit_score_effect():
    raw = result()
    raw["before"]["scores"] = [score("index", 60.0)]
    raw["after"]["scores"] = [score("index", 40.0)]
    raw["deltas"].append(
        dict(
            effect_id="index-effect",
            kind="score",
            before_id="index",
            after_id="index",
            unit="points_0_100",
            delta=-20.0,
            sign_convention="after_minus_before",
            outcome="worsened",
            reason_code=None,
            scope="transport",
        )
    )
    saved = ScenarioResult.model_validate(raw)
    assert saved.deltas[-1].outcome == "worsened"
    # This field remains raw Metric IDs; score deterioration is explicit in deltas.
    assert saved.worsened_metric_ids == ["after-time"]


@pytest.mark.parametrize("change", ["null", "changed", "improved", "empty-worsened", "duplicate"])
def test_known_worsening_cannot_be_hidden_by_labels_null_or_duplicate_effects(change):
    raw = complete_pair()
    if change == "null":
        raw["deltas"][0].update(delta=None, outcome="unknown", reason_code="UNRESOLVED")
        raw["status"] = "partial"
        raw["worsened_metric_ids"] = []
    elif change == "changed":
        raw["deltas"][0]["outcome"] = "changed"
        raw["worsened_metric_ids"] = []
    elif change == "improved":
        raw["deltas"][0]["outcome"] = "improved"
        raw["worsened_metric_ids"] = []
    elif change == "empty-worsened":
        raw["worsened_metric_ids"] = []
    else:
        raw["deltas"].append(dict(raw["deltas"][0], effect_id="duplicate-pair"))
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


def test_stable_ids_cannot_be_cross_paired_even_when_measure_labels_match():
    raw = complete_pair()
    for group in ("before", "after"):
        for index, item in enumerate(raw[group]["metrics"]):
            item["metric_id"] = f"route-{index}"
    raw["deltas"][0].update(before_id="route-0", after_id="route-1", delta=-10.0)
    raw["deltas"][1].update(before_id="route-1", after_id="route-0", delta=25.0)
    raw["worsened_metric_ids"] = ["route-1"]
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("unit", "minutes"),
        ("name", "another_measure"),
        ("time_kind", "movement"),
        (
            "subject",
            dict(
                kind="connection", origin_id="elsewhere", destination_id="elsewhere-2", mode="rail"
            ),
        ),
    ],
)
def test_numeric_effect_does_not_join_different_measure_semantics(field, value):
    raw = result()
    raw["after"]["metrics"][0][field] = value
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


def test_reordering_stable_ids_never_changes_the_pairing():
    raw = complete_pair()
    for group in ("before", "after"):
        for index, item in enumerate(raw[group]["metrics"]):
            item["metric_id"] = f"route-{index}"
    raw["after"]["metrics"].reverse()
    raw["deltas"][0].update(before_id="route-0", after_id="route-0")
    raw["deltas"][1].update(before_id="route-1", after_id="route-1")
    raw["worsened_metric_ids"] = ["route-0"]
    assert ScenarioResult.model_validate(raw).worsened_metric_ids == ["route-0"]


def test_unique_renamed_measure_is_matched_by_meaning_not_list_position():
    raw = complete_pair()
    for group in ("before", "after"):
        raw[group]["metrics"][1]["subject"]["destination_id"] = "route-B-destination"
    raw["after"]["metrics"].reverse()
    assert ScenarioResult.model_validate(raw).status == "available"
    raw["deltas"] = raw["deltas"][1:]
    raw["worsened_metric_ids"] = []
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


def test_ambiguous_unmapped_renamed_measures_require_explicit_correspondence():
    raw = hidden_worsening()
    # B is the sole supplied effect; two other identical-label routes cannot be paired by order.
    raw["before"]["metrics"].append(metric("before-C", 80.0))
    raw["after"]["metrics"].append(metric("after-C", 90.0))
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


def test_unchanged_known_values_do_not_require_zero_effects():
    raw = result()
    raw["before"]["metrics"].append(metric("unchanged", 60.0))
    raw["after"]["metrics"].append(metric("unchanged", 60.0))
    assert ScenarioResult.model_validate(raw).status == "available"


def test_partial_unknown_does_not_require_an_invented_numeric_delta():
    raw = result()
    raw["status"] = "partial"
    raw["after"]["metrics"][0] = metric("after-time", None)
    raw["deltas"][0].update(delta=None, outcome="unknown", reason_code="DATA_UNAVAILABLE")
    raw["worsened_metric_ids"] = []
    assert ScenarioResult.model_validate(raw).deltas[0].delta is None


def test_all_unknown_values_remain_unknown():
    raw = result()
    raw["status"] = "unknown"
    raw["before"]["metrics"][0] = metric("before-time", None)
    raw["after"]["metrics"][0] = metric("after-time", None)
    raw["deltas"][0].update(delta=None, outcome="unknown", reason_code="DATA_UNAVAILABLE")
    raw["worsened_metric_ids"] = []
    assert ScenarioResult.model_validate(raw).status == "unknown"


def test_noncomparable_values_require_no_fabricated_arithmetic():
    raw = result()
    raw["status"] = "partial"
    raw["after"]["metrics"][0]["unit"] = "minutes"
    raw["deltas"][0].update(delta=None, outcome="unknown", reason_code="INCOMPARABLE_UNITS")
    raw["worsened_metric_ids"] = []
    assert ScenarioResult.model_validate(raw).deltas[0].delta is None


def test_confirmed_new_connection_can_keep_its_nonnumeric_index_event():
    raw = result()
    raw["before"]["scores"] = [score("new-index", 0.0) | {"reason_code": "NO_FEASIBLE_ROUTE"}]
    raw["after"]["scores"] = [score("new-index", 50.0)]
    raw["deltas"].append(
        dict(
            effect_id="new-index-event",
            kind="score",
            before_id="new-index",
            after_id="new-index",
            unit="points_0_100",
            delta=None,
            sign_convention="after_minus_before",
            outcome="new_connection",
            reason_code="NEW_CONNECTION",
            scope="transport",
        )
    )
    assert ScenarioResult.model_validate(raw).deltas[-1].delta is None


def test_incomparable_alias_must_not_consume_a_measure_with_a_matching_counterpart():
    raw = hidden_worsening()
    raw["status"] = "partial"
    raw["after"]["metrics"].append(metric("different-unit", 40.0) | {"unit": "minutes"})
    raw["deltas"].append(
        dict(
            result()["deltas"][0],
            effect_id="conceal-A",
            after_id="different-unit",
            delta=None,
            outcome="unknown",
            reason_code="INCOMPARABLE_UNITS",
        )
    )
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


def test_changed_population_basis_is_not_a_comparable_index():
    raw = result()
    raw["before"]["scores"] = [score("index", 50.0)]
    raw["after"]["scores"] = [
        score("index", 60.0)
        | {
            "aggregation_basis": dict(
                population=200.0,
                population_method="synthetic",
                year=2026,
                coverage=1.0,
                unit="persons",
                unknown_positive_weight=False,
            )
        }
    ]
    raw["deltas"].append(
        dict(
            effect_id="index",
            kind="score",
            before_id="index",
            after_id="index",
            unit="points_0_100",
            delta=10.0,
            sign_convention="after_minus_before",
            outcome="improved",
            reason_code=None,
            scope="transport",
        )
    )
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


@pytest.mark.parametrize("kind", ["metric", "score"])
def test_new_connection_must_not_hide_an_existing_numeric_connection(kind):
    raw = result()
    if kind == "metric":
        raw["before"]["metrics"][0]["reason_code"] = "NO_FEASIBLE_ROUTE"
        effect = raw["deltas"][0]
        raw["worsened_metric_ids"] = []
    else:
        raw["before"]["scores"] = [score("index", 50.0) | {"reason_code": "NO_FEASIBLE_ROUTE"}]
        raw["after"]["scores"] = [score("index", 40.0)]
        effect = dict(
            effect_id="index",
            kind="score",
            before_id="index",
            after_id="index",
            unit="points_0_100",
            sign_convention="after_minus_before",
            scope="transport",
        )
        raw["deltas"].append(effect)
    effect.update(delta=None, outcome="new_connection", reason_code="NEW_CONNECTION")
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)


def test_incomparable_partial_pair_does_not_steal_another_stable_pair():
    raw = result()
    raw["status"] = "partial"
    raw["before"]["metrics"].append(metric("stable-B", 60.0))
    raw["after"]["metrics"].append(metric("stable-B", 60.0))
    raw["after"]["metrics"][0]["unit"] = "minutes"
    raw["deltas"][0].update(delta=None, outcome="unknown", reason_code="INCOMPARABLE_UNITS")
    raw["worsened_metric_ids"] = []
    assert ScenarioResult.model_validate(raw).status == "partial"


@pytest.mark.parametrize("reverse_effects", [False, True])
def test_incomparable_pair_respects_explicit_comparable_aliases_in_any_order(reverse_effects):
    raw = result()
    raw["status"] = "partial"
    raw["before"]["metrics"].append(metric("before-B", 60.0))
    raw["after"]["metrics"].append(metric("after-B", 60.0))
    raw["after"]["metrics"][0]["unit"] = "minutes"
    raw["deltas"][0].update(delta=None, outcome="unknown", reason_code="INCOMPARABLE_UNITS")
    raw["deltas"].append(
        dict(
            result()["deltas"][0],
            effect_id="B",
            before_id="before-B",
            after_id="after-B",
            delta=0.0,
            outcome="unchanged",
        )
    )
    if reverse_effects:
        raw["deltas"].reverse()
    raw["worsened_metric_ids"] = []
    assert ScenarioResult.model_validate(raw).status == "partial"
