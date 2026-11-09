"""Narrow applicability boundary between rich reference search and scalar formulas."""

from dataclasses import dataclass
from typing import Literal

from a2_engine.intercity.passenger import ReferenceTime
from a2_engine.routing.reference.models import ReferenceScope, identifier
from a2_engine.routing.reference.search import ReferenceSearchResult
from a2_engine.shared.config import PassengerConfig


@dataclass(frozen=True)
class ReferenceComparison:
    scope: ReferenceScope
    actual_origin_node_id: str
    actual_destination_node_id: str
    actual_traffic_mode: Literal["static", "typical", "live", "historical"] = "static"

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ReferenceScope):
            raise ValueError("REFERENCE_SCOPE_REQUIRED")
        identifier(self.actual_origin_node_id)
        identifier(self.actual_destination_node_id)
        if self.actual_traffic_mode not in ("static", "typical", "live", "historical"):
            raise ValueError("INVALID_ACTUAL_TRAFFIC_MODE")


def reference_time_from_result(
    result: ReferenceSearchResult,
    comparison: ReferenceComparison,
    *,
    config: PassengerConfig,
) -> ReferenceTime:
    """Check the independently supplied analysis context; preserve partial numbers.

    Equality covers direction, graph, snapshot, acceptance/applicability rules,
    fixed boundary sets, allowed modes, methodology/profile and comparison kind.
    City-pair actual nodes may differ from the minimizing reference pair, but must
    belong to those same fixed sets. Actual traffic is deliberately not constrained
    to static: only the calibration of A is static.
    """
    scope = result.scope
    applicable = (
        scope == comparison.scope
        and scope.network.methodology_version == config.methodology_version
        and scope.network.profile_version == config.reference_profile
        and comparison.actual_origin_node_id in scope.origin_node_ids
        and comparison.actual_destination_node_id in scope.destination_node_ids
    )
    return ReferenceTime(
        result.reference_id,
        result.seconds,
        scope.network.profile_version,
        result.completeness,
        applicable,
    )
