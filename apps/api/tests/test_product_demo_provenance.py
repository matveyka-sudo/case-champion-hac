"""TEST_ONLY: snapshot provenance controls admission and ranking disclosure."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from a2_api.adapters import product, product_scenario_runtime
from a2_api.adapters.demo_map import attach_demo_result
from a2_api.adapters.demo_transport import DEMO_LIMITATIONS, DEMO_SOURCE
from a2_api.adapters.product_runtime import load_runtime, registry
from a2_api.core.errors import DomainError
from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import AnalysisPreset
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.results import AnalysisResult, EvidenceRef
from a2_contracts.v2.scenarios import ScenarioResult
from test_product_intercity_modes import configured_intercity as configured_intercity
from test_product_intercity_modes import request as intercity_request
from test_product_runtime import claim_for, configure, request_for, write_document
from test_product_runtime import transport_facts as transport_facts
from test_product_scenarios import make_case, with_claim


def synthetic_snapshot(snapshot, *, territories=(), qa_label=True, verified=True):
    payload = snapshot.model_dump(mode="json")
    payload["source_versions"][DEMO_SOURCE] = "TEST_ONLY-model-v1"
    if qa_label:
        payload["qa"]["checks"].append(DEMO_SOURCE)
    payload["evidence"].append(
        payload["evidence"][0]
        | {
            "evidence_id": "TEST_ONLY-model-proof",
            "source_id": DEMO_SOURCE,
            "source_version": "TEST_ONLY-model-v1",
            "verified": verified,
        }
    )
    payload["geographic_scope"].extend(territories)
    payload["territories"].extend(
        payload["territories"][0] | {"territory_id": identity} for identity in territories
    )
    return AcceptedData.model_validate(payload)


def configured_walk(tmp_path, monkeypatch, transport_facts, snapshot, demo_model):
    _, inputs, profile = transport_facts
    profile = profile.model_copy(
        update={"snapshot_hash": canonical_hash(snapshot.model_dump(mode="json"))}
    )
    manifest = configure(tmp_path, monkeypatch, (snapshot, inputs, profile))
    if demo_model is not None:
        manifest["profiles"][0]["demo_model"] = demo_model
    request = request_for(snapshot)
    preset = AnalysisPreset(preset_id="TEST_ONLY-preset", label="TEST_ONLY", request=request)
    manifest["presets"] = [preset.model_dump(mode="json")]
    ref = write_document(tmp_path / "TEST_ONLY-runtime.json", manifest)
    monkeypatch.setenv("A2_PRODUCT_RUNTIME_SHA256", ref["sha256"])
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + ref["sha256"])
    return request


def assert_model_result(result):
    assert isinstance(result, AnalysisResult)
    assert DEMO_LIMITATIONS <= set(result.limitations)
    assert result.scores and all(not score.ranking_eligible for score in result.scores)
    assert result.result_hash == canonical_hash(
        result.model_dump(mode="json", exclude={"result_hash"})
    )


@pytest.mark.parametrize("demo_model", [None, False])
@pytest.mark.parametrize("deployment", [None, "false"])
def test_verified_model_requires_explicit_deployment_opt_in(
    tmp_path, monkeypatch, transport_facts, demo_model, deployment
):
    if deployment is None:
        monkeypatch.delenv("A2_DEMO_TRANSPORT_ENABLED", raising=False)
    else:
        monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", deployment)
    snapshot = synthetic_snapshot(transport_facts[0])
    request = configured_walk(tmp_path, monkeypatch, transport_facts, snapshot, demo_model)

    assert not load_runtime().profiles
    selected = registry()
    assert not selected.capabilities().analysis_presets
    with pytest.raises(DomainError) as error:
        selected.resolve_snapshot(request)
    assert error.value.status_code == 503
    with pytest.raises(DomainError) as error:
        product.calculate(claim_for(snapshot, request))
    assert error.value.status_code == 503


@pytest.mark.parametrize("demo_model", [None, False])
def test_verified_model_disclosed_even_without_profile_flag(
    tmp_path, monkeypatch, transport_facts, demo_model
):
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    snapshot = synthetic_snapshot(transport_facts[0])
    request = configured_walk(tmp_path, monkeypatch, transport_facts, snapshot, demo_model)

    assert registry().resolve_snapshot(request) == snapshot
    assert_model_result(product.calculate(claim_for(snapshot, request)))


@pytest.mark.parametrize("demo_model", [None, False])
def test_verified_model_keeps_exact_preset_admission_without_profile_flag(
    tmp_path, monkeypatch, transport_facts, demo_model
):
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    snapshot = synthetic_snapshot(transport_facts[0])
    request = configured_walk(tmp_path, monkeypatch, transport_facts, snapshot, demo_model)
    changed = request.model_copy(update={"departure_at": request.departure_at.replace(hour=1)})

    for select in (registry().resolve_snapshot, lambda row: load_runtime().select(row, snapshot)):
        with pytest.raises(DomainError, match="REQUEST_OUTSIDE_SUPPORTED_PRESETS") as error:
            select(changed)
        assert error.value.status_code == 422


@pytest.mark.parametrize("invalid", ["qa", "evidence"])
def test_unverified_reserved_source_cannot_be_loaded_as_real(
    tmp_path, monkeypatch, transport_facts, invalid
):
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    snapshot = synthetic_snapshot(
        transport_facts[0], qa_label=invalid != "qa", verified=invalid != "evidence"
    )
    request = configured_walk(tmp_path, monkeypatch, transport_facts, snapshot, False)

    assert not load_runtime().profiles
    with pytest.raises(DomainError) as error:
        product.calculate(claim_for(snapshot, request))
    assert error.value.status_code == 503


def test_qa_strings_alone_cannot_change_real_profile_admission(
    tmp_path, monkeypatch, transport_facts
):
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "false")
    payload = transport_facts[0].model_dump(mode="json")
    payload["qa"]["checks"].extend(sorted(DEMO_LIMITATIONS))
    snapshot = AcceptedData.model_validate(payload)
    request = configured_walk(tmp_path, monkeypatch, transport_facts, snapshot, None)

    assert registry().resolve_snapshot(request) == snapshot
    result = product.calculate(claim_for(snapshot, request))
    assert isinstance(result, AnalysisResult)
    assert not DEMO_LIMITATIONS.intersection(result.limitations)


@pytest.mark.parametrize(
    ("modes", "missing_timetable", "demo_model"),
    [
        (["private_car"], False, True),
        (["private_car"], False, False),
        (["rail"], True, True),
        (["rail", "private_car"], True, False),
        (["rail"], False, True),
    ],
)
def test_intercity_model_disclosure_preserves_car_and_unknown_timetable_results(
    configured_intercity, monkeypatch, modes, missing_timetable, demo_model
):
    module, original, runtime = configured_intercity
    requested = intercity_request(module, modes)
    key = next(iter(runtime.profiles))
    profile, inputs = runtime.profiles[key]
    if missing_timetable:
        timetable = replace(
            inputs.timetable,
            scope=replace(inputs.timetable.scope, schedule_complete=False, calendar_complete=False),
            runs=(),
            footpaths=(),
            transfers=(),
        )
        inputs = inputs.model_copy(
            update={"timetable": timetable, "transport_modes": (), "physical_services": ()}
        )
        runtime.profiles[key] = (profile, inputs)
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "false")
    ordinary = product.calculate(claim_for(original, requested))
    assert not DEMO_LIMITATIONS.intersection(ordinary.limitations)

    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    snapshot = synthetic_snapshot(original, territories=("city-A", "city-B"))
    runtime.snapshots[snapshot.snapshot_id] = snapshot
    profile = profile.model_copy(
        update={
            "demo_model": demo_model,
            "snapshot_hash": canonical_hash(snapshot.model_dump(mode="json")),
        }
    )
    runtime.profiles[key] = (profile, inputs)
    runtime.manifest.presets.append(
        AnalysisPreset(preset_id="TEST_ONLY-intercity-preset", label="TEST_ONLY", request=requested)
    )
    result = product.calculate(claim_for(snapshot, requested))

    assert_model_result(result)
    assert result.status == ordinary.status
    assert result.metrics == ordinary.metrics
    assert result.journeys == ordinary.journeys
    assert result.scores == [
        score.model_copy(update={"ranking_eligible": False}) for score in ordinary.scores
    ]
    if "private_car" in modes:
        original_car = next(
            score for score in ordinary.scores if score.score_id == "private-car-index"
        )
        assert original_car.ranking_eligible and original_car.value == 50
    if missing_timetable:
        public = [score for score in result.scores if score.subject.mode != "private_car"]
        assert public and all(score.value is None and score.status == "unknown" for score in public)


@pytest.fixture
def saved_scenario(monkeypatch, transport_facts):
    def prepare(*, synthetic, baseline_labels=False, plan_labels=False):
        context, facts, document, baseline, frozen = make_case()
        payload = transport_facts[0].model_dump(mode="json")
        payload["snapshot_id"] = context.snapshot_id
        payload["provenance"].update(
            graph_version=context.versions.graph_version,
            calendar_version=context.versions.calendar_version,
            population_version=context.versions.population_version,
        )
        city = context.normalized_request.city_id
        payload["geographic_scope"].append(city)
        payload["territories"].append(payload["territories"][0] | {"territory_id": city})
        ordinary = AcceptedData.model_validate(payload)
        snapshot = synthetic_snapshot(ordinary) if synthetic else ordinary
        monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
        if baseline_labels:
            model_snapshot = synthetic_snapshot(ordinary)
            evidence = [
                EvidenceRef(
                    evidence_id=row.evidence_id,
                    source_id=row.source_id,
                    url=row.url,
                    retrieved_at=row.retrieved_at,
                    observed_at=None,
                    section="accepted-snapshot",
                )
                for row in model_snapshot.evidence
            ]
            baseline = baseline.model_copy(
                update={"evidence_refs": [*baseline.evidence_refs, *evidence]}
            )
            baseline = attach_demo_result(baseline, model_snapshot)
        snapshot_hash = canonical_hash(snapshot.model_dump(mode="json"))
        context = context.model_copy(update={"snapshot_hash": snapshot_hash})
        document = document.model_copy(update={"snapshot_hash": snapshot_hash})
        if plan_labels:
            plan = document.plans[0].model_copy(
                update={"assumptions": tuple(sorted(DEMO_LIMITATIONS))}
            )
            document = document.model_copy(update={"plans": (plan,)})
        claim, *_ = with_claim((context, facts, document, baseline, frozen))
        profile = SimpleNamespace(executor="accepted_inputs", profile_id=document.profile_id)
        runtime = SimpleNamespace(
            digest=context.versions.code_version,
            snapshots={snapshot.snapshot_id: snapshot},
            select=lambda request, accepted: (profile, facts),
            scenario_documents={document.profile_id: (document,)},
        )

        async def saved_baseline(_):
            return baseline

        monkeypatch.setattr(product, "load_runtime", lambda **kwargs: runtime)
        monkeypatch.setattr(product_scenario_runtime, "_saved_baseline", saved_baseline)
        return claim, baseline, runtime

    return prepare


@pytest.mark.parametrize("synthetic", [False, True])
def test_saved_scenario_uses_verified_baseline_provenance_without_plan_labels(
    saved_scenario, synthetic
):
    claim, baseline, _ = saved_scenario(synthetic=synthetic, baseline_labels=synthetic)
    immutable = baseline.model_dump_json()
    result = product.calculate(claim)

    assert isinstance(result, ScenarioResult)
    assert result.before.metrics == baseline.metrics
    assert result.before.scores == baseline.scores
    assert result.baseline_result_hash == baseline.result_hash
    assert baseline.model_dump_json() == immutable
    assert result.result_hash == canonical_hash(
        result.model_dump(mode="json", exclude={"result_hash"})
    )
    if synthetic:
        assert DEMO_LIMITATIONS <= set(result.limitations)
        assert not any(score.ranking_eligible for score in result.after.scores)
    else:
        assert not DEMO_LIMITATIONS.intersection(result.limitations)
        assert any(score.ranking_eligible for score in result.after.scores)


@pytest.mark.parametrize("synthetic", [False, True])
def test_saved_scenario_rejects_baseline_with_wrong_model_provenance(saved_scenario, synthetic):
    claim, _, _ = saved_scenario(
        synthetic=synthetic, baseline_labels=not synthetic, plan_labels=not synthetic
    )

    with pytest.raises(DomainError, match="SCENARIO_SAVED_BASELINE_PROVENANCE_DIFFERS") as error:
        product.calculate(claim)
    assert error.value.status_code == 409


def test_saved_scenario_rejects_unbound_snapshot_even_with_demo_labels(saved_scenario):
    claim, _, runtime = saved_scenario(synthetic=True, baseline_labels=True, plan_labels=True)
    snapshot = runtime.snapshots[claim.context.snapshot_id]
    runtime.snapshots[snapshot.snapshot_id] = snapshot.model_copy(
        update={"qa": snapshot.qa.model_copy(update={"checks": [*snapshot.qa.checks, "changed"]})}
    )

    with pytest.raises(DomainError, match="Queued product inputs or runtime changed") as error:
        product.calculate(claim)
    assert error.value.status_code == 409
