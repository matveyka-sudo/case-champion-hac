"""Synthetic HTTP boundary checks; no observed transport values or paid calls."""

import json
from pathlib import Path

import pytest
from a2_api.api.application import create_app
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[3] / "packages/contracts/fixtures/v2"


def body(kind="urban_passenger"):
    return json.loads((FIXTURES / f"{kind}-request.json").read_text())["payload"]


def test_canonical_default_capabilities_never_claim_ready_data():
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200
        assert len(response.json()["analyses"]) == 4
        assert all(not item["calculation_available"] for item in response.json()["analyses"])
        assert "a2_session" in client.cookies
        assert "HttpOnly" in response.headers["set-cookie"]
        assert response.headers["x-request-id"]
        assert client.get("/api/v1/territories").status_code == 503
        assert (
            client.post(
                "/api/v1/analyses", json=body(), headers={"Idempotency-Key": "synthetic"}
            ).status_code
            == 503
        )


@pytest.mark.parametrize(
    "payload",
    [
        body() | {"categories": []},
        body() | {"operations": []},
        body() | {"departure_at": "2026-11-09T08:16:00+03:00"},
        body() | {"traffic_mode": "guessed"},
    ],
)
def test_invalid_inputs_are_rejected_with_safe_correlated_fields(payload):
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/analyses", json=payload, headers={"Idempotency-Key": "synthetic"}
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["request_id"] == response.headers["x-request-id"]
        assert error["field_errors"]
        assert "input" not in str(error)


def test_public_routes_do_not_accept_arbitrary_scenario_operations():
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/scenario-analyses", json={"operations": []}).status_code == 404
        response = client.post(
            "/api/v1/scenarios/server-id/runs",
            json={"schema_version": "2", "operations": []},
            headers={"Idempotency-Key": "synthetic"},
        )
        assert response.status_code == 422
        assert client.get("/api/v1/territories?level=county").status_code == 422


def test_canonical_openapi_has_four_discriminated_forms_and_scoped_routes():
    spec = create_app().openapi()
    schema = spec["paths"]["/api/v1/analyses"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert len(schema["oneOf"]) == 4
    assert schema["discriminator"]["propertyName"] == "analysis_kind"
    for path in (
        "/api/v1/jobs/{job_id}/cancel",
        "/api/v1/analyses/{analysis_id}",
        "/api/v1/analyses/{analysis_id}/explanation",
        "/api/v1/analyses/{analysis_id}/scenarios",
        "/api/v1/scenarios/{scenario_id}/runs",
        "/api/v1/scenario-runs/{run_id}",
    ):
        assert path in spec["paths"]
    assert "/api/v1/scenario-analyses" not in spec["paths"]


def test_openapi_examples_cover_saved_partial_unsupported_and_retry():
    from a2_contracts.v2.api import ErrorEnvelope, JobAccepted
    from a2_contracts.v2.results import AnalysisResult

    spec = create_app().openapi()
    post = spec["paths"]["/api/v1/analyses"]["post"]["responses"]
    accepted = post["202"]["content"]["application/json"]["examples"]["accepted"]["value"]
    assert JobAccepted.model_validate(accepted).status == "queued"
    for status, example, retryable in (("422", "unsupported", False), ("503", "retry", True)):
        value = post[status]["content"]["application/json"]["examples"][example]["value"]
        assert ErrorEnvelope.model_validate(value).error.retryable is retryable
    responses = spec["paths"]["/api/v1/analyses/{analysis_id}"]["get"]["responses"]
    partial = responses["200"]["content"]["application/json"]["examples"]["partial"]["value"]
    result = AnalysisResult.model_validate(partial)
    assert result.status == "partial"
    assert any(metric.value is None and metric.reason_code for metric in result.metrics)


def test_session_cookie_supports_https_and_cross_origin_mutations_are_rejected():
    with TestClient(create_app(secure_cookies=True), base_url="https://testserver") as client:
        response = client.get("/api/v1/capabilities")
        assert "Secure" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        response = client.post(
            "/api/v1/analyses",
            json=body(),
            headers={"Origin": "https://foreign.example", "Idempotency-Key": "synthetic"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "x" * 2001},
        {"selection": {"entity_kind": "sample_point", "entity_id": ""}},
        {"operations": []},
    ],
)
def test_chat_context_is_bounded_and_cannot_carry_scenario_commands(payload):
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/analyses/unknown/explanation", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["field_errors"]
