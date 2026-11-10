"""Lossless factoring of repeated leaf-reference edges before bounded worker IPC."""

import json
from collections import defaultdict
from typing import Any

from a2_contracts.common import canonical_hash
from a2_contracts.v2.evidence import (
    EVIDENCE_GROUP_PREFIX,
    MAX_EVIDENCE_GROUPS,
    MAX_GROUP_MEMBERS,
    MAX_TOTAL_GROUP_MEMBERS,
    EvidenceGroup,
    EvidenceResolver,
)
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioProposal, ScenarioResult

REFERENCE_FIELDS = frozenset(
    {
        "source_refs",
        "evidence_refs",
        "calibration_evidence",
        "demand_evidence",
        "resource_evidence",
        "turnaround_evidence",
    }
)


def _reference_edges(payload: dict[str, Any]) -> list[list[str]]:
    """Visit only reference carriers, not coordinates or source definitions."""
    pending: list[Any] = [payload]
    edges: list[list[str]] = []
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"evidence_groups", "evidence_ids"}:
                    continue
                if key in REFERENCE_FIELDS and isinstance(child, list):
                    if child and all(isinstance(ref, str) for ref in child):
                        edges.append(child)
                    # EvidenceRef definitions are provenance, not reference edges.
                    continue
                if isinstance(child, dict):
                    pending.append(child)
                elif isinstance(child, list):
                    pending.extend(row for row in child if isinstance(row, dict))
    return edges


def _encoded_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _factor_edges(
    payload: dict[str, Any], edges: list[list[str]], resolver: EvidenceResolver
) -> bool:
    # Identical occurrence patterns yield a disjoint partition of leaf IDs.
    # Each group is leaf-only and can replace exactly the same set on every edge.
    occurrences: dict[str, list[int]] = defaultdict(list)
    # Short map/leg edges may cite one record from an otherwise shared catalogue.
    # They must not split the catalogue into singleton equivalence classes.
    for index, refs in enumerate(refs for refs in edges if len(refs) >= 32):
        for identity in set(refs):
            if identity in resolver.leaf_ids:
                occurrences[identity].append(index)
    classes: dict[tuple[int, ...], list[str]] = defaultdict(list)
    for identity, positions in occurrences.items():
        if len(positions) > 1:
            classes[tuple(positions)].append(identity)
    existing = {row["group_id"]: row for row in payload.get("evidence_groups", [])}
    original_order = list(existing)
    total_members = sum(len(row["evidence_ids"]) for row in existing.values())
    replacements: dict[str, str] = {}
    for pattern, identities in sorted(classes.items(), key=lambda item: tuple(sorted(item[1]))):
        identities.sort()
        for offset in range(0, len(identities), MAX_GROUP_MEMBERS):
            members = identities[offset : offset + MAX_GROUP_MEMBERS]
            if len(members) < 2:
                continue
            group_id = EVIDENCE_GROUP_PREFIX + canonical_hash(members)
            row = {"group_id": group_id, "evidence_ids": members}
            saving = len(pattern) * (_encoded_size(members) - _encoded_size([group_id]))
            cost = 0 if group_id in existing else _encoded_size(row) + 1
            if saving <= cost:
                continue
            if group_id not in existing:
                if (
                    len(existing) >= MAX_EVIDENCE_GROUPS
                    or total_members + len(members) > MAX_TOTAL_GROUP_MEMBERS
                ):
                    continue
                existing[group_id] = row
                total_members += len(members)
            elif existing[group_id] != row:
                raise ValueError("IMMUTABLE_EVIDENCE_GROUP_CHANGED")
            replacements.update((identity, group_id) for identity in members)
    if not replacements:
        return False
    member_sets = {identity: frozenset(row["evidence_ids"]) for identity, row in existing.items()}
    for refs in edges:
        original = set(refs)
        candidates = {replacements[identity] for identity in refs if identity in replacements}
        allowed = {group_id for group_id in candidates if member_sets[group_id] <= original}
        packed: list[str] = []
        used: set[str] = set()
        for identity in refs:
            target_group = replacements.get(identity)
            replacement = target_group if target_group in allowed else identity
            assert replacement is not None
            if replacement not in used:
                packed.append(replacement)
                used.add(replacement)
        refs[:] = packed
    payload["evidence_groups"] = [existing[identity] for identity in original_order] + [
        existing[identity] for identity in sorted(existing.keys() - set(original_order))
    ]
    return True


def pack_analysis_evidence(result: AnalysisResult) -> AnalysisResult:
    """Retain every fact/definition and exactly every expanded reference leaf set."""
    payload = result.model_dump(mode="json")
    AnalysisResult.model_validate(payload)
    resolver = EvidenceResolver(
        (row.evidence_id for row in result.evidence_refs), result.evidence_groups
    )
    edges = _reference_edges(payload)
    before = [resolver.expand(refs) for refs in edges]
    if not _factor_edges(payload, edges, resolver):
        return result
    grouped = EvidenceResolver(
        resolver.leaf_ids,
        [EvidenceGroup.model_validate(row) for row in payload["evidence_groups"]],
    )
    if any(grouped.expand(refs) != original for refs, original in zip(edges, before, strict=True)):
        raise ValueError("PACKED_EVIDENCE_LEAF_SET_CHANGED")
    packed = AnalysisResult.model_validate(payload)
    return packed.model_copy(
        update={
            "result_hash": canonical_hash(packed.model_dump(mode="json", exclude={"result_hash"}))
        }
    )


def pack_proposal_evidence(proposal: ScenarioProposal) -> ScenarioProposal:
    """Pack worker-generated command edges before the analysis-output envelope.

    Saved commands are never rewritten. Regenerating the same proposal uses this
    same deterministic representation before comparing it to the saved command.
    """
    payload = proposal.model_dump(mode="json")
    ScenarioProposal.model_validate(payload)
    resolver = EvidenceResolver(
        (row.evidence_id for row in proposal.evidence_refs), proposal.evidence_groups
    )
    edges = _reference_edges(payload)
    before = [resolver.expand(refs) for refs in edges]
    if not _factor_edges(payload, edges, resolver):
        return proposal
    grouped = EvidenceResolver(
        resolver.leaf_ids,
        [EvidenceGroup.model_validate(row) for row in payload["evidence_groups"]],
    )
    if any(grouped.expand(refs) != original for refs, original in zip(edges, before, strict=True)):
        raise ValueError("PACKED_PROPOSAL_EVIDENCE_LEAF_SET_CHANGED")
    return ScenarioProposal.model_validate(payload)


def _same_facts(
    left: dict[str, Any],
    right: dict[str, Any],
    left_resolver: EvidenceResolver,
    right_resolver: EvidenceResolver,
) -> bool:
    """Representation-independent leaf equality, used only before restoring a copy."""
    if left.keys() != right.keys():
        return False
    for key, value in left.items():
        other = right[key]
        if (
            key in REFERENCE_FIELDS
            and isinstance(value, list)
            and all(isinstance(ref, str) for ref in value)
        ):
            if left_resolver.expand(value) != right_resolver.expand(other):
                return False
        elif isinstance(value, dict):
            if not isinstance(other, dict) or not _same_facts(
                value, other, left_resolver, right_resolver
            ):
                return False
        elif isinstance(value, list) and value and all(isinstance(row, dict) for row in value):
            if (
                not isinstance(other, list)
                or len(value) != len(other)
                or any(
                    not _same_facts(row, peer, left_resolver, right_resolver)
                    for row, peer in zip(value, other, strict=True)
                )
            ):
                return False
        elif value != other:
            return False
    return True


def pack_scenario_evidence(result: ScenarioResult, baseline: AnalysisResult) -> ScenarioResult:
    """Keep saved before/H/reused journeys exact; factor only new after edges."""
    payload = result.model_dump(mode="json")
    ScenarioResult.model_validate(payload)
    if result.before.metrics != baseline.metrics or result.before.scores != baseline.scores:
        raise ValueError("SAVED_SCENARIO_BEFORE_CHANGED")
    saved_resolver = EvidenceResolver(
        (row.evidence_id for row in baseline.evidence_refs), baseline.evidence_groups
    )
    resolver = EvidenceResolver(
        (row.evidence_id for row in result.evidence_refs), result.evidence_groups
    )
    existing = {group.group_id: group for group in result.evidence_groups}
    for group in baseline.evidence_groups:
        if existing.get(group.group_id) != group:
            raise ValueError("SAVED_BASELINE_EVIDENCE_GROUP_CHANGED")
    # Carry the saved group definitions in their exact original order.
    payload["evidence_groups"] = [
        group.model_dump(mode="json") for group in baseline.evidence_groups
    ] + [
        group.model_dump(mode="json")
        for group in result.evidence_groups
        if group.group_id not in saved_resolver.groups
    ]
    old_metrics = {row.metric_id: row for row in baseline.metrics}
    old_scores = {row.score_id: row for row in baseline.scores}
    protected: set[int] = set()
    for kind, identities, field in (
        ("metrics", old_metrics, "source_refs"),
        ("scores", old_scores, "evidence_refs"),
    ):
        for row in payload["after"][kind]:
            identity = row["metric_id" if kind == "metrics" else "score_id"]
            previous = identities.get(identity)
            if previous is None:
                continue
            old_refs = getattr(previous, field)
            if resolver.expand(row[field]) == saved_resolver.expand(old_refs) and (
                baseline.evidence_groups or row.get("name") == "urban_h_seconds"
            ):
                row[field] = list(old_refs)
                protected.add(id(row[field]))
    saved_journeys = {row.journey_id: row for row in baseline.journeys}
    for index, row in enumerate(payload.get("after_journeys", [])):
        previous_journey = saved_journeys.get(row["journey_id"])
        if previous_journey is not None:
            original = previous_journey.model_dump(mode="json")
            if not _same_facts(row, original, resolver, saved_resolver):
                raise ValueError("SAVED_BASELINE_JOURNEY_CHANGED")
            payload["after_journeys"][index] = original
            protected.update(id(refs) for refs in _reference_edges({"journeys": [original]}))
    all_edges = _reference_edges(payload)
    before = [resolver.expand(refs) for refs in all_edges]
    edges = [
        refs
        for refs in _reference_edges(
            {"after": payload["after"], "after_journeys": payload.get("after_journeys", [])}
        )
        if id(refs) not in protected
    ]
    _factor_edges(payload, edges, resolver)
    grouped = EvidenceResolver(
        resolver.leaf_ids, [EvidenceGroup.model_validate(row) for row in payload["evidence_groups"]]
    )
    if any(
        grouped.expand(refs) != original for refs, original in zip(all_edges, before, strict=True)
    ):
        raise ValueError("PACKED_SCENARIO_EVIDENCE_LEAF_SET_CHANGED")
    packed = ScenarioResult.model_validate(payload)
    if packed.before != result.before:
        raise ValueError("PACKED_SCENARIO_BEFORE_CHANGED")
    return packed.model_copy(
        update={
            "result_hash": canonical_hash(packed.model_dump(mode="json", exclude={"result_hash"}))
        }
    )
