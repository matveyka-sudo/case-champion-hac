"""Synthetic stored point scores -> district aggregation; no routing or IO."""

from datetime import UTC, datetime
from typing import Literal

from a2_engine import (
    MetricResult,
    UrbanAnalysis,
    UrbanArea,
    UrbanCategoryResult,
    UrbanContext,
    UrbanDistrict,
    UrbanDistrictAnalysis,
    UrbanDistrictCoverage,
    UrbanDistrictShare,
    UrbanOriginalCell,
    UrbanPoint,
    UrbanPointResult,
    UrbanPopulationCoverage,
    UrbanTerritoryResult,
    UrbanVersions,
    analyze_urban_districts,
)


def run_demo(*, unknown_third: bool = False) -> UrbanDistrictAnalysis:
    versions = UrbanVersions(
        "synthetic-snapshot",
        "synthetic-graph",
        "synthetic-calendar",
        "synthetic-population",
        "synthetic-geometry",
        "a2-methodology-v3",
        "synthetic-config",
        "synthetic-static",
    )
    context = UrbanContext(
        "synthetic-city",
        "synthetic-city",
        datetime(2026, 11, 8, 12, tzinfo=UTC),
        "static",
        versions,
        "synthetic-search",
    )
    scores = [100, 40, None if unknown_third else 20, 80]
    points = []
    cells = []
    for i, (population, score) in enumerate(zip([100, 300, 600, 600], scores, strict=True), 1):
        point = UrbanPoint(
            f"p{i}",
            f"cell{i}",
            f"p{i}",
            f"p{i}",
            0,
            context.city_id,
            population,
            2025,
            "synthetic-estimate",
            ("synthetic-proof",),
        )
        index = (
            MetricResult(score, "available")
            if score is not None
            else MetricResult(None, "unknown", "SYNTHETIC_UNKNOWN")
        )
        completeness: Literal["full", "partial"] = "full" if score is not None else "partial"
        # These are explicitly synthetic, already-computed point results.
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
            completeness,
            ("synthetic-proof",),
            (),
        )
        points.append(UrbanPointResult(point, "walk", (category,), index, completeness))
        cells.append(
            UrbanOriginalCell(
                point.cell_id,
                context.city_id,
                "synthetic-grid",
                population,
                2025,
                "synthetic-estimate",
                point.point_id,
                False,
                ("synthetic-proof",),
            )
        )
    population_coverage = UrbanPopulationCoverage(
        context, True, 0, ("synthetic-proof",), frozenset(p.point.point_id for p in points)
    )
    analysis = UrbanAnalysis(
        context,
        UrbanArea(context.city_id, 224.668, versions.geometry_version, ("synthetic-area",)),
        {"airport": "high"},
        MetricResult(1800, "available"),
        (),
        (
            UrbanTerritoryResult(
                "walk",
                tuple(points),
                MetricResult(None, "unknown", "NOT_USED"),
                "partial",
                False,
                ("synthetic-proof",),
            ),
        ),
        population_coverage,
        "partial",
        ("TAU_UNAVAILABLE",),
    )
    districts = tuple(
        UrbanDistrict(d, context.city_id, d, "synthetic-boundary:" + d, ("synthetic-proof",))
        for d in "ABC"
    )
    shares = tuple(
        UrbanDistrictShare(f"cell{cell}", district, weight, ("synthetic-proof",))
        for cell, district, weight in [
            (1, "A", 100),
            (2, "A", 100),
            (2, "B", 200),
            (3, "B", 600),
            (4, "C", 600),
        ]
    )
    coverage = UrbanDistrictCoverage(
        context,
        "synthetic-catalog-v1",
        "synthetic-shares-v1",
        frozenset({"synthetic-grid"}),
        {d.district_id: d.boundary_version for d in districts},
        frozenset("ABC"),
        frozenset(c.cell_id for c in cells),
        frozenset("ABC"),
        True,
        ("synthetic-proof",),
    )
    return analyze_urban_districts(analysis, cells, districts, shares, coverage)


if __name__ == "__main__":
    result = run_demo().modes[0].overall
    print(
        "SYNTHETIC",
        {d.district_id: d.index.value for d in result.districts},
        "city=",
        result.city_index.value,
        "gap=",
        result.gap.value,
        "ranking=",
        [r.district_id for r in result.ranking.entries],
    )
