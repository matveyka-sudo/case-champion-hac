"""Publish later request times already supported by accepted passenger timetables."""

import argparse
import json
import os
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from pathlib import Path
from shutil import copytree
from zoneinfo import ZoneInfo

from a2_api.adapters.product_capabilities import compact_modes
from a2_api.adapters.product_inputs import IntercityInputs, UrbanInputs
from a2_api.adapters.product_runtime import Manifest, load_runtime
from a2_engine import (
    BoardingReadiness,
    build_reference,
    load_intercity_passenger_rules,
    load_passenger_config,
)
from a2_engine.intercity.analysis.calculation import analyze_intercity_passenger_with_access

SLOTS = (8, 13, 18, 23)


def extend(base: Path, out: Path, through: date, urban_checks: Path | None = None) -> list[dict]:
    verified_urban = (
        {
            (row["request"]["snapshot_id"], datetime.fromisoformat(row["request"]["departure_at"]))
            for row in json.loads(urban_checks.read_text())
            if row["request"]["analysis_kind"] == "urban_passenger"
            and row["request"]["passenger_mode"] == "public_transport"
            and isinstance(row["score"], (float, int))
            and row["analysis"]
        }
        if urban_checks
        else set()
    )
    manifest_path = base / "manifest.json"
    os.environ["A2_PRODUCT_RUNTIME_PATH"] = str(manifest_path.resolve())
    os.environ["A2_PRODUCT_RUNTIME_SHA256"] = sha256(manifest_path.read_bytes()).hexdigest()
    runtime = load_runtime()
    config = load_passenger_config()
    rules = load_intercity_passenger_rules()
    zones = {row.territory_id: ZoneInfo(row.timezone) for row in runtime.manifest.territories}
    scopes = {}
    for profile, inputs in runtime.profiles.values():
        if profile.compact and isinstance(inputs, IntercityInputs):
            for mode in compact_modes(inputs):
                scopes.setdefault(
                    (profile.territory_level, profile.origin_id, profile.destination_id, mode), []
                ).append(profile)

    expanded = {}
    previous_report = base / "passenger-coverage-audit.json"
    report = json.loads(previous_report.read_text()) if previous_report.is_file() else []
    urban_slots = []
    for key, profiles in scopes.items():
        level, origin, destination, mode = key
        if mode not in {"air", "water", "rail", "road"} or len(profiles) != 1:
            continue
        (profile,) = profiles
        _, inputs = runtime.profiles[profile.profile_id]
        if not isinstance(inputs, IntercityInputs) or not inputs.timetable.runs:
            continue
        zone = zones[origin]
        period = runtime.snapshots[profile.snapshot_id].valid_period
        last_day = min(through, period.end) if period.end else through
        start_day = profile.valid_to.astimezone(zone).date()
        reference = build_reference(inputs.reference_graph, inputs.reference_scope)
        target = "mixed" if inputs.mixed_only else mode
        verified = []
        end = profile.valid_to
        for offset in range(max(0, (last_day - start_day).days + 1)):
            day = start_day + timedelta(days=offset)
            for hour in SLOTS:
                requested = datetime.combine(day, time(hour), zone)
                if requested < profile.valid_to:
                    continue
                if requested >= inputs.timetable.scope.end_at:
                    break
                readiness = inputs.readiness
                if inputs.initial_airport_checkin_seconds is not None:
                    readiness = tuple(
                        BoardingReadiness(
                            node,
                            requested + timedelta(seconds=inputs.initial_airport_checkin_seconds),
                            inputs.scope.evidence_refs,
                        )
                        for node in inputs.scope.comparison_scope.origin_node_ids
                    )
                analysis = analyze_intercity_passenger_with_access(
                    inputs.scope,
                    requested,
                    inputs.timetable,
                    inputs.transport_modes,
                    inputs.physical_services,
                    reference,
                    config=config,
                    rules=rules,
                    frequency_start_at=inputs.frequency_start_at,
                    frequency_end_at=inputs.frequency_end_at,
                    readiness=readiness,
                    road_access=inputs.road_access,
                    resource_limit=inputs.resource_limit,
                )
                winner = next(
                    group.best_known for group in analysis.by_mode if group.category == target
                )
                if winner is None or winner.evaluation.index.value is None:
                    break
                end = requested + timedelta(seconds=1)
                verified.append(
                    {"departure_at": requested.isoformat(), "index": winner.evaluation.index.value}
                )
            else:
                continue
            break
        if end > profile.valid_to:
            expanded[profile.profile_id] = profile.model_copy(update={"valid_to": end})
            report.append(
                {
                    "level": level,
                    "origin": origin,
                    "destination": destination,
                    "mode": mode,
                    "snapshot_id": profile.snapshot_id,
                    "previous_end": profile.valid_to.isoformat(),
                    "valid_to": end.isoformat(),
                    "verified_slots": verified,
                }
            )
    for profile, inputs in runtime.profiles.values():
        if not (
            profile.compact
            and isinstance(inputs, UrbanInputs)
            and profile.passenger_modes == ["public_transport"]
            and inputs.timetable
        ):
            continue
        table = inputs.timetable
        zone = zones[profile.city_id]
        day = profile.valid_to.astimezone(zone).date()
        snapshot_end = runtime.snapshots[profile.snapshot_id].valid_period.end
        service_days = {run.service_date for run in table.runs}
        checked = []
        end = profile.valid_to
        while (
            day <= through and (snapshot_end is None or day <= snapshot_end) and day in service_days
        ):
            for hour in SLOTS:
                requested = datetime.combine(day, time(hour), zone)
                if requested < profile.valid_to:
                    continue
                # A late request needs the whole four-hour waiting horizon.
                if (profile.snapshot_id, requested) not in verified_urban or requested + timedelta(
                    hours=4
                ) > table.scope.end_at:
                    break
                end = requested + timedelta(seconds=1)
                checked.append(requested.isoformat())
            else:
                day += timedelta(days=1)
                continue
            break
        if end > profile.valid_to:
            expanded[profile.profile_id] = profile.model_copy(update={"valid_to": end})
            urban_slots.append(
                {
                    "city": profile.city_id,
                    "snapshot_id": profile.snapshot_id,
                    "request_times_with_calendar_coverage": checked,
                }
            )

    copytree(base, out)
    updated = runtime.manifest.model_copy(
        update={
            "profiles": [
                expanded.get(profile.profile_id, profile) for profile in runtime.manifest.profiles
            ],
        }
    )
    # Validate the serialized manifest as it will be read by API and worker.
    text = updated.model_dump_json(indent=2)
    Manifest.model_validate_json(text)
    (out / "manifest.json").write_text(text + "\n")
    (out / "passenger-coverage-audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    (out / "urban-passenger-coverage-audit.json").write_text(
        json.dumps(urban_slots, ensure_ascii=False, indent=2) + "\n"
    )
    return report


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--base", required=True, type=Path)
    cli.add_argument("--out", required=True, type=Path)
    cli.add_argument("--through", required=True, type=date.fromisoformat)
    cli.add_argument("--urban-checks", type=Path)
    args = cli.parse_args()
    report = extend(args.base, args.out, args.through, args.urban_checks)
    print(
        f"Extended {len(report)} profiles, verified {sum(len(row['verified_slots']) for row in report)} added slots"
    )
