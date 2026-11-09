"""Accepted resource constraints over actual before/after execution occupancy."""

from dataclasses import dataclass, replace
from datetime import datetime

from a2_engine.freight.delivery.search import analyze_intercity_cargo
from a2_engine.scenario.identity import scenario_hash
from a2_engine.scenario.models import (
    ScenarioBaseline,
    ScenarioFeasibility,
    ScenarioFeasibilityInputs,
    ScenarioOperation,
    ScenarioOverlay,
)
from a2_engine.shared.normalized import advance, duration


@dataclass(frozen=True)
class _Occupancy:
    execution_id: str
    physical_id: str
    start_at: datetime
    end_at: datetime
    definite: bool
    calendar_known: bool


def check_scenario_feasibility(
    baseline: ScenarioBaseline,
    operation: ScenarioOperation,
    overlay: ScenarioOverlay,
    inputs: ScenarioFeasibilityInputs | None,
) -> ScenarioFeasibility:
    if inputs is None:
        return ScenarioFeasibility(
            "unverified",
            (
                "TURNAROUND_UNVERIFIED",
                "RESOURCE_COVERAGE_UNVERIFIED",
                "INFRASTRUCTURE_AND_CALENDAR_UNVERIFIED",
            ),
            (),
            (),
        )
    if inputs.baseline_id != baseline.baseline_id or inputs.operation_hash != scenario_hash(
        operation
    ):
        raise ValueError("FEASIBILITY_BASELINE_OR_OPERATION_MISMATCH")
    reasons: set[str] = set()
    violations: set[str] = set()
    evidence = set(inputs.evidence_refs)
    networks: dict[str, dict[str, _Occupancy]] = {}
    physical: dict[str, str] = {}
    identities = {
        run_id: group.physical_service_id
        for dataset in overlay.inputs.timetables
        for group in dataset.physical_services
        for run_id in group.run_ids
    }
    for dataset in overlay.inputs.timetables:
        rows = {}
        for run in dataset.timetable.runs:
            identity = scenario_hash(
                (run.snapshot_id, run.service_date, identities.get(run.run_id, run.run_id))
            )
            physical[run.run_id] = identity
            rows[run.run_id] = _Occupancy(
                run.run_id,
                identity,
                run.events[0].departure_at,
                run.events[-1].arrival_at,
                run.active is True
                and bool(run.calendar_evidence_refs)
                and all(e.evidence_refs for e in run.events),
                run.active is True
                and bool(run.calendar_evidence_refs)
                and all(e.evidence_refs for e in run.events),
            )
        networks[dataset.network_id] = rows
    for job in overlay.inputs.cargo:
        if job.job_id in networks:
            raise ValueError("SCENARIO_RESOURCE_NETWORK_NAMESPACE_CONFLICT")
        result = analyze_intercity_cargo(
            job.scope,
            job.started_at,
            job.profile,
            job.states,
            job.actions,
            job.readiness,
            config=job.config,
            rules=job.rules,
            resource_limit=job.resource_limit,
        )
        cargo_rows = {}
        action_map = {a.action_id: a for a in job.actions}
        for execution in job.executions:
            owned = set(execution.action_ids)
            windows = set()
            fully_covered = True
            for path in result.candidates:
                if owned & set(path.action_ids):
                    if not owned <= set(path.action_ids):
                        fully_covered = False
                        continue
                    intervals = [i for i in path.intervals if i.action_id in owned]
                    windows.add(
                        (min(i.start_at for i in intervals), max(i.end_at for i in intervals))
                    )
            fixed = [
                action_map[a]
                for a in owned
                if action_map[a].scheduled_at is not None and action_map[a].seconds is not None
            ]
            if windows:
                first = min(a for a, _ in windows)
                last = max(b for _, b in windows)
            elif fixed:
                first = min(a.scheduled_at for a in fixed if a.scheduled_at is not None)
                last = max(
                    advance(a.scheduled_at, a.seconds)
                    for a in fixed
                    if a.scheduled_at is not None and a.seconds is not None
                )
            else:
                continue
            identity = scenario_hash(
                (job.scope.context, execution.execution_id, execution.service_date)
            )
            physical[execution.execution_id] = identity
            definite = (
                result.data_complete
                and result.search_complete
                and fully_covered
                and len(windows) == 1
            )
            calendar_known = all(
                action_map[a].active is True and bool(action_map[a].calendar_evidence_refs)
                for a in owned
            )
            cargo_rows[execution.execution_id] = _Occupancy(
                execution.execution_id, identity, first, last, definite, calendar_known
            )
        networks[job.job_id] = cargo_rows
    if not inputs.resources_complete:
        reasons.add("RESOURCE_COVERAGE_UNVERIFIED")
    if not inputs.calendar_confirmed:
        reasons.add("CALENDAR_CONSTRAINTS_UNVERIFIED")
    if not inputs.infrastructure_confirmed:
        reasons.add("INFRASTRUCTURE_CONSTRAINTS_UNVERIFIED")
    affected = set(overlay.affected_execution_ids)
    if affected - set(inputs.covered_execution_ids):
        reasons.add("AFFECTED_EXECUTION_COVERAGE_UNVERIFIED")
    resource_rows: dict[str, dict[str, _Occupancy]] = {}
    resource_links: dict[str, dict[tuple[str, str], float]] = {}
    assigned: dict[str, str] = {}
    resources = set()
    seen_blocks = set()
    for block in inputs.blocks:
        if (block.network_id, block.resource_id) in seen_blocks:
            raise ValueError("DUPLICATE_SCENARIO_RESOURCE_BLOCK")
        seen_blocks.add((block.network_id, block.resource_id))
        resources.add(block.resource_id)
        evidence.update(block.evidence_refs)
        for accepted_link in block.links:
            key = accepted_link.previous_run_id, accepted_link.next_run_id
            bucket = resource_links.setdefault(block.resource_id, {})
            if key in bucket and bucket[key] != accepted_link.minimum_seconds:
                raise ValueError("CONFLICTING_GLOBAL_RESOURCE_LINK")
            bucket[key] = accepted_link.minimum_seconds
        if block.network_id not in networks:
            raise ValueError("FEASIBILITY_DATASET_MISSING")
        rows = networks[block.network_id]
        if set(block.run_ids) - rows.keys():
            reasons.add("EXECUTION_OCCUPANCY_UNVERIFIED")
        unique = {}
        for run_id in block.run_ids:
            if run_id not in rows:
                continue
            row = rows[run_id]
            applies = (
                inputs.constraints_valid_from <= row.start_at
                and row.end_at <= inputs.constraints_valid_to
            )
            row = replace(row, definite=row.definite and applies)
            if row.physical_id in assigned and assigned[row.physical_id] != block.resource_id:
                raise ValueError("PHYSICAL_EXECUTION_ASSIGNED_MULTIPLE_RESOURCES")
            assigned[row.physical_id] = block.resource_id
            unique[row.physical_id] = row
            resource_rows.setdefault(block.resource_id, {})[row.physical_id] = row
            if not row.definite:
                reasons.add("EXECUTION_PROCESSING_OCCUPANCY_UNVERIFIED")
            if not row.calendar_known:
                reasons.add("EXECUTION_CALENDAR_UNVERIFIED")
            if not (
                inputs.constraints_valid_from <= row.start_at
                and row.end_at <= inputs.constraints_valid_to
            ):
                reasons.add("FEASIBILITY_EVIDENCE_OUTSIDE_PERIOD")
            if not (block.available_from <= row.start_at and row.end_at <= block.available_to):
                # A broad union of alternative processing windows can only show an
                # uncertainty. Definite fixed occupancy can prove a violation.
                (violations if row.definite else reasons).add(
                    "RESOURCE_AVAILABILITY_VIOLATED"
                    if row.definite
                    else "RESOURCE_AVAILABILITY_UNVERIFIED"
                )
        ordered = sorted(unique.values(), key=lambda r: (r.start_at, r.execution_id))
        if not ordered:
            continue
        if block.minimum_before_seconds is None or block.minimum_after_seconds is None:
            reasons.add("TURNAROUND_BOUNDARIES_UNVERIFIED")
        else:
            if (
                ordered[0].start_at - block.available_from < duration(block.minimum_before_seconds)
                and ordered[0].definite
            ):
                violations.add("RESOURCE_PREPARATION_MINIMUM_VIOLATED")
            if (
                block.available_to - ordered[-1].end_at < duration(block.minimum_after_seconds)
                and ordered[-1].definite
            ):
                violations.add("RESOURCE_RELEASE_MINIMUM_VIOLATED")
        links = {(link.previous_run_id, link.next_run_id): link for link in block.links}
        if len(links) != len(block.links):
            raise ValueError("DUPLICATE_RESOURCE_TURNAROUND_RULE")
        for previous, current in zip(ordered, ordered[1:]):
            if current.start_at < previous.end_at:
                (violations if previous.definite and current.definite else reasons).add(
                    "RESOURCE_EXECUTIONS_OVERLAP"
                    if previous.definite and current.definite
                    else "RESOURCE_OVERLAP_UNVERIFIED"
                )
                continue
            link = links.get((previous.execution_id, current.execution_id))
            if link is None:
                reasons.add("TURNAROUND_UNVERIFIED")
            else:
                evidence.update(link.evidence_refs)
                if current.start_at - previous.end_at < duration(link.minimum_seconds):
                    (violations if previous.definite and current.definite else reasons).add(
                        "TURNAROUND_MINIMUM_VIOLATED"
                        if previous.definite and current.definite
                        else "TURNAROUND_UNVERIFIED"
                    )
    # The same resource can appear in several data extracts. Check their union,
    # not only each block separately, to avoid hiding cross-network conflicts.
    for resource_id, assignments in resource_rows.items():
        ordered_global = sorted(assignments.values(), key=lambda r: (r.start_at, r.execution_id))
        # An uncertain short interval between two confirmed windows must not
        # hide their proven overlap in the adjacent-window checks below.
        confirmed = [row for row in ordered_global if row.definite]
        if any(
            current.start_at < previous.end_at
            for previous, current in zip(confirmed, confirmed[1:])
        ):
            violations.add("RESOURCE_EXECUTIONS_OVERLAP")
        for previous, current in zip(ordered_global, ordered_global[1:]):
            if current.start_at < previous.end_at:
                (violations if previous.definite and current.definite else reasons).add(
                    "RESOURCE_EXECUTIONS_OVERLAP"
                    if previous.definite and current.definite
                    else "RESOURCE_OVERLAP_UNVERIFIED"
                )
            else:
                accepted = resource_links.get(resource_id, {}).get(
                    (previous.execution_id, current.execution_id)
                )
                if accepted is None:
                    reasons.add("TURNAROUND_UNVERIFIED")
                elif current.start_at - previous.end_at < duration(accepted):
                    (violations if previous.definite and current.definite else reasons).add(
                        "TURNAROUND_MINIMUM_VIOLATED"
                        if previous.definite and current.definite
                        else "TURNAROUND_UNVERIFIED"
                    )
    if any(physical.get(run_id, run_id) not in assigned for run_id in affected):
        reasons.add("AFFECTED_RESOURCE_ASSIGNMENT_MISSING")
    state = "infeasible" if violations else "unverified" if reasons else "feasible"
    from typing import Literal, cast

    return ScenarioFeasibility(
        cast(Literal["feasible", "infeasible", "unverified"], state),
        tuple(sorted(reasons | violations)),
        tuple(sorted(resources)),
        tuple(sorted(evidence)),
    )
