"""HTTP and worker regressions for exact demo preset group admission."""

import httpx
import pytest
from a2_api.api.application import create_app
from a2_api.api.bootstrap import build_analysis_service
from a2_api.core.config import RuntimeSettings
from a2_api.core.errors import DomainError
from a2_api.db.repositories.jobs import JobRepository
from a2_api.services.analysis_groups import AnalysisGroupService
from a2_api.workers.runner import Worker
from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import AnalysisPreset
from a2_contracts.v2.dataset import AcceptedData
from test_product_runtime import configure, request_for, write_document
from test_product_runtime import transport_facts as transport_facts


@pytest.fixture
def demo_group(tmp_path, monkeypatch, transport_facts):
    source = "DEMO_SYNTHETIC_TRANSPORT_MODEL"
    version = "TEST_ONLY-model"
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    snapshot, facts, profile = transport_facts
    payload = snapshot.model_dump(mode="json")
    payload["source_versions"][source] = version
    payload["qa"]["checks"].append(source)
    payload["evidence"].append(
        dict(
            payload["evidence"][0],
            evidence_id="TEST_ONLY-demo",
            source_id=source,
            source_version=version,
            verified=True,
        )
    )
    snapshot = AcceptedData.model_validate(payload)
    request = request_for(
        snapshot,
        categories=["adult_polyclinic", "school"],
        priorities={"adult_polyclinic": "medium", "school": "medium"},
    )
    profile = profile.model_copy(
        update={
            "snapshot_hash": canonical_hash(snapshot.model_dump(mode="json")),
            "demo_model": True,
            "categories": request.categories,
        }
    )
    manifest = configure(tmp_path, monkeypatch, (snapshot, facts, profile))

    def service(pool, *, child_presets=False, overlapping_profile=False):
        presets = [request]
        if child_presets:
            presets.extend(
                request_for(snapshot, categories=[category], priorities={category: priority})
                for category, priority in request.priorities.items()
            )
        manifest["presets"] = [
            AnalysisPreset(
                preset_id=f"TEST_ONLY-{index}", label="Test preset", request=preset
            ).model_dump(mode="json")
            for index, preset in enumerate(presets)
        ]
        if overlapping_profile:
            manifest["profiles"].append(
                dict(manifest["profiles"][0], profile_id="TEST_ONLY-overlap", categories=["school"])
            )
        ref = write_document(tmp_path / "TEST_ONLY-runtime.json", manifest)
        monkeypatch.setenv("A2_PRODUCT_RUNTIME_SHA256", ref["sha256"])
        monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + ref["sha256"])
        settings = RuntimeSettings(
            registry_hook="a2_api.adapters.product:registry",
            preflight_hook="a2_api.adapters.product:preflight",
            calculation_handler="a2_api.adapters.product:calculate",
        )
        return build_analysis_service(settings, JobRepository(pool))

    return request, service


def product_worker(analyses, groups):
    return Worker(
        analyses.repository,
        handler="a2_api.adapters.product:calculate",
        code_version=analyses.code_version,
        timeout_seconds=15,
        dispatch_groups=groups.dispatch_once,
    )


@pytest.mark.asyncio
async def test_published_parent_with_unsupported_children_is_rejected_before_queueing(
    database, demo_group
):
    pool, _, _ = database
    _, service = demo_group
    analyses = service(pool)
    groups = AnalysisGroupService(analyses)
    transport = httpx.ASGITransport(app=create_app(analyses))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/capabilities")
        (preset,) = response.json()["analysis_presets"]
        headers = {"Idempotency-Key": "TEST_ONLY-group"}
        response = await client.post(
            "/api/v1/analysis-groups", json=preset["request"], headers=headers
        )
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["message"] == "ANALYSIS_GROUP_CATEGORY_REQUEST_UNSUPPORTED"
        assert error["code"] == "invalid_request" and not error["retryable"]
        assert error["field_errors"][0]["location"] == ["categories", "0"]
        assert "/api/v1/analyses" in error["field_errors"][0]["message"]
        assert not await groups.dispatch_once()
        assert await pool.fetchval("SELECT count(*) FROM analysis_groups") == 0
        assert await pool.fetchval("SELECT count(*) FROM jobs") == 0

        for changed in (
            {"categories": ["school"], "priorities": {"school": "medium"}},
            {"priorities": {"school": "medium", "adult_polyclinic": "high"}},
        ):
            for endpoint in ("analysis-groups", "analyses"):
                response = await client.post(
                    f"/api/v1/{endpoint}", json=preset["request"] | changed, headers=headers
                )
                assert response.status_code == 422, response.text
                assert response.json()["error"]["message"] == "REQUEST_OUTSIDE_SUPPORTED_PRESETS"

        response = await client.post("/api/v1/analyses", json=preset["request"], headers=headers)
        assert response.status_code == 202, response.text
        assert await pool.fetchval("SELECT count(*) FROM jobs") == 1
        assert await product_worker(analyses, groups).run_once()
        job = (await client.get(f"/api/v1/jobs/{response.json()['job_id']}")).json()
        assert job["status"] == "succeeded", job
        saved = (await client.get(f"/api/v1/analyses/{job['analysis_id']}")).json()
        assert saved["request"]["categories"] == preset["request"]["categories"]


@pytest.mark.asyncio
async def test_declared_child_with_ambiguous_profiles_does_not_make_group_admissible(
    database, demo_group
):
    pool, _, _ = database
    _, service = demo_group
    analyses = service(pool, child_presets=True, overlapping_profile=True)
    transport = httpx.ASGITransport(app=create_app(analyses))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/capabilities")
        published = {p["preset_id"]: p["request"] for p in response.json()["analysis_presets"]}
        assert set(published) == {"TEST_ONLY-0", "TEST_ONLY-1"}
        response = await client.post(
            "/api/v1/analysis-groups",
            json=published["TEST_ONLY-0"],
            headers={"Idempotency-Key": "TEST_ONLY-group"},
        )
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["message"] == "ANALYSIS_GROUP_CATEGORY_REQUEST_UNSUPPORTED"
        assert error["field_errors"][0]["location"] == ["categories", "1"]
    assert await pool.fetchval("SELECT count(*) FROM analysis_groups") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("preset_id", ["TEST_ONLY-0", "TEST_ONLY-1"])
async def test_groups_with_individually_published_children_use_normal_worker_and_scope(
    database, demo_group, preset_id
):
    pool, _, _ = database
    _, service = demo_group
    analyses = service(pool, child_presets=True)
    groups = AnalysisGroupService(analyses)
    worker = product_worker(analyses, groups)
    transport = httpx.ASGITransport(app=create_app(analyses))
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as owner,
        httpx.AsyncClient(transport=transport, base_url="http://test") as other,
    ):
        response = await owner.get("/api/v1/capabilities")
        preset = next(p for p in response.json()["analysis_presets"] if p["preset_id"] == preset_id)
        headers = {"Idempotency-Key": "TEST_ONLY-group"}
        request = preset["request"]
        response = await owner.post("/api/v1/analysis-groups", json=request, headers=headers)
        assert response.status_code == 202, response.text
        group_id = response.json()["group_id"]
        path = f"/api/v1/analysis-groups/{group_id}"
        duplicate = await owner.post("/api/v1/analysis-groups", json=request, headers=headers)
        assert duplicate.status_code == 202 and duplicate.json()["group_id"] == group_id
        changed = request | {"priorities": dict(request["priorities"], adult_polyclinic="high")}
        conflict = await owner.post("/api/v1/analysis-groups", json=changed, headers=headers)
        assert conflict.status_code == 409
        assert (await other.get(path)).status_code == 404
        assert await pool.fetchval("SELECT count(*) FROM jobs") == 0

        for _ in request["categories"]:
            assert await worker.run_once()
        assert not await worker.run_once()
        group = (await owner.get(path)).json()
        assert group["status"] == "succeeded", group
        assert await pool.fetchval("SELECT count(*) FROM jobs") == len(request["categories"])
        for item in group["items"]:
            result_path = f"/api/v1/analyses/{item['analysis_id']}"
            result = await owner.get(result_path)
            assert result.status_code == 200, result.text
            assert result.json()["request"]["categories"] == [item["category"]]
            assert (await other.get(result_path)).status_code == 404


@pytest.mark.asyncio
async def test_historical_group_with_unsupported_children_is_rejected_before_retry(
    database, demo_group
):
    pool, _, _ = database
    request, service = demo_group
    groups = AnalysisGroupService(service(pool))
    plan = groups.plan(request)
    scope = "TEST_ONLY-owner"
    group_id = await groups.repository.create(plan, "old-key", "old-digest", scope)
    await groups.cancel(group_id, scope, "cancel")

    with pytest.raises(DomainError, match="ANALYSIS_GROUP_CATEGORY_REQUEST_UNSUPPORTED"):
        await groups.retry(group_id, "retry", scope, "trace")

    assert (await groups.get(group_id, scope, "poll")).status == "cancelled"
    assert await pool.fetchval("SELECT retry_keys FROM analysis_groups") == []
    assert not await groups.dispatch_once()
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0
