"""Complete semantic comparison; no externally supplied list can hide a loss."""

from a2_engine.comparison.deltas import (
    ComparisonContext,
    DeltaResult,
    TravelTime,
    delta_index,
    time_saved_seconds,
)
from a2_engine.scenario.models import ScenarioEffect, ScenarioMeasure
from a2_engine.shared.results import MetricResult


def compare_scenario_metrics(
    before: tuple[ScenarioMeasure, ...], after: tuple[ScenarioMeasure, ...]
) -> tuple[ScenarioEffect, ...]:
    left = {m.key: m for m in before}
    right = {m.key: m for m in after}
    if len(left) != len(before) or len(right) != len(after):
        raise ValueError("DUPLICATE_SCENARIO_METRIC")
    if set(left) != set(right):
        raise ValueError("SCENARIO_SEMANTIC_METRIC_ROSTER_CHANGED")
    effects = []
    for key, previous in sorted(left.items()):
        current = right[key]
        if (previous.unit, previous.prefer_higher, previous.scope_hash, previous.metric_id) != (
            current.unit,
            current.prefer_higher,
            current.scope_hash,
            current.metric_id,
        ):
            raise ValueError("SCENARIO_METRIC_SEMANTICS_OR_VERSION_CHANGED")
        if (
            not previous.proven
            or not current.proven
            or "unknown" in (previous.state, current.state)
        ):
            effect = ScenarioEffect(
                current.metric_id,
                key,
                DeltaResult(None, "unknown", "SCENARIO_EFFECT_UNCONFIRMED"),
                "unknown",
            )
        elif previous.state == "absent" and current.state == "known":
            effect = ScenarioEffect(
                current.metric_id,
                key,
                DeltaResult(None, "unknown", "CONNECTION_APPEARED"),
                "new_connection",
            )
        elif previous.state == "known" and current.state == "absent":
            effect = ScenarioEffect(
                current.metric_id,
                key,
                DeltaResult(None, "unknown", "CONNECTION_DISAPPEARED"),
                "lost_connection",
            )
        elif previous.state == current.state == "absent":
            effect = ScenarioEffect(
                current.metric_id, key, DeltaResult(0, "available"), "unchanged"
            )
        else:
            assert previous.value is not None and current.value is not None
            comparison = ComparisonContext(True, previous.scope_hash)
            if previous.unit == "index":
                # Existing signed-index formula; invert only for a lower-is-better gap.
                delta = delta_index(
                    MetricResult(previous.value, "available"),
                    MetricResult(current.value, "available"),
                    comparison=comparison,
                )
                if not previous.prefer_higher:
                    delta = DeltaResult(-(delta.value or 0), "available")
            elif previous.unit == "seconds" and not previous.prefer_higher:
                delta = time_saved_seconds(
                    TravelTime(previous.value, True),
                    TravelTime(current.value, True),
                    comparison=comparison,
                )
            else:
                delta = DeltaResult(
                    (current.value - previous.value)
                    if previous.prefer_higher
                    else (previous.value - current.value),
                    "available",
                )
            from typing import Literal, cast

            outcome = (
                "improved"
                if (delta.value or 0) > 0
                else "worsened"
                if (delta.value or 0) < 0
                else "unchanged"
            )
            effect = ScenarioEffect(
                current.metric_id,
                key,
                delta,
                cast(Literal["improved", "worsened", "unchanged"], outcome),
            )
        effects.append(effect)
    return tuple(effects)


def validate_effect_coverage(
    before: tuple[ScenarioMeasure, ...],
    after: tuple[ScenarioMeasure, ...],
    effects: tuple[ScenarioEffect, ...],
    worsened_metric_ids: tuple[str, ...],
) -> None:
    expected = compare_scenario_metrics(before, after)
    if (
        len({e.metric_id for e in effects}) != len(effects)
        or tuple(sorted(effects, key=lambda e: e.key)) != expected
    ):
        raise ValueError("SCENARIO_EFFECTS_INCOMPLETE_OR_SUBSTITUTED")
    worsened = tuple(
        sorted(e.metric_id for e in expected if e.outcome in ("worsened", "lost_connection"))
    )
    if tuple(sorted(worsened_metric_ids)) != worsened or len(set(worsened_metric_ids)) != len(
        worsened_metric_ids
    ):
        raise ValueError("SCENARIO_WORSENING_OMITTED")
