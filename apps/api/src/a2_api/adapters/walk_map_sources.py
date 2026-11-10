"""Project source polygons from the already verified immutable producer bundle.

The private selector contains identities, never client-supplied coordinates or
scores. Its caller must hash-verify the document and load the producer through
the existing source-admission checks before calling this pure projection.
"""

from typing import Literal

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import ContractModel, Identifier
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.map_data import MapFeature, MultiPolygonGeometry, PolygonGeometry
from a2_data_pipeline.bundle import BundleData
from pydantic import Field, TypeAdapter


class WalkMapSourceSelection(ContractModel):
    schema_version: Literal["waydex-walk-map-sources/1"]
    snapshot_id: Identifier
    snapshot_hash: Identifier
    bundle_manifest_hash: Identifier
    city_id: Identifier
    cell_ids: list[Identifier] = Field(max_length=16384)
    territory_ids: list[Identifier] = Field(max_length=4096)


def validate_walk_map_features(
    document: object, *, snapshot: AcceptedData, bundle: BundleData
) -> tuple[MapFeature, ...]:
    """Keep accepted population and geometry source identity; derive no indices."""
    selection = WalkMapSourceSelection.model_validate(document)
    if (
        selection.snapshot_id != snapshot.snapshot_id
        or bundle.manifest.bundle_id != snapshot.snapshot_id
        or selection.snapshot_hash != canonical_hash(snapshot.model_dump(mode="json"))
        or selection.bundle_manifest_hash != canonical_hash(bundle.manifest.model_dump(mode="json"))
        or selection.city_id not in snapshot.geographic_scope
        or selection.city_id not in bundle.manifest.geographic_scope
        or bundle.manifest.acceptance_state != "accepted"
        or len(set(selection.cell_ids)) != len(selection.cell_ids)
        or len(set(selection.territory_ids)) != len(selection.territory_ids)
        or not (selection.cell_ids or selection.territory_ids)
    ):
        raise ValueError("WALK_MAP_SOURCE_SELECTION_MISMATCH")
    cells = {row.cell_id: row for row in snapshot.population_cells}
    source_cells = {row.cell_id: row for row in bundle.population_cells}
    territories = {row.territory_id: row for row in snapshot.territories}
    source_territories = {row.territory_id: row for row in bundle.territories}
    shape = TypeAdapter(PolygonGeometry | MultiPolygonGeometry)
    features = []
    for identity in sorted(selection.cell_ids):
        cell, source = cells.get(identity), source_cells.get(identity)
        if (
            cell is None
            or source is None
            or cell.territory_id != selection.city_id
            or source.city_id != selection.city_id
            or cell.geometry.geometry_id != identity
            or cell.grid_version != source.grid_id
            or cell.geometry.version != source.grid_id
            or (cell.population, cell.population_year, cell.population_method)
            != (source.population, source.population_year, source.population_method)
            or cell.sample_status != ("included" if source.status == "sampled" else "excluded")
            or cell.exclusion_reason != source.exclusion_reason
            or not set(source.evidence_ids) <= set(cell.source_refs)
        ):
            raise ValueError("WALK_MAP_CELL_SOURCE_MISMATCH")
        features.append(
            MapFeature(
                entity_kind="cell",
                entity_id=identity,
                cell_id=identity,
                territory_id=selection.city_id,
                geometry=shape.validate_python(source.geometry),
                geometry_kind="actual",
                geometry_ref=cell.geometry.geometry_id,
                reason_code=cell.exclusion_reason if cell.sample_status == "excluded" else None,
                population=cell.population,
                population_year=cell.population_year,
                population_method=cell.population_method,
                source_refs=sorted(set(cell.source_refs) | set(cell.geometry.source_refs)),
            )
        )
    for identity in sorted(selection.territory_ids):
        territory, source_territory = territories.get(identity), source_territories.get(identity)
        if (
            source_territory is None
            or not (
                identity == selection.city_id
                and source_territory.level == "city"
                and source_territory.parent_id is None
                or source_territory.level == "district"
                and source_territory.parent_id == selection.city_id
            )
            or (identity == selection.city_id and territory is None)
            or territory is not None
            and (
                territory.level != source_territory.level
                or territory.name != source_territory.name
                or territory.geometry.version != source_territory.boundary_version
                or not set(source_territory.evidence_ids) <= set(territory.source_refs)
            )
        ):
            raise ValueError("WALK_MAP_TERRITORY_SOURCE_MISMATCH")
        features.append(
            MapFeature(
                entity_kind="territory",
                entity_id=identity,
                territory_id=identity,
                name=source_territory.name,
                geometry=shape.validate_python(source_territory.geometry),
                geometry_kind="actual",
                geometry_ref=f"{identity}:boundary",
                source_refs=sorted(set(source_territory.evidence_ids)),
            )
        )
    return tuple(features)
