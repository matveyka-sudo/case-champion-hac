"""Pure projection of accepted connector records; no GIS or provider imports.

This preserves the historical rounded connector adapter. It does not replace an
unrounded source-geometry/native-offset certificate or prove a route minimum.
"""

from collections.abc import Sequence
from typing import Any

from .bundle import BundleData

CONNECTOR_RULES = "walk-connector-geometry-v2"


def project_walk_connectors(
    data: BundleData,
    *,
    walking_speed_kmh: float,
    speed_evidence_refs: Sequence[str],
    profile_version: str,
) -> list[dict[str, Any]]:
    """Project one already validated immutable bundle with the accepted profile speed."""
    if not 0.5 <= walking_speed_kmh <= 25 or not speed_evidence_refs:
        raise ValueError("an accepted walking speed with evidence is required")
    nodes = {n.access_node_id: n for n in data.access_nodes}
    places = {
        **{("point", p.point_id): (p.lon, p.lat) for p in data.sample_points},
        **{("destination", d.destination_id): (d.lon, d.lat) for d in data.destinations},
    }
    rows = []
    for connector in data.walk_connectors:
        lon, lat = places[(connector.entity_kind, connector.entity_id)]
        node = nodes[connector.access_node_id]
        rows.append(
            {
                "snapshot_id": data.manifest.bundle_id,
                "source_graph_version": data.manifest.graph_version,
                "physical_node_id": connector.physical_node_id,
                "access_node_id": connector.access_node_id,
                "physical_location": {"lon": lon, "lat": lat},
                "access_location": {"lon": node.lon, "lat": node.lat},
                "seconds": round(connector.length_m / (walking_speed_kmh / 3.6), 1)
                if connector.traversable
                else None,
                "traversable": connector.traversable,
                "time_basis": f"{CONNECTOR_RULES}:straight_line_length/{profile_version}",
                "evidence_refs": [*connector.evidence_ids, *speed_evidence_refs],
            }
        )
    return rows
