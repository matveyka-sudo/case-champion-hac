"""validation — canonical urban implementation."""

from collections.abc import Sequence

from a2_engine.urban.models import UrbanContext, UrbanPoint


def _check_points(points: Sequence[UrbanPoint], context: UrbanContext) -> dict[str, UrbanPoint]:
    by_id: dict[str, UrbanPoint] = {}
    cells: dict[str, set[str | None]] = {}
    for point in points:
        if not isinstance(point, UrbanPoint) or point.territory_id != context.territory_id:
            raise ValueError("POINT_TERRITORY_MISMATCH")
        if point.point_id in by_id:
            raise ValueError("DUPLICATE_POINT_ID")
        shares = cells.setdefault(point.cell_id, set())
        if point.population_share_id in shares or (
            shares and (None in shares or point.population_share_id is None)
        ):
            raise ValueError("DUPLICATE_POPULATION_WEIGHT")
        shares.add(point.population_share_id)
        by_id[point.point_id] = point
    return by_id
