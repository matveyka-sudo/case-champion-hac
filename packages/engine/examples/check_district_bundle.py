"""Real ML2 district readiness. No route calls, GIS, or invented point indices."""

import argparse
import importlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from a2_engine import (
    UrbanSharePrecision,
    analyze_urban_candidates,
    analyze_urban_districts,
    load_urban_config,
    load_urban_journey_rules,
    prepare_urban_district_inputs,
    prepare_walk_inputs,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", default="kaliningrad-urban-2026-11-08.4")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    if args.bundle != "kaliningrad-urban-2026-11-08.4":
        raise ValueError("Precision/source audit below is pinned to .4 at e631929")
    ml2 = importlib.import_module("a2_data_pipeline.contract_v2")
    store = importlib.import_module("a2_data_pipeline.store")
    producer = store.load_bundle(args.bundle)
    accepted = ml2.load_accepted_data(args.bundle)
    raw = accepted.model_dump(mode="json")
    config = load_urban_config()
    inputs = prepare_walk_inputs(
        raw,
        city_id="kaliningrad",
        departure_at=datetime(2026, 11, 8, 12, tzinfo=UTC),
        methodology_version=config.methodology_version,
        config_hash="readiness-unbound",
        search_parameters_id="readiness-unbound",
    )
    analysis = analyze_urban_candidates(
        inputs.context,
        inputs.points,
        inputs.destinations,
        (),
        (),
        area=inputs.area,
        selected_categories={"airport": "high"},
        modes=("walk",),
        population_coverage=inputs.population_coverage,
        config=config,
        rules=load_urban_journey_rules(),
    )
    districts = [
        t.territory_id
        for t in producer.territories
        if t.level == "district" and t.parent_id == "kaliningrad"
    ]
    source = "git:e631929c01792ced9f0a9305ddfce5b312305fa4:packages/data_pipeline/src/a2_data_pipeline/build_city.py:_cells"
    prepared = prepare_urban_district_inputs(
        analysis,
        raw,
        producer.model_dump(mode="json"),
        catalog_version=args.bundle + ":district-boundaries",
        distribution_version=args.bundle + ":district-population",
        complete_district_ids=districts,
        original_cells_complete=True,
        evidence_refs=(source, "bundle:" + args.bundle + ":qa:districts_partition_city"),
        precision=UrbanSharePrecision(0.001, 0.001, (source,)),
    )
    result = analyze_urban_districts(
        analysis,
        prepared.original_cells,
        prepared.district_catalog,
        prepared.population_shares,
        prepared.coverage,
    )
    names = {d.district_id: d.name for d in prepared.district_catalog}
    report = {
        "snapshot_id": args.bundle,
        "ml2_commit": "e631929c01792ced9f0a9305ddfce5b312305fa4",
        "cells": len(result.original_cells),
        "points": len(inputs.points),
        "districts": len(districts),
        "accepted_objects": len(inputs.destinations),
        "h_seconds": analysis.h_seconds.value,
        "city_population": str(result.city_population),
        "allocated_population": str(result.allocated_population),
        "residual_population": str(result.residual_population),
        "excluded_population": str(result.excluded_population),
        "documented_residual_cells": sum(q.documented_precision for q in result.cell_qa),
        "district_populations": [
            {
                "district_id": p.district_id,
                "name": names[p.district_id],
                "population": str(p.population),
                "excluded_population": str(p.excluded_population),
                "reasons": p.reasons,
            }
            for p in result.district_populations
        ],
        "city_index_status": result.modes[0].overall.city_index.status,
        "gap_status": result.modes[0].overall.gap.status,
        "ranking_complete": result.modes[0].overall.ranking.complete,
        "network_run": False,
    }
    if args.fixture:
        assert prepared.coverage.precision is not None
        fixture = {
            "snapshot_id": args.bundle,
            "versions": asdict(inputs.context.versions),
            "cells": [asdict(c) for c in prepared.original_cells],
            "districts": [asdict(d) for d in prepared.district_catalog],
            "shares": [asdict(s) for s in prepared.population_shares],
            "catalog_version": prepared.coverage.catalog_version,
            "distribution_version": prepared.coverage.distribution_version,
            "source": source,
            "precision": asdict(prepared.coverage.precision),
        }
        args.fixture.write_text(
            json.dumps(fixture, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
