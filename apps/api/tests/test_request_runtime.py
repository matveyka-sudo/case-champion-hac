"""Synthetic scope isolation; selected sources must still be revalidated on every call."""

import json

import pytest
from a2_api.adapters import product, product_runtime
from a2_api.core.errors import DomainError
from test_product_runtime import (
    claim_for,
    configure,
    request_for,
    write_document,
)
from test_product_runtime import (
    transport_facts as transport_facts,
)


def publish(tmp_path, monkeypatch, manifest):
    ref = write_document(tmp_path / "TEST_ONLY-runtime.json", manifest)
    monkeypatch.setenv("A2_PRODUCT_RUNTIME_SHA256", ref["sha256"])
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + ref["sha256"])


@pytest.mark.asyncio
async def test_request_does_not_open_other_city_sources(tmp_path, monkeypatch, transport_facts):
    manifest = configure(tmp_path, monkeypatch, transport_facts)
    unrelated = dict(
        manifest["profiles"][0],
        profile_id="TEST_ONLY-other",
        city_id="city-other",
        snapshot_id="snapshot-other",
    )
    manifest["profiles"].append(unrelated)
    manifest["snapshots"].append(
        dict(
            manifest["snapshots"][0],
            snapshot_id="snapshot-other",
            path="TEST_ONLY-not-mounted.json",
        )
    )
    publish(tmp_path, monkeypatch, manifest)
    # Full startup still rejects an incomplete release; request execution is scoped.
    with pytest.raises(DomainError, match="PRODUCT_RUNTIME_UNAVAILABLE"):
        product_runtime.load_runtime()
    snapshot = transport_facts[0]
    request = request_for(snapshot)
    assert await product.preflight(request, snapshot) is None
    result = product.calculate(claim_for(snapshot, request))
    assert result.request == request
    assert all(score.value is None for score in result.scores)


@pytest.mark.asyncio
async def test_selected_input_change_is_not_hidden_by_repeated_admission(
    tmp_path, monkeypatch, transport_facts
):
    configure(tmp_path, monkeypatch, transport_facts)
    snapshot = transport_facts[0]
    request = request_for(snapshot)
    assert await product.preflight(request, snapshot) is None
    (tmp_path / "TEST_ONLY-inputs.json").write_text("{}")
    with pytest.raises(DomainError, match="RUNTIME_INPUTS_UNAVAILABLE"):
        await product.preflight(request, snapshot)
    with pytest.raises(DomainError, match="RUNTIME_INPUTS_UNAVAILABLE"):
        product.calculate(claim_for(snapshot, request))


@pytest.mark.asyncio
async def test_all_matching_profiles_remain_ambiguous(tmp_path, monkeypatch, transport_facts):
    manifest = configure(tmp_path, monkeypatch, transport_facts)
    manifest["profiles"].append(dict(manifest["profiles"][0], profile_id="TEST_ONLY-overlap"))
    publish(tmp_path, monkeypatch, manifest)
    snapshot = transport_facts[0]
    with pytest.raises(DomainError, match="RUNTIME_PROFILE_SCOPE_AMBIGUOUS"):
        await product.preflight(request_for(snapshot), snapshot)


@pytest.mark.asyncio
async def test_selected_snapshot_bytes_are_rechecked(tmp_path, monkeypatch, transport_facts):
    configure(tmp_path, monkeypatch, transport_facts)
    snapshot = transport_facts[0]
    request = request_for(snapshot)
    assert await product.preflight(request, snapshot) is None
    path = tmp_path / "TEST_ONLY-snapshot.json"
    source = json.loads(path.read_text())
    source["snapshot_id"] = "TEST_ONLY-changed"
    path.write_text(json.dumps(source))
    with pytest.raises(DomainError, match="PRODUCT_RUNTIME_UNAVAILABLE"):
        await product.preflight(request, snapshot)


@pytest.mark.parametrize("part", ["snapshots", "profiles"])
def test_duplicate_manifest_identities_are_rejected_even_when_scoped(
    tmp_path, monkeypatch, transport_facts, part
):
    manifest = configure(tmp_path, monkeypatch, transport_facts)
    manifest[part].append(dict(manifest[part][0]))
    publish(tmp_path, monkeypatch, manifest)
    snapshot = transport_facts[0]
    with pytest.raises(DomainError, match="PRODUCT_RUNTIME_UNAVAILABLE"):
        product_runtime.load_runtime(
            request=request_for(snapshot), snapshot_id=snapshot.snapshot_id
        )


def test_scope_requires_consistent_request_and_snapshot(tmp_path, monkeypatch, transport_facts):
    configure(tmp_path, monkeypatch, transport_facts)
    snapshot = transport_facts[0]
    request = request_for(snapshot)
    with pytest.raises(ValueError, match="REQUIRED_TOGETHER"):
        product_runtime.load_runtime(request=request)
    with pytest.raises(DomainError, match="RUNTIME_REQUEST_SNAPSHOT_MISMATCH"):
        product_runtime.load_runtime(request=request, snapshot_id="other")


def test_scoped_and_full_calculation_results_are_identical(tmp_path, monkeypatch, transport_facts):
    configure(tmp_path, monkeypatch, transport_facts)
    snapshot = transport_facts[0]
    claim = claim_for(snapshot, request_for(snapshot))
    scoped = product.calculate(claim)
    monkeypatch.setattr(product, "load_runtime", lambda **kwargs: product_runtime.load_runtime())
    full = product.calculate(claim)
    assert scoped.model_dump(mode="json") == full.model_dump(mode="json")
