"""Urban roster, compatibility and directed truck boundary controls."""

import importlib.util
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from a2_engine.freight.models import CargoTemperature
from a2_engine.freight.urban_analysis import CargoNode, UrbanCargoCoverage, analyze_urban_cargo
from a2_engine.shared.config import load_freight_config

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/synthetic_urban_cargo.py"
spec = importlib.util.spec_from_file_location("cargo_demo", EXAMPLE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def evaluate(
    *,
    fast: str = "forbidden",
    missing: bool = False,
    inbound: bool = False,
    duplicate: bool = False,
    complete: bool = True,
) -> Any:
    base = m.run_demo()
    sites: tuple[Any, ...] = (m.CargoSite("site1", m.CONTEXT, m.PROOF),)
    if missing:
        sites = ()
    if duplicate:
        sites += sites
    nodes = (
        CargoNode("fast", "port", "port", m.CONTEXT, m.PROOF),
        CargoNode("slow", "port", "port", m.CONTEXT, m.PROOF),
    )
    a, ap = m.truck("site1", "fast", 600 if not inbound else 1800, inbound=inbound)
    b, bp = m.truck("site1", "slow", 1200 if not inbound else 2400, inbound=inbound)
    a = replace(a, compatibility=replace(a.compatibility, state=fast))
    ap = replace(ap, route_fingerprint=a.fingerprint, complete=complete)
    direction = "inbound" if inbound else "outbound"
    coverage = replace(base.coverage, expected_site_ids=("site1",), direction=direction)
    routes = (a, b) if sites else ()
    proofs = (ap, bp) if sites else ()
    return analyze_urban_cargo(
        m.PROFILE,
        m.START,
        sites,
        nodes,
        routes,
        proofs,
        coverage,
        ("port",),
        100,
        config=load_freight_config(),
    )


def test_urban_control_and_nearest_compatible() -> None:
    assert m.run_demo().by_type[0].index.value == 37.5
    result = evaluate()
    assert result.by_type[0].sites[0].selected_node_id == "slow"
    assert result.by_type[0].index.value == 50
    assert len(result.by_type[0].sites[0].observed_routes) == 2


def test_unknown_faster_alternative_does_not_prove_minimum() -> None:
    row = evaluate(fast="unknown").by_type[0]
    assert row.sites[0].best_known_route.seconds == 1200
    assert row.index.value is None and not row.full_ranking_eligible
    assert "CARGO_COMPATIBILITY_UNKNOWN" in row.sites[0].reasons


def test_inbound_is_not_reversed_and_duplicates_do_not_double_weight() -> None:
    assert evaluate(fast="confirmed").by_type[0].sites[0].best_known_route.seconds == 600
    assert (
        evaluate(fast="confirmed", inbound=True).by_type[0].sites[0].best_known_route.seconds
        == 1800
    )
    assert evaluate(duplicate=True).by_type[0].index.value == evaluate().by_type[0].index.value


def test_missing_expected_site_and_unproven_route() -> None:
    assert evaluate(missing=True).by_type[0].index.value is None
    assert evaluate(fast="confirmed", complete=False).by_type[0].index.value is None


def test_profile_urgency_does_not_change_physical_requirements() -> None:
    assert replace(m.PROFILE, urgency="urgent").requirements_hash == m.PROFILE.requirements_hash
    assert (
        m.PROFILE.goods == "food"
        and not m.PROFILE.temperature.controlled
        and m.PROFILE.urgency == "ordinary"
    )
    assert (
        replace(m.PROFILE, temperature=CargoTemperature(True, 2, 8, "chilled")).requirements_hash
        != m.PROFILE.requirements_hash
    )


def test_passenger_result_and_substituted_receipts_rejected() -> None:
    base = m.run_demo()
    route, proof = m.truck("site1", "port", 1200)

    def run(routes: Any, proofs: Any) -> None:
        analyze_urban_cargo(
            m.PROFILE,
            m.START,
            (m.CargoSite("site1", m.CONTEXT, m.PROOF),),
            (CargoNode("port", "port", "port", m.CONTEXT, m.PROOF),),
            routes,
            proofs,
            replace(base.coverage, expected_site_ids=("site1",)),
            ("port",),
            100,
            config=load_freight_config(),
        )

    with pytest.raises(ValueError, match="TRUCK_ROUTE_REQUIRED"):
        run((object(),), ())
    with pytest.raises(ValueError, match="RECEIPT_MISMATCH"):
        run((route,), (replace(proof, route_fingerprint="other"),))
    with pytest.raises(ValueError, match="BINDING_MISMATCH"):
        run(
            (
                replace(
                    route, compatibility=replace(route.compatibility, requirements_hash="other")
                ),
            ),
            (),
        )
    with pytest.raises(ValueError):
        replace(route, costing=cast(Any, "auto"))


def test_empty_complete_catalogue_vs_unknown_catalogue() -> None:
    coverage = UrbanCargoCoverage(
        m.CONTEXT, m.PROFILE.requirements_hash, "outbound", ("site1",), ("port",), True, m.PROOF
    )
    sites: tuple[Any, ...] = (m.CargoSite("site1", m.CONTEXT, m.PROOF),)
    result = analyze_urban_cargo(
        m.PROFILE,
        m.START,
        sites,
        (),
        (),
        (),
        coverage,
        ("port",),
        100,
        config=load_freight_config(),
    )
    assert result.by_type[0].index.value == 0
    unknown = analyze_urban_cargo(
        m.PROFILE,
        m.START,
        sites,
        (),
        (),
        (),
        replace(coverage, complete_node_types=()),
        ("port",),
        100,
        config=load_freight_config(),
    )
    assert unknown.by_type[0].index.value is None


def test_explicit_ambiguous_node_mapping_and_temperature_content() -> None:
    terminal = CargoNode("terminal", "logistics_terminal", "terminal", m.CONTEXT, m.PROOF)
    assert terminal.analysis_type == "terminal"
    with pytest.raises(ValueError):
        CargoNode("airport", "airport", "terminal", m.CONTEXT, m.PROOF)
    frozen = CargoTemperature(True, -10, -2, "frozen-range")
    assert frozen.min_c == -10 and frozen.max_c == -2
    assert replace(m.PROFILE, temperature=frozen).requirements_hash != m.PROFILE.requirements_hash
    with pytest.raises(ValueError):
        CargoTemperature(True, 8, 2, "reversed")
    with pytest.raises(ValueError):
        CargoTemperature(True, None, None, "missing-range")
