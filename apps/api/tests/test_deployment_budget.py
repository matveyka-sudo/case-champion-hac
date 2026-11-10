"""Synthetic PostgreSQL spending-guard tests. Never contacts Yandex Cloud."""

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from apps.api.tests.migration_support import migrate

MODEL = "gpt://synthetic-folder/aliceai-llm-flash"
LIMIT = Decimal("1.00")
TOKEN_LIMIT = 10000


def ledger(pool, limit=LIMIT, token_limit=TOKEN_LIMIT):
    from a2_api.db.repositories.budget import TARIFF_ID, PostgresDeploymentBudget

    return PostgresDeploymentBudget(
        pool,
        model_uri=MODEL,
        limit_amount=limit,
        token_limit=token_limit,
        currency="RUB",
        tariff_id=TARIFF_ID,
    )


async def configure(pool, limit=LIMIT, token_limit=TOKEN_LIMIT):
    from a2_api.db.repositories.budget import TARIFF_ID, configure_budget

    await configure_budget(
        pool,
        limit_amount=limit,
        token_limit=token_limit,
        model_uri=MODEL,
        currency="RUB",
        tariff_id=TARIFF_ID,
    )


async def reserve(budget, identifier, *, input_tokens=1000, output_tokens=1000):
    return await budget.reserve(
        reservation_id=identifier,
        model=MODEL,
        max_input_tokens=input_tokens,
        max_output_tokens=output_tokens,
    )


@pytest.mark.asyncio
async def test_no_budget_is_fail_closed_and_successful_reservations_use_money(database):
    pool, _, _ = database
    budget = ledger(pool)
    assert await reserve(budget, "one") is False
    await configure(pool)
    assert await reserve(budget, "one") is True
    assert await reserve(budget, "one") is False
    assert await reserve(budget, "two") is True
    assert await reserve(budget, "three") is True
    assert await reserve(budget, "four") is False
    snapshot = await budget.status()
    assert snapshot.limit_amount == LIMIT
    assert snapshot.charged_amount == Decimal("0")
    assert snapshot.held_amount == Decimal("0.9")
    assert snapshot.available_amount == Decimal("0.1")
    assert snapshot.token_limit == TOKEN_LIMIT
    assert snapshot.held_tokens == 6000
    assert snapshot.available_tokens == 4000
    assert snapshot.reservation_count == 3


@pytest.mark.asyncio
async def test_known_usage_settles_once_and_unknown_usage_keeps_full_reserve(database):
    from a2_api.db.repositories.budget import BudgetUnavailable

    pool, _, _ = database
    await configure(pool)
    budget = ledger(pool)
    assert await reserve(budget, "known")
    await budget.record_usage(reservation_id="known", input_tokens=100, output_tokens=100)
    snapshot = await budget.status()
    assert snapshot.charged_amount == Decimal("0.03")
    assert snapshot.held_amount == Decimal("0")
    assert snapshot.charged_tokens == 200
    assert snapshot.held_tokens == 0
    await budget.record_usage(reservation_id="known", input_tokens=100, output_tokens=100)
    assert (await budget.status()).charged_amount == Decimal("0.03")
    with pytest.raises(BudgetUnavailable):
        await budget.record_usage(reservation_id="known", input_tokens=200, output_tokens=100)
    assert await reserve(budget, "unknown")
    await budget.record_usage(reservation_id="unknown", input_tokens=None, output_tokens=None)
    assert (await budget.status()).held_amount == Decimal("0.3")
    assert (await budget.status()).held_tokens == 2000
    await budget.record_usage(reservation_id="unknown", input_tokens=None, output_tokens=None)
    assert (await budget.status()).held_amount == Decimal("0.3")
    await budget.record_usage(reservation_id="unknown", input_tokens=200, output_tokens=100)
    snapshot = await budget.status()
    assert snapshot.charged_amount == Decimal("0.07")
    assert snapshot.held_amount == Decimal("0")
    assert snapshot.charged_tokens == 500
    assert snapshot.held_tokens == 0


@pytest.mark.asyncio
async def test_concurrent_connections_share_one_atomic_deployment_budget(database):
    pool, schema, url = database
    await configure(pool)
    second = await asyncpg.create_pool(url, server_settings={"search_path": schema})
    try:
        first_budget, second_budget = ledger(pool), ledger(second)
        outcomes = await asyncio.gather(
            *(reserve(first_budget if i % 2 else second_budget, f"race-{i}") for i in range(10))
        )
        assert sum(outcomes) == 3
        assert (await first_budget.status()).held_amount == Decimal("0.9")
        assert (await first_budget.status()).held_tokens == 6000
        assert (await second_budget.status()).reservation_count == 3
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_process_restart_or_unknown_failure_does_not_refund_reservation(database):
    pool, _, _ = database
    await configure(pool)
    assert await reserve(ledger(pool), "crashed")
    restarted = ledger(pool)
    assert await reserve(restarted, "crashed") is False
    assert (await restarted.status()).held_amount == Decimal("0.3")
    await restarted.record_usage(reservation_id="crashed", input_tokens=None, output_tokens=None)
    assert (await ledger(pool).status()).held_amount == Decimal("0.3")
    assert (await ledger(pool).status()).held_tokens == 2000


@pytest.mark.asyncio
async def test_token_ceiling_is_independent_of_money_and_no_request_count_cap(database):
    pool, _, _ = database
    await configure(pool, Decimal("10"), token_limit=2500)
    budget = ledger(pool, Decimal("10"), token_limit=2500)
    assert await reserve(budget, "token-first") is True
    assert await reserve(budget, "token-second") is False
    assert (await budget.status()).available_amount == Decimal("9.7")
    await budget.record_usage(reservation_id="token-first", input_tokens=100, output_tokens=100)
    assert (await budget.status()).available_tokens == 2300
    for index in range(25):
        assert await reserve(budget, f"small-{index}", input_tokens=1, output_tokens=1) is True
    assert (await budget.status()).reservation_count == 26


@pytest.mark.asyncio
async def test_unexpected_provider_usage_blocks_future_reservations(database):
    pool, _, _ = database
    await configure(pool)
    budget = ledger(pool)
    assert await reserve(budget, "overrun", input_tokens=100, output_tokens=100)
    await budget.record_usage(reservation_id="overrun", input_tokens=200, output_tokens=100)
    snapshot = await budget.status()
    assert snapshot.charged_amount == Decimal("0.04")
    assert snapshot.charged_tokens == 300
    assert snapshot.blocked is True
    assert await reserve(budget, "later") is False


@pytest.mark.asyncio
async def test_database_failure_denies_reserve_and_never_releases_unknown_cost(database):
    from a2_api.db.repositories.budget import BudgetUnavailable

    pool, schema, url = database
    await configure(pool)
    assert await reserve(ledger(pool), "outage")
    other = await asyncpg.create_pool(url, server_settings={"search_path": schema})
    budget = ledger(other)
    await other.close()
    assert await reserve(budget, "next") is False
    with pytest.raises(BudgetUnavailable):
        await budget.record_usage(reservation_id="outage", input_tokens=100, output_tokens=100)
    assert (await ledger(pool).status()).held_amount == Decimal("0.3")


@pytest.mark.asyncio
async def test_limit_increase_preserves_spend_and_rejects_mismatch(database):
    from a2_api.db.repositories.budget import BudgetUnavailable

    pool, _, _ = database
    await configure(pool)
    original = ledger(pool)
    assert await reserve(original, "charged")
    await original.record_usage(reservation_id="charged", input_tokens=100, output_tokens=100)
    assert await reserve(original, "held")
    await configure(pool, Decimal("2.00"), token_limit=20000)
    increased = ledger(pool, Decimal("2.00"), token_limit=20000)
    assert (await increased.status()).charged_amount == Decimal("0.03")
    assert (await increased.status()).held_amount == Decimal("0.3")
    assert await reserve(original, "stale-process") is False
    assert await reserve(increased, "fresh-process") is True
    with pytest.raises(BudgetUnavailable):
        await configure(pool, Decimal("1.00"))


@pytest.mark.asyncio
async def test_wrong_model_currency_or_tariff_never_reserves(database):
    from a2_api.db.repositories.budget import TARIFF_ID, PostgresDeploymentBudget

    pool, _, _ = database
    await configure(pool)
    assert (
        await ledger(pool).reserve(
            reservation_id="wrong-model",
            model="gpt://other-folder/aliceai-llm-flash",
            max_input_tokens=1000,
            max_output_tokens=1000,
        )
        is False
    )
    wrong_currency = PostgresDeploymentBudget(
        pool,
        model_uri=MODEL,
        limit_amount=LIMIT,
        token_limit=TOKEN_LIMIT,
        currency="USD",
        tariff_id=TARIFF_ID,
    )
    assert await reserve(wrong_currency, "wrong-currency") is False
    wrong_tariff = PostgresDeploymentBudget(
        pool,
        model_uri=MODEL,
        limit_amount=LIMIT,
        token_limit=TOKEN_LIMIT,
        currency="RUB",
        tariff_id="stale",
    )
    assert await reserve(wrong_tariff, "wrong-tariff") is False


@pytest.mark.asyncio
async def test_guarded_downgrade_preserves_configured_ledger(database):
    pool, schema, url = database
    await configure(pool)
    assert await reserve(ledger(pool), "saved")
    with pytest.raises(RuntimeError, match="budget"):
        await asyncio.to_thread(migrate, url, schema, "0003_evidence_search", True)
    assert await pool.fetchval("SELECT version_num FROM alembic_version") == "0006_rasp_quota"
    assert (await ledger(pool).status()).held_amount == Decimal("0.3")


@pytest.mark.asyncio
async def test_budget_hook_uses_explicit_operator_settings_and_shared_pool(database, monkeypatch):
    from a2_api.assistant.budget import build_budget
    from a2_api.db.repositories.budget import TARIFF_ID
    from a2_api.db.repositories.jobs import JobRepository
    from a2_api.services.analyses import AnalysisService

    pool, _, _ = database
    service = AnalysisService(repository=JobRepository(pool))
    monkeypatch.setenv("YANDEX_FOLDER_ID", "synthetic-folder")
    monkeypatch.setenv("A2_ALICE_CURRENCY", "RUB")
    monkeypatch.setenv("A2_ALICE_TARIFF_ID", TARIFF_ID)
    monkeypatch.setenv("A2_ALICE_BUDGET_LIMIT", "1.00")
    monkeypatch.setenv("A2_ALICE_TOKEN_LIMIT", "10000")
    budget = build_budget(service)
    assert budget.pool is pool
    assert await reserve(budget, "not-configured") is False
    await configure(pool)
    assert await reserve(budget, "configured") is True
    monkeypatch.setenv("A2_ALICE_CURRENCY", "USD")
    with pytest.raises(ValueError, match="budget configuration"):
        build_budget(service)
    monkeypatch.setenv("A2_ALICE_CURRENCY", "RUB")
    monkeypatch.setenv("A2_ALICE_TARIFF_ID", "unreviewed-price")
    with pytest.raises(ValueError, match="budget configuration"):
        build_budget(service)


@pytest.mark.asyncio
async def test_admin_cli_reports_aggregate_and_only_explicitly_sets_limit(database, monkeypatch):
    from a2_api.assistant.budget_cli import run_command
    from a2_api.db.repositories.budget import TARIFF_ID

    pool, _, _ = database
    assert await run_command("status", pool) == {"status": "unconfigured"}
    monkeypatch.setenv("YANDEX_FOLDER_ID", "synthetic-folder")
    monkeypatch.setenv("A2_ALICE_CURRENCY", "RUB")
    monkeypatch.setenv("A2_ALICE_TARIFF_ID", TARIFF_ID)
    monkeypatch.setenv("A2_ALICE_BUDGET_LIMIT", "1.00")
    monkeypatch.setenv("A2_ALICE_TOKEN_LIMIT", "10000")
    view = await run_command("set-limit", pool)
    assert view["limit_amount"] == "1.000000000"
    assert view["token_limit"] == 10000
    assert "reservation_id" not in view and "synthetic-folder" not in str(view)
    assert await reserve(ledger(pool), "cli")
    view = await run_command("status", pool)
    assert view["held_amount"] == "0.300000000"
    assert view["available_tokens"] == 8000


def saved_scope():
    from a2_assistant import AccessScope
    from a2_contracts.v2.results import AnalysisResult

    fixture = (
        Path(__file__).resolve().parents[3]
        / "packages/contracts/fixtures/v2/urban_passenger-result.json"
    )
    saved = AnalysisResult.model_validate(json.loads(fixture.read_text())["payload"])
    scope = AccessScope(
        principal_id="synthetic-owner",
        analysis_ids=frozenset({saved.analysis_id}),
        territory_ids=frozenset(),
        source_ids=frozenset(),
        snapshot_ids=frozenset(),
    )
    return saved, scope


def set_hook_env(monkeypatch, *, limit="2.00", token_limit="20000"):
    from a2_api.db.repositories.budget import TARIFF_ID

    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("A2_ALICE_BILLING_CURRENCY_CONFIRMED", "true")  # synthetic test only
    monkeypatch.setenv("YANDEX_API_KEY", "SYNTHETIC-NOT-A-REAL-KEY")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "synthetic-folder")
    monkeypatch.setenv("YANDEX_MODEL", "aliceai-llm-flash")
    monkeypatch.setenv("YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1")
    monkeypatch.setenv("A2_ASSISTANT_BUDGET_HOOK", "a2_api.assistant.budget:build_budget")
    monkeypatch.setenv("A2_ASSISTANT_TOOLS_ENABLED", "false")
    monkeypatch.setenv("A2_ALICE_CURRENCY", "RUB")
    monkeypatch.setenv("A2_ALICE_TARIFF_ID", TARIFF_ID)
    monkeypatch.setenv("A2_ALICE_BUDGET_LIMIT", limit)
    monkeypatch.setenv("A2_ALICE_TOKEN_LIMIT", token_limit)


@pytest.mark.asyncio
async def test_existing_factory_falls_back_without_configured_budget(database, monkeypatch):
    from a2_api.assistant import alice
    from a2_api.db.repositories.jobs import JobRepository
    from a2_api.services.analyses import AnalysisService
    from test_alice_runtime import FakeSDKTransport

    pool, _, _ = database
    set_hook_env(monkeypatch)
    monkeypatch.setattr(alice, "SDKTransport", FakeSDKTransport)
    FakeSDKTransport.instances = []
    FakeSDKTransport.failure = None
    agent = alice.build_assistant(AnalysisService(repository=JobRepository(pool)))
    saved, scope = saved_scope()
    result = await agent.explain(
        saved, access_scope=scope, registered_template_ids=set(), request_id="no-budget"
    )
    assert result.status == "template" and result.reason_code == "BUDGET_EXCEEDED"
    assert FakeSDKTransport.instances[0].calls == 0
    await configure(pool, Decimal("2.00"), token_limit=20000)
    result = await agent.explain(
        saved, access_scope=scope, registered_template_ids=set(), request_id="funded"
    )
    assert result.status == "generated"
    snapshot = await agent.budget.status()
    assert snapshot.reservation_count == 1
    assert snapshot.charged_amount == Decimal("0.002")
    assert snapshot.held_amount == Decimal("0")
    assert FakeSDKTransport.instances[0].calls == 1


@pytest.mark.asyncio
async def test_retry_and_cancellation_keep_uncertain_cost_reserved(database):
    from a2_assistant import FlashAssistant, FlashConfig
    from a2_assistant.transport import ModelFailure, ModelReply

    pool, _, _ = database
    await configure(pool, Decimal("4.00"), token_limit=40000)
    budget = ledger(pool, Decimal("4.00"), token_limit=40000)
    saved, scope = saved_scope()

    class SequenceTransport:
        live = True

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ModelFailure("provider_error", status_code=429, retry_after=0)
            fact = json.loads(messages[1]["content"])["saved_facts"]
            metric = fact["metrics"][0]
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
                input_tokens=100,
                output_tokens=50,
            )

    async def no_delay(_seconds):
        return None

    transport = SequenceTransport()
    agent = FlashAssistant(
        FlashConfig(api_key="synthetic", folder_id="synthetic-folder"),
        transport=transport,
        tool_backend=None,
        deployment_budget=budget,
        sleep=no_delay,
    )
    result = await agent.explain(
        saved, access_scope=scope, registered_template_ids=set(), request_id="retry"
    )
    assert result.status == "generated" and transport.calls == 2
    snapshot = await budget.status()
    assert snapshot.reservation_count == 2
    assert snapshot.held_amount == Decimal("1.6")
    assert snapshot.charged_amount == Decimal("0.02")

    started = asyncio.Event()

    class WaitingTransport:
        live = True

        async def complete(self, messages, tools, **kwargs):
            started.set()
            await asyncio.Event().wait()

    waiting = FlashAssistant(
        FlashConfig(api_key="synthetic", folder_id="synthetic-folder"),
        transport=WaitingTransport(),
        tool_backend=None,
        deployment_budget=budget,
    )
    task = asyncio.create_task(
        waiting.explain(
            saved, access_scope=scope, registered_template_ids=set(), request_id="cancel"
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    snapshot = await budget.status()
    assert snapshot.reservation_count == 3
    assert snapshot.held_amount == Decimal("3.2")
