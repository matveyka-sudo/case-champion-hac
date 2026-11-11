"""Opt-in exact finite SYNTHETIC model receipt, never physical city completeness."""

import os
from typing import Literal

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import ContractModel
from a2_contracts.v2.dataset import AcceptedData
from a2_engine import (
    Timetable,
    TimetableEvent,
    TimetableFootpath,
    TimetableScope,
    TimetableTransfer,
    scenario_hash,
)

from a2_api.adapters.timetable_source import ServiceClock, verify_canonical_timetable

DEMO_SOURCE = "DEMO_SYNTHETIC_TRANSPORT_MODEL"
DEMO_LIMITATIONS = frozenset({DEMO_SOURCE, "NOT_A_REAL_CITY_RANKING"})


class DemoTransportReceipt(ContractModel):
    schema_version: Literal["waydex-demo-closed-transport/1"]
    snapshot_hash: str
    model: Timetable
    limitations: tuple[str, ...]


def verify_demo_snapshot(snapshot: AcceptedData) -> frozenset[str]:
    """Check explicit model provenance after the runtime hash-registers a snapshot.

    Snapshot names and city names confer no trust. The runtime binds this exact
    snapshot to a server profile; these checks are also reused at output time so
    ordinary accepted facts cannot accidentally acquire model-only privileges.
    """
    if os.environ.get("A2_DEMO_TRANSPORT_ENABLED", "").strip().lower() != "true":
        raise ValueError("DEMO_TRANSPORT_DEPLOYMENT_DISABLED")
    version = snapshot.source_versions.get(DEMO_SOURCE)
    if DEMO_SOURCE not in snapshot.qa.checks or not version:
        raise ValueError("DEMO_TRANSPORT_EXPLICIT_SYNTHETIC_LABEL_REQUIRED")
    proof = frozenset(
        row.evidence_id
        for row in snapshot.evidence
        if row.source_id == DEMO_SOURCE and row.source_version == version and row.verified
    )
    if not proof:
        raise ValueError("DEMO_TRANSPORT_SYNTHETIC_EVIDENCE_REQUIRED")
    return proof


def verify_demo_model(
    snapshot: AcceptedData,
    table: Timetable,
    clock: ServiceClock | None,
    receipt: DemoTransportReceipt,
) -> DemoTransportReceipt:
    """Validate a server-hash-pinned finite model without changing closure flags.

    Scenario admission reuses this BASELINE model receipt, then applies the
    independently validated overlay. A changed after timetable is not a new
    accepted source and must not be supplied as the baseline receipt model.
    """
    snapshot = AcceptedData.model_validate(snapshot.model_dump(mode="json"))
    receipt = DemoTransportReceipt.model_validate(receipt.model_dump(mode="json"))
    proof = verify_demo_snapshot(snapshot)
    if (
        receipt.snapshot_hash != canonical_hash(snapshot.model_dump(mode="json"))
        or receipt.model != table
        or scenario_hash(receipt.model) != scenario_hash(table)
    ):
        raise ValueError("DEMO_TRANSPORT_RECEIPT_IDENTITY_MISMATCH")
    if not DEMO_LIMITATIONS <= set(receipt.limitations):
        raise ValueError("DEMO_TRANSPORT_EXPLICIT_SYNTHETIC_LABEL_REQUIRED")
    rows: list[TimetableScope | TimetableFootpath | TimetableTransfer | TimetableEvent] = [
        table.scope,
        *table.footpaths,
        *table.transfers,
    ]
    if not proof or any(not proof.intersection(row.evidence_refs) for row in rows):
        raise ValueError("DEMO_TRANSPORT_SYNTHETIC_EVIDENCE_REQUIRED")
    known_evidence = {row.evidence_id for row in snapshot.evidence}
    for run in table.runs:
        if (
            run.source_id != DEMO_SOURCE
            or not proof.intersection(run.calendar_evidence_refs)
            or not set(run.calendar_evidence_refs) <= known_evidence
        ):
            raise ValueError("DEMO_TRANSPORT_SYNTHETIC_RUN_REQUIRED")
        rows.extend(run.events)
    if any(
        not set(row.evidence_refs) <= known_evidence or not proof.intersection(row.evidence_refs)
        for row in rows
    ):
        raise ValueError("DEMO_TRANSPORT_SYNTHETIC_EVIDENCE_REQUIRED")
    if not all(
        (
            table.scope.schedule_complete,
            table.scope.calendar_complete,
            table.scope.footpaths_complete,
            table.scope.transfers_complete,
            table.scope.continuations_complete,
        )
    ):
        raise ValueError("DEMO_TRANSPORT_CLOSED_MODEL_REQUIRED")
    canonical = verify_canonical_timetable(snapshot, table, clock)
    if not canonical.materialization_complete:
        raise ValueError("DEMO_TRANSPORT_CANONICAL_EXECUTIONS_INCOMPLETE")
    return receipt
