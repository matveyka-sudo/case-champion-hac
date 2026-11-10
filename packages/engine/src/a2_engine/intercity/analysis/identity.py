"""Evidence-bound physical service aliases; no deduction from coincident times."""

from dataclasses import replace

from a2_engine.intercity.analysis.models import PhysicalService, TransportModeBinding
from a2_engine.routing.timetable_models import Timetable, TimetableRun, TimetableTransfer, refs


def normalize_physical_services(
    timetable: Timetable,
    bindings: tuple[TransportModeBinding, ...],
    services: tuple[PhysicalService, ...],
) -> tuple[Timetable, tuple[tuple[str, str], ...], dict[tuple[str, int], str]]:
    mode_map = {b.transport_type: b.mode for b in bindings}
    if len(mode_map) != len(bindings):
        raise ValueError("DUPLICATE_TRANSPORT_TYPE_BINDING")
    runs = {r.run_id: r for r in timetable.runs}
    remap: dict[str, str] = {}
    normalized: list[TimetableRun] = []
    departures: dict[tuple[str, int], str] = {}
    seen_services: set[str] = set()
    seen_departures: set[str] = set()
    for service in sorted(services, key=lambda s: s.physical_service_id):
        if service.physical_service_id in seen_services:
            raise ValueError("DUPLICATE_PHYSICAL_SERVICE")
        seen_services.add(service.physical_service_id)
        if set(service.run_ids) - runs.keys() or set(service.run_ids) & remap.keys():
            raise ValueError("UNKNOWN_OR_MULTIPLY_ALIASED_RUN")
        aliases = [runs[run_id] for run_id in service.run_ids]
        first = aliases[0]
        shape = tuple(
            (e.sequence, e.node_id, e.arrival_at, e.departure_at, e.pickup, e.dropoff)
            for e in first.events
        )
        for alias in aliases:
            if (
                tuple(
                    (e.sequence, e.node_id, e.arrival_at, e.departure_at, e.pickup, e.dropoff)
                    for e in alias.events
                )
                != shape
                or alias.active != first.active
                or alias.service_date != first.service_date
                or mode_map.get(alias.transport_type) != mode_map.get(first.transport_type)
                or (
                    first.transport_type not in mode_map
                    and alias.transport_type != first.transport_type
                )
            ):
                raise ValueError("INCONSISTENT_PHYSICAL_SERVICE_ALIAS")
        mode = mode_map.get(first.transport_type)
        kind = (
            "physical:" + mode if mode is not None else "unmapped-physical:" + first.transport_type
        )
        if mode is not None and kind in mode_map and mode_map[kind] != mode:
            raise ValueError("RESERVED_PHYSICAL_TRANSPORT_TYPE")
        if mode is not None:
            mode_map[kind] = mode
        events = tuple(
            replace(
                e,
                evidence_refs=refs(
                    tuple(ref for a in aliases for ref in a.events[i].evidence_refs)
                    + service.evidence_refs
                )
                if all(a.events[i].evidence_refs for a in aliases)
                else (),
            )
            for i, e in enumerate(first.events)
        )
        canonical = replace(
            first,
            source_id="accepted-physical-service",
            trip_id=service.physical_service_id,
            transport_type=kind,
            events=events,
            calendar_evidence_refs=refs(
                tuple(ref for a in aliases for ref in a.calendar_evidence_refs)
                + service.evidence_refs
            )
            if all(a.calendar_evidence_refs for a in aliases)
            else (),
        )
        if canonical.run_id in runs and canonical.run_id not in service.run_ids:
            raise ValueError("PHYSICAL_RUN_ID_COLLISION")
        normalized.append(canonical)
        for alias in aliases:
            remap[alias.run_id] = canonical.run_id
        events_by_sequence = {e.sequence: e for e in first.events[:-1]}
        for departure in service.departures:
            if (
                departure.event_sequence not in events_by_sequence
                or departure.departure_id in seen_departures
            ):
                raise ValueError("INVALID_OR_DUPLICATE_PHYSICAL_DEPARTURE")
            seen_departures.add(departure.departure_id)
            departures[canonical.run_id, departure.event_sequence] = departure.departure_id
    normalized.extend(r for r in timetable.runs if r.run_id not in remap)
    rules: dict[tuple[str, str, str | None, str | None], TimetableTransfer] = {}
    for rule in timetable.transfers:
        mapped = replace(
            rule,
            from_run_id=remap.get(rule.from_run_id, rule.from_run_id) if rule.from_run_id else None,
            to_run_id=remap.get(rule.to_run_id, rule.to_run_id) if rule.to_run_id else None,
        )
        pattern = mapped.from_node_id, mapped.to_node_id, mapped.from_run_id, mapped.to_run_id
        old = rules.get(pattern)
        if old is not None:
            if (old.allowed, old.minimum_seconds, old.semantics) != (
                mapped.allowed,
                mapped.minimum_seconds,
                mapped.semantics,
            ):
                raise ValueError("CONFLICTING_PHYSICAL_ALIAS_TRANSFER_RULES")
            mapped = replace(
                min((old, mapped), key=lambda r: r.rule_id),
                evidence_refs=refs(old.evidence_refs + mapped.evidence_refs),
            )
        rules[pattern] = mapped
    return (
        replace(timetable, runs=tuple(normalized), transfers=tuple(rules.values())),
        tuple(sorted(mode_map.items())),
        departures,
    )
