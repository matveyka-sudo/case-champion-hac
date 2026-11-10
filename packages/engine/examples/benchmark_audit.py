"""Bounded synthetic audit measurements; seconds and Python traced peak bytes, no SLA."""

import json
import runpy
import time
import tracemalloc
from dataclasses import replace
from pathlib import Path
from typing import Any

from a2_engine import TimetableFootpath
from a2_engine.routing.timetable import walk_access_paths
from a2_engine.scenario.impact import scenario_impact


def measure() -> list[dict[str, Any]]:
    demo = runpy.run_path(str(Path(__file__).with_name("synthetic_scenario.py")))
    inputs, _, _ = demo["demo_inputs"]()
    rows = []
    for size in (500, 2000):
        job = inputs.urban[0]
        points = tuple(
            replace(job.points[0], point_id=f"p{i}", cell_id=f"c{i}") for i in range(size)
        )
        bundle = replace(
            inputs,
            urban=(
                replace(
                    job,
                    points=points,
                    original_cells=(),
                    districts=(),
                    district_shares=(),
                    district_coverage=None,
                ),
            ),
        )
        tracemalloc.start()
        start = time.perf_counter()
        scenario_impact(bundle, ())
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rows.append(dict(kind="impact", size=size, seconds=elapsed, peak_bytes=peak))
        table = inputs.timetables[0].timetable
        table = replace(
            table,
            runs=(),
            transfers=(),
            scope=replace(table.scope, node_ids=frozenset(str(i) for i in range(size + 1))),
            footpaths=tuple(
                TimetableFootpath(f"e{i}", str(i), str(i + 1), 1, True, ("proof",))
                for i in range(size)
            ),
        )
        tracemalloc.start()
        start = time.perf_counter()
        paths, expanded, interrupted = walk_access_paths(table, "0")
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert paths[str(size)][0] == size * 1000000 and not interrupted
        rows.append(
            dict(kind="walk_chain", size=size, seconds=elapsed, peak_bytes=peak, expanded=expanded)
        )
    return rows


if __name__ == "__main__":
    print(json.dumps(measure(), indent=2))
