"""Whole-chain controls and an independent exhaustive finite-schedule oracle."""

import importlib.util
import math
import os
import random
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from a2_engine import (
    AcceptedIntercityCar,
    BoardingReadiness,
    CapitalBoundary,
    IntercityAnalysisScope,
    IntercityPassengerAnalysis,
    PhysicalDeparture,
    PhysicalService,
    Timetable,
    TimetableEvent,
    TimetableFootpath,
    TimetableRun,
    TimetableTransfer,
    TransportModeBinding,
    analyze_intercity_passenger,
    build_reference,
    intercity_passenger_rules_from_mapping,
    load_intercity_passenger_rules,
    load_passenger_config,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/synthetic_intercity.py"
_spec = importlib.util.spec_from_file_location("synthetic_intercity", EXAMPLE)
assert _spec is not None and _spec.loader is not None
_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_demo)
START = datetime(2026, 11, 9, tzinfo=UTC)
PROOF = ("synthetic-proof",)
BINDINGS = (
    TransportModeBinding("train", "rail", PROOF),
    TransportModeBinding("flight", "air", PROOF),
    TransportModeBinding("bus", "road", PROOF),
)


def event(
    node: str,
    seconds: float,
    *,
    seq: int,
    dwell: float = 0,
    pickup: bool = True,
    dropoff: bool = True,
) -> TimetableEvent:
    return TimetableEvent(
        seq,
        node,
        START + timedelta(seconds=seconds),
        START + timedelta(seconds=seconds + dwell),
        pickup,
        dropoff,
        PROOF,
    )


def run(name: str, a: str, dep: float, b: str, arr: float, kind: str = "train") -> TimetableRun:
    return TimetableRun(
        "snapshot",
        "source",
        name,
        date(2026, 11, 9),
        "UTC",
        kind,
        (event(a, dep, seq=0), event(b, arr, seq=1)),
        True,
        PROOF,
    )


def table(
    runs: tuple[TimetableRun, ...],
    paths: tuple[TimetableFootpath, ...] = (),
    transfers: tuple[TimetableTransfer, ...] = (),
    **changes: Any,
) -> Timetable:
    _, base = _demo.demo_inputs()
    nodes = {"A", "B", "X", "Y", "C"} | {e.node_id for r in runs for e in r.events}
    nodes.update(p.from_node_id for p in paths)
    nodes.update(p.to_node_id for p in paths)
    scope = replace(
        base.scope, node_ids=frozenset(nodes), end_at=START + timedelta(days=14), **changes
    )
    return Timetable(scope, runs, paths, transfers)


def services(timetable: Timetable) -> tuple[PhysicalService, ...]:
    return tuple(
        PhysicalService(
            r.trip_id,
            (r.run_id,),
            tuple(
                PhysicalDeparture(e.sequence, f"physical:{r.trip_id}:{e.sequence}", PROOF)
                for e in r.events[:-1]
            ),
            PROOF,
        )
        for r in timetable.runs
    )


def analyze(
    timetable: Timetable,
    *,
    ref_seconds: float = 1800,
    scope: IntercityAnalysisScope | None = None,
    physical: tuple[PhysicalService, ...] | None = None,
    **kwargs: Any,
) -> IntercityPassengerAnalysis:
    base_scope, _ = _demo.demo_inputs()
    scope = scope or base_scope
    reference = kwargs.pop(
        "reference",
        build_reference(_demo.demo_reference(scope, ref_seconds), scope.comparison_scope),
    )
    return analyze_intercity_passenger(
        scope,
        START,
        timetable,
        BINDINGS,
        services(timetable) if physical is None else physical,
        reference,
        config=load_passenger_config(),
        rules=load_intercity_passenger_rules(),
        frequency_start_at=START,
        frequency_end_at=START + timedelta(days=7),
        **kwargs,
    )


def test_later_arrival_beats_earliest_arrival_by_whole_chain_score() -> None:
    result = _demo.run_demo()
    assert isinstance(result, IntercityPassengerAnalysis)
    winner = result.public.best_known
    assert winner is not None
    assert winner.initial_wait_seconds == 3600 and winner.path_seconds == 1800
    assert winner.evaluation.index.value == pytest.approx(97.15319411536)
    other = next(c for c in result.candidates if c.initial_wait_seconds == 0)
    assert other.evaluation.index.value == pytest.approx(70.71067811865)
    assert other.route.path.arrival_at < winner.route.path.arrival_at
    assert result.public.optimum_proven and result.full_ranking_eligible


def test_mixed_time_decomposition_and_alight_to_departure_minimum() -> None:
    r1 = run("r1", "A", 43200, "X", 45000)
    r2 = run("r2", "Y", 46800, "B", 50400, "flight")
    path = TimetableFootpath("walk", "X", "Y", 600, True, PROOF)
    rule = TimetableTransfer("minimum", "X", "Y", 1800, True, PROOF)
    result = analyze(table((r1, r2), (path,), (rule,)), ref_seconds=3600)
    best = result.public.best_known
    assert best is not None
    assert (best.initial_wait_seconds, best.path_seconds, best.total_seconds) == (
        43200,
        7200,
        50400,
    )
    assert (best.riding_seconds, best.walking_seconds, best.transfer_wait_seconds) == (
        5400,
        600,
        1200,
    )
    assert best.evaluation.path_index.value == best.evaluation.waiting_index.value == 50
    assert best.evaluation.index.value == pytest.approx(50)
    assert best.category == "mixed" and best.boardings == 2 and best.transfers == 1
    assert result.frequency[-1].count == 1


def test_first_window_half_open_and_continuations_beyond_seven_days() -> None:
    last = 7 * 86400 - 0.000001
    a = run("at-start", "A", 0, "B", 1)
    b = run("before-end", "A", last, "X", 7 * 86400 + 100)
    c = run("at-end", "A", 7 * 86400, "B", 7 * 86400 + 1)
    continuation = run("after-end", "X", 7 * 86400 + 200, "B", 7 * 86400 + 300)
    result = analyze(
        table(
            (a, b, c, continuation), transfers=(TimetableTransfer("t", "X", "X", 0, True, PROOF),)
        )
    )
    assert len(result.candidates) == 2
    assert {c.initial_wait_seconds for c in result.candidates} == {0, last}
    assert any(c.route.path.arrival_at > START + timedelta(days=7) for c in result.candidates)
    assert result.frequency[-1].count == 2 and result.public.optimum_proven


def test_waits_above_urban_four_hours_are_valid() -> None:
    result = analyze(
        table(
            (
                run("first", "A", 43200, "X", 45000),
                run("later", "X", 45000 + 5 * 3600, "B", 45000 + 6 * 3600),
            ),
            transfers=(TimetableTransfer("wait", "X", "X", 0, True, PROOF),),
        )
    )
    assert result.public.best_known is not None
    assert result.public.best_known.initial_wait_seconds == 43200
    assert result.public.best_known.transfer_wait_seconds == 5 * 3600
    assert result.public.optimum_proven


def test_staying_onboard_dwell_and_pickup_dropoff() -> None:
    through = replace(
        run("through", "A", 8 * 3600, "B", 9 * 3600),
        events=(
            event("A", 8 * 3600, seq=0),
            event("X", 8.5 * 3600, seq=1, dwell=600, pickup=False, dropoff=False),
            event("B", 9 * 3600, seq=2),
        ),
    )
    result = analyze(table((through,)))
    best = result.public.best_known
    assert best is not None and best.path_seconds == best.riding_seconds == 3600
    assert best.boardings == 1 and best.transfers == 0 and best.transfer_wait_seconds == 0
    assert (
        analyze(
            table(
                (
                    replace(
                        through,
                        events=(replace(through.events[0], pickup=False),) + through.events[1:],
                    ),
                )
            )
        ).connection_state
        == "confirmed_absent"
    )
    assert (
        analyze(
            table(
                (
                    replace(
                        through,
                        events=through.events[:-1] + (replace(through.events[-1], dropoff=False),),
                    ),
                )
            )
        ).connection_state
        == "confirmed_absent"
    )


def test_run_specific_transfer_keeps_slower_compatible_arrival() -> None:
    fast, slow, onward = (
        run("fast", "A", 0, "X", 100),
        run("slow", "A", 0, "X", 200),
        run("onward", "X", 300, "B", 400),
    )
    rules = (
        TimetableTransfer("forbid", "X", "X", 0, False, PROOF, fast.run_id, onward.run_id),
        TimetableTransfer("permit", "X", "X", 100, True, PROOF, slow.run_id, onward.run_id),
    )
    result = analyze(table((fast, slow, onward), transfers=rules))
    assert len(result.candidates) == 1
    best = result.public.best_known
    assert best is not None and best.first_physical_departure_id == "physical:slow:0"


@pytest.mark.parametrize(
    "allowed,minimum,expected",
    [
        (None, 0, "unknown"),
        (True, None, "unknown"),
        (False, 0, "confirmed_absent"),
        (True, 0, "reachable"),
    ],
)
def test_unknown_transfer_not_zero_or_forbidden(
    allowed: bool | None, minimum: float | None, expected: str
) -> None:
    rule = TimetableTransfer("t", "X", "X", minimum, allowed, PROOF)
    result = analyze(table((run("a", "A", 0, "X", 1), run("b", "X", 2, "B", 3)), transfers=(rule,)))
    assert result.connection_state == expected


def test_boundary_walks_never_create_first_boarding_or_final_arrival() -> None:
    paths = (
        TimetableFootpath("access", "A", "X", 10, True, PROOF),
        TimetableFootpath("tail", "Y", "B", 10, True, PROOF),
    )
    bad_first = run("bad-first", "X", 20, "B", 100)
    bad_last = run("bad-last", "A", 20, "Y", 100)
    valid = run("valid", "A", 30, "B", 200)
    result = analyze(table((bad_first, bad_last, valid), paths))
    assert len(result.candidates) == 1
    assert result.candidates[0].initial_wait_seconds == 30
    assert result.candidates[0].walking_seconds == 0
    assert analyze(table((), paths)).connection_state == "confirmed_absent"
    assert not analyze(table((), paths)).candidates


def test_modes_selected_independently_and_car_outside_public_maximum() -> None:
    scope, _ = _demo.demo_inputs()
    tt = table(
        (
            run("rail", "A", 0, "B", 7200),
            run("mix1", "A", 0, "X", 900),
            run("mix2", "X", 1200, "B", 3600, "flight"),
        ),
        transfers=(TimetableTransfer("t", "X", "X", 0, True, PROOF),),
    )
    car = AcceptedIntercityCar("car", scope.comparison_scope, "A", "B", 100, True, PROOF, "live")
    result = analyze(tt, scope=scope, car=car)
    by_mode = {row.category: row for row in result.by_mode}
    assert by_mode["rail"].best_known is not None and by_mode["mixed"].best_known is not None
    assert result.public.best_known == by_mode["mixed"].best_known
    assert result.car is not None and result.car.path_index.value == 100
    assert result.public.best_known.evaluation.index.value == pytest.approx(math.sqrt(50) * 10)
    # Walking between two trains is rail, not mixed.
    changed = replace(tt, runs=tuple(replace(r, transport_type="train") for r in tt.runs))
    assert all(c.category == "rail" for c in analyze(changed).candidates)


def test_codeshare_aliases_combinations_and_identical_time_independent_departures() -> None:
    first = run("physical-one", "A", 0, "X", 100)
    alias = replace(first, trip_id="marketing-alias")
    independent = replace(first, trip_id="physical-two")
    onward = tuple(
        run(f"next:{i}", "X", 200 + i, "B", 1000 + 100 * i, "flight" if i % 2 else "train")
        for i in range(3)
    )
    tt = table(
        (first, alias, independent) + onward,
        transfers=(TimetableTransfer("t", "X", "X", 0, True, PROOF),),
    )
    grouped = PhysicalService(
        "one", (first.run_id, alias.run_id), (PhysicalDeparture(0, "departure:one", PROOF),), PROOF
    )
    rest = services(replace(tt, runs=(independent,) + onward))
    result = analyze(tt, physical=(grouped,) + rest)
    assert result.frequency[-1].count == 2
    assert set(result.frequency[-1].known_departure_ids) == {
        "departure:one",
        "physical:physical-two:0",
    }
    groups = {row.category: row for row in result.frequency}
    assert groups["rail"].count == groups["mixed"].count == 2
    assert result.frequency[-1].count != groups["rail"].count + groups["mixed"].count
    # Identical event times are not used to merge unrelated departures without evidence.
    unknown = analyze(tt, physical=())
    assert unknown.frequency[-1].count is None and unknown.public.best_known is not None
    assert unknown.public.optimum_proven


def test_alias_continuation_not_fake_boarding_and_repeated_visits_distinct() -> None:
    through = replace(
        run("through", "A", 0, "B", 1000),
        events=(event("A", 0, seq=0), event("A", 100, seq=1, dwell=10), event("B", 1000, seq=2)),
    )
    alias = replace(through, trip_id="alias")
    tt = table((through, alias))
    service = PhysicalService(
        "real",
        (through.run_id, alias.run_id),
        (PhysicalDeparture(0, "visit:0", PROOF), PhysicalDeparture(1, "visit:1", PROOF)),
        PROOF,
    )
    result = analyze(tt, physical=(service,))
    assert result.frequency[-1].count == 2
    assert all(c.boardings == 1 and c.transfers == 0 for c in result.candidates)


@pytest.mark.parametrize(
    "field",
    [
        "schedule_complete",
        "calendar_complete",
        "footpaths_complete",
        "transfers_complete",
        "continuations_complete",
    ],
)
def test_schedule_coverage_separate_from_known_chain_scalar(field: str) -> None:
    result = analyze(
        table((run("direct", "A", 0, "B", 3600),), **cast(dict[str, Any], {field: False}))
    )
    assert result.public.best_known is not None
    assert result.public.best_known.evaluation.completeness == "full"
    assert not result.public.optimum_proven and not result.full_ranking_eligible
    assert result.frequency[-1].count is None


def test_partial_unknown_and_inapplicable_reference_keep_raw_data() -> None:
    scope, _ = _demo.demo_inputs()
    graph = _demo.demo_reference(scope)
    tt = table((run("direct", "A", 100, "B", 3700),))
    partial = build_reference(replace(graph, coverage="partial"), scope.comparison_scope)
    result = analyze(tt, reference=partial)
    assert (
        result.public.best_known is not None
        and result.public.best_known.evaluation.index.value is not None
    )
    assert "PARTIAL_REFERENCE" in result.public.best_known.evaluation.diagnostics
    assert not result.public.optimum_proven and result.frequency[-1].count == 1
    unknown = build_reference(replace(graph, edges=(), coverage="partial"), scope.comparison_scope)
    for reference in (
        unknown,
        replace(
            partial,
            calibrated_graph=replace(
                partial.calibrated_graph,
                scope=replace(scope.comparison_scope, origin_territory_id="different"),
            ),
        ),
    ):
        missing = analyze(tt, reference=reference)
        assert missing.public.best_known is None
        assert missing.candidates[0].path_seconds == 3600
        assert missing.candidates[0].evaluation.waiting_index.value is not None
        assert missing.frequency[-1].count == 1


def test_mode_coverage_and_missing_mapping_never_full() -> None:
    scope, _ = _demo.demo_inputs()
    result = analyze(
        table((run("direct", "A", 0, "B", 3600),)), scope=replace(scope, modes_complete=False)
    )
    assert result.public.best_known is not None and not result.public.optimum_proven
    unmapped = analyze(table((run("direct", "A", 0, "B", 3600, "not-a-train-substring"),)))
    assert unmapped.connection_state == "unknown" and not unmapped.candidates


def test_cancellation_partial_best_known_and_no_score100_early_stop() -> None:
    tt = table(tuple(run(f"r:{i}", "A", 0, "B", 100 + i) for i in range(4)))
    full = analyze(tt)
    assert full.frequency[-1].count == 4
    assert (
        full.public.best_known is not None and full.public.best_known.evaluation.index.value == 100
    )
    zero = analyze(tt, resource_limit=0)
    assert zero.search.interrupted and zero.connection_state == "unknown" and not zero.candidates
    limited = analyze(tt, resource_limit=1)
    assert limited.public.best_known is not None
    assert limited.timetable_complete and limited.search.interrupted
    assert not limited.public.optimum_proven and limited.frequency[-1].count is None


def test_full_no_route_distinct_from_unknown_calendar_and_inactive_exception() -> None:
    r = run("direct", "A", 0, "B", 100)
    assert analyze(table((replace(r, active=False),))).connection_state == "confirmed_absent"
    assert analyze(table((replace(r, active=None),))).connection_state == "unknown"
    assert analyze(table(())).connection_state == "confirmed_absent"
    assert analyze(table((), schedule_complete=False)).connection_state == "unknown"


def test_readiness_horizon_validation_and_region_capital_binding() -> None:
    tt = table((run("early", "A", 0, "B", 100), run("later", "A", 1000, "B", 1100)))
    result = analyze(tt, readiness=(BoardingReadiness("A", START + timedelta(seconds=1), PROOF),))
    assert len(result.candidates) == 1 and result.candidates[0].initial_wait_seconds == 1000
    truncated = replace(tt, scope=replace(tt.scope, end_at=START + timedelta(days=2)))
    assert not analyze(truncated).public.optimum_proven
    scope, _ = _demo.demo_inputs()
    with pytest.raises(ValueError, match="CONTEXT_MISMATCH"):
        analyze(replace(tt, scope=replace(tt.scope, graph_version="wrong")))
    with pytest.raises(ValueError, match="CAPITAL"):
        replace(scope, territory_kind="regions")
    regions = replace(
        scope,
        territory_kind="regions",
        capital_boundaries=(
            CapitalBoundary("city-A", "capital-A", ("A",), PROOF),
            CapitalBoundary("city-B", "capital-B", ("B",), PROOF),
        ),
    )
    assert analyze(tt, scope=regions).scope.territory_kind == "regions"


def test_timezone_dst_after_midnight_and_service_time_above24_normalized() -> None:
    # Caller supplies service date Nov8 and normalized local 25:00 / next-day 01:00.
    r = run("overnight", "A", 3600, "B", 7200)
    r = replace(
        r,
        service_date=date(2026, 11, 8),
        timezone="Europe/Moscow",
        events=(
            replace(
                r.events[0],
                arrival_at=(START + timedelta(hours=1)).astimezone(ZoneInfo("Europe/Moscow")),
                departure_at=(START + timedelta(hours=1)).astimezone(ZoneInfo("Europe/Moscow")),
            ),
            replace(
                r.events[1],
                arrival_at=(START + timedelta(hours=2)).astimezone(ZoneInfo("Asia/Vladivostok")),
                departure_at=(START + timedelta(hours=2)).astimezone(ZoneInfo("Asia/Vladivostok")),
            ),
        ),
    )
    result = analyze(table((r,)))
    assert result.candidates[0].path_seconds == 3600
    # DST fallback: two local 01:30 timestamps are one physical UTC hour apart.
    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    last = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    dst = replace(
        r,
        service_date=date(2026, 11, 1),
        timezone=zone.key,
        events=(
            replace(r.events[0], arrival_at=first, departure_at=first),
            replace(r.events[1], arrival_at=last, departure_at=last),
        ),
    )
    scope, base = _demo.demo_inputs()
    dst_table = replace(
        base,
        scope=replace(
            base.scope,
            start_at=first.astimezone(UTC),
            end_at=last.astimezone(UTC) + timedelta(days=8),
        ),
        runs=(dst,),
    )
    reference = build_reference(_demo.demo_reference(scope), scope.comparison_scope)
    analysis = analyze_intercity_passenger(
        scope,
        first,
        dst_table,
        BINDINGS,
        services(dst_table),
        reference,
        config=load_passenger_config(),
        rules=load_intercity_passenger_rules(),
        frequency_start_at=first,
        frequency_end_at=first.astimezone(UTC) + timedelta(days=7),
    )
    assert (
        analysis.candidates[0].path_seconds == 3600
        and analysis.candidates[0].initial_wait_seconds == 0
    )


def test_permutation_stable_ids_zero_walk_cycles_and_no_mutation() -> None:
    tt = table(
        (run("a", "A", 0, "X", 100), run("b", "X", 200, "B", 300)),
        (TimetableFootpath("zero-loop", "X", "X", 0, True, PROOF),),
        (TimetableTransfer("t", "X", "X", 0, True, PROOF),),
    )
    result = analyze(tt)
    assert result == analyze(replace(tt, runs=tuple(reversed(tt.runs))))
    assert tt.runs == tuple(sorted(tt.runs, key=lambda r: r.run_id))
    assert result.public.best_known is not None and result.public.best_known.walking_seconds == 0
    assert result.public.best_known.boardings == 2


def test_fresh_process_identity_hash_seeds(tmp_path: Path) -> None:
    code = "import runpy,sys; print(runpy.run_path(sys.argv[1])['run_demo']().analysis_id)"
    outputs = set()
    for seed in ("0", "72", "random"):
        completed = subprocess.run(
            [sys.executable, "-c", code, str(EXAMPLE)],
            cwd=tmp_path,
            env={
                **os.environ,
                "PYTHONPATH": str(EXAMPLE.parents[1] / "src"),
                "PYTHONHASHSEED": seed,
            },
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.add(completed.stdout)
    assert len(outputs) == 1


def test_alias_conflicts_and_identity_errors_rejected() -> None:
    first, second = run("a", "A", 0, "B", 100), run("b", "A", 0, "B", 200)
    tt = table((first, second))
    group = PhysicalService(
        "bad", (first.run_id, second.run_id), (PhysicalDeparture(0, "p", PROOF),), PROOF
    )
    with pytest.raises(ValueError, match="INCONSISTENT_PHYSICAL"):
        analyze(tt, physical=(group,))
    with pytest.raises(ValueError):
        analyze(tt, physical=(replace(group, run_ids=("unknown",)),))
    with pytest.raises(ValueError):
        analyze(
            tt,
            physical=(
                replace(
                    group, run_ids=(first.run_id,), departures=(PhysicalDeparture(99, "p", PROOF),)
                ),
            ),
        )
    with pytest.raises(ValueError):
        PhysicalDeparture(0, "p", ())


def test_window_loader_reads_config_without_changing_passenger_config(
    product: dict[str, Any],
) -> None:
    assert load_intercity_passenger_rules().departure_window_days == 7
    assert intercity_passenger_rules_from_mapping(product).departure_window_days == 7
    for value in (True, 0, -1, "7", float("inf")):
        product["methodologies"]["intercity_passenger"]["parameters"]["departure_window_days"] = (
            value
        )
        with pytest.raises(ValueError):
            intercity_passenger_rules_from_mapping(product)


def test_independent_exhaustive_whole_chain_oracle() -> None:
    rng = random.Random(6638)
    for case in range(35):
        # Finite acyclic stop graph. Oracle exhaustively enumerates every whole
        # chain from RAW events, with no engine queue, labels, dominance or scorer.
        legs = tuple(
            run(
                f"{case}:{i}",
                a,
                100 + i * 300 + rng.randrange(20),
                b,
                250 + i * 300 + rng.randrange(20),
                kind,
            )
            for i, (a, b, kind) in enumerate(
                (
                    ("A", "X", "train"),
                    ("A", "X", "bus"),
                    ("A", "Y", "train"),
                    ("A", "B", "train"),
                    ("X", "Y", "flight"),
                    ("X", "B", "train"),
                    ("X", "B", "flight"),
                    ("Y", "B", "flight"),
                    ("Y", "B", "train"),
                )
            )
            if rng.random() < 0.8
        )
        paths = (TimetableFootpath("xy", "X", "Y", 30, True, PROOF),)
        rules = tuple(
            TimetableTransfer(f"t:{a}:{b}", a, b, 100, True, PROOF)
            for a, b in (("X", "X"), ("Y", "Y"), ("X", "Y"))
        )
        tt = table(legs, paths, rules)
        seconds = rng.randrange(100, 1200)
        result = analyze(tt, ref_seconds=seconds)
        mode_map = {"train": "rail", "bus": "road", "flight": "air"}
        chains: list[tuple[float, datetime, str, str, float, float]] = []

        def visit(history: tuple[TimetableRun, ...]) -> None:
            first, current = history[0], history[-1]
            if current.events[-1].node_id == "B":
                used = {mode_map[r.transport_type] for r in history}
                category = next(iter(used)) if len(used) == 1 else "mixed"
                wait = (first.events[0].departure_at - START).total_seconds()
                path = (
                    current.events[-1].arrival_at - first.events[0].departure_at
                ).total_seconds()
                index = math.sqrt(100 * min(1, seconds / path)) * math.sqrt(
                    100 * 2 ** (-wait / 43200)
                )
                chains.append(
                    (index, current.events[-1].arrival_at, category, first.trip_id, wait, path)
                )
                return
            for nxt in legs:
                a, b = current.events[-1].node_id, nxt.events[0].node_id
                walk = 0 if a == b else 30 if (a, b) == ("X", "Y") else None
                gap = (nxt.events[0].departure_at - current.events[-1].arrival_at).total_seconds()
                if nxt not in history and walk is not None and gap >= max(walk, 100):
                    visit(history + (nxt,))

        for first in legs:
            if first.events[0].node_id == "A":
                visit((first,))
        for category in ("rail", "road", "air", "mixed", "public"):
            expected = [c for c in chains if category == "public" or c[2] == category]
            selected = (
                result.public
                if category == "public"
                else next(r for r in result.by_mode if r.category == category)
            )
            frequency = next(r for r in result.frequency if r.category == category)
            assert frequency.count == len({c[3] for c in expected})
            if expected:
                best = min(expected, key=lambda c: (-c[0], c[1]))
                assert selected.best_known is not None
                actual = selected.best_known
                assert actual.evaluation.index.value == pytest.approx(best[0])
                assert actual.route.path.arrival_at == best[1]
                assert (actual.initial_wait_seconds, actual.path_seconds) == (best[4], best[5])
            else:
                assert selected.best_known is None


@pytest.mark.parametrize("missing", ["calendar", "event"])
def test_alias_identity_evidence_does_not_confirm_missing_calendar_or_events(missing: str) -> None:
    r = run("unconfirmed", "A", 0, "B", 100)
    if missing == "calendar":
        r = replace(r, calendar_evidence_refs=())
    else:
        r = replace(r, events=(replace(r.events[0], evidence_refs=()), r.events[1]))
    result = analyze(table((r,)))
    assert result.connection_state == "unknown" and not result.candidates
    assert result.frequency[-1].count is None


def test_frequency_has_its_own_period_beyond_analysis_first_window() -> None:
    scope, _ = _demo.demo_inputs()
    tt = table(
        (run("inside", "A", 0, "B", 100), run("later", "A", 8 * 86400, "B", 8 * 86400 + 100))
    )
    reference = build_reference(_demo.demo_reference(scope), scope.comparison_scope)
    result = analyze_intercity_passenger(
        scope,
        START,
        tt,
        BINDINGS,
        services(tt),
        reference,
        config=load_passenger_config(),
        rules=load_intercity_passenger_rules(),
        frequency_start_at=START,
        frequency_end_at=START + timedelta(days=9),
    )
    assert len(result.candidates) == 1
    assert result.frequency[-1].count == 2
    assert result.frequency_search is not result.search


def test_cap_equal_score_uses_earliest_arrival_deterministically() -> None:
    tt = table((run("slower", "A", 0, "B", 200), run("faster", "A", 0, "B", 100)))
    result = analyze(tt, ref_seconds=9000)
    assert result.public.best_known is not None
    assert result.public.best_known.path_seconds == 100
    assert result.public.best_known.evaluation.index.value == 100
    assert "REFERENCE_INCONSISTENCY" in result.public.best_known.evaluation.diagnostics


def test_walk_between_trains_stays_rail() -> None:
    tt = table(
        (run("first", "A", 0, "X", 100), run("second", "Y", 200, "B", 300)),
        (TimetableFootpath("xy", "X", "Y", 30, True, PROOF),),
        (TimetableTransfer("minimum", "X", "Y", 100, True, PROOF),),
    )
    result = analyze(tt)
    assert result.public.best_known is not None
    assert result.public.best_known.category == "rail"
    assert result.public.best_known.walking_seconds == 30
    assert next(m for m in result.by_mode if m.category == "mixed").best_known is None


def test_empty_public_never_replaced_by_car() -> None:
    scope, _ = _demo.demo_inputs()
    car = AcceptedIntercityCar("car", scope.comparison_scope, "A", "B", 100, True, PROOF)
    result = analyze(table(()), car=car)
    assert result.car is not None and result.car.path_index.value == 100
    assert result.public.best_known is None and result.connection_state == "confirmed_absent"
    assert not result.full_ranking_eligible


def test_input_order_of_modes_and_physical_groups_preserves_equal_score_tie() -> None:
    scope, _ = _demo.demo_inputs()
    tt = table((run("same1", "A", 0, "B", 100), run("same2", "A", 0, "B", 100)))
    reference = build_reference(_demo.demo_reference(scope), scope.comparison_scope)

    def invoke(
        bindings: tuple[TransportModeBinding, ...], groups: tuple[PhysicalService, ...]
    ) -> IntercityPassengerAnalysis:
        return analyze_intercity_passenger(
            scope,
            START,
            tt,
            bindings,
            groups,
            reference,
            config=load_passenger_config(),
            rules=load_intercity_passenger_rules(),
            frequency_start_at=START,
            frequency_end_at=START + timedelta(days=7),
        )

    assert invoke(BINDINGS, services(tt)) == invoke(
        tuple(reversed(BINDINGS)), tuple(reversed(services(tt)))
    )


def test_zero_time_transfer_cycles_do_not_create_infinite_better_prefixes() -> None:
    tt = table(
        (
            run("origin", "A", 0, "X", 0),
            run("cycle1", "X", 0, "Y", 0),
            run("cycle2", "Y", 0, "X", 0),
            run("finish", "X", 0, "B", 1),
        ),
        transfers=(
            TimetableTransfer("xx", "X", "X", 0, True, PROOF),
            TimetableTransfer("yy", "Y", "Y", 0, True, PROOF),
        ),
    )
    result = analyze(tt)
    assert result.public.optimum_proven and result.public.best_known is not None
    assert result.public.best_known.path_seconds == 1
    assert result.public.best_known.boardings == 2
    assert result.frequency[-1].count == 1


def test_uncovered_final_arrival_is_not_confirmed_absence() -> None:
    tt = table((run("long", "A", 0, "B", 15 * 86400),))
    result = analyze(tt)
    assert result.connection_state == "unknown" and not result.candidates
    assert "CONTINUATION_OUTSIDE_HORIZON" in result.reasons
    assert not result.public.optimum_proven and result.frequency[-1].count is None
