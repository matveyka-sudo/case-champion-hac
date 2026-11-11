"""Exact public presets resolve scope without relying on overlapping geography."""

from dataclasses import replace

import pytest
from a2_api.adapters.product_runtime import load_runtime
from a2_api.core.errors import DomainError
from test_product_runtime import configure, request_for
from test_product_runtime import transport_facts as transport_facts


def test_presets_are_published_only_when_registered(tmp_path, monkeypatch, transport_facts):
    from a2_contracts.v2.api import AnalysisPreset

    snapshot, _, profile = transport_facts
    request = request_for(snapshot)
    preset = AnalysisPreset(preset_id="TEST_ONLY-preset", label="Test preset", request=request)
    manifest = configure(tmp_path, monkeypatch, transport_facts)
    from test_product_runtime import write_document

    manifest["presets"] = [preset.model_dump(mode="json")]
    ref = write_document(tmp_path / "TEST_ONLY-runtime.json", manifest)
    monkeypatch.setenv("A2_PRODUCT_RUNTIME_SHA256", ref["sha256"])
    runtime = load_runtime()
    assert runtime.manifest.presets == [preset]
    from a2_api.adapters.product_capabilities import analysis_presets

    assert analysis_presets(runtime) == [preset]
    assert analysis_presets(replace(runtime, profiles={})) == []


def test_presets_omit_stale_methodology_and_unloaded_operations(
    tmp_path, monkeypatch, transport_facts
):
    from a2_contracts.v2.api import AnalysisPreset
    from test_product_runtime import write_document

    request = request_for(transport_facts[0])
    assert request.methodology is not None
    preset = AnalysisPreset(
        preset_id="TEST_ONLY-preset",
        label="Test preset",
        request=request,
        scenario_operations=["additional_departure"],
    )
    stale = preset.model_copy(
        update={
            "preset_id": "TEST_ONLY-stale",
            "request": request.model_copy(
                update={
                    "methodology": request.methodology.model_copy(update={"config_hash": "stale"}),
                }
            ),
        }
    )
    manifest = configure(tmp_path, monkeypatch, transport_facts)
    manifest["presets"] = [p.model_dump(mode="json") for p in (preset, stale)]
    ref = write_document(tmp_path / "TEST_ONLY-runtime.json", manifest)
    monkeypatch.setenv("A2_PRODUCT_RUNTIME_SHA256", ref["sha256"])
    from a2_api.adapters.product_capabilities import analysis_presets

    published = analysis_presets(load_runtime())
    assert len(published) == 1
    assert published[0].scenario_operations == []


def test_registered_demo_preset_with_failed_inputs_is_retryable(
    tmp_path, monkeypatch, transport_facts
):
    from a2_api.adapters.product_runtime import registry
    from a2_contracts.v2.api import AnalysisPreset
    from test_product_runtime import write_document

    request = request_for(transport_facts[0])
    manifest = configure(tmp_path, monkeypatch, transport_facts)
    manifest["profiles"][0]["demo_model"] = True
    manifest["profiles"][0]["inputs"]["path"] = "missing-inputs.json"
    manifest["presets"] = [
        AnalysisPreset(
            preset_id="TEST_ONLY-preset",
            label="Test preset",
            request=request,
        ).model_dump(mode="json")
    ]
    ref = write_document(tmp_path / "TEST_ONLY-runtime.json", manifest)
    monkeypatch.setenv("A2_PRODUCT_RUNTIME_SHA256", ref["sha256"])
    selected = registry()
    with pytest.raises(DomainError) as error:
        selected.resolve_snapshot(request)
    assert error.value.status_code == 503
    assert error.value.retryable


def test_runtime_registry_selects_supported_snapshot_and_rejects_old_date(
    tmp_path, monkeypatch, transport_facts
):
    configure(tmp_path, monkeypatch, transport_facts)
    from a2_api.adapters.product_runtime import registry

    selected = registry()
    request = selected.normalize(request_for(transport_facts[0], snapshot_id=None))
    assert selected.resolve_snapshot(request).snapshot_id == transport_facts[0].snapshot_id
    with pytest.raises(DomainError) as error:
        selected.resolve_snapshot(
            request.model_copy(update={"departure_at": request.departure_at.replace(year=2020)})
        )
    assert error.value.status_code == 422
    assert not error.value.retryable
    assert error.value.message == "REQUEST_OUTSIDE_SUPPORTED_PRESETS"


def test_scenario_request_does_not_load_unrelated_snapshots(tmp_path, monkeypatch):
    import json

    from a2_api.adapters.product_runtime import load_runtime
    from test_product_runtime import write_document
    from test_product_scenario_runtime import configure_cargo

    snapshot, request = configure_cargo(tmp_path, monkeypatch)
    path = tmp_path / "TEST_ONLY-runtime.json"
    manifest = json.loads(path.read_text())
    manifest["snapshots"].append(
        {**manifest["snapshots"][0], "snapshot_id": "unrelated", "path": "not-mounted.json"}
    )
    ref = write_document(path, manifest)
    monkeypatch.setenv("A2_PRODUCT_RUNTIME_SHA256", ref["sha256"])
    runtime = load_runtime(request=request, snapshot_id=snapshot.snapshot_id)
    assert set(runtime.snapshots) == {snapshot.snapshot_id}
    assert len(next(iter(runtime.scenario_documents.values()))) == 1
