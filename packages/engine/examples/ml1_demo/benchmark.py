"""Cold subprocess and warm same-process engine timings with returned search counters."""

import cProfile
import json
import resource
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Literal

from a2_engine.scenario.engine import create_scenario_baseline, validate_scenario_result
from a2_engine.scenario.models import ScenarioBaseline

from ml1_demo.matrix import (
    DemoCase,
    Row,
    baseline_rows,
    comparison_row,
    load_cases,
    operations,
    run_comparison,
    work_states,
)

Kind = Literal["baseline", "scenario"]


def peak_rss_mib() -> float:
    # macOS returns bytes, Linux returns KiB.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 ** (2 if sys.platform == "darwin" else 1))


def measure(
    kind: Kind,
    cases: tuple[DemoCase, ...],
    frozen: tuple[ScenarioBaseline, ...] | None,
    *,
    startup_s: float = 0.0,
    preparation_s: float = 0.0,
) -> Row:
    began = perf_counter()
    stage: dict[str, float] = defaultdict(float)
    states: Counter[str] = Counter()
    durations: dict[str, list[float]] = {}
    completed = 0
    for i, case in enumerate(cases):
        case_began = perf_counter()
        if kind == "baseline":
            baseline = create_scenario_baseline(case.inputs)
            stage["baseline_calculation_s"] += perf_counter() - case_began
            mark = perf_counter()
            rows = baseline_rows(case, baseline)
            stage["car_replay_and_report_s"] += perf_counter() - mark
            states.update(work_states(baseline.before))
            states["car_replayed_arcs"] += sum(row.get("road_responses", 0) for row in rows)
            states["reference_settled"] += sum(
                j.reference.settled_states for j in case.inputs.intercity
            )
            completed += len(rows)
        else:
            assert frozen is not None
            baseline = frozen[i]
            for operation, proof in operations(baseline):
                mark = perf_counter()
                result = run_comparison(baseline, operation, proof, validate=False)
                stage["validated_overlay_recomputation_s"] += perf_counter() - mark
                mark = perf_counter()
                validate_scenario_result(baseline, operation, result, feasibility=proof)
                stage["publication_validation_s"] += perf_counter() - mark
                mark = perf_counter()
                comparison_row(case, result)
                stage["comparison_report_s"] += perf_counter() - mark
                for direction, computation in (("before", result.before), ("after", result.after)):
                    states.update(
                        {f"{direction}:{k}": v for k, v in work_states(computation).items()}
                    )
                completed += 1
        family = case.id.split(":")[0]
        durations.setdefault(family, []).append(perf_counter() - case_began)
    return {
        "wall_s": perf_counter() - began + startup_s + preparation_s,
        "startup_s": startup_s,
        "fixture_preparation_s": preparation_s,
        "phases": dict(stage),
        "per_case_s": durations,
        "peak_rss_mib": peak_rss_mib(),
        "returned_search_states": dict(states),
        "completed_rows": completed,
    }


def sample(kind: Kind, startup_s: float) -> Row:
    began = perf_counter()
    cases = load_cases()
    frozen = (
        tuple(create_scenario_baseline(c.inputs) for c in cases) if kind == "scenario" else None
    )
    return measure(kind, cases, frozen, startup_s=startup_s, preparation_s=perf_counter() - began)


def spread(samples: list[float]) -> Row:
    return {"min": min(samples), "median": median(samples), "max": max(samples)}


def profile_phases(
    kind: Kind,
    cases: tuple[DemoCase, ...],
    frozen: tuple[ScenarioBaseline, ...] | None,
) -> Row:
    """Attribute exclusive Python CPU times on a separate, instrumented pass."""
    profiler = cProfile.Profile()
    profiler.runcall(measure, kind, cases, frozen)
    groups: dict[str, float] = defaultdict(float)
    for entry in profiler.getstats():
        if isinstance(entry.code, str):
            continue
        filename, name = entry.code.co_filename, entry.code.co_name
        if "/a2_engine/" not in filename:
            continue
        if name in ("__post_init__", "__init__"):
            group = "normalized_data_and_result_construction"
        elif "/scenario/" in filename:
            group = "scenario_overlay_validation_identity_and_effects"
        elif "index" in name or "/scoring.py" in filename or "/passenger.py" in filename:
            group = "indices_and_population_aggregation"
        elif "/districts/" in filename or "aggregation" in name:
            group = "territory_aggregation"
        elif name in ("usable_runs", "walk_access_paths", "normalize_physical_services"):
            group = "graph_and_service_preparation"
        elif "/routing/" in filename or "/search.py" in filename:
            group = "routing_and_chain_selection"
        else:
            group = "other_engine_python"
        groups[group] += entry.inlinetime
    return dict(groups)


def benchmark(entry: Path) -> Row:
    output: Row = {
        "cold": "elapsed subprocess wall time including interpreter startup, imports and report",
        "warm": "same process and normalized inputs, scenario baseline frozen once",
        "states": "returned before/after search counters per workload, excluding validation replay",
        "memory": "process peak RSS in MiB, cumulative during warm samples",
        "units": {"time": "seconds", "memory": "MiB"},
        "results": {},
    }
    for kind in ("baseline", "scenario"):
        cold = []
        for _ in range(3):
            began = perf_counter()
            row = json.loads(
                subprocess.check_output([sys.executable, str(entry), "--sample", kind], text=True)
            )
            row["subprocess_pipeline_s"] = row["wall_s"]
            row["wall_s"] = perf_counter() - began
            cold.append(row)
        cases = load_cases()
        frozen = (
            tuple(create_scenario_baseline(c.inputs) for c in cases) if kind == "scenario" else None
        )
        warm = [measure(kind, cases, frozen) for _ in range(3)]
        for rows in (cold, warm):
            assert all(r["completed_rows"] == rows[0]["completed_rows"] for r in rows)
            assert all(
                r["returned_search_states"] == rows[0]["returned_search_states"] for r in rows
            )
        output["results"][kind] = {
            label: {
                "wall_s": spread([r["wall_s"] for r in rows]),
                "peak_rss_mib": spread([r["peak_rss_mib"] for r in rows]),
                "samples": rows,
            }
            for label, rows in (("cold", cold), ("warm", warm))
        }
        output["results"][kind]["profile_exclusive_python_s"] = profile_phases(kind, cases, frozen)
    return output
