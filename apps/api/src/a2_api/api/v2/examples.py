"""Synthetic contract examples only; not dataset acceptance or observed results."""

from copy import deepcopy
from typing import Any

from a2_contracts.common import canonical_hash

_SYNTHETIC_ANALYSIS: dict[str, Any] = {
    "analysis_id": "demo-urban_passenger",
    "analysis_kind": "urban_passenger",
    "config_hash": "de48211883fe826978554436e644a8d9038cd5f6a2aa05b30126619ace4c9b96",
    "counts": [],
    "evidence_refs": [
        {
            "evidence_id": "demo-evidence",
            "observed_at": None,
            "retrieved_at": "2026-11-08T09:00:00Z",
            "section": "Synthetic test only",
            "source_id": "demo-source",
            "url": "https://example.org/synthetic",
        }
    ],
    "journeys": [],
    "last_returns": [],
    "limitations": ["synthetic_only"],
    "methodology_version": "a2-methodology-v3",
    "metrics": [
        {
            "completeness": "full",
            "dependency_job_id": None,
            "metric_id": "demo-time",
            "name": "Synthetic elapsed travel time",
            "population_basis": None,
            "reason_code": None,
            "site_basis": None,
            "source_refs": ["demo-evidence"],
            "status": "available",
            "subject": {
                "category": "school",
                "destination_id": None,
                "kind": "sample_point",
                "mode": "walk",
                "node_id": None,
                "origin_id": None,
                "point_id": "demo-point",
                "site_id": None,
                "territory_id": None,
            },
            "unit": "seconds",
            "value": 1800.0,
        }
    ],
    "references": [],
    "request": {
        "analysis_kind": "urban_passenger",
        "area_id": None,
        "categories": ["school"],
        "city_id": "demo-city",
        "departure_at": "2026-11-09T08:15:00+03:00",
        "methodology": {
            "config_hash": "de48211883fe826978554436e644a8d9038cd5f6a2aa05b30126619ace4c9b96",
            "reference_profile": "edge-calibrated-v1",
            "version": "a2-methodology-v3",
        },
        "passenger_mode": "walk",
        "priorities": {"school": "high"},
        "schema_version": "2",
        "snapshot_id": "demo-snapshot",
        "traffic_mode": "static",
    },
    "result_hash": "demo-hash",
    "schema_version": "2",
    "scores": [
        {
            "aggregation_basis": None,
            "completeness": "full",
            "components": [{"id": "demo-time", "kind": "metric"}],
            "evidence_refs": ["demo-evidence"],
            "methodology_version": "a2-methodology-v3",
            "name": "Synthetic category index",
            "ranking_eligible": False,
            "reason_code": None,
            "score_id": "demo-score",
            "status": "available",
            "subject": {
                "category": "school",
                "destination_id": None,
                "kind": "sample_point",
                "mode": "walk",
                "node_id": None,
                "origin_id": None,
                "point_id": "demo-point",
                "site_id": None,
                "territory_id": None,
            },
            "value": 50.0,
        }
    ],
    "snapshot_id": "demo-snapshot",
    "status": "available",
    "traffic_mode": "static",
    "versions": {
        "calendar_version": "demo-calendar",
        "code_version": "demo-code",
        "config_hash": "de48211883fe826978554436e644a8d9038cd5f6a2aa05b30126619ace4c9b96",
        "graph_version": "demo-graph",
        "methodology_version": "a2-methodology-v3",
        "population_version": None,
        "snapshot_id": "demo-snapshot",
    },
}


def _analysis_example(*, partial: bool = False) -> dict[str, Any]:
    result = deepcopy(_SYNTHETIC_ANALYSIS)
    if partial:
        missing = deepcopy(result["metrics"][0])
        missing.update(
            metric_id="demo-unknown-time",
            value=None,
            status="unknown",
            completeness="partial",
            reason_code="INPUT_NOT_OBSERVED",
            source_refs=[],
        )
        result["metrics"].append(missing)
        result["status"] = "partial"
    result["result_hash"] = canonical_hash({k: v for k, v in result.items() if k != "result_hash"})
    return result


ANALYSIS_EXAMPLES = {
    "success": {
        "summary": "Saved synthetic result; contract example only",
        "value": _analysis_example(),
    },
    "partial": {
        "summary": "Saved partial result; unknown stays null",
        "value": _analysis_example(partial=True),
    },
}
ACCEPTED_EXAMPLES = {
    "accepted": {
        "summary": "Queued calculation; poll this job",
        "value": {
            "job_id": "demo-job",
            "status": "queued",
            "request_id": "demo-request",
            "poll_after_seconds": 2,
        },
    },
}
ERROR_EXAMPLES = {
    409: {
        "recalculate": {
            "summary": "Explicitly recalculate the saved baseline",
            "value": {
                "error": {
                    "code": "incompatible_versions",
                    "message": "SCENARIO_SOURCE_BINDING_MISMATCH",
                    "field_errors": [],
                    "request_id": "demo-request",
                    "retryable": False,
                }
            },
        }
    },
    422: {
        "unsupported": {
            "summary": "Choose a request from capabilities.analysis_presets",
            "value": {
                "error": {
                    "code": "invalid_request",
                    "message": "REQUEST_OUTSIDE_SUPPORTED_PRESETS",
                    "field_errors": [
                        {
                            "location": ["request"],
                            "code": "SUPPORTED_PRESETS",
                            "message": "Select capabilities.analysis_presets",
                        }
                    ],
                    "request_id": "demo-request",
                    "retryable": False,
                }
            },
        }
    },
    503: {
        "retry": {
            "summary": "Registered inputs temporarily unavailable",
            "value": {
                "error": {
                    "code": "data_unavailable",
                    "message": "RUNTIME_INPUTS_UNAVAILABLE_FOR_REQUEST",
                    "field_errors": [],
                    "request_id": "demo-request",
                    "retryable": True,
                }
            },
        }
    },
}
