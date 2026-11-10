"""District aggregation consumes already-computed synthetic indices, not routes."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from a2_engine import (
    MetricResult,
    UrbanAnalysis,
    UrbanArea,
    UrbanCategoryResult,
    UrbanContext,
    UrbanPoint,
    UrbanPointResult,
    UrbanPopulationCoverage,
    UrbanTerritoryResult,
    UrbanVersions,
)
from a2_engine.urban.district_models import (
    UrbanDistrict,
    UrbanDistrictCoverage,
    UrbanDistrictShare,
    UrbanOriginalCell,
    UrbanSharePrecision,
)
from a2_engine.urban.districts import UrbanDistrictAnalysis, analyze_urban_districts


@dataclass(frozen=True)
class Case:
    analysis: UrbanAnalysis
    cells: tuple[UrbanOriginalCell, ...]
    catalog: tuple[UrbanDistrict, ...]
    shares: tuple[UrbanDistrictShare, ...]
    coverage: UrbanDistrictCoverage

    def run(self, **kwargs: Any) -> UrbanDistrictAnalysis:
        args: dict[str, Any] = dict(
            urban_analysis=self.analysis,
            original_cells=self.cells,
            district_catalog=self.catalog,
            population_shares=self.shares,
            coverage=self.coverage,
        )
        args.update(kwargs)
        return analyze_urban_districts(**args)


def make_case(values: tuple[float | None, ...] = (100, 40, 20, 80)) -> Case:
    versions = UrbanVersions(
        "synthetic-snapshot",
        "graph",
        "calendar",
        "population",
        "geometry",
        "a2-methodology-v3",
        "config",
        "static",
    )
    context = UrbanContext(
        "city", "city", datetime(2026, 11, 8, 12, tzinfo=UTC), "static", versions, "search"
    )
    populations = (100, 300, 600, 600)
    points = tuple(
        UrbanPoint(
            f"p{i}", f"cell{i}", f"p{i}", f"p{i}", 0, "city", pop, 2025, "estimate", ("evidence",)
        )
        for i, pop in enumerate(populations, 1)
    )
    results = []
    for point, value in zip(points, values, strict=True):
        index = (
            MetricResult(value, "available")
            if value is not None
            else MetricResult(None, "unknown", "TEST_UNKNOWN")
        )
        category = UrbanCategoryResult(
            point.point_id,
            "airport",
            "walk",
            None,
            (),
            (),
            index,
            MetricResult(None, "unknown", "TAU_UNAVAILABLE"),
            None,
            None,
            True,
            "full" if value is not None else "partial",
            ("evidence",),
            (),
        )
        results.append(
            UrbanPointResult(
                point, "walk", (category,), index, "full" if value is not None else "partial"
            )
        )
    population = UrbanPopulationCoverage(
        context, True, 0, ("population-proof",), frozenset(p.point_id for p in points)
    )
    mode = UrbanTerritoryResult(
        "walk",
        tuple(results),
        MetricResult(None, "unknown", "IGNORED_UPPER_AGGREGATE"),
        "partial",
        False,
        ("evidence",),
    )
    analysis = UrbanAnalysis(
        context,
        UrbanArea("city", 224.668, "geometry", ("area-proof",)),
        {"airport": "high"},
        MetricResult(1800, "available"),
        (),
        (mode,),
        population,
        "partial",
        ("TAU_UNAVAILABLE",),
    )
    cells = tuple(
        UrbanOriginalCell(
            p.cell_id,
            "city",
            "grid",
            p.population,
            2025,
            "estimate",
            p.point_id,
            False,
            ("evidence",),
        )
        for p in points
    )
    catalog = tuple(UrbanDistrict(d, "city", d, "boundary:" + d, ("evidence",)) for d in "ABC")
    shares = tuple(
        UrbanDistrictShare("cell" + str(c), d, p, ("evidence",), area_share=0.01)
        for c, d, p in [(1, "A", 100), (2, "A", 100), (2, "B", 200), (3, "B", 600), (4, "C", 600)]
    )
    coverage = UrbanDistrictCoverage(
        context,
        "catalog-v1",
        "shares-v1",
        frozenset({"grid"}),
        {d.district_id: d.boundary_version for d in catalog},
        frozenset("ABC"),
        frozenset(c.cell_id for c in cells),
        frozenset("ABC"),
        True,
        ("coverage-proof",),
    )
    return Case(analysis, cells, catalog, shares, coverage)


def values(result: UrbanDistrictAnalysis) -> dict[str, float | None]:
    return {d.district_id: d.index.value for d in result.modes[0].overall.districts}


def test_control_means_gap_deficits_and_competition_ranking() -> None:
    case = make_case()
    result = case.run()
    metric = result.modes[0].overall
    assert values(result) == {"A": 70, "B": 25, "C": 80}
    assert metric.city_index.value == 51.25
    assert metric.city_index.value != pytest.approx((70 + 25 + 80) / 3)
    assert metric.gap.value == 55
    assert [v.deficit.value for v in metric.districts] == [0, 26.25, 0]
    assert [e.district_id for e in metric.ranking.entries] == ["C", "A", "B"]
    assert [p.population for p in result.district_populations] == [
        Decimal(200),
        Decimal(800),
        Decimal(600),
    ]
    assert result.city_population == 1600 and result.allocated_population == 1600
    assert result.source_analysis is case.analysis
    assert (
        metric.scope.h_seconds is case.analysis.h_seconds and metric.scope.h_seconds.value == 1800
    )
    assert metric.ranking.complete and metric.ranking.positions_scope == "all_expected_districts"
    assert all(
        v.ranking_eligible for v in metric.districts
    )  # Source top status/count/rank flag do not gate H.


def test_unknown_point_affects_only_positive_district_weights() -> None:
    result = make_case((100, 40, None, 80)).run()
    metric = result.modes[0].overall
    assert values(result) == {"A": 70, "B": None, "C": 80}
    assert metric.city_index.status == metric.gap.status == "unknown"
    assert all(v.deficit.status == "unknown" for v in metric.districts)
    assert [e.district_id for e in metric.ranking.entries] == ["C", "A"]
    assert not metric.ranking.complete and metric.ranking.positions_scope == "available_subset"
    assert metric.ranking.excluded[0][0] == "B"


def test_city_does_not_depend_on_district_annotation_completeness() -> None:
    case = make_case()
    coverage = replace(case.coverage, complete_district_ids=frozenset())
    result = case.run(population_shares=(), coverage=coverage)
    assert result.modes[0].overall.city_index.value == 51.25
    assert all(v.index.status == "unknown" for v in result.modes[0].overall.districts)
    assert result.residual_population == 1600


def test_deficit_remains_available_when_another_district_unknown() -> None:
    case = make_case((20, 20, 20, 80))
    result = case.run(coverage=replace(case.coverage, complete_district_ids=frozenset("AC")))
    metric = result.modes[0].overall
    assert metric.city_index.value == 42.5
    assert metric.districts[0].deficit.value == 22.5
    assert metric.districts[1].index.status == "unknown" and metric.gap.status == "unknown"


def test_unknown_share_is_not_zero_or_global_city_unknown() -> None:
    case = make_case()
    rows = tuple(replace(s, population=None) if s.cell_id == "cell3" else s for s in case.shares)
    result = case.run(population_shares=rows)
    assert values(result) == {"A": 70, "B": None, "C": 80}
    assert result.modes[0].overall.city_index.value == 51.25
    assert result.allocated_population is None and result.residual_population is None


def test_double_population_rejected_and_area_share_ignored() -> None:
    case = make_case()
    with pytest.raises(ValueError, match="EXCEED_CELL"):
        case.run(
            population_shares=tuple(
                replace(s, population=101) if s.cell_id == "cell2" and s.district_id == "A" else s
                for s in case.shares
            )
        )
    result = case.run(population_shares=tuple(replace(s, area_share=1) for s in case.shares))
    assert values(result) == {"A": 70, "B": 25, "C": 80}


def test_precision_residual_requires_documented_rule_and_is_never_redistributed() -> None:
    case = make_case()
    rows = tuple(replace(s, population=99.999) if s.cell_id == "cell1" else s for s in case.shares)
    result = case.run(population_shares=rows)
    assert result.modes[0].overall.city_index.value == 51.25
    assert result.modes[0].overall.gap.status == "unknown"
    assert result.residual_population == Decimal("0.001")
    coverage = replace(
        case.coverage, precision=UrbanSharePrecision(0.001, 0.001, ("published-floor-rule",))
    )
    result = case.run(population_shares=rows, coverage=coverage)
    assert result.cell_qa[0].documented_precision
    assert result.district_populations[0].population == Decimal("199.999")
    assert values(result)["A"] == pytest.approx((99.999 * 100 + 100 * 40) / 199.999)
    assert result.residual_population == Decimal("0.001")
    too_much = tuple(
        replace(s, population=99.9) if s.cell_id == "cell1" else s for s in case.shares
    )
    assert (
        case.run(population_shares=too_much, coverage=coverage).modes[0].overall.gap.status
        == "unknown"
    )


@pytest.mark.parametrize("population", [100, 0, None])
def test_excluded_cells_block_only_affected_district(population: float | None) -> None:
    case = make_case()
    cell = UrbanOriginalCell(
        "excluded", "city", "grid", population, 2025, "estimate", None, True, ("evidence",)
    )
    share = UrbanDistrictShare("excluded", "A", population, ("evidence",))
    result = case.run(
        original_cells=(*case.cells, cell),
        population_shares=(*case.shares, share),
        coverage=replace(
            case.coverage, expected_cell_ids=case.coverage.expected_cell_ids | {"excluded"}
        ),
    )
    assert values(result)["B"] == 25 and values(result)["C"] == 80
    if population == 0:
        assert values(result)["A"] == 70 and result.modes[0].overall.city_index.value == 51.25
    else:
        assert (
            values(result)["A"] is None and result.modes[0].overall.city_index.status == "unknown"
        )
    assert result.district_populations[0].excluded_population == (
        None if population is None else Decimal(population)
    )


def test_confirmed_zero_index_is_real_and_zero_district_is_not_zero_index() -> None:
    case = make_case((0, 0, 0, 0))
    result = case.run()
    assert values(result) == {"A": 0, "B": 0, "C": 0}
    assert result.modes[0].overall.gap.value == 0
    d = UrbanDistrict("D", "city", "D", "boundary:D", ("evidence",))
    coverage = replace(
        case.coverage,
        expected_district_ids=frozenset("ABCD"),
        complete_district_ids=frozenset("ABCD"),
        district_boundary_versions={**case.coverage.district_boundary_versions, "D": "boundary:D"},
    )
    metric = case.run(district_catalog=(*case.catalog, d), coverage=coverage).modes[0].overall
    assert metric.districts[-1].index == MetricResult(None, "not_applicable", "ZERO_WEIGHT")
    assert metric.ranking.complete and [e.rank for e in metric.ranking.entries] == [1, 1, 1]


def test_complete_expected_empty_and_single_applicable_set() -> None:
    case = make_case()
    empty = replace(
        case.coverage,
        expected_district_ids=frozenset(),
        complete_district_ids=frozenset(),
        district_boundary_versions={},
    )
    result = case.run(district_catalog=(), population_shares=(), coverage=empty)
    assert result.modes[0].overall.gap.status == "not_applicable"
    assert result.modes[0].overall.ranking.complete
    one = replace(
        case.coverage,
        expected_district_ids=frozenset("A"),
        complete_district_ids=frozenset("A"),
        district_boundary_versions={"A": "boundary:A"},
    )
    shares = tuple(
        UrbanDistrictShare(c.cell_id, "A", c.population, ("evidence",)) for c in case.cells
    )
    metric = (
        case.run(district_catalog=case.catalog[:1], population_shares=shares, coverage=one)
        .modes[0]
        .overall
    )
    assert metric.gap.value == 0 and metric.city_index.value == metric.districts[0].index.value


def test_missing_expected_district_never_yields_full_gap_or_rank() -> None:
    case = make_case()
    metric = case.run(district_catalog=(case.catalog[0], case.catalog[2])).modes[0].overall
    assert metric.gap.status == "unknown" and not metric.ranking.complete
    assert metric.scope.expected_district_ids == ("A", "B", "C")
    assert metric.districts[1].reasons == ("MISSING_DISTRICT_CATALOG_ENTRY",)


def test_ties_competition_ranks_and_unrounded_order() -> None:
    metric = make_case((80, 80, 80, 70)).run().modes[0].overall
    assert [(e.district_id, e.rank) for e in metric.ranking.entries] == [
        ("A", 1),
        ("B", 1),
        ("C", 3),
    ]
    metric = make_case((70.001, 70.001, 20, 70.002)).run().modes[0].overall
    assert [e.district_id for e in metric.ranking.entries] == ["C", "A", "B"]


@pytest.mark.parametrize(
    "field",
    [
        "snapshot_id",
        "graph_version",
        "calendar_version",
        "population_version",
        "geometry_version",
        "methodology_version",
        "config_hash",
        "traffic_version",
    ],
)
def test_comparison_versions_must_match(field: str) -> None:
    case = make_case()
    changed = replace(
        case.coverage.context,
        versions=replace(case.coverage.context.versions, **{field: "foreign"}),
    )
    with pytest.raises(ValueError, match="CONTEXT_MISMATCH"):
        case.run(coverage=replace(case.coverage, context=changed))


@pytest.mark.parametrize(
    "changes",
    [
        {"city_id": "foreign"},
        {"traffic_mode": "typical"},
        {"search_parameters_id": "other"},
        {"requested_departure_at": datetime(2026, 11, 9, tzinfo=UTC)},
    ],
)
def test_comparison_settings_must_match(changes: dict[str, Any]) -> None:
    case = make_case()
    with pytest.raises(ValueError, match="CONTEXT_MISMATCH"):
        case.run(coverage=replace(case.coverage, context=replace(case.coverage.context, **changes)))


@pytest.mark.parametrize("kind", ["cell", "district", "share", "point", "mode"])
def test_duplicates_rejected(kind: str) -> None:
    case = make_case()
    args: dict[str, Any] = {}
    if kind == "cell":
        args["original_cells"] = (*case.cells, case.cells[0])
    if kind == "district":
        args["district_catalog"] = (*case.catalog, case.catalog[0])
    if kind == "share":
        args["population_shares"] = (*case.shares, case.shares[0])
    if kind == "point":
        args["urban_analysis"] = replace(
            case.analysis,
            modes=(
                replace(
                    case.analysis.modes[0],
                    points=(*case.analysis.modes[0].points, case.analysis.modes[0].points[0]),
                ),
            ),
        )
    if kind == "mode":
        args["urban_analysis"] = replace(case.analysis, modes=case.analysis.modes * 2)
    with pytest.raises(ValueError, match="DUPLICATE_"):
        case.run(**args)


@pytest.mark.parametrize(
    "field,value",
    [
        ("population", 101),
        ("population_year", 2024),
        ("population_method", "other"),
        ("point_id", "other"),
        ("grid_version", "other"),
    ],
)
def test_point_and_cell_basis_must_match(field: str, value: Any) -> None:
    case = make_case()
    with pytest.raises(ValueError):
        case.run(original_cells=(replace(case.cells[0], **{field: value}), *case.cells[1:]))


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "100"])
def test_invalid_population_numbers(bad: Any) -> None:
    case = make_case()
    with pytest.raises(ValueError):
        replace(case.cells[0], population=bad)
    with pytest.raises(ValueError):
        replace(case.shares[0], population=bad)


def test_boundaries_admin_level_and_share_membership() -> None:
    case = make_case()
    with pytest.raises(ValueError):
        replace(case.catalog[0], administrative_level="okrug")
    with pytest.raises(ValueError):
        case.run(
            district_catalog=(replace(case.catalog[0], boundary_version="other"), *case.catalog[1:])
        )
    with pytest.raises(ValueError):
        case.run(population_shares=(replace(case.shares[0], cell_id="other"), *case.shares[1:]))


def test_input_order_and_immutable_values() -> None:
    case = make_case()
    snapshot = repr(case)
    result = case.run()
    assert result == case.run(
        original_cells=list(reversed(case.cells)),
        district_catalog=list(reversed(case.catalog)),
        population_shares=list(reversed(case.shares)),
    )
    assert repr(case) == snapshot
    with pytest.raises(TypeError):
        case.coverage.district_boundary_versions["A"] = "other"  # type: ignore[index]


def test_modes_categories_and_priorities_remain_separate() -> None:
    case = make_case()
    walk = case.analysis.modes[0]
    points = []
    for p in walk.points:
        second = replace(
            p.categories[0], category="shopping_center", index=MetricResult(0, "available")
        )
        points.append(
            replace(
                p,
                categories=(*p.categories, second),
                index=MetricResult((p.index.value or 0) * 0.75, "available"),
            )
        )
    walk = replace(walk, points=tuple(points))
    car = replace(
        walk,
        passenger_mode="private_car",
        points=tuple(
            replace(
                p,
                passenger_mode="private_car",
                index=MetricResult(100, "available"),
                categories=tuple(
                    replace(c, passenger_mode="private_car", index=MetricResult(100, "available"))
                    for c in p.categories
                ),
            )
            for p in walk.points
        ),
    )
    analysis = replace(
        case.analysis,
        selected_categories={"airport": "high", "shopping_center": "low"},
        modes=(walk, car),
    )
    result = case.run(urban_analysis=analysis)
    assert (
        result.modes[0].passenger_mode == "private_car"
        and result.modes[0].overall.city_index.value == 100
    )
    assert result.modes[1].overall.city_index.value == 51.25 * 0.75
    assert [m.city_index.value for m in result.modes[1].categories] == [51.25, 0]
    assert result.modes[1].overall.scope.selected_categories == (
        ("airport", "high"),
        ("shopping_center", "low"),
    )


def test_real_v4_weights_and_readiness_without_synthetic_scores() -> None:
    import json
    from pathlib import Path

    raw = json.loads(
        (Path(__file__).parent / "fixtures" / "kaliningrad_district_weights_v4.json").read_text()
    )
    base = make_case().analysis
    context = replace(
        base.context,
        city_id="kaliningrad",
        territory_id="kaliningrad",
        versions=UrbanVersions(**raw["versions"]),
        search_parameters_id="readiness-unbound",
    )
    analysis = replace(
        base,
        context=context,
        area=UrbanArea(
            "kaliningrad", 224.668, context.versions.geometry_version, ("real-boundary",)
        ),
        population_coverage=UrbanPopulationCoverage(
            context, False, 277.772, ("real-population",), frozenset()
        ),
        modes=(replace(base.modes[0], points=()),),
    )
    cells = tuple(UrbanOriginalCell(**row) for row in raw["cells"])
    catalog = tuple(UrbanDistrict(**row) for row in raw["districts"])
    shares = tuple(UrbanDistrictShare(**row) for row in raw["shares"])
    ids = frozenset(d.district_id for d in catalog)
    coverage = UrbanDistrictCoverage(
        context,
        raw["catalog_version"],
        raw["distribution_version"],
        frozenset(c.grid_version for c in cells),
        {d.district_id: d.boundary_version for d in catalog},
        ids,
        frozenset(c.cell_id for c in cells),
        ids,
        True,
        (raw["source"],),
        UrbanSharePrecision(**raw["precision"]),
    )
    result = analyze_urban_districts(analysis, cells, catalog, shares, coverage)
    assert len(cells) == 286 and sum(c.point_id is not None for c in cells) == 216
    assert result.city_population == Decimal("485776.410")
    assert result.allocated_population == Decimal(
        "485776.273"
    ) and result.residual_population == Decimal("0.137")
    assert result.excluded_population == Decimal("277.772")
    assert [p.population for p in result.district_populations] == list(
        map(Decimal, ["138478.673", "193507.398", "153790.202"])
    )
    assert [p.excluded_population for p in result.district_populations] == list(
        map(Decimal, ["193.203", "63.411", "21.140"])
    )
    assert sum(q.documented_precision for q in result.cell_qa) == 133
    assert all(v.index.status == "unknown" for v in result.modes[0].overall.districts)
    assert not result.modes[0].overall.ranking.entries


def projection_payloads() -> tuple[Case, dict[str, Any], dict[str, Any]]:
    case = make_case()
    v = case.analysis.context.versions
    canonical = {
        "schema_version": "2",
        "snapshot_id": v.snapshot_id,
        "qa": {"acceptance_state": "accepted"},
        "provenance": {
            "population_version": v.population_version,
            "geometry_version": v.geometry_version,
            "graph_version": v.graph_version,
        },
        "territories": [{"territory_id": "city", "geometry": {"version": "city-boundary"}}],
        "population_cells": [
            {
                "cell_id": c.cell_id,
                "territory_id": "city",
                "grid_version": c.grid_version,
                "geometry": {"version": c.grid_version},
                "population": c.population,
                "population_year": c.population_year,
                "population_method": c.population_method,
                "sample_status": "included",
            }
            for c in case.cells
        ],
        "sample_points": [
            {"point_id": c.point_id, "cell_id": c.cell_id, "territory_id": "city"}
            for c in case.cells
        ],
    }
    supplement = {
        "manifest": {
            "bundle_id": v.snapshot_id,
            "acceptance_state": "accepted",
            "graph_version": v.graph_version,
        },
        "territories": [
            {"territory_id": "city", "level": "city", "boundary_version": "city-boundary"},
            *[
                {
                    "territory_id": d.district_id,
                    "level": "district",
                    "parent_id": "city",
                    "name": d.name,
                    "boundary_version": d.boundary_version,
                    "evidence_ids": d.evidence_refs,
                }
                for d in case.catalog
            ],
        ],
        "population_cells": [
            {
                "cell_id": c.cell_id,
                "city_id": "city",
                "grid_id": c.grid_version,
                "population": c.population,
                "population_year": c.population_year,
                "population_method": c.population_method,
                "status": "sampled",
                "evidence_ids": c.evidence_refs,
                "district_shares": [
                    {
                        "territory_id": s.district_id,
                        "population": s.population,
                        "area_share": s.area_share,
                    }
                    for s in case.shares
                    if s.cell_id == c.cell_id
                ],
            }
            for c in case.cells
        ],
        "sample_points": [{"point_id": c.point_id, "cell_id": c.cell_id} for c in case.cells],
    }
    return case, canonical, supplement


@pytest.mark.parametrize(
    "field", ["none", "snapshot", "population", "geometry", "grid", "roster", "parent"]
)
def test_supplement_cross_checks_canonical_identity(field: str) -> None:
    from a2_engine import prepare_urban_district_inputs

    case, canonical, supplement = projection_payloads()
    if field == "snapshot":
        supplement["manifest"]["bundle_id"] = "other"
    if field == "population":
        supplement["population_cells"][0]["population"] = 101
    if field == "geometry":
        canonical["provenance"]["geometry_version"] = "other"
    if field == "grid":
        supplement["population_cells"][0]["grid_id"] = "other"
    if field == "roster":
        supplement["sample_points"].pop()
    if field == "parent":
        supplement["territories"][1]["parent_id"] = "other"

    def prepare() -> Any:
        return prepare_urban_district_inputs(
            case.analysis,
            canonical,
            supplement,
            catalog_version="catalog",
            distribution_version="shares",
            complete_district_ids=list("ABC"),
            original_cells_complete=True,
            evidence_refs=("proof",),
        )

    if field == "none":
        result = prepare()
        assert values(
            analyze_urban_districts(
                case.analysis,
                result.original_cells,
                result.district_catalog,
                result.population_shares,
                result.coverage,
            )
        ) == {"A": 70, "B": 25, "C": 80}
    else:
        with pytest.raises(ValueError):
            prepare()


def test_preserves_h_and_external_institution_route_from_existing_analysis() -> None:
    import runpy
    from pathlib import Path

    analysis = runpy.run_path(str(Path(__file__).parents[1] / "examples" / "synthetic_walk.py"))[
        "run_demo"
    ]().analysis
    p = analysis.modes[0].points[0].point
    cell = UrbanOriginalCell(
        p.cell_id,
        analysis.context.city_id,
        "grid",
        p.population,
        p.population_year,
        p.population_method,
        p.point_id,
        False,
        p.source_refs,
    )
    district = UrbanDistrict("small", analysis.context.city_id, "Small", "boundary", ("proof",))
    coverage = UrbanDistrictCoverage(
        analysis.context,
        "catalog",
        "shares",
        frozenset({"grid"}),
        {"small": "boundary"},
        frozenset({"small"}),
        frozenset({p.cell_id}),
        frozenset({"small"}),
        True,
        ("proof",),
    )
    result = analyze_urban_districts(
        analysis,
        (cell,),
        (district,),
        (UrbanDistrictShare(p.cell_id, "small", p.population, ("proof",)),),
        coverage,
    )
    assert result.modes[0].overall.districts[0].index.value == 50
    assert result.modes[0].overall.scope.h_seconds.value == 1800
    assert result.source_analysis.journeys is analysis.journeys
    assert (
        result.source_analysis.modes[0].points[0].categories[0].best
        is analysis.modes[0].points[0].categories[0].best
    )


def test_explicit_partial_index_never_enters_full_ranking() -> None:
    case = make_case()
    mode = case.analysis.modes[0]
    first = replace(mode.points[0], completeness="partial")
    analysis = replace(case.analysis, modes=(replace(mode, points=(first, *mode.points[1:])),))
    result = case.run(urban_analysis=analysis)
    assert values(result)["A"] is None and values(result)["B"] == 25


def test_zero_unknown_point_and_unknown_population() -> None:
    case = make_case((None, 40, 20, 80))
    first = case.analysis.modes[0].points[0]
    first = replace(first, point=replace(first.point, population=0))
    analysis = replace(
        case.analysis,
        modes=(
            replace(case.analysis.modes[0], points=(first, *case.analysis.modes[0].points[1:])),
        ),
    )
    cells = (replace(case.cells[0], population=0), *case.cells[1:])
    shares = tuple(replace(s, population=0) if s.cell_id == "cell1" else s for s in case.shares)
    assert (
        case.run(urban_analysis=analysis, original_cells=cells, population_shares=shares)
        .modes[0]
        .overall.districts[0]
        .index.value
        == 40
    )
    first = replace(first, point=replace(first.point, population=None))
    analysis = replace(
        analysis, modes=(replace(analysis.modes[0], points=(first, *analysis.modes[0].points[1:])),)
    )
    cells = (replace(cells[0], population=None), *cells[1:])
    shares = tuple(replace(s, population=None) if s.cell_id == "cell1" else s for s in shares)
    result = case.run(urban_analysis=analysis, original_cells=cells, population_shares=shares)
    assert result.modes[0].overall.city_index.status == "unknown" and values(result)["A"] is None
    assert values(result)["B"] == 25


def test_incomplete_original_roster_blocks_city_and_district_completeness() -> None:
    case = make_case()
    coverage = replace(
        case.coverage, expected_cell_ids=case.coverage.expected_cell_ids | {"missing-cell"}
    )
    result = case.run(coverage=coverage)
    assert result.modes[0].overall.city_index.status == "unknown"
    assert not result.modes[0].overall.ranking.entries


def test_unknown_h_and_confirmed_zero_weight_are_distinct() -> None:
    case = make_case()
    analysis = replace(case.analysis, h_seconds=MetricResult(None, "unknown", "AREA_UNKNOWN"))
    assert not case.run(urban_analysis=analysis).modes[0].overall.ranking.entries
    district = UrbanDistrict("D", "city", "D", "boundary:D", ("proof",))
    coverage = replace(
        case.coverage,
        expected_district_ids=frozenset("ABCD"),
        complete_district_ids=frozenset("ABCD"),
        district_boundary_versions={**case.coverage.district_boundary_versions, "D": "boundary:D"},
    )
    value = (
        case.run(
            urban_analysis=analysis, district_catalog=(*case.catalog, district), coverage=coverage
        )
        .modes[0]
        .overall.districts[-1]
    )
    assert value.index == MetricResult(None, "not_applicable", "ZERO_WEIGHT")


def test_population_version_absence_blocks_full_ranking() -> None:
    case = make_case()
    context = replace(
        case.analysis.context,
        versions=replace(case.analysis.context.versions, population_version=None),
    )
    analysis = replace(
        case.analysis,
        context=context,
        population_coverage=replace(case.analysis.population_coverage, context=context),
    )
    result = case.run(urban_analysis=analysis, coverage=replace(case.coverage, context=context))
    assert not result.modes[0].overall.ranking.entries


def test_unknown_district_allocation_does_not_inherit_city_ranking_flag() -> None:
    case = make_case()
    mode = replace(
        case.analysis.modes[0],
        ranking_eligible=True,
        completeness="full",
        index=MetricResult(51.25, "available"),
    )
    analysis = replace(case.analysis, modes=(mode,), status="available")
    result = case.run(
        urban_analysis=analysis,
        coverage=replace(case.coverage, complete_district_ids=frozenset("AC")),
    )
    assert not result.modes[0].overall.districts[1].ranking_eligible


def test_extreme_population_diagnostics_stay_finite_and_decimal() -> None:
    case = make_case((80, 80, 80, 80))
    cells = tuple(replace(c, population=1e308) for c in case.cells)
    points = tuple(
        replace(p, point=replace(p.point, population=1e308)) for p in case.analysis.modes[0].points
    )
    analysis = replace(case.analysis, modes=(replace(case.analysis.modes[0], points=points),))
    shares = tuple(
        UrbanDistrictShare(c.cell_id, "A" if i < 2 else "B" if i == 2 else "C", 1e308, ("proof",))
        for i, c in enumerate(cells)
    )
    result = case.run(urban_analysis=analysis, original_cells=cells, population_shares=shares)
    assert result.city_population == Decimal("4e308") and result.city_population.is_finite()
    assert values(result) == {"A": 80, "B": 80, "C": 80}


@pytest.mark.parametrize(
    "change",
    [{"excluded_population": None}, {"excluded_population": 10}, {"point_ids": frozenset()}],
)
def test_city_population_coverage_proof_remains_required(change: dict[str, Any]) -> None:
    case = make_case()
    analysis = replace(
        case.analysis, population_coverage=replace(case.analysis.population_coverage, **change)
    )
    metric = case.run(urban_analysis=analysis).modes[0].overall
    assert metric.city_index.status == "unknown"
    assert all(d.deficit.status == "unknown" for d in metric.districts)
    assert metric.gap.value == 55 and metric.ranking.complete
