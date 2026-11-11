"""Deterministic ML1 stress cases, timed separately from Python allocation tracing."""

import argparse
import json
import statistics
import subprocess
import sys
import tracemalloc
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from time import perf_counter
from typing import TypedDict

from a2_engine import Timetable, TimetableEvent, TimetableRun, TimetableTransfer, scenario_hash
from a2_engine.routing.timetable import solve_timetable
from audit_completion_workloads import cargo, cargo_case, od, passenger, timetable_case

CASES = ("timetable_303", "intercity_240", "cargo_32", "od_32")
REPEATS = 3


class WorkerResult(TypedDict):
    prepare_seconds: float
    calculation_seconds: list[float]
    peak_python_bytes: int | None
    counts: dict[str, int]
    result_sha256: str | None


def timetable_303() -> Timetable:
    _, base, _ = timetable_case(0)
    assert isinstance(base, Timetable)
    start = base.scope.start_at
    proof = ("benchmark",)

    def run(name: str, a: str, departure: int, b: str, arrival: int) -> TimetableRun:
        return TimetableRun(
            base.scope.snapshot_id,
            "benchmark-303",
            name,
            start.date(),
            "UTC",
            "rail",
            tuple(
                TimetableEvent(
                    i,
                    node,
                    start + timedelta(seconds=t),
                    start + timedelta(seconds=t),
                    True,
                    True,
                    proof,
                )
                for i, (node, t) in enumerate(((a, departure), (b, arrival)))
            ),
            True,
            proof,
        )

    feeders = tuple(run(f"in:{i}", "A", i, "X", i + 300) for i in range(151))
    continuations = tuple(run(f"out:{i}", "X", i + 600, "B", i + 1200) for i in range(152))
    transfers = tuple(
        TimetableTransfer(f"other:{r.trip_id}", "Y", "X", 0, True, proof, to_run_id=r.run_id)
        for r in feeders + continuations
    ) + (TimetableTransfer("connection", "X", "X", 0, True, proof),)
    return replace(base, runs=feeders + continuations, transfers=transfers)


def prepare(name: str) -> Callable[[], tuple[object, dict[str, int]]]:
    if name == "timetable_303":
        table = timetable_303()

        def timetable() -> tuple[object, dict[str, int]]:
            result = solve_timetable(table, "A", "B", table.scope.start_at, max_wait_seconds=14400)
            assert result.proven and result.best_known is not None
            assert result.best_known.total_seconds == 1200
            return result, {"expanded_states": result.expanded_states}

        return timetable

    if name == "intercity_240":
        scope, table, _ = timetable_case(240)

        def intercity() -> tuple[object, dict[str, int]]:
            result = passenger(scope, table)
            assert result.public.optimum_proven
            return result, {
                "expanded_states": result.search.expanded_states,
                "candidates": len(result.candidates),
            }

        return intercity

    if name == "cargo_32":
        case = cargo_case(32)

        def freight() -> tuple[object, dict[str, int]]:
            result = cargo(case)
            assert result.overall.optimum_proven and len(result.candidates) == 1024
            return result, {
                "expanded_states": result.expanded_states,
                "candidates": len(result.candidates),
            }

        return freight

    def od_week() -> tuple[object, dict[str, int]]:
        result = od(32)
        assert result.tau_seconds.value is not None
        return result, {"directed_pair_slices": 28 * 32 * 31}

    return od_week


def worker(name: str, *, repeats: int, trace: bool) -> WorkerResult:
    assert repeats >= 1
    if trace:
        tracemalloc.start()
    started = perf_counter()
    run = prepare(name)
    prepare_seconds = perf_counter() - started
    elapsed = []
    fingerprints: set[str] = set()
    for _ in range(repeats):
        started = perf_counter()
        value, counts = run()
        elapsed.append(perf_counter() - started)
        if not trace:
            # Verify complete deterministic outputs outside the timing interval.
            fingerprints.add(scenario_hash(value))
        del value
    peak = tracemalloc.get_traced_memory()[1] if trace else None
    if trace:
        tracemalloc.stop()
    assert len(fingerprints) == (0 if trace else 1)
    return {
        "prepare_seconds": prepare_seconds,
        "calculation_seconds": elapsed,
        "peak_python_bytes": peak,
        "counts": counts,
        "result_sha256": None if trace else fingerprints.pop(),
    }


def summarize(samples: list[float]) -> dict[str, float]:
    return {"min": min(samples), "median": statistics.median(samples), "max": max(samples)}


def run_subprocess(name: str, *, repeats: int, trace: bool = False) -> WorkerResult:
    args = [sys.executable, __file__, "--worker", name, "--repeats", str(repeats)]
    if trace:
        args.append("--trace")
    result: WorkerResult = json.loads(subprocess.check_output(args, text=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, action="append")
    parser.add_argument("--worker", choices=CASES)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()
    if args.worker is not None:
        if args.repeats < 1:
            parser.error("At least one worker repetition is required")
        print(json.dumps(worker(args.worker, repeats=args.repeats, trace=args.trace)))
        return
    if args.repeats < REPEATS:
        parser.error("At least three repetitions are required")
    cases = args.case or CASES
    results = []
    for name in cases:
        cold = [run_subprocess(name, repeats=args.repeats + 1) for _ in range(args.repeats)]
        memory = run_subprocess(name, repeats=1, trace=True)
        all_times = [row["calculation_seconds"] for row in cold]
        prep = [row["prepare_seconds"] for row in cold]
        fingerprints = {row["result_sha256"] for row in cold}
        assert len(fingerprints) == 1
        results.append(
            {
                "case": name,
                "prepare_seconds": summarize(prep),
                "cold_calculation_seconds": summarize([samples[0] for samples in all_times]),
                "warm_calculation_seconds": summarize(
                    [seconds for samples in all_times for seconds in samples[1:]]
                ),
                "counts": cold[0]["counts"],
                "peak_python_bytes": memory["peak_python_bytes"],
                "result_sha256": fingerprints.pop(),
            }
        )
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
