import pytest
from a2_api.api.legacy import create_app
from a2_api.services import DomainError, Services
from a2_contracts.dataset import Source
from fastapi.testclient import TestClient


def client():
    return TestClient(create_app())


def test_liveness_does_not_claim_transport_data_are_loaded():
    response = client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "path",
    [
        "/profiles",
        "/places",
        "/destinations",
        "/coverage",
        "/evidence",
        "/evidence/demo",
        "/jobs/demo",
        "/runs/demo",
    ],
)
def test_unconfigured_services_do_not_serve_fake_results(path):
    response = client().get("/api/v1" + path, params={"bundle_id": "demo"})
    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "engine_unavailable"
    assert body["retryable"] is True
    assert body["trace_id"] == response.headers["x-trace-id"]


def test_validation_errors_use_same_safe_envelope_as_domain_errors():
    response = client().post("/api/v1/journeys", json={"unexpected": "private-text"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["field_errors"]
    assert "private-text" not in response.text


def test_malformed_json_is_a_validation_error():
    response = client().post(
        "/api/v1/journeys", content="{", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_heavy_posts_require_idempotency_key_before_scheduling():
    response = client().post(
        "/api/v1/analyses",
        json={
            "bundle_id": "demo",
            "profile_id": "regular",
            "profile_version": 1,
            "task_set_hash": "demo",
            "origin_ids": ["demo"],
            "include_case_results": False,
        },
    )
    assert response.status_code == 422
    assert any("idempotency-key" in e["location"] for e in response.json()["error"]["field_errors"])


def test_unknown_route_uses_error_envelope():
    response = client().get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_id"


def test_domain_service_is_injected_and_errors_keep_status():
    class Catalog(Services):
        async def sources(self, bundle_id):
            return [
                Source(
                    source_id="demo-source",
                    publisher="Synthetic test",
                    url="https://example.org/",
                    license_status="unknown",
                )
            ]

        async def evidence(self, bundle_id):
            raise DomainError(409, "incompatible_versions", "Bundle versions differ")

    api = TestClient(create_app(Catalog()))
    response = api.get("/api/v1/sources", params={"bundle_id": "demo"})
    assert response.status_code == 200
    assert response.json()[0]["source_id"] == "demo-source"
    response = api.get("/api/v1/evidence", params={"bundle_id": "demo"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "incompatible_versions"


def test_unexpected_service_error_is_sanitized():
    class Broken(Services):
        async def profiles(self):
            raise RuntimeError("secret-db-password")

    api = TestClient(create_app(Broken()), raise_server_exceptions=False)
    response = api.get("/api/v1/profiles")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret-db-password" not in response.text


def test_lost_database_connection_is_retryable_503_without_credentials():
    import asyncpg

    class Disconnected(Services):
        async def job(self, job_id):
            raise asyncpg.ConnectionDoesNotExistError("secret_connection_details")

    response = TestClient(create_app(Disconnected())).get("/api/v1/jobs/demo")
    assert response.status_code == 503
    assert response.json()["error"]["retryable"] is True
    assert "secret_connection_details" not in response.text
