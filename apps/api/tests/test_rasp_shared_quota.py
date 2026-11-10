"""TEST_ONLY HTTP and disposable PG schemas; never send provider traffic."""

import argparse
import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from a2_api.core.rasp_quota import (
    QuotaUnavailable,
    credential_fingerprint,
    request_fingerprint,
)
from a2_api.db.repositories.rasp_quota import (
    PostgresRaspQuota,
    export_reservations,
    initialize_day,
    read_status,
)
from a2_api.providers.rasp import RaspCopyright, RaspError, RaspProvider
from a2_api.providers.rasp_runtime import RaspSettings, open_rasp_provider
from pydantic import ValidationError
from rasp_quota_helpers import TestOnlyQuota
from test_rasp_provider import page, query

from apps.api.tests.migration_support import migrate

SECRET = "TEST_ONLY-private-rasp-key"
ACCOUNT = "TEST_ONLY-shared-account"


async def initialize(pool, *, already_used, allowance=500, key=SECRET):
    day = (await pool.fetchval("SELECT clock_timestamp()")).astimezone(UTC).date()
    await initialize_day(
        pool,
        account_id=ACCOUNT,
        credential_fingerprint=credential_fingerprint(key),
        quota_timezone="UTC",
        quota_day=day,
        allowance=allowance,
        already_used=already_used,
        baseline_ref="TEST_ONLY-known-baseline-" + str(already_used),
        exclusive_usage_attested=True,
    )
    return day


def ledger(pool):
    return PostgresRaspQuota(pool, account_id=ACCOUNT, quota_timezone="UTC")


async def reserve(quota, *, attempt_id=None, key=SECRET, path="/v3.0/search/"):
    return await quota.reserve_attempt(
        attempt_id=attempt_id or str(uuid4()),
        credential_fingerprint=credential_fingerprint(key),
        request_fingerprint=request_fingerprint("api.rasp.yandex-net.ru", path, {"format": "json"}),
        max_wire_seconds=10,
    )


def copyright_body():
    return {
        "copyright": {
            "url": "https://rasp.yandex.ru/",
            "text": "TEST_ONLY provider attribution",
            "logo_vm": "<iframe>TEST_ONLY never execute</iframe>",
        }
    }


@pytest.mark.asyncio
async def test_key_without_durable_guard_and_unknown_account_never_send(database):
    pool, _, _ = database
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: calls.append(req))
    ) as client:
        for quota in (None, ledger(pool)):
            provider = RaspProvider(client, api_key=SECRET, wire_quota=quota)
            with pytest.raises(RaspError, match="quota_unavailable"):
                await provider.copyright()
    assert calls == []
    assert await pool.fetchval("SELECT count(*) FROM rasp_quota_days") == 0
    assert await read_status(pool, account_id=ACCOUNT) == {
        "status": "unconfigured",
        "hard_daily_limit": 500,
    }


@pytest.mark.asyncio
async def test_concurrent_instances_and_sessions_share_last_three_attempts(database):
    pool, _, _ = database
    await initialize(pool, already_used=497)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=copyright_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        providers = [
            RaspProvider(client, api_key=SECRET, wire_quota=ledger(pool)) for _ in range(12)
        ]
        results = await asyncio.gather(
            *(provider.copyright() for provider in providers), return_exceptions=True
        )
    assert len(calls) == sum(isinstance(result, RaspCopyright) for result in results) == 3
    assert all(
        isinstance(result, RaspCopyright)
        or isinstance(result, RaspError)
        and result.code == "quota_exceeded"
        for result in results
    )
    assert await pool.fetchval("SELECT reserved_count FROM rasp_quota_days") == 3
    assert await pool.fetchval("SELECT max(day_reserved_total) FROM rasp_quota_attempts") == 500


@pytest.mark.asyncio
async def test_duplicate_attempt_and_restart_cannot_authorize_again(database):
    pool, _, _ = database
    await initialize(pool, already_used=499)
    identity = str(uuid4())
    first = await reserve(ledger(pool), attempt_id=identity)
    assert first is not None and first.day_reserved_total == 500
    assert await reserve(ledger(pool), attempt_id=identity) is None
    assert await reserve(ledger(pool)) is None
    assert await pool.fetchval("SELECT count(*) FROM rasp_quota_attempts") == 1


@pytest.mark.asyncio
async def test_credential_rotation_and_host_alias_do_not_reset_account_budget(database):
    pool, _, _ = database
    await initialize(pool, already_used=499)
    assert await reserve(ledger(pool)) is not None
    rotated = "TEST_ONLY-rotated-key"
    await initialize(pool, already_used=499, key=rotated)
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: calls.append(req))
    ) as client:
        provider = RaspProvider(
            client, api_key=rotated, host="api.rasp.yandex.net", wire_quota=ledger(pool)
        )
        with pytest.raises(RaspError, match="quota_exceeded"):
            await provider.copyright()
    assert calls == []
    assert await pool.fetchval("SELECT count(*) FROM rasp_quota_accounts") == 1
    assert await pool.fetchval("SELECT count(*) FROM rasp_quota_credentials") == 2


@pytest.mark.asyncio
async def test_changed_initialization_blocks_instead_of_resetting_usage(database):
    pool, _, _ = database
    await initialize(pool, already_used=0)
    assert await reserve(ledger(pool)) is not None
    await initialize(pool, already_used=0)
    assert await pool.fetchval("SELECT reserved_count FROM rasp_quota_days") == 1
    with pytest.raises(QuotaUnavailable):
        await initialize(pool, already_used=1)
    assert (await read_status(pool, account_id=ACCOUNT))["status"] == "blocked"
    with pytest.raises(QuotaUnavailable):
        await reserve(ledger(pool))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unknown_used", "over_cap", "no_attestation", "tomorrow"])
async def test_explicit_current_day_known_baseline_is_required(database, case):
    pool, _, _ = database
    day = (await pool.fetchval("SELECT clock_timestamp()")).astimezone(UTC).date()
    with pytest.raises(QuotaUnavailable):
        await initialize_day(
            pool,
            account_id=ACCOUNT,
            credential_fingerprint=credential_fingerprint(SECRET),
            quota_timezone="UTC",
            quota_day=day + timedelta(days=1) if case == "tomorrow" else day,
            allowance=501 if case == "over_cap" else 500,
            already_used=None if case == "unknown_used" else 0,
            baseline_ref="TEST_ONLY-explicit-baseline",
            exclusive_usage_attested=case != "no_attestation",
        )
    assert await pool.fetchval("SELECT count(*) FROM rasp_quota_accounts") == 0


@pytest.mark.asyncio
async def test_new_quota_day_and_midnight_window_never_assume_zero(database):
    pool, _, _ = database
    day = await initialize(pool, already_used=0)
    await pool.execute(
        "UPDATE rasp_quota_days SET window_end=clock_timestamp()+interval '5 seconds'"
    )
    assert await reserve(ledger(pool)) is None  # Does not fit the full10s wire timeout.
    assert await pool.fetchval("SELECT reserved_count FROM rasp_quota_days") == 0
    await pool.execute("UPDATE rasp_quota_days SET quota_day=$1", day - timedelta(days=1))
    with pytest.raises(QuotaUnavailable):
        await reserve(ledger(pool))
    assert (await read_status(pool, account_id=ACCOUNT))["status"] == "unknown_usage"
    assert await pool.fetchval("SELECT count(*) FROM rasp_quota_days") == 1


@pytest.mark.asyncio
async def test_search_retries_and_copyright_consume_one_shared_ledger(database):
    pool, _, _ = database
    day = await initialize(pool, already_used=497)
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(
            200,
            json=copyright_body()
            if request.url.path.endswith("copyright/")
            else page(request=request),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = RaspProvider(
            client, api_key=SECRET, wire_quota=ledger(pool), max_pages=1, requests_per_second=1000
        )
        await query(provider)
        first = await provider.copyright()
        second = await provider.copyright()  # Cache hits do not spend or renew authorization.
        assert first == second
        assert len(calls) == 3
        with pytest.raises(RaspError, match="quota_exceeded"):
            await RaspProvider(client, api_key=SECRET, wire_quota=ledger(pool)).copyright()
    rows = await pool.fetch(
        "SELECT reservation_id,request_fingerprint FROM rasp_quota_attempts ORDER BY day_reserved_total"
    )
    assert [row["request_fingerprint"] for row in rows] == [
        request_fingerprint(request.url.host, request.url.path, dict(request.url.params))
        for request in calls
    ]
    exported = await export_reservations(
        pool,
        account_id=ACCOUNT,
        quota_day=day,
        reservation_ids=[row["reservation_id"] for row in rows],
    )
    assert exported["hard_daily_limit"] == exported["allowance"] == 500
    assert [row["day_reserved_total"] for row in exported["reservations"]] == [498, 499, 500]
    assert all(row["reserved_before_send"] for row in exported["reservations"])
    assert SECRET not in json.dumps(exported) and "credential_fingerprint" not in json.dumps(
        exported
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_or_cancel_retains_committed_attempt(database, cancel):
    pool, _, _ = database
    await initialize(pool, already_used=499)
    entered = asyncio.Event()

    async def handle(request):
        entered.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = RaspProvider(
            client, api_key=SECRET, wire_quota=ledger(pool), timeout_seconds=0.1
        )
        task = asyncio.create_task(provider.copyright())
        await asyncio.wait_for(entered.wait(), 3)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RaspError, match="timeout"):
                await task
    assert await pool.fetchval("SELECT reserved_count FROM rasp_quota_days") == 1
    assert await reserve(ledger(pool)) is None


@pytest.mark.asyncio
async def test_expired_permit_and_lost_commit_ack_never_send_or_refund(database):
    pool, _, _ = database
    await initialize(pool, already_used=498)
    identities = []

    class Expired(PostgresRaspQuota):
        async def reserve_attempt(self, **kwargs):
            permit = await super().reserve_attempt(**kwargs)
            return replace(permit, latest_start_monotonic=time.monotonic() - 1)

    class LostAck(PostgresRaspQuota):
        async def reserve_attempt(self, **kwargs):
            identities.append(kwargs["attempt_id"])
            await super().reserve_attempt(**kwargs)
            raise QuotaUnavailable()

    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: calls.append(req))
    ) as client:
        for kind in (Expired, LostAck):
            provider = RaspProvider(
                client,
                api_key=SECRET,
                wire_quota=kind(pool, account_id=ACCOUNT, quota_timezone="UTC"),
            )
            with pytest.raises(RaspError, match="quota_unavailable"):
                await provider.copyright()
    assert calls == []
    assert await pool.fetchval("SELECT reserved_count FROM rasp_quota_days") == 2
    assert await reserve(ledger(pool), attempt_id=identities[0]) is None


@pytest.mark.asyncio
async def test_owned_factory_is_explicit_and_uses_nonretrying_transport(database, monkeypatch):
    from a2_api.providers import rasp_runtime

    pool, _, _ = database
    await initialize(pool, already_used=499)
    transport_options = []
    calls = []

    def transport(**kwargs):
        transport_options.append(kwargs)
        return httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200, json=copyright_body())
        )

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport)
    config = RaspSettings(
        _env_file=None,
        live_enabled=True,
        api_key=SECRET,
        quota_database_url="postgresql://TEST_ONLY-central",
        quota_database_schema="test_only_central",
        account_id=ACCOUNT,
        quota_timezone="UTC",
    )

    # Use a valid schema identifier; no application DATABASE_URL is consulted.
    @asynccontextmanager
    async def selected(settings):
        assert settings.quota_database_schema == "test_only_central"
        yield pool

    monkeypatch.setattr(rasp_runtime, "open_quota_pool", selected)
    captures = []

    async def capture(value):
        captures.append(value)

    async with open_rasp_provider(config, on_page_capture=capture) as provider:
        await provider.copyright()
    assert len(calls) == len(captures) == 1
    assert transport_options[0]["retries"] == 0 and transport_options[0]["trust_env"] is False
    with pytest.raises(RaspError, match="disabled"):
        async with open_rasp_provider(config.model_copy(update={"live_enabled": False})):
            pytest.fail("Disabled provider yielded")


@pytest.mark.asyncio
async def test_raw_capture_binds_original_page_bytes_to_committed_reservation(database):
    pool, _, _ = database
    day = await initialize(pool, already_used=499)
    captures = []

    async def capture(value):
        captures.append(value)

    def handle(request):
        body = page(request=request)
        body["unrelated_provider_field"] = {"TEST_ONLY": "preserved-before-DTO-trimming"}
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = RaspProvider(
            client, api_key=SECRET, wire_quota=ledger(pool), max_pages=1, on_page_capture=capture
        )
        await query(provider)
    assert len(captures) == 1
    captured = captures[0]
    assert (
        json.loads(captured.raw_json)["unrelated_provider_field"]["TEST_ONLY"]
        == "preserved-before-DTO-trimming"
    )
    assert captured.request_fingerprint == request_fingerprint(
        "api.rasp.yandex-net.ru", "/v3.0/search/", dict(captured.params)
    )
    assert captured.fetched_at.tzinfo is not None
    assert "?" not in captured.endpoint and SECRET.encode() not in captured.raw_json
    assert not any(
        "key" in name.lower() or name.lower() == "authorization" for name, _ in captured.params
    )
    exported = await export_reservations(
        pool, account_id=ACCOUNT, quota_day=day, reservation_ids=[captured.reservation_id]
    )
    reservation = exported["reservations"][0]
    assert reservation["request_fingerprint"] == captured.request_fingerprint
    assert datetime.fromisoformat(reservation["reserved_at"]) <= captured.fetched_at


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["callback", "credential_echo"])
async def test_capture_failure_or_credential_echo_retains_quota_and_never_caches(database, failure):
    pool, _, _ = database
    await initialize(pool, already_used=499)
    captured = []

    async def capture(value):
        captured.append(value)
        raise RuntimeError(SECRET)

    body = copyright_body()
    if failure == "credential_echo":
        body["copyright"]["text"] = SECRET
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ) as client:
        provider = RaspProvider(
            client, api_key=SECRET, wire_quota=ledger(pool), on_page_capture=capture
        )
        with pytest.raises(RaspError, match="invalid_response") as error:
            await provider.copyright()
        assert SECRET not in str(error.value) and error.value.__cause__ is None
        with pytest.raises(RaspError, match="quota_exceeded"):
            await provider.copyright()
    assert len(captured) == (1 if failure == "callback" else 0)
    assert await pool.fetchval("SELECT reserved_count FROM rasp_quota_days") == 1


@pytest.mark.asyncio
async def test_credential_cannot_be_rebound_to_a_fresh_account_or_different_day_zone(database):
    pool, _, _ = database
    day = await initialize(pool, already_used=499)
    assert await reserve(ledger(pool)) is not None
    with pytest.raises(QuotaUnavailable):
        await initialize_day(
            pool,
            account_id="TEST_ONLY-other-account",
            credential_fingerprint=credential_fingerprint(SECRET),
            quota_timezone="UTC",
            quota_day=day,
            allowance=500,
            already_used=0,
            baseline_ref="TEST_ONLY-known-zero",
            exclusive_usage_attested=True,
        )
    with pytest.raises(QuotaUnavailable):
        await reserve(PostgresRaspQuota(pool, account_id=ACCOUNT, quota_timezone="Europe/Moscow"))
    assert await pool.fetchval("SELECT count(*) FROM rasp_quota_accounts") == 1
    assert await pool.fetchval("SELECT reserved_count FROM rasp_quota_days") == 1


@pytest.mark.asyncio
async def test_guarded_downgrade_preserves_initialized_quota_history(database):
    pool, schema, url = database
    await initialize(pool, already_used=0)
    with pytest.raises(RuntimeError, match="Rasp quota history"):
        await asyncio.to_thread(migrate, url, schema, "0005_od_checkpoints", True)
    assert await pool.fetchval("SELECT version_num FROM alembic_version") == "0006_rasp_quota"
    assert await pool.fetchval("SELECT count(*) FROM rasp_quota_days") == 1


@pytest.mark.asyncio
async def test_admin_initialization_cannot_default_unknown_usage_to_zero(database, monkeypatch):
    from a2_api.providers import rasp_quota_cli

    pool, _, _ = database
    day = (await pool.fetchval("SELECT clock_timestamp()")).astimezone(UTC).date()
    config = RaspSettings(
        _env_file=None,
        api_key=SECRET,
        quota_database_url="postgresql://TEST_ONLY-central",
        quota_database_schema="test_only_central",
        account_id=ACCOUNT,
        quota_timezone="UTC",
    )

    @asynccontextmanager
    async def selected(settings):
        yield pool

    monkeypatch.setattr(rasp_quota_cli, "open_quota_pool", selected)
    args = argparse.Namespace(
        command="initialize-day",
        day=day.isoformat(),
        allowance=500,
        already_used=None,
        baseline_ref="TEST_ONLY-unknown",
        exclusive_managed_usage_attested=True,
    )
    with pytest.raises(QuotaUnavailable):
        await rasp_quota_cli.run_command(config, args)
    assert await pool.fetchval("SELECT count(*) FROM rasp_quota_days") == 0


def test_release_application_database_is_never_a_fallback_for_shared_quota(monkeypatch):
    monkeypatch.delenv("RASP_QUOTA_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://TEST_ONLY-release-local")
    with pytest.raises(ValidationError):
        RaspSettings(
            _env_file=None,
            account_id=ACCOUNT,
            quota_timezone="UTC",
            quota_database_schema="test_only_central",
        )


@pytest.mark.asyncio
async def test_storage_guard_failure_is_sanitized_and_cannot_reach_wire():
    class Broken:
        durable = True

        async def reserve_attempt(self, **kwargs):
            raise RuntimeError(SECRET)

    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: calls.append(request))
    ) as client:
        provider = RaspProvider(client, api_key=SECRET, wire_quota=Broken())
        with pytest.raises(RaspError, match="quota_unavailable") as error:
            await provider.copyright()
    assert calls == [] and SECRET not in str(error.value) and error.value.__cause__ is None


@pytest.mark.parametrize("key", ["apikey", "api_key", "Authorization", "KEY"])
def test_canonical_request_fingerprint_cannot_include_credentials(key):
    with pytest.raises(QuotaUnavailable):
        request_fingerprint("api.rasp.yandex-net.ru", "/v3.0/search/", {key: SECRET})


@pytest.mark.asyncio
async def test_duplicate_wire_parameters_cannot_diverge_from_capture_fingerprint(monkeypatch):
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: calls.append(request))
    ) as client:
        original = client.build_request

        def duplicate(*args, **kwargs):
            request = original(*args, **kwargs)
            request.url = request.url.copy_add_param("format", "json")
            return request

        monkeypatch.setattr(client, "build_request", duplicate)
        provider = RaspProvider(client, api_key=SECRET, wire_quota=TestOnlyQuota())
        with pytest.raises(RaspError, match="invalid_request"):
            await provider.copyright()
    assert calls == []


@pytest.mark.asyncio
async def test_ledger_latency_does_not_compress_owned_client_rate_interval():
    class SlowFirst(TestOnlyQuota):
        async def reserve_attempt(self, **kwargs):
            if not self.attempt_ids:
                await asyncio.sleep(0.03)
            return await super().reserve_attempt(**kwargs)

    times = []

    def handle(request):
        times.append(time.monotonic())
        return httpx.Response(
            200,
            json=copyright_body()
            if request.url.path.endswith("copyright/")
            else page(request=request),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = RaspProvider(
            client, api_key=SECRET, wire_quota=SlowFirst(), requests_per_second=10, max_pages=1
        )
        await provider.copyright()
        await query(provider)
    assert len(times) == 2 and times[1] - times[0] >= 0.09


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_enabled", [False, True])
@pytest.mark.parametrize("escaped", [False, True])
@pytest.mark.parametrize("location", ["text", "logo", "nested_value", "dictionary_key"])
async def test_direct_or_escaped_credential_echo_is_rejected_before_capture_dto_and_cache(
    database, capture_enabled, escaped, location
):
    pool, _, _ = database
    await initialize(pool, already_used=499)
    body = copyright_body()
    if location == "text":
        body["copyright"]["text"] = SECRET
    elif location == "logo":
        body["copyright"]["logo_vm"] = "TEST_ONLY-prefix" + SECRET + "-suffix"
    elif location == "nested_value":
        body["unrelated_source_metadata"] = [{"nested": [SECRET]}]
    else:
        body[SECRET] = "TEST_ONLY-value"
    raw = json.dumps(body).encode()
    if escaped:
        encoded = "".join("\\u%04x" % ord(character) for character in SECRET).encode()
        raw = raw.replace(SECRET.encode(), encoded)
        assert SECRET.encode() not in raw
    captures, calls = [], []

    async def capture(value):
        captures.append(value)

    def handle(request):
        calls.append(request)
        return httpx.Response(200, content=raw)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = RaspProvider(
            client,
            api_key=SECRET,
            wire_quota=ledger(pool),
            on_page_capture=capture if capture_enabled else None,
        )
        with pytest.raises(RaspError, match="invalid_response") as error:
            await provider.copyright()
        assert SECRET not in str(error.value) and error.value.__cause__ is None
        assert provider._copyright_cache is None
        with pytest.raises(RaspError, match="quota_exceeded"):
            await provider.copyright()
    assert len(calls) == 1 and captures == []
    assert await pool.fetchval("SELECT reserved_count FROM rasp_quota_days") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_enabled", [False, True])
@pytest.mark.parametrize("first_value", ["benign", "direct_secret", "escaped_secret"])
async def test_duplicate_json_keys_are_rejected_before_discarding_any_value(
    database, capture_enabled, first_value
):
    pool, _, _ = database
    await initialize(pool, already_used=499)
    value = "TEST_ONLY-first" if first_value == "benign" else SECRET
    encoded = json.dumps(value)
    if first_value == "escaped_secret":
        encoded = encoded.replace(
            SECRET, "".join("\\u%04x" % ord(character) for character in SECRET)
        )
    raw = (
        '{"copyright":{"url":"https://rasp.yandex.ru/","text":'
        + encoded
        + ',"text":"TEST_ONLY-final"}}'
    ).encode()
    captures = []

    async def capture(value):
        captures.append(value)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw))
    ) as client:
        provider = RaspProvider(
            client,
            api_key=SECRET,
            wire_quota=ledger(pool),
            on_page_capture=capture if capture_enabled else None,
        )
        with pytest.raises(RaspError, match="invalid_response") as error:
            await provider.copyright()
        assert SECRET not in str(error.value) and error.value.__cause__ is None
        assert provider._copyright_cache is None
    assert captures == []
    assert await pool.fetchval("SELECT reserved_count FROM rasp_quota_days") == 1
