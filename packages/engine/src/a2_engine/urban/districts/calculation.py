"""districts / calculation — canonical urban implementation."""

from collections.abc import Mapping, Sequence
from decimal import Decimal, localcontext

from a2_engine.shared.results import MetricResult, validate_index
from a2_engine.urban.analysis.models import UrbanAnalysis
from a2_engine.urban.districts.models import (
    UrbanCellAllocation,
    UrbanDistrict,
    UrbanDistrictAnalysis,
    UrbanDistrictComparison,
    UrbanDistrictCoverage,
    UrbanDistrictMetric,
    UrbanDistrictModeResult,
    UrbanDistrictPopulation,
    UrbanDistrictRank,
    UrbanDistrictRanking,
    UrbanDistrictShare,
    UrbanDistrictValue,
    UrbanOriginalCell,
)
from a2_engine.urban.indices import territory_index
from a2_engine.urban.models import references


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _sum_decimal(values: Sequence[Decimal | None]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    with localcontext() as ctx:
        ctx.prec = 1000
        return sum((value for value in values if value is not None), Decimal())


def _sum(values: Sequence[float | None]) -> Decimal | None:
    return _sum_decimal([_decimal(value) if value is not None else None for value in values])


def _unique(rows: Sequence[object], keys: Sequence[object], name: str) -> None:
    if len(set(keys)) != len(rows):
        raise ValueError("DUPLICATE_" + name)


def _allocation(
    cell: UrbanOriginalCell, rows: Sequence[UrbanDistrictShare], coverage: UrbanDistrictCoverage
) -> UrbanCellAllocation:
    with localcontext() as ctx:
        ctx.prec = 1000
        allocated = _sum([row.population for row in rows])
        known = sum(
            (_decimal(row.population) for row in rows if row.population is not None), Decimal()
        )
        if cell.population is not None and known > _decimal(cell.population):
            raise ValueError("DISTRICT_SHARES_EXCEED_CELL_POPULATION")
        residual = (
            _decimal(cell.population) - allocated
            if cell.population is not None and allocated is not None
            else None
        )
        reasons = set()
        precision = coverage.precision
        documented = False
        if residual is None:
            reasons.add("ALLOCATION_POPULATION_UNKNOWN")
        elif residual > 0:
            if precision is not None and rows:
                cell_quantum, share_quantum = (
                    _decimal(precision.cell_quantum),
                    _decimal(precision.share_quantum),
                )
                if _decimal(cell.population or 0) % cell_quantum != 0 or any(
                    row.population is not None and _decimal(row.population) % share_quantum != 0
                    for row in rows
                ):
                    raise ValueError("POPULATION_PRECISION_MISMATCH")
                # Explicit published rule, not an arbitrary small epsilon.
                bound = cell_quantum / 2 + len(rows) * share_quantum
                documented = residual <= bound
            reasons.add("DOCUMENTED_PRECISION_RESIDUAL" if documented else "UNALLOCATED_POPULATION")
        return UrbanCellAllocation(
            cell.cell_id, cell.population, allocated, residual, documented, tuple(sorted(reasons))
        )


def analyze_urban_districts(
    urban_analysis: UrbanAnalysis,
    original_cells: Sequence[UrbanOriginalCell],
    district_catalog: Sequence[UrbanDistrict],
    population_shares: Sequence[UrbanDistrictShare],
    coverage: UrbanDistrictCoverage,
) -> UrbanDistrictAnalysis:
    """Reuse point/category indices and the city's H; never reroute or change selections."""
    analysis = urban_analysis
    context = analysis.context
    if coverage.context != context:
        raise ValueError("DISTRICT_CONTEXT_MISMATCH")
    if (
        context.territory_id != context.city_id
        or analysis.area.city_id != context.city_id
        or analysis.area.geometry_version != context.versions.geometry_version
        or analysis.population_coverage.context != context
    ):
        raise ValueError("DISTRICT_REQUIRES_CONSISTENT_CITY_ANALYSIS")
    if not analysis.selected_categories:
        raise ValueError("DISTRICT_CATEGORY_SELECTION_REQUIRED")
    if not analysis.modes or any(
        mode.passenger_mode not in ("walk", "public_transport", "private_car")
        for mode in analysis.modes
    ):
        raise ValueError("DISTRICT_MODE_SELECTION_REQUIRED")
    if any(j.candidate.context != context for j in analysis.journeys):
        raise ValueError("POINT_COMPARISON_CONTEXT_MISMATCH")
    cells = tuple(sorted(original_cells, key=lambda cell: cell.cell_id))
    catalog = tuple(sorted(district_catalog, key=lambda d: d.district_id))
    shares = tuple(sorted(population_shares, key=lambda s: (s.cell_id, s.district_id)))
    _unique(cells, [c.cell_id for c in cells], "ORIGINAL_CELL")
    _unique(catalog, [d.district_id for d in catalog], "DISTRICT")
    _unique(shares, [(s.cell_id, s.district_id) for s in shares], "DISTRICT_SHARE")
    _unique(
        cells,
        [c.point_id for c in cells if c.point_id is not None]
        + [("excluded", c.cell_id) for c in cells if c.point_id is None],
        "CELL_POINT",
    )
    cell_map = {cell.cell_id: cell for cell in cells}
    district_map = {d.district_id: d for d in catalog}
    expected = tuple(sorted(coverage.expected_district_ids))
    if (
        set(cell_map) - coverage.expected_cell_ids
        or set(district_map) - set(expected)
        or any(
            cell.city_id != context.city_id or cell.grid_version not in coverage.grid_versions
            for cell in cells
        )
    ):
        raise ValueError("CELL_OR_DISTRICT_SCOPE_MISMATCH")
    for district in catalog:
        if (
            district.city_id != context.city_id
            or district.administrative_level != "district"
            or district.boundary_version
            != coverage.district_boundary_versions[district.district_id]
        ):
            raise ValueError("DISTRICT_BOUNDARY_OR_CITY_MISMATCH")
    if any(s.cell_id not in cell_map or s.district_id not in expected for s in shares):
        raise ValueError("UNKNOWN_SHARE_CELL_OR_DISTRICT")
    by_cell = {
        cell.cell_id: tuple(s for s in shares if s.cell_id == cell.cell_id) for cell in cells
    }
    qa = tuple(_allocation(cell, by_cell[cell.cell_id], coverage) for cell in cells)
    allocation_issues = set()
    if set(cell_map) != coverage.expected_cell_ids:
        allocation_issues.add("INCOMPLETE_ORIGINAL_CELLS")
    for item in qa:
        if "UNALLOCATED_POPULATION" in item.reasons:
            allocation_issues.add("UNALLOCATED_POPULATION")
        if item.residual_population is None and not by_cell[item.cell_id]:
            allocation_issues.add("ALLOCATION_POPULATION_UNKNOWN")
    population_diagnostics = []
    district_reasons: dict[str, set[str]] = {}
    weights: dict[str, dict[str, float | None]] = {}
    for district_id in expected:
        rows = [s for s in shares if s.district_id == district_id]
        reasons = set(allocation_issues)
        if district_id not in district_map:
            reasons.add("MISSING_DISTRICT_CATALOG_ENTRY")
        if district_id not in coverage.complete_district_ids:
            reasons.add("DISTRICT_ALLOCATION_UNCONFIRMED")
        excluded = _sum([s.population for s in rows if cell_map[s.cell_id].excluded])
        if excluded is None or excluded > 0:
            reasons.add("EXCLUDED_DISTRICT_POPULATION")
        if any(s.population is None for s in rows):
            reasons.add("DISTRICT_POPULATION_UNKNOWN")
        if any(cell_map[s.cell_id].population is None and s.population != 0 for s in rows):
            reasons.add("CELL_POPULATION_UNKNOWN")
        district_reasons[district_id] = reasons
        weights[district_id] = {s.cell_id: s.population for s in rows}
        population_diagnostics.append(
            UrbanDistrictPopulation(
                district_id, _sum([s.population for s in rows]), excluded, tuple(sorted(reasons))
            )
        )
    baseline_reasons = set()
    if context.versions.population_version is None:
        baseline_reasons.add("POPULATION_VERSION_UNAVAILABLE")
    if analysis.h_seconds.value is None:
        baseline_reasons.add("H_UNAVAILABLE")
    elif analysis.h_seconds.value <= 0:
        raise ValueError("INVALID_CITY_H")
    city_reasons = set(baseline_reasons)
    if (
        not coverage.original_cells_complete
        or set(cell_map) != coverage.expected_cell_ids
        or not analysis.population_coverage.complete
    ):
        city_reasons.add("INCOMPLETE_ORIGINAL_CELLS")
    if analysis.population_coverage.excluded_population != 0:
        city_reasons.add("INCOMPLETE_POPULATION_COVERAGE")
    if analysis.population_coverage.point_ids != frozenset(
        cell.point_id for cell in cells if cell.point_id is not None
    ):
        city_reasons.add("CITY_POINT_ROSTER_UNCONFIRMED")
    _unique(analysis.modes, [m.passenger_mode for m in analysis.modes], "DISTRICT_MODE")
    modes = []
    for mode in sorted(analysis.modes, key=lambda m: m.passenger_mode):
        _unique(mode.points, [p.point.point_id for p in mode.points], "DISTRICT_POINT")
        _unique(mode.points, [p.point.cell_id for p in mode.points], "DISTRICT_POINT_CELL")
        points = {p.point.cell_id: p for p in mode.points}
        for point in mode.points:
            cell = cell_map.get(point.point.cell_id)
            if (
                cell is None
                or cell.point_id != point.point.point_id
                or cell.excluded
                or point.point.territory_id != context.city_id
                or point.passenger_mode != mode.passenger_mode
                or (
                    point.point.population,
                    point.point.population_year,
                    point.point.population_method,
                )
                != (cell.population, cell.population_year, cell.population_method)
            ):
                raise ValueError("POINT_CELL_POPULATION_MISMATCH")
            if len(point.categories) != len(analysis.selected_categories) or {
                c.category for c in point.categories
            } != set(analysis.selected_categories):
                raise ValueError("POINT_CATEGORY_SELECTION_MISMATCH")
            validate_index(point.index)
            for category in point.categories:
                if (
                    category.point_id != point.point.point_id
                    or category.passenger_mode != mode.passenger_mode
                    or (
                        category.coverage is not None and category.coverage.scope.context != context
                    )
                ):
                    raise ValueError("POINT_COMPARISON_CONTEXT_MISMATCH")
                validate_index(category.index)

        def metric(category: str | None) -> UrbanDistrictMetric:
            indices = {}
            for cell in cells:
                p = points.get(cell.cell_id)
                if p is None:
                    indices[cell.cell_id] = MetricResult(None, "unknown", "POINT_INDEX_UNAVAILABLE")
                elif category is None:
                    indices[cell.cell_id] = (
                        p.index
                        if p.completeness == "full"
                        else MetricResult(None, "unknown", "INCOMPLETE_POINT_INDEX")
                    )
                else:
                    category_result = next(c for c in p.categories if c.category == category)
                    indices[cell.cell_id] = (
                        category_result.index
                        if category_result.completeness == "full"
                        else MetricResult(None, "unknown", "INCOMPLETE_POINT_CATEGORY")
                    )

            def aggregate(
                populations: Mapping[str, float | None], reasons: set[str]
            ) -> MetricResult:
                value = territory_index({key: indices[key] for key in populations}, populations)
                if value.status == "not_applicable" and not reasons.difference(
                    {"H_UNAVAILABLE", "POPULATION_VERSION_UNAVAILABLE"}
                ):
                    return value
                return MetricResult(None, "unknown", sorted(reasons)[0]) if reasons else value

            city = aggregate({c.cell_id: c.population for c in cells}, city_reasons)
            values = []
            for district_id in expected:
                reasons = district_reasons[district_id] | baseline_reasons
                value = aggregate(weights[district_id], reasons)
                if value.reason_code:
                    reasons = reasons | {value.reason_code}
                if value.value is not None and city.value is not None:
                    deficit = MetricResult(max(0, city.value - value.value), "available")
                else:
                    deficit = MetricResult(
                        None,
                        "not_applicable" if value.status == "not_applicable" else "unknown",
                        "ZERO_WEIGHT"
                        if value.status == "not_applicable"
                        else "CITY_INDEX_UNAVAILABLE"
                        if city.value is None
                        else "DISTRICT_INDEX_UNAVAILABLE",
                    )
                values.append(
                    UrbanDistrictValue(
                        district_id,
                        value,
                        deficit,
                        value.status == "available",
                        tuple(sorted(reasons)),
                    )
                )
            scope = UrbanDistrictComparison(
                context,
                mode.passenger_mode,
                category,
                tuple(sorted(analysis.selected_categories.items())),
                analysis.h_seconds,
                coverage.catalog_version,
                coverage.distribution_version,
                coverage.grid_versions,
                tuple(sorted(coverage.district_boundary_versions.items())),
                expected,
            )
            available = sorted(
                (v for v in values if v.ranking_eligible),
                key=lambda v: (-float(v.index.value or 0), v.district_id),
            )
            entries = []
            previous = None
            rank = 0
            for position, ranked_value in enumerate(available, 1):
                assert ranked_value.index.value is not None
                if previous != ranked_value.index.value:
                    rank = position
                entries.append(
                    UrbanDistrictRank(ranked_value.district_id, ranked_value.index.value, rank)
                )
                previous = ranked_value.index.value
            full = all(v.index.status != "unknown" for v in values)
            applicable = [v.index.value for v in values if v.index.value is not None]
            gap = (
                MetricResult(None, "unknown", "INCOMPLETE_DISTRICT_GAP")
                if not full
                else MetricResult(max(applicable) - min(applicable), "available")
                if applicable
                else MetricResult(None, "not_applicable", "NO_APPLICABLE_DISTRICTS")
            )
            ranking = UrbanDistrictRanking(
                scope,
                tuple(entries),
                tuple((v.district_id, v.reasons) for v in values if not v.ranking_eligible),
                full,
                "all_expected_districts" if full else "available_subset",
            )
            return UrbanDistrictMetric(scope, city, tuple(values), gap, ranking)

        modes.append(
            UrbanDistrictModeResult(
                mode.passenger_mode,
                metric(None),
                tuple(metric(category) for category in sorted(analysis.selected_categories)),
            )
        )
    evidence = list(coverage.evidence_refs)
    if coverage.precision:
        evidence.extend(coverage.precision.evidence_refs)
    evidence.extend(ref for cell in cells for ref in cell.evidence_refs)
    evidence.extend(ref for district in catalog for ref in district.evidence_refs)
    evidence.extend(ref for share in shares for ref in share.evidence_refs)
    return UrbanDistrictAnalysis(
        analysis,
        cells,
        catalog,
        shares,
        coverage,
        qa,
        tuple(population_diagnostics),
        _sum([c.population for c in cells]),
        _sum([s.population for s in shares]),
        _sum_decimal([q.residual_population for q in qa]),
        _sum([c.population for c in cells if c.excluded]),
        tuple(modes),
        references(evidence),
    )
