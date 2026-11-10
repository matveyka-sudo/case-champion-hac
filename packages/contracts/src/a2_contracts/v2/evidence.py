"""Lossless leaf-only evidence groups; groups introduce no new source claims."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Self

from pydantic import Field, model_validator

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import ContractModel, Identifier

EVIDENCE_GROUP_PREFIX = "evidence-group:"
MAX_EVIDENCE_GROUPS = 8192
MAX_GROUP_MEMBERS = 4096
MAX_TOTAL_GROUP_MEMBERS = 65536

if TYPE_CHECKING:
    from a2_contracts.v2.results import AnalysisResult
    from a2_contracts.v2.scenarios import ScenarioProposal, ScenarioResult


class EvidenceGroup(ContractModel):
    group_id: str = Field(pattern=r"^evidence-group:[a-f0-9]{64}$")
    evidence_ids: list[Identifier] = Field(min_length=2, max_length=MAX_GROUP_MEMBERS)

    @model_validator(mode="after")
    def content_identity(self) -> Self:
        if self.evidence_ids != sorted(set(self.evidence_ids)):
            raise ValueError("Evidence group leaves must be sorted and unique")
        if any(identity.startswith(EVIDENCE_GROUP_PREFIX) for identity in self.evidence_ids):
            raise ValueError("Evidence groups cannot contain groups")
        if self.group_id != EVIDENCE_GROUP_PREFIX + canonical_hash(self.evidence_ids):
            raise ValueError("Evidence group content hash mismatch")
        return self


class EvidenceResolver:
    """Validate again at use time, including model_copy-mutated saved models."""

    def __init__(self, leaf_ids: Iterable[str], groups: Sequence[EvidenceGroup] = ()) -> None:
        if isinstance(leaf_ids, (str, bytes)):
            raise ValueError("Evidence definitions must be an identity collection")
        leaves = tuple(leaf_ids)
        if any(not isinstance(identity, str) or not identity for identity in leaves):
            raise ValueError("Evidence definition identity required")
        if len(set(leaves)) != len(leaves):
            raise ValueError("Duplicate evidence definition identity")
        if any(identity.startswith(EVIDENCE_GROUP_PREFIX) for identity in leaves):
            raise ValueError("Leaf and group evidence namespaces must be disjoint")
        if len(groups) > MAX_EVIDENCE_GROUPS:
            raise ValueError("Evidence group count limit exceeded")
        self.leaf_ids = frozenset(leaves)
        resolved: dict[str, frozenset[str]] = {}
        total_members = 0
        for group in groups:
            if not isinstance(group, EvidenceGroup):
                raise ValueError("Canonical evidence group required")
            current = EvidenceGroup.model_validate(group.model_dump(mode="python"))
            total_members += len(current.evidence_ids)
            if total_members > MAX_TOTAL_GROUP_MEMBERS:
                raise ValueError("Evidence group member budget exceeded")
            if current.group_id in resolved:
                raise ValueError("Duplicate evidence group identity")
            members = frozenset(current.evidence_ids)
            if not members <= self.leaf_ids:
                raise ValueError("Evidence group contains an unresolved leaf")
            resolved[current.group_id] = members
        self.groups = MappingProxyType(resolved)
        self.reference_ids = self.leaf_ids | frozenset(resolved)

    def require(self, refs: Iterable[str]) -> None:
        if isinstance(refs, (str, bytes)):
            raise ValueError("Evidence references must be an identity collection")
        identities = tuple(refs)
        if (
            any(not isinstance(identity, str) for identity in identities)
            or not set(identities) <= self.reference_ids
        ):
            raise ValueError("Unknown evidence reference")

    def expand(self, refs: Iterable[str]) -> frozenset[str]:
        if isinstance(refs, (str, bytes)):
            raise ValueError("Evidence references must be an identity collection")
        identities = tuple(refs)
        self.require(identities)
        leaves: set[str] = set()
        for identity in dict.fromkeys(identities):
            group = self.groups.get(identity)
            if group is None:
                leaves.add(identity)
            else:
                leaves.update(group)
        return frozenset(leaves)


def require_result_evidence(
    result: AnalysisResult | ScenarioResult | ScenarioProposal,
) -> EvidenceResolver:
    """Recheck all saved evidence edges without dumping or expanding the payload."""
    from a2_contracts.v2.results import AnalysisResult
    from a2_contracts.v2.scenarios import ScenarioProposal, ScenarioResult

    resolver = EvidenceResolver(
        (row.evidence_id for row in result.evidence_refs), result.evidence_groups
    )
    if isinstance(result, ScenarioProposal):
        resolver.require(result.fixed_parameters.demand_evidence)
        resolver.require(result.fixed_parameters.resource_evidence)
        resolver.require(result.fixed_parameters.turnaround_evidence)
        return resolver
    values = (result,) if isinstance(result, AnalysisResult) else (result.before, result.after)
    for value in values:
        for metric in value.metrics:
            resolver.require(metric.source_refs)
        for score in value.scores:
            resolver.require(score.evidence_refs)
    journeys = result.journeys if isinstance(result, AnalysisResult) else result.after_journeys
    for journey in journeys:
        resolver.require(journey.evidence_refs)
        if journey.geometry is not None:
            resolver.require(journey.geometry.evidence_refs)
        for leg in journey.legs:
            resolver.require(leg.evidence_refs)
            if leg.geometry is not None:
                resolver.require(leg.geometry.evidence_refs)
    if isinstance(result, AnalysisResult):
        for feature in result.map_data.features:
            resolver.require(feature.source_refs)
        for reference in result.references:
            resolver.require(reference.calibration_evidence)
        for count in result.counts:
            resolver.require(count.evidence_refs)
        for last_return in result.last_returns:
            resolver.require(last_return.evidence_refs)
    elif isinstance(result, ScenarioResult):
        resolver.require(result.resource_evidence)
        resolver.require(result.turnaround_evidence)
    return resolver
