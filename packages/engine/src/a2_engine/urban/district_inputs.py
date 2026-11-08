"""Narrow producer district supplement, cross-checked against canonical AcceptedData."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from a2_engine.urban.analysis import UrbanAnalysis
from a2_engine.urban.district_models import (
    UrbanDistrict,
    UrbanDistrictCoverage,
    UrbanDistrictShare,
    UrbanOriginalCell,
    UrbanSharePrecision,
)
from a2_engine.urban.models import references


@dataclass(frozen=True)
class UrbanDistrictInputs:
    original_cells: tuple[UrbanOriginalCell, ...]
    district_catalog: tuple[UrbanDistrict, ...]
    population_shares: tuple[UrbanDistrictShare, ...]
    coverage: UrbanDistrictCoverage


def _table(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Mapping[str, Any]]:
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError("DUPLICATE_SUPPLEMENT_" + key.upper())
    return result


def prepare_urban_district_inputs(
    urban_analysis: UrbanAnalysis,
    accepted: Mapping[str, Any],
    supplement: Mapping[str, Any],
    *,
    catalog_version: str,
    distribution_version: str,
    complete_district_ids: Sequence[str],
    original_cells_complete: bool,
    evidence_refs: Sequence[str],
    precision: UrbanSharePrecision | None = None,
) -> UrbanDistrictInputs:
    """Inputs are model_dump(mode='json') of validated ML2/AcceptedData models.

    Only cell/district/point links are projected. No bundle loader, GIS or second
    BundleData -> AcceptedData conversion is implemented here.
    """
    context = urban_analysis.context
    manifest = supplement["manifest"]
    provenance = accepted["provenance"]
    if (
        accepted["schema_version"] != "2"
        or accepted["qa"]["acceptance_state"] != "accepted"
        or manifest["acceptance_state"] != "accepted"
        or manifest["bundle_id"] != accepted["snapshot_id"]
        or accepted["snapshot_id"] != context.versions.snapshot_id
        or provenance["population_version"] != context.versions.population_version
        or provenance["geometry_version"] != context.versions.geometry_version
        or provenance["graph_version"] != manifest["graph_version"]
    ):
        raise ValueError("DISTRICT_SUPPLEMENT_VERSION_MISMATCH")
    city_id = context.city_id
    canonical_cities = _table(accepted["territories"], "territory_id")
    territories = _table(supplement["territories"], "territory_id")
    city = territories[city_id]
    if (
        city["level"] != "city"
        or city["boundary_version"] != canonical_cities[city_id]["geometry"]["version"]
    ):
        raise ValueError("DISTRICT_SUPPLEMENT_CITY_GEOMETRY_MISMATCH")
    canonical_cells = {
        k: v
        for k, v in _table(accepted["population_cells"], "cell_id").items()
        if v["territory_id"] == city_id
    }
    producer_cells = {
        k: v
        for k, v in _table(supplement["population_cells"], "cell_id").items()
        if v["city_id"] == city_id
    }
    canonical_points = {
        k: v
        for k, v in _table(accepted["sample_points"], "point_id").items()
        if v["territory_id"] == city_id
    }
    producer_points = {
        k: v
        for k, v in _table(supplement["sample_points"], "point_id").items()
        if v["cell_id"] in producer_cells
    }
    if (
        canonical_cells.keys() != producer_cells.keys()
        or canonical_points.keys() != producer_points.keys()
        or any(
            canonical_points[key]["cell_id"] != row["cell_id"]
            for key, row in producer_points.items()
        )
    ):
        raise ValueError("DISTRICT_SUPPLEMENT_CELL_POINT_ROSTER_MISMATCH")
    by_cell = {p["cell_id"]: p["point_id"] for p in producer_points.values()}
    if len(by_cell) != len(producer_points):
        raise ValueError("DUPLICATE_SUPPLEMENT_CELL_POINT")
    districts = tuple(
        UrbanDistrict(
            row["territory_id"],
            city_id,
            row["name"],
            row["boundary_version"],
            tuple(row["evidence_ids"]),
            row["level"],
        )
        for row in sorted(territories.values(), key=lambda r: r["territory_id"])
        if row["level"] == "district" and row["parent_id"] == city_id
    )
    district_refs = {d.district_id: d.evidence_refs for d in districts}
    cells = []
    shares = []
    for key, row in sorted(producer_cells.items()):
        canonical = canonical_cells[key]
        if (
            (row["population"], row["population_year"], row["population_method"], row["grid_id"])
            != (
                canonical["population"],
                canonical["population_year"],
                canonical["population_method"],
                canonical["grid_version"],
            )
            or canonical["geometry"]["version"] != row["grid_id"]
            or (row["status"] == "sampled") != (canonical["sample_status"] == "included")
            or (row["status"] == "sampled") != (key in by_cell)
        ):
            raise ValueError("DISTRICT_SUPPLEMENT_POPULATION_GRID_MISMATCH")
        cells.append(
            UrbanOriginalCell(
                key,
                city_id,
                row["grid_id"],
                row["population"],
                row["population_year"],
                row["population_method"],
                by_cell.get(key),
                row["status"] == "excluded",
                tuple(row["evidence_ids"]),
            )
        )
        for share in row["district_shares"]:
            district_id = share["territory_id"]
            if district_id not in district_refs:
                raise ValueError("DISTRICT_SUPPLEMENT_PARENT_MISMATCH")
            shares.append(
                UrbanDistrictShare(
                    key,
                    district_id,
                    share["population"],
                    references(tuple(row["evidence_ids"]) + district_refs[district_id]),
                    share["area_share"],
                )
            )
    expected = frozenset(d.district_id for d in districts)
    coverage = UrbanDistrictCoverage(
        context,
        catalog_version,
        distribution_version,
        frozenset(c.grid_version for c in cells),
        {d.district_id: d.boundary_version for d in districts},
        expected,
        frozenset(c.cell_id for c in cells),
        frozenset(complete_district_ids),
        original_cells_complete,
        tuple(evidence_refs),
        precision,
    )
    return UrbanDistrictInputs(tuple(cells), districts, tuple(shares), coverage)
