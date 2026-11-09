"""ML1 readiness on ML2's accepted bundles: the engine calls of ML1's own examples.

ML1's ``check_walk_bundle.py`` and ``check_district_bundle.py`` are pinned to
Kaliningrad ``.2``/``.4``; this runs the same steps for any bundle and city and
prints one JSON line per bundle. No routing: without a Valhalla build the plan
blocks every pair (``VALHALLA_BUILD_UNAVAILABLE``), which is the expected answer.

Run from the repository root with ML1's engine and BACKEND's contracts importable:

    PYTHONPATH=packages/data_pipeline/src:packages/engine/src:packages/contracts/src \
        python ml/ml1_readiness.py kaliningrad-urban-2026-11-09.1 ...
"""

import json
import sys
import traceback
from collections import Counter
from datetime import UTC, datetime

from a2_data_pipeline.contract_v2 import load_accepted_data
from a2_data_pipeline.store import load_bundle
from a2_engine import (
    UrbanSharePrecision,
    analyze_urban_candidates,
    analyze_urban_districts,
    load_urban_config,
    load_urban_journey_rules,
    plan_walk_routes,
    prepare_urban_district_inputs,
    prepare_walk_inputs,
)


def step(report, name, fn):
    try:
        value = fn()
        report[name] = "ok"
        return value
    except Exception as error:
        report[name] = f"FAIL {type(error).__name__}: {str(error)[:400]}"
        report.setdefault("tracebacks", {})[name] = traceback.format_exc()[-1500:]
        return None


results = []
for bundle in sys.argv[1:]:
    rep = {"bundle": bundle}
    producer = load_bundle(bundle)
    city = next(t.territory_id for t in producer.territories if t.level == "city")
    accepted = step(rep, "load_accepted_data", lambda: load_accepted_data(bundle))
    if accepted is None:
        results.append(rep)
        continue
    raw = accepted.model_dump(mode="json")
    config = load_urban_config()
    inputs = step(
        rep,
        "prepare_walk_inputs",
        lambda: prepare_walk_inputs(
            raw,
            city_id=city,
            departure_at=datetime(2026, 11, 10, 9, tzinfo=UTC),
            methodology_version=config.methodology_version,
            config_hash="readiness-unbound",
            search_parameters_id="readiness-unbound",
        ),
    )
    if inputs is None:
        results.append(rep)
        continue
    rep["points"] = len(inputs.points)
    rep["destinations"] = len(inputs.destinations)
    rep["destination_categories"] = dict(
        Counter(c for d in inputs.destinations for c in getattr(d, "categories", ()))
    )
    rep["limitations"] = list(inputs.limitations)
    cats = sorted(rep["destination_categories"]) or ["airport"]
    plan = step(
        rep,
        "plan_walk_routes",
        lambda: plan_walk_routes(
            inputs, point_ids=[p.point_id for p in inputs.points[:3]], categories=cats
        ),
    )
    if plan is not None:
        rep["route_requests"] = len(plan.requests)
        rep["blocked_pairs"] = len(plan.issues)
    analysis = step(
        rep,
        "analyze_urban_candidates",
        lambda: analyze_urban_candidates(
            inputs.context,
            inputs.points,
            inputs.destinations,
            (),
            (),
            area=inputs.area,
            selected_categories={c: "high" for c in cats},
            modes=("walk",),
            population_coverage=inputs.population_coverage,
            config=config,
            rules=load_urban_journey_rules(),
        ),
    )
    if analysis is not None:
        rep["h_seconds"] = analysis.h_seconds.value
    districts = [
        t.territory_id
        for t in producer.territories
        if t.level == "district" and t.parent_id == city
    ]
    rep["districts"] = len(districts)
    if analysis is not None and districts:
        src = f"bundle:{bundle}:population_cells.district_shares"
        prepared = step(
            rep,
            "prepare_urban_district_inputs",
            lambda: prepare_urban_district_inputs(
                analysis,
                raw,
                producer.model_dump(mode="json"),
                catalog_version=bundle + ":district-boundaries",
                distribution_version=bundle + ":district-population",
                complete_district_ids=districts,
                original_cells_complete=True,
                evidence_refs=(src, f"bundle:{bundle}:qa:districts_partition_city"),
                precision=UrbanSharePrecision(0.001, 0.001, (src,)),
            ),
        )
        if prepared is not None:
            res = step(
                rep,
                "analyze_urban_districts",
                lambda: analyze_urban_districts(
                    analysis,
                    prepared.original_cells,
                    prepared.district_catalog,
                    prepared.population_shares,
                    prepared.coverage,
                ),
            )
            if res is not None:
                rep["city_population"] = str(res.city_population)
                rep["allocated_population"] = str(res.allocated_population)
    results.append(rep)
for r in results:
    tb = r.pop("tracebacks", {})
    print(json.dumps(r, ensure_ascii=False, default=str))
    for k, v in tb.items():
        print("---", r["bundle"], k)
        print(v)
