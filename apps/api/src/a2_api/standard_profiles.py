"""Bounded standard requests derived from the verified runtime, never city-specific code.

Planning is read-only. Submission uses the existing HTTP queue; it is deliberately
separate from API startup and from opening a page.
"""

from datetime import datetime
from typing import Any

from a2_contracts.common import canonical_hash
from a2_contracts.v2.requests import parse_analysis_request

from a2_api.adapters.product_inputs import IntercityInputs
from a2_api.adapters.product_runtime import Runtime


def standard_plan(
    runtime: Runtime, departure_at: datetime, deployment_version: str
) -> dict[str, Any]:
    if departure_at.utcoffset() is None:
        raise ValueError("EXPLICIT_TIMEZONE_REQUIRED")
    if not deployment_version.strip():
        raise ValueError("DEPLOYMENT_VERSION_REQUIRED")
    requests: dict[str, dict[str, Any]] = {}
    skipped = [
        {"profile_id": key, "reason": reason}
        for key, reason in sorted(runtime.profile_failures.items())
    ]
    for profile_id, (profile, inputs) in sorted(runtime.profiles.items()):
        if not profile.valid_from <= departure_at < profile.valid_to:
            skipped.append({"profile_id": profile_id, "reason": "OUTSIDE_PROFILE_VALIDITY"})
            continue
        if "static" not in profile.traffic_modes:
            skipped.append({"profile_id": profile_id, "reason": "LIVE_PROFILE_NOT_PRECOMPUTED"})
            continue
        base: dict[str, Any] = {
            "schema_version": "2",
            "analysis_kind": profile.analysis_kind,
            "snapshot_id": profile.snapshot_id,
            "departure_at": departure_at.isoformat(),
            "traffic_mode": "static",
        }
        variants: list[dict[str, Any]] = []
        if profile.analysis_kind == "urban_passenger":
            # One category per job: never submit the entire city's category matrix.
            variants = [
                dict(
                    city_id=profile.city_id,
                    passenger_mode=mode,
                    categories=[category],
                    priorities={category: "medium"},
                )
                for mode in sorted(profile.passenger_modes)
                for category in sorted(profile.categories)
            ]
        elif profile.analysis_kind == "urban_cargo":
            variants = [
                dict(
                    city_id=profile.city_id,
                    cargo_profile_id=profile.cargo_profile_id,
                    direction=profile.direction,
                )
            ]
        else:
            base.update(
                territory_level=profile.territory_level,
                origin_id=profile.origin_id,
                destination_id=profile.destination_id,
            )
            if profile.analysis_kind == "intercity_passenger":
                if isinstance(inputs, IntercityInputs):
                    modes = (
                        {str(mode) for mode in inputs.scope.applicable_modes}
                        if inputs.timetable.runs
                        else set()
                    )
                    if inputs.car is not None:
                        modes.add("private_car")
                    variants = [{"passenger_modes": [mode]} for mode in sorted(modes)]
            else:
                variants = [
                    dict(
                        cargo_profile_id=profile.cargo_profile_id,
                        cargo_transport=profile.cargo_transport,
                        pipeline_product=profile.pipeline_product,
                    )
                ]
        if not variants:
            skipped.append({"profile_id": profile_id, "reason": "NO_EXECUTABLE_STANDARD_MODE"})
        for variant in variants:
            request = parse_analysis_request({**base, **variant})
            payload = request.model_dump(mode="json")
            identity = canonical_hash(
                {
                    "runtime": runtime.digest,
                    "deployment_version": deployment_version,
                    "request": payload,
                }
            )
            requests.setdefault(identity, {"key": identity, "request": payload, "profiles": []})[
                "profiles"
            ].append(profile_id)
    return {
        "schema_version": "waydex-standard-plan/1",
        "runtime_sha256": runtime.digest,
        "deployment_version": deployment_version,
        "requests": list(requests.values()),
        "skipped": skipped,
    }
