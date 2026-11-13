"""Offline Alice hook integration. All keys and provider replies are synthetic."""

import asyncio
import json
import logging
from decimal import Decimal
from pathlib import Path

import pytest
from a2_api.api.bootstrap import build_app
from a2_api.core.config import RuntimeSettings
from a2_api.db.connection import open_pool
from a2_api.services.analyses import AnalysisService
from a2_api.workers.runner import Worker
from a2_assistant import AccessScope, FlashAssistant
from a2_assistant.transport import ModelFailure, ModelReply
from a2_contracts.v2.results import AnalysisResult
from fastapi.testclient import TestClient
from test_v2_integration import request_body

pytestmark = pytest.mark.usefixtures("no_local_dotenv")


class SyntheticDurableBudget:
    """Test double only; production needs an atomic shared ledger."""

    durable = True
    currency = "RUB"
    limit_amount = Decimal("1")

    def __init__(self):
        self.reservations = []

    async def reserve(self, **kwargs):
        self.reservations.append(kwargs)
        return True

    async def record_usage(self, **kwargs):
        pass


budget = SyntheticDurableBudget()


def synthetic_budget_hook(_service):
    return budget


class FakeSDKTransport:
    live = True
    instances = []
    failure = None

    def __init__(self, config):
        self.config = config
        self.calls = 0
        self.tools_seen = []
        self.closed = False
        self.instances.append(self)

    async def complete(self, messages, tools, **kwargs):
        self.calls += 1
        self.tools_seen = tools
        if self.failure is not None:
            raise self.failure
        facts = json.loads(messages[1]["content"])["saved_facts"]
        metric = facts["metrics"][0]
        return ModelReply(
            content=json.dumps(
                {
                    "findings": [
                        {
                            "metric_id": metric["metric_id"],
                            "explanation_code": "saved_value",
                        }
                    ],
                    "template_ids": [],
                }
            ),
            finish_reason="stop",
            input_tokens=16,
            output_tokens=2,
        )

    async def aclose(self):
        self.closed = True


def _alice_env(monkeypatch, *, enabled=True, key=True, budget_hook=True):
    # Explicit values override any developer-local .env read by AliceSettings.
    # These tests cover the previous assistant, kept behind CONSULTANT_ENABLED=false.
    monkeypatch.setenv("CONSULTANT_ENABLED", "false")
    monkeypatch.setenv("LLM_ENABLED", str(enabled).lower())
    monkeypatch.setenv("YANDEX_API_KEY", "SYNTHETIC-SECRET-DO-NOT-LOG" if key else "")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "synthetic-folder")
    monkeypatch.setenv("YANDEX_MODEL", "aliceai-llm-flash")
    monkeypatch.setenv("YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1")
    monkeypatch.setenv("A2_ALICE_BILLING_CURRENCY_CONFIRMED", "true")
    monkeypatch.setenv(
        "A2_ASSISTANT_BUDGET_HOOK",
        "test_alice_runtime:synthetic_budget_hook" if budget_hook else "",
    )
    monkeypatch.setenv("A2_ASSISTANT_TOOLS_ENABLED", "false")


@pytest.mark.parametrize(
    "enabled,key,budget_hook", [(False, True, True), (True, False, True), (True, True, False)]
)
def test_factory_fails_closed_without_all_live_gates(monkeypatch, enabled, key, budget_hook):
    from a2_api.assistant.alice import build_assistant

    _alice_env(monkeypatch, enabled=enabled, key=key, budget_hook=budget_hook)
    assistant = build_assistant(AnalysisService())
    assert isinstance(assistant, FlashAssistant)
    assert assistant.transport is None


def test_factory_requires_billing_currency_confirmation_before_live_transport(monkeypatch):
    from a2_api.assistant import alice

    _alice_env(monkeypatch)
    monkeypatch.setenv("A2_ALICE_BILLING_CURRENCY_CONFIRMED", "false")
    monkeypatch.setattr(alice, "SDKTransport", FakeSDKTransport)
    FakeSDKTransport.instances = []
    assert alice.build_assistant(AnalysisService()).transport is None
    assert FakeSDKTransport.instances == []


def test_factory_disables_retries_and_extra_calls_without_tools(monkeypatch):
    from a2_api.assistant import alice

    _alice_env(monkeypatch)
    monkeypatch.setattr(alice, "SDKTransport", FakeSDKTransport)
    agent = alice.build_assistant(AnalysisService())
    assert agent.config.tools_enabled is False
    assert agent.config.max_model_calls == 1
    assert agent.config.max_retries == 0


@pytest.mark.parametrize("missing", ["key", "budget_hook"])
def test_missing_live_gate_cannot_be_restored_from_local_dotenv(monkeypatch, tmp_path, missing):
    from a2_api.assistant import alice

    (tmp_path / ".env").write_text(
        "YANDEX_API_KEY=SYNTHETIC-DOTENV-KEY\n"
        "A2_ASSISTANT_BUDGET_HOOK=test_alice_runtime:synthetic_budget_hook\n"
        "A2_ASSISTANT_TOOLS_ENABLED=true\n"
    )
    monkeypatch.chdir(tmp_path)
    for name in (
        "YANDEX_API_KEY",
        "A2_ASSISTANT_BUDGET_HOOK",
        "A2_ASSISTANT_TOOLS_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    # Prove the fixture is testing a real pydantic-settings dotenv fallback.
    dotenv_settings = alice.AliceSettings()
    assert dotenv_settings.yandex_api_key.get_secret_value() == "SYNTHETIC-DOTENV-KEY"
    assert dotenv_settings.assistant_budget_hook == ("test_alice_runtime:synthetic_budget_hook")
    assert dotenv_settings.assistant_tools_enabled is True

    _alice_env(monkeypatch, key=missing != "key", budget_hook=missing != "budget_hook")
    settings = alice.AliceSettings()
    if missing == "key":
        assert settings.yandex_api_key is not None
        assert settings.yandex_api_key.get_secret_value() == ""
    else:
        assert settings.assistant_budget_hook == ""
    assert settings.assistant_tools_enabled is False
    monkeypatch.setattr(alice, "SDKTransport", FakeSDKTransport)
    FakeSDKTransport.instances = []
    assert alice.build_assistant(AnalysisService()).transport is None
    assert FakeSDKTransport.instances == []


def test_factory_rejects_invalid_configuration_without_leaking_key(monkeypatch):
    from a2_api.assistant.alice import build_assistant

    _alice_env(monkeypatch)
    monkeypatch.setenv("YANDEX_BASE_URL", "https://other.example.invalid")
    with pytest.raises(ValueError, match="Alice AI configuration") as error:
        build_assistant(AnalysisService())
    assert "SYNTHETIC-SECRET" not in str(error.value)


def test_factory_rejects_non_durable_budget_before_creating_sdk(monkeypatch):
    from a2_api.assistant import alice

    _alice_env(monkeypatch)
    monkeypatch.setattr(alice, "SDKTransport", FakeSDKTransport)
    FakeSDKTransport.instances = []
    monkeypatch.setattr(budget, "durable", False)
    with pytest.raises(ValueError, match="valid durable budget"):
        alice.build_assistant(AnalysisService())
    assert FakeSDKTransport.instances == []


def test_runtime_hook_explains_saved_analysis_and_closes_transport(
    monkeypatch, database_sync_config, caplog
):
    from a2_api.assistant import alice

    _alice_env(monkeypatch)
    monkeypatch.setattr(alice, "SDKTransport", FakeSDKTransport)
    FakeSDKTransport.instances = []
    FakeSDKTransport.failure = None
    budget.reservations.clear()
    url, schema = database_sync_config
    config = RuntimeSettings(
        database_url=url,
        database_schema=schema,
        registry_hook="v2_handlers:registry",
        code_version="demo-code",
        calculation_handler="v2_handlers:success",
        assistant_hook="a2_api.assistant.alice:build_assistant",
    )
    with caplog.at_level(logging.INFO):
        with TestClient(build_app(config)) as owner, TestClient(build_app(config)) as stranger:
            submitted = owner.post(
                "/api/v1/analyses",
                json=request_body(),
                headers={"Idempotency-Key": "alice-offline"},
            )
            assert submitted.status_code == 202

            async def calculate():
                pool = await open_pool(config)
                try:
                    from a2_api.db.repositories.jobs import JobRepository

                    await Worker(
                        JobRepository(pool),
                        handler="v2_handlers:success",
                        code_version="demo-code",
                        timeout_seconds=5,
                    ).run_once()
                finally:
                    await pool.close()

            asyncio.run(calculate())
            job = owner.get(f"/api/v1/jobs/{submitted.json()['job_id']}").json()
            assert job["status"] == "succeeded"
            endpoint = f"/api/v1/analyses/{job['analysis_id']}/explanation"
            assert stranger.post(endpoint).status_code == 404
            response = owner.post(endpoint, json={"question": "Что известно о времени?"})
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "generated"
            assert response.json()["model_id"] == "gpt://synthetic-folder/aliceai-llm-flash"
            assert len(budget.reservations) == 1
            assert len(FakeSDKTransport.instances) == 2
            assert sum(item.calls for item in FakeSDKTransport.instances) == 1
            assert all(item.tools_seen == [] for item in FakeSDKTransport.instances)
            assert all(
                item.config.api_key.get_secret_value() == "SYNTHETIC-SECRET-DO-NOT-LOG"
                for item in FakeSDKTransport.instances
            )
        assert all(item.closed for item in FakeSDKTransport.instances)
    assert "SYNTHETIC-SECRET" not in caplog.text
    assert "Authorization" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected_calls", [(401, 1), (403, 1), (429, 1), (503, 1)])
async def test_factory_provider_errors_use_safe_fallback(monkeypatch, status, expected_calls):
    from a2_api.assistant import alice

    _alice_env(monkeypatch)
    monkeypatch.setattr(alice, "SDKTransport", FakeSDKTransport)
    FakeSDKTransport.instances = []
    FakeSDKTransport.failure = ModelFailure("provider_error", status_code=status, retry_after=0)
    try:
        agent = alice.build_assistant(AnalysisService())
        agent.sleep = _no_delay
        fixture = (
            Path(__file__).resolve().parents[3]
            / "packages/contracts/fixtures/v2/urban_passenger-result.json"
        )
        saved = AnalysisResult.model_validate(json.loads(fixture.read_text())["payload"])
        scope = AccessScope(
            principal_id="owner",
            analysis_ids=frozenset({saved.analysis_id}),
            territory_ids=frozenset({saved.request.city_id}),
            source_ids=frozenset(),
            snapshot_ids=frozenset({saved.snapshot_id}),
        )
        result = await agent.explain(
            saved, access_scope=scope, registered_template_ids=set(), request_id="synthetic"
        )
        assert result.status == "template" and result.reason_code == "PROVIDER_UNAVAILABLE"
        assert FakeSDKTransport.instances[0].calls == expected_calls
        assert len(budget.reservations) >= expected_calls
    finally:
        FakeSDKTransport.failure = None


async def _no_delay(_seconds):
    return None


@pytest.mark.asyncio
async def test_factory_timeout_falls_back_and_cancellation_propagates(monkeypatch):
    from a2_api.assistant import alice

    _alice_env(monkeypatch)
    fixture = (
        Path(__file__).resolve().parents[3]
        / "packages/contracts/fixtures/v2/urban_passenger-result.json"
    )
    saved = AnalysisResult.model_validate(json.loads(fixture.read_text())["payload"])
    scope = AccessScope(
        principal_id="owner",
        analysis_ids=frozenset({saved.analysis_id}),
        territory_ids=frozenset(),
        source_ids=frozenset(),
        snapshot_ids=frozenset(),
    )
    monkeypatch.setattr(alice, "SDKTransport", FakeSDKTransport)
    FakeSDKTransport.failure = ModelFailure("timeout")
    try:
        agent = alice.build_assistant(AnalysisService())
        result = await agent.explain(
            saved, access_scope=scope, registered_template_ids=set(), request_id="timeout"
        )
        assert result.status == "template" and result.reason_code == "TIMEOUT"
    finally:
        FakeSDKTransport.failure = None

    started = asyncio.Event()

    class WaitingSDKTransport(FakeSDKTransport):
        async def complete(self, messages, tools, **kwargs):
            started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(alice, "SDKTransport", WaitingSDKTransport)
    waiting = alice.build_assistant(AnalysisService())
    task = asyncio.create_task(
        waiting.explain(
            saved, access_scope=scope, registered_template_ids=set(), request_id="cancel"
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
