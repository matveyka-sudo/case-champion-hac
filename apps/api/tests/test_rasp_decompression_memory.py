"""Synthetic wire streams prove compressed responses cannot allocate decoded bombs."""

import gzip
import json
import tracemalloc
from datetime import UTC, datetime

import httpx
import pytest
from a2_api.providers.rasp import RaspError, RaspProvider
from rasp_quota_helpers import TestOnlyQuota
from test_rasp_provider import page, segment


class WireStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.read_chunks = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read_chunks += 1
            yield chunk

    async def aclose(self):
        self.closed = True


async def query(provider):
    return await provider.intercity_departures(
        "c1",
        "c2",
        datetime(2026, 11, 10, tzinfo=UTC),
        origin_timezone="UTC",
        transport_types=("train",),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 503])
async def test_single_chunk_gzip_bomb_is_rejected_before_decompression(status):
    # Generate outside tracemalloc: the measured allocation is provider processing,
    # not construction of our explicitly synthetic 16 MiB adversarial fixture.
    compressed = gzip.compress(b"x" * (16 * 1024 * 1024), mtime=0)
    assert len(compressed) < 20_000
    stream = WireStream([compressed])
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            status, headers={"Content-Encoding": "gzip", "Content-Length": "1"}, stream=stream
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = RaspProvider(
            client, api_key="synthetic", max_response_bytes=2048, wire_quota=TestOnlyQuota()
        )
        tracemalloc.start()
        try:
            with pytest.raises(RaspError, match="invalid_response"):
                await query(provider)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    print(f"gzip {status}: compressed={len(compressed)} peak={peak}")
    # Old HTTPX aiter_bytes eagerly allocated roughly 50 MiB before the cap.
    # Two MiB leaves ample margin for HTTP/client overhead without allowing it.
    assert peak < 2 * 1024 * 1024
    assert stream.closed
    assert stream.read_chunks == 0
    assert requests[0].headers["Accept-Encoding"] == "identity"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoding", ["gzip", "GZip", "deflate", "br", "gzip, identity", "", "unknown"]
)
async def test_actual_nonidentity_encoding_is_rejected_even_for_small_body(encoding):
    stream = WireStream([b"must-not-be-read"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Encoding": encoding}, stream=stream
            )
        )
    ) as client:
        with pytest.raises(RaspError, match="invalid_response"):
            await query(RaspProvider(client, api_key="synthetic", wire_quota=TestOnlyQuota()))
    assert stream.closed
    assert stream.read_chunks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", [None, "identity", "IDENTITY"])
async def test_uncompressed_wire_limit_and_control_response(encoding):
    body = json.dumps(page([segment()])).encode()
    headers = {} if encoding is None else {"Content-Encoding": encoding}
    for extra in (0, 1):
        stream = WireStream([body[:20], body[20:] + b" " * extra])
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, headers=headers, stream=stream)
            )
        ) as client:
            provider = RaspProvider(
                client,
                api_key="synthetic",
                max_pages=1,
                max_response_bytes=len(body),
                wire_quota=TestOnlyQuota(),
            )
            if extra:
                with pytest.raises(RaspError, match="invalid_response"):
                    await query(provider)
            else:
                assert len((await query(provider)).departures) == 1
        assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 503])
async def test_single_large_identity_chunk_is_rejected_without_rechunk_buffer(status):
    payload = b"x" * (16 * 1024 * 1024)
    stream = WireStream([payload])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, headers={"Content-Length": "1"}, stream=stream)
        )
    ) as client:
        provider = RaspProvider(
            client, api_key="synthetic", max_response_bytes=2048, wire_quota=TestOnlyQuota()
        )
        tracemalloc.start()
        try:
            with pytest.raises(RaspError, match="invalid_response"):
                await query(provider)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    print(f"identity {status}: peak={peak}")
    assert peak < 2 * 1024 * 1024
    assert stream.closed
