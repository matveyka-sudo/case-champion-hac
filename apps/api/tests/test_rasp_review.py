"""Synthetic provider adversarial responses; no live traffic."""

import gzip
import json
from datetime import UTC, datetime

import httpx
import pytest
from a2_api.providers.rasp import RaspError, RaspProvider
from test_rasp_provider import page, segment


async def run_response(body, **options):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    ) as client:
        return await RaspProvider(
            client, api_key="synthetic", max_pages=1, **options
        ).intercity_departures(
            "s1",
            "s2",
            datetime(2026, 11, 10, tzinfo=UTC),
            origin_timezone="UTC",
            transport_types=("train",),
        )


def station_page(rows):
    data = page(rows)
    data["search"].update({"from": {"code": "s1"}, "to": {"code": "s2"}})
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["reverse", "scope", "date"])
async def test_rejects_foreign_scope(case):
    body = station_page([segment()])
    if case == "reverse":
        body["segments"][0]["from"], body["segments"][0]["to"] = (
            body["segments"][0]["to"],
            body["segments"][0]["from"],
        )
    elif case == "scope":
        body["search"]["to"]["code"] = "s3"
    else:
        body["search"]["date"] = "2026-11-11"
    with pytest.raises(RaspError, match="invalid_response"):
        await run_response(body)


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_conflicting_duplicates_are_never_selected(reverse):
    rows = [segment(), segment(arrival="2026-11-10T10:00:00Z", duration=3600)]
    if reverse:
        rows.reverse()
    with pytest.raises(RaspError, match="invalid_response"):
        await run_response(station_page(rows))


@pytest.mark.asyncio
async def test_identical_duplicates_are_deduplicated():
    result = await run_response(station_page([segment(), segment()]))
    assert len(result.departures) == 1


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("compressed,status", [(False, 200), (True, 200), (False, 500)])
async def test_oversized_response_closed_without_body_leak(compressed, status):
    payload = b"synthetic-private-body" * 100
    stream = Chunks([gzip.compress(payload)] if compressed else [payload[:500], payload[500:]])
    headers = {"content-length": "1"}
    if compressed:
        headers["content-encoding"] = "gzip"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(status, headers=headers, stream=stream)
        )
    ) as client:
        with pytest.raises(RaspError) as exc:
            await RaspProvider(
                client, api_key="synthetic", max_response_bytes=512
            ).intercity_departures(
                "s1",
                "s2",
                datetime(2026, 11, 10, tzinfo=UTC),
                origin_timezone="UTC",
                transport_types=("train",),
            )
    assert exc.value.code == "invalid_response"
    assert "private" not in str(exc.value)
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [0, 1])
async def test_decoded_byte_limit_exact_boundary(extra):
    body = json.dumps(station_page([segment()])).encode()
    stream = Chunks([body[:10], body[10:] + b" " * extra])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream))
    ) as client:
        provider = RaspProvider(
            client, api_key="synthetic", max_pages=1, max_response_bytes=len(body)
        )
        if extra:
            with pytest.raises(RaspError, match="invalid_response"):
                await provider.intercity_departures(
                    "s1",
                    "s2",
                    datetime(2026, 11, 10, tzinfo=UTC),
                    origin_timezone="UTC",
                    transport_types=("train",),
                )
        else:
            result = await provider.intercity_departures(
                "s1",
                "s2",
                datetime(2026, 11, 10, tzinfo=UTC),
                origin_timezone="UTC",
                transport_types=("train",),
            )
            assert len(result.departures) == 1
    assert stream.closed


@pytest.mark.asyncio
async def test_conflicting_duplicate_across_pages_is_not_cached():
    calls = []

    def handle(req):
        calls.append(req)
        offset = int(req.url.params["offset"])
        row = segment() if offset == 0 else segment(arrival="2026-11-10T10:00:00Z", duration=3600)
        body = station_page([row])
        body["pagination"].update(total=2, offset=offset, limit=1)
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = RaspProvider(client, api_key="synthetic", page_size=1, requests_per_second=1000)
        for _ in range(2):
            with pytest.raises(RaspError, match="invalid_response"):
                await provider.intercity_departures(
                    "s1",
                    "s2",
                    datetime(2026, 11, 10, tzinfo=UTC),
                    origin_timezone="UTC",
                    transport_types=("train",),
                )
    assert len(calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_read_timeout_and_cancellation_close_response(cancel):
    import asyncio

    entered = asyncio.Event()

    class Slow(Chunks):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"never"

    stream = Slow([])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream))
    ) as client:
        provider = RaspProvider(client, api_key="synthetic", timeout_seconds=0.05)
        task = asyncio.create_task(
            provider.intercity_departures(
                "s1",
                "s2",
                datetime(2026, 11, 10, tzinfo=UTC),
                origin_timezone="UTC",
                transport_types=("train",),
            )
        )
        await entered.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RaspError, match="timeout"):
                await task
    assert stream.closed


@pytest.mark.asyncio
async def test_city_scope_uses_echo_not_literal_station_code_and_local_date():
    requests = []

    def handle(req):
        requests.append(req)
        body = page([segment("2026-11-09T22:00:00Z")], request=req)
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await RaspProvider(client, api_key="synthetic", max_pages=1).intercity_departures(
            "c1",
            "c2",
            datetime(2026, 11, 9, 21, tzinfo=UTC),
            origin_timezone="Europe/Moscow",
            transport_types=("train",),
        )
    assert requests[0].url.params["date"] == "2026-11-10"
    assert result.departures[0].from_station.code == "s1"
