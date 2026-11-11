"""Immutable plans for serial category jobs; no merged or averaged metric."""

from dataclasses import dataclass
from typing import Literal, Self

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import ContractModel, Identifier, Timestamp, VersionRefs
from a2_contracts.v2.requests import UrbanPassengerRequest
from pydantic import model_validator


class GroupPlan(ContractModel):
    schema_version: Literal["analysis-group/1"] = "analysis-group/1"
    request: UrbanPassengerRequest
    snapshot_hash: Identifier
    versions: VersionRefs
    valid_until: Timestamp | None

    @model_validator(mode="after")
    def pinned_snapshot(self) -> Self:
        if self.request.snapshot_id != self.versions.snapshot_id:
            raise ValueError("Group request must pin its versioned snapshot")
        return self

    def child(self, ordinal: int) -> UrbanPassengerRequest:
        category = self.request.categories[ordinal]
        return UrbanPassengerRequest.model_validate(
            self.request.model_dump(mode="json")
            | {
                "categories": [category],
                "priorities": {category: self.request.priorities[category]},
            }
        )

    @property
    def identity(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


@dataclass(frozen=True)
class GroupAdmission:
    group_id: str
    ordinal: int
    attempt: int
    plan_hash: str

    @property
    def key(self) -> str:
        return f"analysis-group:{self.group_id}:{self.ordinal}:{self.attempt}"
