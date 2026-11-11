"""Regressions for intercity boundary validity and transfer rule resolution."""

from dataclasses import replace
from datetime import timedelta
from math import sqrt

import pytest
from a2_engine import (
    CargoProfile,
    CargoTemperature,
    CargoVehicle,
    TimetablePath,
    TimetableTransfer,
    search_scheduled_chains,
)
from a2_engine.routing.timetable import solve_timetable
from test_intercity_analysis import START, analyze, event, run, table


@pytest.mark.parametrize("valid_continuation", [False, True])
def test_zero_time_intercity_path_cannot_mask_valid_continuation(
    valid_continuation: bool,
) -> None:
    first_at = 600
    valid_path_seconds = 3600
    direct = run("repeated-destination", "A", first_at, "B", first_at)
    if valid_continuation:
        direct = replace(
            direct,
            events=(
                direct.events[0],
                event("B", first_at, seq=1, pickup=False),
                event("B", first_at + valid_path_seconds, seq=2, pickup=False),
            ),
        )
    result = analyze(table((direct,)), ref_seconds=1800)
    assert "NONPOSITIVE_PASSENGER_PATH" in result.reasons
    assert not result.timetable_complete and not result.full_ranking_eligible
    assert not result.public.optimum_proven

    frequency = next(row for row in result.frequency if row.category == "rail")
    assert frequency.count is None and not frequency.complete
    assert "NONPOSITIVE_PASSENGER_PATH" in frequency.reasons
    best = result.public.best_known
    if valid_continuation:
        assert result.connection_state == "reachable"
        assert best is not None
        assert best.path_seconds == valid_path_seconds
        assert best.total_seconds == first_at + valid_path_seconds
        assert best.evaluation.index.value == pytest.approx(
            100 * sqrt((1800 / valid_path_seconds) * 2 ** (-first_at / 43200))
        )
        assert frequency.known_departure_ids == ("physical:repeated-destination:0",)
    else:
        assert result.connection_state == "unknown"
        assert best is None
        assert not frequency.known_departure_ids


@pytest.mark.parametrize("scope", ["generic", "incoming", "outgoing", "ambiguous", "exact"])
def test_transfer_index_preserves_rule_specificity_in_both_searches(scope: str) -> None:
    incoming = run("in", "A", 0, "X", 60)
    outgoing = run("out", "X", 120, "B", 180)
    generic = TimetableTransfer("generic", "X", "X", 60, True, ("generic-proof",))
    from_rule = replace(
        generic, rule_id="incoming", minimum_seconds=61, from_run_id=incoming.run_id
    )
    to_rule = replace(generic, rule_id="outgoing", minimum_seconds=61, to_run_id=outgoing.run_id)
    exact = replace(
        generic,
        rule_id="exact",
        evidence_refs=("exact-proof",),
        from_run_id=incoming.run_id,
        to_run_id=outgoing.run_id,
    )
    rules = {
        "generic": (generic,),
        "incoming": (generic, from_rule),
        "outgoing": (generic, to_rule),
        "ambiguous": (generic, from_rule, to_rule),
        "exact": (generic, from_rule, to_rule, exact),
    }[scope]
    timetable = table((incoming, outgoing), transfers=rules)

    def urban() -> TimetablePath | None:
        return solve_timetable(timetable, "A", "B", START, max_wait_seconds=60).best_known

    def intercity() -> TimetablePath | None:
        result = search_scheduled_chains(
            timetable, ("A",), ("B",), START, START + timedelta(days=7), (("train", "rail"),)
        )
        return result.candidates[0].path if result.candidates else None

    for search in (urban, intercity):
        if scope == "ambiguous":
            with pytest.raises(ValueError, match="AMBIGUOUS_TRANSFER_RULE"):
                search()
            continue
        path = search()
        if scope in ("incoming", "outgoing"):
            assert path is None
        else:
            assert path is not None
            assert path.total_seconds == 180
            assert len(path.boardings) == 2
            assert scope + "-proof" in path.steps[-1].evidence_refs


def test_cargo_requirements_hash_preserves_signed_zero_identity() -> None:
    profile = CargoProfile(
        "profile",
        "version",
        "food",
        "urgent",
        CargoTemperature(True, 0.0, 1.0, "chilled"),
        (),
        (),
        CargoVehicle("truck", 5000, 2, 2, 5, "access"),
    )
    other = replace(profile, temperature=replace(profile.temperature, min_c=-0.0))
    # Dataclass equality does not capture the distinct canonical JSON bytes.
    assert profile == other
    assert profile.requirements_hash != other.requirements_hash
