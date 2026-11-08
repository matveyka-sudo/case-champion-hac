"""Provider boundary tests: synthetic HTTP only, no key or live API access."""

import asyncio
import importlib
import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

START = datetime(2026, 11, 10, 8, 30, tzinfo=UTC)
SECRET = "synthetic-private-key"


def module():
    try:
        return importlib.import_module("a2_api.providers.rasp")
    except ModuleNotFoundError:
        pytest.fail("Standalone Rasp provider is not implemented")


def segment(departure="2026-11-10T09:00:00Z", uid="train-1", **changes):
    start = datetime.fromisoformat(departure.replace("Z", "+00:00"))
    return {
        "departure": departure,
        "arrival": (start + timedelta(hours=2)).isoformat(),
        "duration": 7200,
        "from": {"code": "s1", "title": "Synthetic origin", "station_type": "station"},
        "to": {"code": "s2", "title": "Synthetic destination", "station_type": "station"},
        "thread": {"uid": uid, "transport_type": "train", "title": "Synthetic service"},
        "has_transfers": False,
        "tickets_info": None,
    } | changes


def page(rows=(), *, total=None, offset=0, limit=100, intervals=()):
    return {
        "segments": list(rows),
        "interval_segments": list(intervals),
        "pagination": {
            "total": len(rows) if total is None else total,
            "limit": limit,
            "offset": offset,
        },
        "search": {"date": "2026-11-10", "from": {"code": "c1"}, "to": {"code": "c2"}},
    }


def provider(client, **options):
    return module().RaspProvider(client, api_key=SECRET, **options)


async def query(adapter, **options):
    return await adapter.intercity_departures(
        "c1", "c2", START, origin_timezone="UTC", transport_types=("train",), **options
    )


@pytest.mark.asyncio
async def test_missing_key_is_disabled_without_http():
    async def no_http(request):
        pytest.fail("Disabled provider performed HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_http)) as client:
        adapter = module().RaspProvider(client, api_key=None)
        with pytest.raises(module().RaspError) as error:
            await query(adapter)
        assert error.value.code == "disabled"


@pytest.mark.asyncio
async def test_date_api_is_filtered_to_exact_seven_day_window_and_stays_partial():
    requests = []

    async def handle(request):
        requests.append(request)
        rows = [
            segment("2026-11-10T08:00:00Z", "too-early"),
            segment(),
            segment("2026-11-17T08:29:00Z", "last"),
            segment("2026-11-17T08:30:00Z", "too-late"),
        ]
        return httpx.Response(200, json=page(rows))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = provider(client, requests_per_second=1000)
        result = await query(adapter)
    assert [r.service_uid for r in result.departures] == ["train-1", "last"]
    assert len(requests) == 8  # 08:30 + seven days intersects eight calendar dates.
    assert [r.url.params["date"] for r in requests] == [f"2026-11-{d:02}" for d in range(10, 18)]
    request = requests[0]
    assert request.url.host == "api.rasp.yandex-net.ru" and request.url.scheme == "https"
    assert request.url.path == "/v3.0/search/"
    assert request.headers["Authorization"] == SECRET
    assert SECRET not in str(request.url)
    assert request.url.params["transport_types"] == "train"
    assert request.url.params["transfers"] == "false"
    assert request.url.params["result_timezone"] == "UTC"
    assert result.coverage == "partial" and result.pagination_complete is True
    assert result.requested_departure_at == START
    assert result.window_end == datetime(2026, 11, 17, 8, 30, tzinfo=UTC)
    assert "DIRECT_SERVICES_ONLY" in result.reason_codes
    assert SECRET not in result.model_dump_json()


@pytest.mark.asyncio
async def test_empty_provider_response_never_confirms_no_route():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=page()))
    ) as client:
        result = await query(provider(client, requests_per_second=1000))
    assert result.departures == []
    assert result.coverage == "partial"
    assert "PROVIDER_COVERAGE_NOT_COMPLETE" in result.reason_codes


@pytest.mark.asyncio
async def test_bounded_pagination_preserves_known_departures_and_reports_cutoff():
    offsets = []

    def handle(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        rows = [segment(uid=f"train-{offset + i}") for i in range(2)]
        return httpx.Response(200, json=page(rows, total=6, offset=offset, limit=2))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await query(provider(client, max_pages=2, page_size=2, requests_per_second=1000))
    assert offsets == [0, 2]
    assert len(result.departures) == 4 and result.pagination_complete is False
    assert "PAGE_LIMIT" in result.reason_codes


@pytest.mark.asyncio
async def test_interval_services_remain_unknown_instead_of_inventing_departures():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=page(intervals=[{"thread": {"uid": "interval"}}])
            )
        )
    ) as client:
        result = await query(provider(client, requests_per_second=1000))
    assert result.departures == []
    assert "INTERVAL_SERVICES_UNRESOLVED" in result.reason_codes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"from_code": "https://evil.example/"},
        {"from_code": "c1&apikey=leak"},
        {"to_code": "c1"},
        {"departure_at": datetime(2026, 11, 10)},
        {"origin_timezone": "Invalid/Timezone"},
        {"transport_types": ("car",)},
        {"transport_types": ()},
        {"transport_types": ("train", "train")},
    ],
)
async def test_invalid_query_is_rejected_before_network(changes):
    def no_http(request):
        pytest.fail("Invalid query performed HTTP")

    args = {
        "from_code": "c1",
        "to_code": "c2",
        "departure_at": START,
        "origin_timezone": "UTC",
        "transport_types": ("train",),
    } | changes
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_http)) as client:
        with pytest.raises(module().RaspError) as error:
            await provider(client).intercity_departures(**args)
        assert error.value.code == "invalid_request"


@pytest.mark.asyncio
async def test_host_and_unknown_options_are_rejected_and_redirects_are_not_followed():
    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/?apikey=" + SECRET})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(redirect), follow_redirects=True
    ) as client:
        with pytest.raises(ValueError):
            provider(client, host="evil.example")
        with pytest.raises(TypeError):
            await query(provider(client), url="https://evil.example")
        with pytest.raises(module().RaspError) as error:
            await query(provider(client))
    assert len(requests) == 1 and error.value.code == "redirect_rejected"
    assert SECRET not in str(error.value)


@pytest.mark.asyncio
async def test_client_default_query_parameters_cannot_smuggle_provider_options():
    async with httpx.AsyncClient(
        params={"callback": "evil"},
        transport=httpx.MockTransport(
            lambda request: pytest.fail("Unsafe merged params reached HTTP")
        ),
    ) as client:
        with pytest.raises(module().RaspError) as error:
            await query(provider(client))
    assert error.value.code == "invalid_request"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_errors_retry_at_most_twice_obeying_retry_after(status):
    calls = 0
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, headers={"Retry-After": "2"}, text=SECRET)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(module().RaspError) as error:
            await query(provider(client, sleep=sleep))
    assert calls == 3 and sleeps.count(2) == 2
    assert error.value.code == ("rate_limited" if status == 429 else "upstream_error")
    assert SECRET not in str(error.value) and error.value.__cause__ is None


@pytest.mark.asyncio
async def test_long_retry_after_does_not_retry_earlier_than_provider_allows():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "3600"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(module().RaspError) as error:
            await query(provider(client))
    assert len(calls) == 1 and error.value.code == "rate_limited"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, text=SECRET),
        httpx.Response(200, text=SECRET),
        httpx.Response(200, json={"segments": []}),
        httpx.Response(200, json=page([segment(departure="2026-11-10T08:30:00")])),
        httpx.Response(200, content=json.dumps(page([segment(duration=float("nan"))]))),
    ],
)
async def test_provider_failure_is_typed_and_sanitized(response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        with pytest.raises(module().RaspError) as error:
            await query(provider(client))
    assert error.value.code in {"upstream_error", "invalid_response"}
    assert SECRET not in str(error.value)


@pytest.mark.asyncio
async def test_total_timeout_also_bounds_mock_transport_and_does_not_leak_exception():
    async def handle(request):
        await asyncio.sleep(1)
        raise RuntimeError(SECRET)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(module().RaspError) as error:
            await query(provider(client, timeout_seconds=0.01, operation_timeout_seconds=0.02))
    assert error.value.code == "timeout" and SECRET not in str(error.value)


@pytest.mark.asyncio
async def test_quota_includes_retries_and_cutoff_after_one_page_is_partial():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=page([segment()]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await query(provider(client, request_quota=1, requests_per_second=1000))
    assert len(calls) == 1 and result.pagination_complete is False
    assert result.errors[0].code == "quota_exceeded" and len(result.departures) == 1


@pytest.mark.asyncio
async def test_cache_is_ttl_scoped_and_returned_models_cannot_corrupt_it():
    calls = []
    now = [0.0]

    async def sleep(seconds):
        now[0] += seconds

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=page([segment()]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = provider(client, clock=lambda: now[0], sleep=sleep, cache_ttl_seconds=10)
        first = await query(adapter)
        saved_key, saved_hash = first.cache_key, first.content_hash
        first.departures.clear()
        second = await query(adapter)
        assert len(calls) == 8 and len(second.departures) == 1
        assert second.cache_key == saved_key and second.content_hash == saved_hash
        await adapter.intercity_departures(
            "c2", "c1", START, origin_timezone="UTC", transport_types=("train",)
        )
        assert len(calls) == 16
        now[0] += 11
        third = await query(adapter)
        assert len(calls) == 24 and third.cache_key == saved_key


@pytest.mark.asyncio
async def test_shared_rate_and_concurrency_are_enforced_across_queries():
    now = [0.0]
    started = []
    active, peak = 0, 0

    async def sleep(seconds):
        now[0] += seconds
        await asyncio.sleep(0)

    async def handle(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.append(now[0])
        await asyncio.sleep(0)
        active -= 1
        return httpx.Response(200, json=page())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = provider(
            client, requests_per_second=2, max_concurrency=1, clock=lambda: now[0], sleep=sleep
        )
        await asyncio.gather(
            query(adapter),
            adapter.intercity_departures(
                "c2", "c1", START, origin_timezone="UTC", transport_types=("train",)
            ),
        )
    assert peak == 1 and all(b - a >= 0.5 for a, b in zip(started, started[1:]))


@pytest.mark.asyncio
async def test_observation_hash_changes_when_source_coverage_changes():
    intervals = [[]]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=page([segment()], intervals=intervals[0]))
        )
    ) as client:
        first = await query(provider(client, max_pages=1, requests_per_second=1000))
        intervals[0] = [{"thread": {"uid": "not-exact-departures"}}]
        second = await query(provider(client, max_pages=1, requests_per_second=1000))
    assert first.departures == second.departures
    assert first.content_hash != second.content_hash


@pytest.mark.asyncio
async def test_retry_after_cools_down_other_queries_using_the_same_credentials():
    first_started = asyncio.Event()
    requests = []

    def handle(request):
        requests.append((request.url.params["from"], time.monotonic()))
        if len(requests) == 1:
            first_started.set()
            return httpx.Response(429, headers={"Retry-After": "0.1"})
        return httpx.Response(200, json=page())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = provider(client, requests_per_second=1000, max_concurrency=2, max_pages=1)
        first = asyncio.create_task(query(adapter))
        await first_started.wait()
        await asyncio.gather(
            first,
            adapter.intercity_departures(
                "c2", "c1", START, origin_timezone="UTC", transport_types=("train",)
            ),
        )
    assert min(at for code, at in requests if code == "c2") - requests[0][1] >= 0.09


@pytest.mark.asyncio
async def test_http_exception_does_not_expose_credentials_in_traceback():
    import traceback

    def handle(request):
        raise httpx.ConnectError(SECRET, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(module().RaspError) as error:
            await query(provider(client))
    assert SECRET not in "".join(traceback.format_exception(error.value))
    assert error.value.code == "upstream_error"


@pytest.mark.asyncio
async def test_legacy_host_requires_explicit_selection_and_response_urls_are_inert():
    urls = []

    def handle(request):
        urls.append(str(request.url))
        row = segment()
        row["thread"]["thread_method_link"] = "https://evil.example/?key=" + SECRET
        return httpx.Response(200, json=page([row]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await query(provider(client, host="api.rasp.yandex.net", max_pages=1))
    assert len(urls) == 1 and urls[0].startswith("https://api.rasp.yandex.net/v3.0/search/")
    assert SECRET not in result.model_dump_json()


@pytest.mark.asyncio
async def test_multiple_transport_filters_do_not_assume_undocumented_search_comma_syntax():
    requests = []

    def handle(request):
        requests.append(request)
        rows = [
            segment(),
            segment(uid="plane-1", thread={"uid": "plane-1", "transport_type": "plane"}),
            segment(uid="bus-1", thread={"uid": "bus-1", "transport_type": "bus"}),
        ]
        return httpx.Response(200, json=page(rows))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await provider(client, max_pages=1).intercity_departures(
            "c1", "c2", START, origin_timezone="UTC", transport_types=("train", "plane")
        )
    assert "transport_types" not in requests[0].url.params
    assert {row.transport_type for row in result.departures} == {"train", "plane"}
    assert result.errors == []
