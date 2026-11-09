"""Bounded growth measurements for the remaining audit domains; no external IO."""

import json
import runpy
import time
import tracemalloc
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from a2_engine import create_scenario_baseline, simulate_scenario
from a2_engine.routing.timetable import solve_timetable

W = runpy.run_path(str(Path(__file__).with_name("audit_completion_workloads.py")))


def measure(kind: str, size: int, call: Callable[[], Any]) -> tuple[dict[str, Any], Any]:
    tracemalloc.start()
    start = time.perf_counter()
    value = call()
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return dict(kind=kind, size=size, seconds=elapsed, peak_python_bytes=peak), value


def main() -> None:
    rows = []
    for size in (60, 240):
        scope, table, _ = W["timetable_case"](size)
        row, result = measure(
            "urban_timetable",
            size,
            lambda: solve_timetable(table, "A", "B", table.scope.start_at, max_wait_seconds=14400),
        )
        row["states"] = result.expanded_states
        rows.append(row)
        row, result = measure("intercity", size, lambda: W["passenger"](scope, table))
        row["states"] = result.search.expanded_states
        rows.append(row)
        assert W["passenger"](scope, table, 10).search.interrupted
    for width in (3, 8):
        case = W["cargo_case"](width)
        row, result = measure("cargo_branch_width", width, lambda: W["cargo"](case))
        row.update(states=result.expanded_states, paths=len(result.candidates))
        rows.append(row)
        assert not W["cargo"](case, 10).overall.optimum_proven
    for size in (12, 32):
        row, result = measure("od_points", size, lambda: W["od"](size))
        assert result.tau_seconds.value is not None
        row["pairs"] = 28 * size * (size - 1)
        rows.append(row)
    m = W["demo"]("scenario")
    baseline, operation = m["demo_baseline"]()
    for size in (4, 16):
        inputs = replace(
            baseline.inputs,
            urban=tuple(replace(baseline.inputs.urban[0], job_id=f"job:{i}") for i in range(size)),
        )

        def scenario() -> Any:
            base = create_scenario_baseline(inputs)
            return simulate_scenario(base, replace(operation, baseline_id=base.baseline_id))

        row, result = measure("scenario_jobs", size, scenario)
        assert result.complete and result.impact.known_affected_population == 100
        row["metrics"] = len(result.effects)
        rows.append(row)
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
