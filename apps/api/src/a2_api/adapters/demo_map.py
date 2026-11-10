"""Explicit model labels and factual sampled map coordinates for opted-in DEMO output."""

from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.map_data import AnalysisMapData, MapFeature, PointGeometry
from a2_contracts.v2.results import AnalysisResult

from a2_api.adapters.demo_transport import DEMO_LIMITATIONS


def attach_demo_result(result: AnalysisResult, snapshot: AcceptedData) -> AnalysisResult:
    if (
        not snapshot.snapshot_id.startswith("demo-moscow-")
        or result.snapshot_id != snapshot.snapshot_id
    ):
        raise ValueError("DEMO_RESULT_SNAPSHOT_MISMATCH")
    cells = {cell.cell_id: cell for cell in snapshot.population_cells}
    features = []
    for point in snapshot.sample_points:
        cell = cells[point.cell_id]
        features.append(
            MapFeature(
                entity_kind="sample_point",
                entity_id=point.point_id,
                territory_id=point.territory_id,
                point_id=point.point_id,
                cell_id=point.cell_id,
                geometry=PointGeometry(
                    type="Point", coordinates=(point.location.lon, point.location.lat)
                ),
                geometry_kind="actual",
                population=cell.population,
                population_year=cell.population_year,
                population_method=cell.population_method,
                source_refs=sorted(set(point.source_refs) | set(cell.source_refs)),
            )
        )
    for cell in snapshot.population_cells:
        features.append(
            MapFeature(
                entity_kind="cell",
                entity_id=cell.cell_id,
                territory_id=cell.territory_id,
                cell_id=cell.cell_id,
                geometry=None,
                geometry_kind="none",
                geometry_ref=cell.geometry.geometry_id,
                reason_code="DEMO_SAMPLED_POPULATION_ONLY",
                population=cell.population,
                population_year=cell.population_year,
                population_method=cell.population_method,
                source_refs=cell.source_refs,
            )
        )
    for destination in snapshot.destinations:
        features.append(
            MapFeature(
                entity_kind="destination",
                entity_id=destination.destination_id,
                territory_id=destination.territory_id,
                categories=destination.categories,
                name="Поликлиника в синтетической модели (реальные координаты)",
                geometry=PointGeometry(
                    type="Point", coordinates=(destination.location.lon, destination.location.lat)
                ),
                geometry_kind="actual",
                source_refs=destination.source_refs,
            )
        )
    values = result.model_dump(mode="json")
    values["limitations"] = sorted(set(result.limitations) | DEMO_LIMITATIONS)
    values["scores"] = [
        {**score.model_dump(mode="json"), "ranking_eligible": False} for score in result.scores
    ]
    values["map_data"] = AnalysisMapData(
        status="available",
        completeness="partial",
        reason_code="DEMO_MODEL_MAP_ONLY",
        features=features,
    ).model_dump(mode="json")
    values["result_hash"] = "pending-demo-decoration"
    checked = AnalysisResult.model_validate(values)
    return checked.model_copy(
        update={
            "result_hash": canonical_hash(checked.model_dump(mode="json", exclude={"result_hash"}))
        }
    )
