"""Nearest compatible freight infrastructure over a fixed physical-site roster."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from a2_engine.freight.models import CargoContext, CargoProfile, compatibility_state
from a2_engine.freight.truck import TruckRoute, TruckSearchReceipt
from a2_engine.freight.urban import (
    CargoScope,
    CargoSiteResult,
    urban_cargo_site_index,
    urban_cargo_territory_index,
)
from a2_engine.shared.area import select_area_h
from a2_engine.shared.config import FreightConfig
from a2_engine.shared.normalized import advance, checked_id, content_hash, ids, utc
from a2_engine.shared.results import MetricResult


@dataclass(frozen=True)
class CargoSite:
    site_id: str
    context: CargoContext
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        checked_id(self.site_id)
        if not isinstance(self.context, CargoContext):
            raise ValueError("CARGO_CONTEXT_REQUIRED")
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))


@dataclass(frozen=True)
class CargoNode:
    node_id: str
    canonical_kind: str
    analysis_type: str
    context: CargoContext
    mapping_evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.node_id, self.canonical_kind, self.analysis_type):
            checked_id(value)
        if not isinstance(self.context, CargoContext):
            raise ValueError("CARGO_CONTEXT_REQUIRED")
        object.__setattr__(self, "mapping_evidence_refs", ids(self.mapping_evidence_refs))


@dataclass(frozen=True)
class UrbanCargoCoverage:
    context: CargoContext
    requirements_hash: str
    direction: Literal["inbound", "outbound"]
    expected_site_ids: tuple[str, ...]
    complete_node_types: tuple[str, ...]
    roster_complete: bool
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.context, CargoContext)
            or self.direction not in ("inbound", "outbound")
            or type(self.roster_complete) is not bool
        ):
            raise ValueError("INVALID_URBAN_CARGO_COVERAGE")
        checked_id(self.requirements_hash)
        for key in ("expected_site_ids", "complete_node_types"):
            object.__setattr__(self, key, ids(getattr(self, key), required=False))
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))


@dataclass(frozen=True)
class UrbanCargoSiteAnalysis:
    site_id: str
    selected_node_id: str | None
    best_known_route: TruckRoute | None
    observed_routes: tuple[TruckRoute, ...]
    minimum_proven: bool
    evaluation: CargoSiteResult
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class UrbanCargoTypeAnalysis:
    scope: CargoScope
    sites: tuple[UrbanCargoSiteAnalysis, ...]
    index: MetricResult
    full_ranking_eligible: bool


@dataclass(frozen=True)
class UrbanCargoAnalysis:
    analysis_id: str
    context: CargoContext
    profile: CargoProfile
    requested_at: datetime
    coverage: UrbanCargoCoverage
    h_seconds: MetricResult
    by_type: tuple[UrbanCargoTypeAnalysis, ...]
    evidence_refs: tuple[str, ...]


def analyze_urban_cargo(
    profile: CargoProfile,
    requested_at: datetime,
    sites: tuple[CargoSite, ...],
    nodes: tuple[CargoNode, ...],
    routes: tuple[TruckRoute, ...],
    receipts: tuple[TruckSearchReceipt, ...],
    coverage: UrbanCargoCoverage,
    selected_node_types: tuple[str, ...],
    area_km2: float | None,
    *,
    config: FreightConfig,
) -> UrbanCargoAnalysis:
    at = utc(requested_at)
    kinds = ids(selected_node_types)
    context = coverage.context
    if (
        context.methodology_version != config.methodology_version
        or coverage.requirements_hash != profile.requirements_hash
        or profile.urgency not in config.delivery_half_life_seconds
    ):
        raise ValueError("URBAN_CARGO_PROFILE_CONTEXT_MISMATCH")
    site_by_id: dict[str, CargoSite] = {}
    for site in sites:
        if site.context != context or site.site_id not in coverage.expected_site_ids:
            raise ValueError("SITE_OUTSIDE_ACCEPTED_ROSTER")
        if site.site_id in site_by_id and site_by_id[site.site_id] != site:
            raise ValueError("CONFLICTING_PHYSICAL_SITE")
        site_by_id[site.site_id] = site
    node_by_id = {n.node_id: n for n in nodes}
    if len(node_by_id) != len(nodes) or any(n.context != context for n in nodes):
        raise ValueError("INVALID_OR_DUPLICATE_CARGO_NODE")
    receipt_by_id = {r.route_id: r for r in receipts}
    if len(receipt_by_id) != len(receipts):
        raise ValueError("DUPLICATE_TRUCK_RECEIPT")
    pairs: dict[
        tuple[str, str], list[tuple[TruckRoute, bool, Literal["confirmed", "forbidden", "unknown"]]]
    ] = {}
    evidence = set(coverage.evidence_refs)
    for route in routes:
        if not isinstance(route, TruckRoute):
            raise ValueError("TRUCK_ROUTE_REQUIRED_NOT_PASSENGER_AUTO")
        if (
            route.context != context
            or route.requirements_hash != profile.requirements_hash
            or route.vehicle != profile.vehicle
            or route.requested_at != at
            or route.direction != coverage.direction
        ):
            raise ValueError("TRUCK_ROUTE_QUERY_MISMATCH")
        site_id, node_id = (
            (route.from_id, route.to_id)
            if coverage.direction == "outbound"
            else (route.to_id, route.from_id)
        )
        if site_id not in site_by_id or node_id not in node_by_id:
            raise ValueError("UNKNOWN_CARGO_ROUTE_ENDPOINT")
        receipt = receipt_by_id.get(route.route_id)
        proven = False
        if receipt is not None:
            expected_scope = content_hash(
                (
                    context,
                    profile.requirements_hash,
                    route.from_id,
                    route.to_id,
                    at,
                    coverage.direction,
                )
            )
            if (
                receipt.route_fingerprint != route.fingerprint
                or receipt.search_scope_id != expected_scope
            ):
                raise ValueError("TRUCK_SEARCH_RECEIPT_MISMATCH")
            proven = receipt.complete and receipt.minimum_confirmed
            evidence.update(receipt.evidence_refs)
        end = advance(at, route.seconds or 0)
        state = compatibility_state(
            route.compatibility,
            context=context,
            profile=profile,
            from_id=route.from_id,
            to_id=route.to_id,
            service_id=route.service_id,
            direction=coverage.direction,
            start_at=at,
            end_at=end,
        )
        pairs.setdefault((site_id, node_id), []).append((route, proven, state))
        evidence.update(route.evidence_refs + route.compatibility.evidence_refs)
    ids(tuple(r.route_id for r in routes), required=False)
    if set(receipt_by_id) - {r.route_id for r in routes}:
        raise ValueError("ORPHAN_TRUCK_RECEIPT")
    h = select_area_h(area_km2, bands=config.area_h_bands)
    results = []
    for kind in kinds:
        cargo_scope = CargoScope(
            kind, coverage.direction, profile.profile_id
        )  # validated direction above
        site_results = []
        relevant = tuple(n for n in nodes if n.analysis_type == kind)
        for site_id in coverage.expected_site_ids:
            reasons = set()
            if site_id not in site_by_id:
                reasons.add("EXPECTED_PHYSICAL_SITE_MISSING")
            if not coverage.roster_complete:
                reasons.add("PHYSICAL_SITE_ROSTER_INCOMPLETE")
            if kind not in coverage.complete_node_types:
                reasons.add("CARGO_NODE_CATALOGUE_INCOMPLETE")
            observed = []
            allowed = []
            for node in relevant:
                data = pairs.get((site_id, node.node_id), ())
                if not data:
                    reasons.add("CARGO_PAIR_ROUTE_UNKNOWN")
                for route, proven, state in data:
                    observed.append(route)
                    if state == "forbidden":
                        continue
                    if state == "unknown":
                        reasons.add("CARGO_COMPATIBILITY_UNKNOWN")
                        continue
                    if not proven or route.status == "unknown":
                        reasons.add("TRUCK_MINIMUM_UNPROVEN")
                    if route.seconds is not None:
                        allowed.append((route.seconds, node.node_id, route.route_id, route))
            best = min(allowed, key=lambda item: item[:3]) if allowed else None
            proven = not reasons
            evaluation = urban_cargo_site_index(
                site_id,
                best[0] if best is not None and proven else None,
                h.value,
                scope=cargo_scope,
                compatibility_confirmed=True,
                confirmed_no_route=best is None and proven,
            )
            site_results.append(
                UrbanCargoSiteAnalysis(
                    site_id,
                    best[1] if best else None,
                    best[3] if best else None,
                    tuple(sorted(observed, key=lambda r: r.route_id)),
                    proven,
                    evaluation,
                    tuple(sorted(reasons)),
                )
            )
        index = urban_cargo_territory_index(
            tuple(s.evaluation for s in site_results), scope=cargo_scope
        )
        if not coverage.roster_complete:
            index = MetricResult(None, "unknown", "PHYSICAL_SITE_ROSTER_INCOMPLETE")
        results.append(
            UrbanCargoTypeAnalysis(
                cargo_scope,
                tuple(site_results),
                index,
                coverage.roster_complete
                and all(s.minimum_proven for s in site_results)
                and index.value is not None,
            )
        )
    evidence.update(ref for s in sites for ref in s.evidence_refs)
    evidence.update(ref for n in nodes for ref in n.mapping_evidence_refs)
    identity = content_hash(
        (
            profile,
            at,
            tuple(sorted(site_by_id.values(), key=lambda s: s.site_id)),
            tuple(sorted(nodes, key=lambda n: n.node_id)),
            tuple(sorted(routes, key=lambda r: r.route_id)),
            tuple(sorted(receipts, key=lambda r: r.route_id)),
            coverage,
            kinds,
            area_km2,
            config.methodology_version,
            config.area_h_bands,
        )
    )
    return UrbanCargoAnalysis(
        "urban-cargo:" + identity,
        context,
        profile,
        at,
        coverage,
        h,
        tuple(results),
        tuple(sorted(evidence)),
    )
