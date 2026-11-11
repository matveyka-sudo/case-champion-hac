"""TEST_ONLY: data-driven planning and bounded queue replay, no real route claims."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from a2_api.adapters.product_runtime import Manifest, Profile, Runtime
from a2_api.precompute import execute
from a2_api.standard_profiles import standard_plan

AT = datetime(2026, 11, 11, 12, tzinfo=UTC)


def runtime():
    profiles = [
        Profile(
            profile_id="TEST_ONLY-" + city,
            analysis_kind="urban_passenger",
            city_id=city,
            passenger_modes=["walk"],
            categories=["school", "kindergarten"],
            valid_from="2026-11-01T00:00:00Z",
            valid_to="2026-12-01T00:00:00Z",
            snapshot_id="TEST_ONLY-snapshot-" + city,
            snapshot_hash="a" * 64,
            graph_fingerprint="b" * 64,
            routing_profile_fingerprint="c" * 64,
            max_route_pairs=100,
        )
        for city in ("city-one", "city-two")
    ]
    return Runtime(
        "d" * 64,
        Manifest(
            schema_version="waydex-product-runtime/1",
            snapshots=[],
            territories=[],
            profiles=profiles,
        ),
        {},
        {profile.profile_id: (profile, None) for profile in profiles},
        frozenset(),
    )


def test_plan_is_generic_single_category_and_version_bound():
    source = runtime()
    plan = standard_plan(source, AT, "TEST_ONLY-code-v1")
    assert len(plan["requests"]) == 4
    assert {r["request"]["city_id"] for r in plan["requests"]} == {"city-one", "city-two"}
    assert all(len(r["request"]["categories"]) == 1 for r in plan["requests"])
    assert plan == standard_plan(source, AT, "TEST_ONLY-code-v1")
    assert {r["key"] for r in plan["requests"]}.isdisjoint(
        r["key"] for r in standard_plan(source, AT, "TEST_ONLY-code-v2")["requests"]
    )
    assert not standard_plan(source, datetime(2027, 1, 1, tzinfo=UTC), "v1")["requests"]
    first = source.manifest.profiles[0]
    source.profiles[first.profile_id] = (first.model_copy(update={"traffic_modes": ["live"]}), None)
    assert len(standard_plan(source, AT, "v1")["requests"]) == 2


def job(status="succeeded"):
    return dict(
        job_id="TEST_ONLY-job",
        status=status,
        request_id="TEST_ONLY-trace",
        created_at=AT.isoformat(),
        started_at=AT.isoformat(),
        finished_at=AT.isoformat() if status == "succeeded" else None,
        analysis_id="TEST_ONLY-result" if status == "succeeded" else None,
        scenario_run_id=None,
        error=None,
    )


def test_serial_bound_and_restart_keep_session_and_job(tmp_path):
    submitted = []
    plan = standard_plan(runtime(), AT, "TEST_ONLY-version")

    def handler(request):
        if request.url.path.endswith("capabilities"):
            return httpx.Response(
                200, json={}, headers={"Set-Cookie": "a2_session=TEST_ONLY-token; Path=/"}
            )
        assert request.headers["cookie"] == "a2_session=TEST_ONLY-token"
        if request.method == "POST":
            submitted.append(request.headers["idempotency-key"])
            return httpx.Response(
                202, json=dict(job_id="TEST_ONLY-job", status="queued", request_id="trace")
            )
        return httpx.Response(200, json=job())

    state = tmp_path / "private-state.json"
    for _ in range(2):
        with httpx.Client(
            base_url="http://test/api/v1/", transport=httpx.MockTransport(handler)
        ) as client:
            result = execute(plan, client, state, max_jobs=1, max_seconds=10, poll_seconds=0)
        assert result["admitted_this_run"] == 1
        assert "session" not in result
    assert len(set(submitted)) == 2  # second run resumes, then advances by one request
    assert state.stat().st_mode & 0o777 == 0o600
    assert len(json.loads(state.read_text())["jobs"]) == 2
    with httpx.Client(
        base_url="http://different/api/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ValueError, match="CHECKPOINT_PLAN_OR_SERVER_MISMATCH"):
            execute(plan, client, state, max_jobs=1, max_seconds=10)


def test_lost_post_response_reuses_identity_and_stops_on_rejection(tmp_path):
    submitted = []
    state = tmp_path / "private-state.json"
    plan = standard_plan(runtime(), AT, "TEST_ONLY-version")

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={}, headers={"Set-Cookie": "a2_session=TEST_ONLY-token; Path=/"}
            )
        submitted.append(request.headers["idempotency-key"])
        if len(submitted) == 1:
            raise httpx.ReadTimeout("TEST_ONLY lost response")
        return httpx.Response(503, json={"error": "unavailable"})

    with httpx.Client(
        base_url="http://test/api/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(httpx.ReadTimeout):
            execute(plan, client, state, max_jobs=100, max_seconds=10)
    assert json.loads(state.read_text())["session"] == "TEST_ONLY-token"
    with httpx.Client(
        base_url="http://test/api/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            execute(plan, client, state, max_jobs=100, max_seconds=10)
    assert len(submitted) == 2 and submitted[0] == submitted[1]


def test_modified_plan_cannot_be_submitted(tmp_path):
    plan = standard_plan(runtime(), AT, "TEST_ONLY-version")
    plan["requests"][0]["request"]["city_id"] = "foreign-city"
    with httpx.Client(base_url="http://test/api/v1/") as client:
        with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
            execute(plan, client, tmp_path / "state.json", max_jobs=1, max_seconds=10)


def test_pending_checkpoint_resumes_without_resubmission(tmp_path, monkeypatch):
    from itertools import count

    ticks = count()
    monkeypatch.setattr("a2_api.precompute.time.monotonic", lambda: next(ticks))
    plan = standard_plan(runtime(), AT, "TEST_ONLY-version")
    plan["requests"] = plan["requests"][:1]
    status = "running"
    posts = []

    def handler(request):
        if request.url.path.endswith("capabilities"):
            return httpx.Response(
                200, json={}, headers={"Set-Cookie": "a2_session=TEST_ONLY-token; Path=/"}
            )
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(
                202, json=dict(job_id="TEST_ONLY-job", status="queued", request_id="trace")
            )
        return httpx.Response(200, json=job(status))

    state = tmp_path / "state.json"
    with httpx.Client(
        base_url="http://test/api/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        first = execute(plan, client, state, max_jobs=1, max_seconds=3, poll_seconds=0)
        assert first["jobs"][0]["status"] == "running"
        status = "succeeded"
        second = execute(plan, client, state, max_jobs=1, max_seconds=3, poll_seconds=0)
        assert second["jobs"][0]["status"] == "succeeded"
    assert len(posts) == 1
