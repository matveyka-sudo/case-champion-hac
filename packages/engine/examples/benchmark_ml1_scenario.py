"""Time a 16-job frozen scenario from the synthetic transfer fixture."""

import argparse
import json
import random
import resource
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from time import perf_counter
from typing import TypedDict

from a2_engine import (
    ScenarioOperation,
    create_scenario_baseline,
    execution_hash,
    scenario_hash,
    simulate_scenario,
    validate_scenario_result,
)
from synthetic_scenario import PROOF, demo_inputs


class Sample(TypedDict):
    baseline_seconds: float
    simulation_seconds: float
    validation_seconds: float
    peak_process_bytes: int
    before_states: int
    after_states: int
    before_hash: str
    scenario_id: str


class WorkerResult(TypedDict):
    prepare_seconds: float
    samples: list[Sample]


def worker(jobs: int, repeats: int) -> WorkerResult:
    started = perf_counter()
    inputs, incoming_id, outgoing_id = demo_inputs()
    source = next(r for r in inputs.timetables[0].timetable.runs if r.run_id == outgoing_id)
    inputs = replace(
        inputs,
        urban=tuple(replace(inputs.urban[0], job_id=f"job:{i}") for i in range(jobs)),
    )
    prepare_seconds = perf_counter() - started
    samples: list[Sample] = []
    for _ in range(repeats):
        started = perf_counter()
        baseline = create_scenario_baseline(inputs)
        baseline_seconds = perf_counter() - started
        operation = ScenarioOperation(
            baseline.baseline_id,
            "bench",
            "coordinate_transfer",
            "timetable",
            "network",
            outgoing_id,
            source.service_date,
            "0",
            execution_hash(baseline, "timetable", "network", outgoing_id),
            180,
            incoming_id,
            "1",
            PROOF,
        )
        started = perf_counter()
        result = simulate_scenario(baseline, operation)
        simulation_seconds = perf_counter() - started
        started = perf_counter()
        validate_scenario_result(baseline, operation, result)
        validation_seconds = perf_counter() - started
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        samples.append(
            {
                "baseline_seconds": baseline_seconds,
                "simulation_seconds": simulation_seconds,
                "validation_seconds": validation_seconds,
                "peak_process_bytes": rss if sys.platform == "darwin" else rss * 1024,
                "before_states": sum(
                    s.search.expanded_states for job in result.before.urban for s in job.searches
                ),
                "after_states": sum(
                    s.search.expanded_states for job in result.after.urban for s in job.searches
                ),
                "before_hash": baseline.result_hash,
                "scenario_id": result.scenario_id,
            }
        )
        del result, baseline
    return {"prepare_seconds": prepare_seconds, "samples": samples}


def run_worker(jobs: int, repeats: int) -> WorkerResult:
    args = [sys.executable, __file__, "--jobs", str(jobs), "--worker-repeats", str(repeats)]
    output: WorkerResult = json.loads(subprocess.check_output(args, text=True))
    return output


def summarize(samples: list[Sample]) -> dict[str, dict[str, float]]:
    return {
        name: {"min": min(values), "median": statistics.median(values), "max": max(values)}
        for name, values in (
            ("baseline_seconds", [row["baseline_seconds"] for row in samples]),
            ("simulation_seconds", [row["simulation_seconds"] for row in samples]),
            ("validation_seconds", [row["validation_seconds"] for row in samples]),
            ("peak_process_bytes", [row["peak_process_bytes"] for row in samples]),
        )
    }


def identity_rows() -> list[dict[str, object]]:
    """Reproduce full outputs under seeded shifts, partial inputs and both timing models."""
    rows: list[dict[str, object]] = []
    for seed in range(24):
        rng = random.Random(seed)
        inputs, incoming_id, outgoing_id = demo_inputs()
        dataset = inputs.timetables[0]
        table = dataset.timetable
        source = next(run for run in table.runs if run.run_id == outgoing_id)
        if seed % 4 == 0:
            table = replace(table, scope=replace(table.scope, continuations_complete=False))
        order = tuple(rng.sample(table.runs, len(table.runs)))
        inputs = replace(
            inputs, timetables=(replace(dataset, timetable=replace(table, runs=order)),)
        )
        baseline = create_scenario_baseline(inputs)
        operation = ScenarioOperation(
            baseline.baseline_id,
            f"coordination:{seed}",
            "coordinate_transfer",
            "timetable",
            "network",
            outgoing_id,
            source.service_date,
            "0",
            execution_hash(baseline, "timetable", "network", outgoing_id),
            rng.randrange(180, 80_000),
            incoming_id,
            "1",
            PROOF,
            timing_model="whole_execution" if seed % 2 else "departure_tail",
        )
        result = simulate_scenario(baseline, operation)
        validate_scenario_result(baseline, operation, result)
        rows.append(
            {
                "seed": seed,
                "shift": operation.shift_seconds,
                "baseline": scenario_hash(baseline),
                "full_result": scenario_hash(result),
                "outcomes": Counter(effect.outcome for effect in result.effects),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=16)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--worker-repeats", type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        "--identity-check", action="store_true", help="Print 24 seeded result hashes"
    )
    args = parser.parse_args()
    if args.identity_check:
        print(json.dumps(identity_rows(), sort_keys=True))
        return
    if (
        args.jobs < 1
        or args.repeats < 3
        or (args.worker_repeats is not None and args.worker_repeats < 1)
    ):
        parser.error("At least one job and three cold/warm samples are required")
    if args.worker_repeats is not None:
        print(json.dumps(worker(args.jobs, args.worker_repeats)))
        return
    cold = [run_worker(args.jobs, 1) for _ in range(args.repeats)]
    warm = run_worker(args.jobs, args.repeats + 1)
    cold_samples = [row["samples"][0] for row in cold]
    warm_samples = warm["samples"][1:]
    fingerprints = {
        (row["scenario_id"], row["before_hash"], row["before_states"], row["after_states"])
        for row in cold_samples + warm_samples
    }
    assert len(fingerprints) == 1
    print(
        json.dumps(
            {
                "jobs": args.jobs,
                "cold": summarize(cold_samples),
                "warm": summarize(warm_samples),
                "cold_workers": cold,
                "warm_worker": warm,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
