"""Leaf-only citations from the canonical saved evidence closure."""

from collections.abc import Iterable

from a2_contracts.v2.evidence import EvidenceResolver


def leaf_references(refs: Iterable[str], resolver: EvidenceResolver) -> list[str]:
    """Expand before presentation caps, preserving the order of legacy leaf refs."""
    identities = tuple(refs)
    resolver.require(identities)
    if all(identity in resolver.leaf_ids for identity in identities):
        return list(identities)
    return list(
        dict.fromkeys(
            leaf
            for identity in dict.fromkeys(identities)
            for leaf in (
                (identity,)
                if identity in resolver.leaf_ids
                else sorted(resolver.expand((identity,)))
            )
        )
    )
