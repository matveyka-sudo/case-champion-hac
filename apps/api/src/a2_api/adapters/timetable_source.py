"""Server-derived bindings from canonical dated services to engine executions.

AcceptedData does not establish exhaustive physical connectors, run-specific
transfer permissions or continuation closure. Those claims require a future
typed receipt rather than an asserted boolean on a supplemental timetable.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import ContractModel
from a2_contracts.v2.dataset import AcceptedData, ScheduledService
from a2_engine import Timetable, scenario_hash

ServiceClock = Literal["gtfs_noon_elapsed_v1", "local_service_midnight_v1"]


class TimetableSourceReceipt(ContractModel):
    schema_version: Literal["waydex-timetable-source-receipt/1"]
    snapshot_hash: str
    normalized_timetable_hash: str
    service_clock: ServiceClock
    bindings: list[tuple[str, str, date]]
    expected_execution_count: int
    materialization_complete: bool


def service_timestamp(day: date, seconds: int, timezone: str, clock: ServiceClock) -> datetime:
    zone = ZoneInfo(timezone)
    if clock == "gtfs_noon_elapsed_v1":
        # Match the explicit ML2 GTFS normalizer, including >24h service clocks.
        noon = datetime.combine(day, time(12), zone).astimezone(UTC)
        return noon - timedelta(hours=12) + timedelta(seconds=seconds)
    return (datetime.combine(day, time(0), zone) + timedelta(seconds=seconds)).astimezone(UTC)


def verify_timetable_source(
    snapshot: AcceptedData,
    table: Timetable,
    clock: ServiceClock | None,
    mode_bindings: dict[str, str] | None = None,
) -> TimetableSourceReceipt:
    if clock is None:
        raise ValueError("EXPLICIT_TIMETABLE_SERVICE_CLOCK_BINDING_REQUIRED")
    scope = table.scope
    if scope.footpaths_complete or scope.transfers_complete or scope.continuations_complete:
        raise ValueError("EXHAUSTIVE_CONNECTOR_TRANSFER_CONTINUATION_RECEIPT_REQUIRED")
    if (scope.snapshot_id, scope.graph_version, scope.calendar_version) != (
        snapshot.snapshot_id,
        snapshot.provenance.graph_version,
        snapshot.provenance.calendar_version,
    ):
        raise ValueError("TIMETABLE_SOURCE_VERSION_MISMATCH")
    evidence = {e.evidence_id: e for e in snapshot.evidence}
    services: dict[tuple[str, date], list[ScheduledService]] = {}
    expected = set()
    for service in snapshot.scheduled_services:
        for day in service.service_dates:
            for identity in {service.trip_id, service.service_id}:
                services.setdefault((identity, day), []).append(service)
            start = service_timestamp(
                day, service.stop_times[0].arrival_seconds, service.timezone, clock
            )
            end = service_timestamp(
                day, service.stop_times[-1].departure_seconds, service.timezone, clock
            )
            if start < scope.end_at and end >= scope.start_at:
                expected.add((service.service_id, day))
    bindings = []
    observed = set()
    modes = mode_bindings or {
        "bus": "public_transport",
        "tram": "public_transport",
        "trolleybus": "public_transport",
        "metro": "public_transport",
        "public_transport": "public_transport",
        "train": "rail",
        "rail": "rail",
        "flight": "air",
        "air": "air",
        "ferry": "water",
        "water": "water",
    }
    for run in table.runs:
        matches = []
        for service in services.get((run.trip_id, run.service_date), ()):
            source_ids = {evidence[ref].source_id for ref in service.source_refs}
            if (
                run.timezone != service.timezone
                or run.source_id not in source_ids
                or modes.get(run.transport_type) != service.mode
                or run.active is not True
                or len(run.events) != len(service.stop_times)
                or not set(service.source_refs) <= set(run.calendar_evidence_refs)
            ):
                continue
            valid = True
            for event, original in zip(run.events, service.stop_times, strict=True):
                if (
                    event.node_id,
                    event.sequence,
                    event.pickup,
                    event.dropoff,
                    event.arrival_at,
                    event.departure_at,
                ) != (
                    original.node_id,
                    original.sequence,
                    original.pickup,
                    original.dropoff,
                    service_timestamp(
                        run.service_date, original.arrival_seconds, service.timezone, clock
                    ),
                    service_timestamp(
                        run.service_date, original.departure_seconds, service.timezone, clock
                    ),
                ) or not set(service.source_refs) <= set(event.evidence_refs):
                    valid = False
                    break
            if valid:
                matches.append(service)
        if len(matches) != 1:
            raise ValueError("DATED_TIMETABLE_EXECUTION_DOES_NOT_MATCH_CANONICAL_SERVICE")
        service = matches[0]
        key = service.service_id, run.service_date
        if key in observed:
            raise ValueError("DUPLICATE_CANONICAL_SERVICE_EXECUTION")
        observed.add(key)
        bindings.append((run.run_id, service.service_id, run.service_date))
    if scope.schedule_complete and observed != expected:
        raise ValueError("COMPLETE_TIMETABLE_OMITS_CANONICAL_DATED_EXECUTIONS")
    return TimetableSourceReceipt(
        schema_version="waydex-timetable-source-receipt/1",
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        normalized_timetable_hash=scenario_hash(table),
        service_clock=clock,
        bindings=sorted(bindings),
        expected_execution_count=len(expected),
        materialization_complete=observed == expected,
    )
