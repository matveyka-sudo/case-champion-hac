"""Pure territory scope extraction shared by analyses and read-only tools."""

from a2_contracts.v2.requests import AnalysisRequest, UrbanCargoRequest, UrbanPassengerRequest


def territory_ids(request: AnalysisRequest) -> frozenset[str]:
    if isinstance(request, (UrbanPassengerRequest, UrbanCargoRequest)):
        return frozenset({request.city_id})
    return frozenset({request.origin_id, request.destination_id})
