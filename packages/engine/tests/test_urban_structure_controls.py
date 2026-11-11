import dataclasses
import hashlib
import json
import runpy
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from a2_engine import (
    ValhallaCorrelation,
    analyze_car_responses,
    assemble_urban_od_week,
    calculate_urban_tau,
    load_urban_config,
    load_urban_journey_rules,
    load_urban_tau_rules,
)


def stable(x: Any) -> Any:
    if dataclasses.is_dataclass(x):
        # Historical exact-correlation controls predate the two optional fields.
        # Retain the original result and request identities when neither is used.
        additions = (
            {"coordinate_precision_digits", "serialization_evidence_refs"}
            if isinstance(x, ValhallaCorrelation)
            and x.coordinate_precision_digits is None
            and not x.serialization_evidence_refs
            else set()
        )
        return {
            f.name: stable(getattr(x, f.name))
            for f in dataclasses.fields(x)
            if f.name not in additions
        }
    if isinstance(x, Mapping):
        return {k: stable(v) for k, v in x.items()}
    if isinstance(x, (tuple, list)):
        return [stable(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted(stable(v) for v in x)
    if isinstance(x, (date, datetime, time)):
        return x.isoformat()
    if isinstance(x, (Decimal, Fraction)):
        return str(x)
    return x


def capture() -> dict[str, str]:
    car = runpy.run_path("packages/engine/examples/synthetic_car.py")
    od = runpy.run_path("packages/engine/examples/synthetic_od.py")
    from a2_engine import plan_car_routes

    p = plan_car_routes(car["synthetic_inputs"](), categories=("school",))
    r = analyze_car_responses(
        p,
        [car["synthetic_response"](p.requests[0])],
        selected_categories={"school": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    plan, en, ex, context, pop = od["synthetic_plan"]()
    rows = od["synthetic_observations"](plan, en, ex, two_stage=True)
    week = assemble_urban_od_week(plan, rows)
    tau = calculate_urban_tau(
        context,
        plan.points,
        pop,
        [week],
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=plan.reference_at,
        rules=load_urban_tau_rules(),
    )
    partial_car = analyze_car_responses(
        p,
        [car["synthetic_response"](p.requests[0], proven=False)],
        selected_categories={"school": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    incomplete = assemble_urban_od_week(plan, rows[1:])
    unknown_tau = calculate_urban_tau(
        context,
        plan.points,
        pop,
        [incomplete],
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=plan.reference_at,
        rules=load_urban_tau_rules(),
    )
    partial_districts = runpy.run_path("packages/engine/examples/synthetic_districts.py")[
        "run_demo"
    ](unknown_third=True)
    objects = {
        **{
            key: runpy.run_path("packages/engine/examples/synthetic_" + key + ".py")["run_demo"]()
            for key in ("walk", "districts", "transit")
        },
        "car_result": r,
        "partial_car": partial_car,
        "partial_districts": partial_districts,
        "incomplete_week": incomplete,
        "unknown_tau": unknown_tau,
        "car_payload": p.requests[0].payload(),
        "od_plan": plan,
        "od_observations": rows,
        "week": week,
        "tau": tau,
        "threshold": tau.to_threshold(),
    }
    return {
        name: hashlib.sha256(
            json.dumps(
                stable(value), sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        for name, value in objects.items()
    }


def test_existing_synthetic_results_and_identities_unchanged() -> None:
    expected = json.loads(
        (Path(__file__).parent / "fixtures/urban_structure_controls.json").read_text()
    )
    assert capture() == expected
