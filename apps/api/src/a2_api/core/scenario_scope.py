"""Server-owned endpoint facts frozen from the baseline's accepted snapshot."""

from typing import Self

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import Category, ContractModel, Identifier
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import UrbanPassengerRequest
from pydantic import Field, model_validator


class ScenarioEndpointScope(ContractModel):
    snapshot_id: Identifier
    snapshot_hash: str = Field(pattern="^[a-f0-9]{64}$")
    city_id: Identifier
    point_origins: dict[Identifier, Identifier]
    point_access_nodes: dict[Identifier, Identifier]
    destination_nodes: dict[Identifier, Identifier]
    destination_access_nodes: dict[Identifier, Identifier]
    destination_categories: dict[Identifier, list[Category]]

    @model_validator(mode="after")
    def complete_endpoint_records(self) -> Self:
        if self.point_origins.keys() != self.point_access_nodes.keys() or not (
            self.destination_nodes.keys()
            == self.destination_access_nodes.keys()
            == self.destination_categories.keys()
        ):
            raise ValueError("Scenario endpoint inventories must describe the same identities")
        if any(
            not values or len(values) != len(set(values))
            for values in self.destination_categories.values()
        ):
            raise ValueError("Scenario destination categories must be nonempty and unique")
        return self


def build_scenario_endpoint_scope(
    snapshot: AcceptedData, request: UrbanPassengerRequest
) -> ScenarioEndpointScope:
    """Use the same physical endpoint identity policy as product input validation.

    These facts come from the server's immutable accepted snapshot, never from
    a proposal or a calculation output. Include all eligible category objects,
    rather than just the previous winning object or previously reachable paths.
    """
    if request.snapshot_id != snapshot.snapshot_id:
        raise ValueError("Scenario endpoint snapshot differs from saved request")
    points = [p for p in snapshot.sample_points if p.territory_id == request.city_id]
    destinations = [d for d in snapshot.destinations if set(d.categories) & set(request.categories)]
    return ScenarioEndpointScope(
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        city_id=request.city_id,
        point_origins={p.point_id: "physical:point:" + p.point_id for p in points},
        point_access_nodes={p.point_id: p.access_node_id for p in points},
        destination_nodes={
            d.destination_id: "physical:destination:" + d.destination_id for d in destinations
        },
        destination_access_nodes={d.destination_id: d.access_node_id for d in destinations},
        destination_categories={d.destination_id: sorted(d.categories) for d in destinations},
    )
