"""Real readiness only: use ML2's existing loader and canonical validation, no HTTP."""

import argparse
import importlib
import json
from datetime import datetime
from pathlib import Path

from a2_engine import load_urban_config, plan_walk_routes, prepare_walk_inputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", default="kaliningrad-urban-2026-11-08.2")
    parser.add_argument("--departure-at", default="2026-11-08T12:00:00+00:00")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    # Optional foreign packages are needed only for this integration check, not engine.
    ml2 = importlib.import_module("a2_data_pipeline.contract_v2")
    accepted = ml2.load_accepted_data(args.bundle)
    raw = accepted.model_dump(mode="json")
    config = load_urban_config()
    inputs = prepare_walk_inputs(
        raw,
        city_id="kaliningrad",
        departure_at=datetime.fromisoformat(args.departure_at),
        methodology_version=config.methodology_version,
        config_hash="readiness-only-unbound",
        search_parameters_id="readiness-only-unbound",
    )
    point_ids = [p.point_id for p in inputs.points[:3]]
    plan = plan_walk_routes(inputs, point_ids=point_ids, categories=["airport"])
    report = {
        "snapshot_id": raw["snapshot_id"],
        "canonical_payload_hash": inputs.canonical_payload_hash,
        "counts": {
            key: len(raw[key])
            for key in [
                "territories",
                "population_cells",
                "sample_points",
                "destinations",
                "access_nodes",
                "evidence",
            ]
        },
        "point_ids": point_ids,
        "category": "airport",
        "source_graph_version": inputs.source_graph_version,
        "calendar_version": inputs.context.versions.calendar_version,
        "excluded_population": inputs.population_coverage.excluded_population,
        "requests": len(plan.requests),
        "blocked_pairs": len(plan.issues),
        "limitations": inputs.limitations,
        "network_run": False,
    }
    source = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(source)
    print(source, end="")


if __name__ == "__main__":
    main()
