"""Synthetic ML2 mappings and MockTransport only: no live provider calls."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from a2_api.providers.departures import RaspDepartureGateway, RaspPlaceBinding
from a2_api.providers.rasp import RaspProvider
from a2_contracts.v2.dataset import AcceptedData
from pydantic import ValidationError
from test_rasp_provider import page, segment

START = datetime(2026, 11, 10, 8, 30, tzinfo=UTC)


def snapshot():
    path = Path(__file__).resolve().parents[3] / "packages/contracts/fixtures/v2/accepted-data.json"
    raw = json.loads(path.read_text())["payload"]
    second = dict(raw["territories"][0], territory_id="city-2", name="Synthetic destination")
    raw["territories"].append(second)
    raw["geographic_scope"].append("city-2")
    raw["evidence"].append(dict(raw["evidence"][0], evidence_id="mapping-e"))
    return AcceptedData.model_validate(raw)


def bindings(saved):
    return [
        RaspPlaceBinding(
            territory_id=territory.territory_id,
            snapshot_id=saved.snapshot_id,
            provider_code="c1" if territory.territory_id == "city-1" else "c2",
            timezone=territory.timezone,
            evidence_id="mapping-e",
        )
        for territory in saved.territories
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_approved_mapping_queries_fixed_provider_and_returns_separate_partial_observation(
    empty,
):
    saved = snapshot()
    original = saved.model_dump_json()
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=page([] if empty else [segment()], request=request))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = RaspProvider(client, api_key="synthetic", requests_per_second=1000)
        gateway = RaspDepartureGateway(provider, bindings(saved), transport_types=("train",))
        observed = await gateway.get_departures(
            "city-1", "city-2", START, snapshots=[saved], source_ids=frozenset({"source-1"})
        )
    assert observed.status == "available" and observed.source == "yandex_rasp"
    assert observed.accepted_snapshot is False
    assert observed.coverage == observed.completeness == "partial"
    assert observed.query.departure_at == START
    assert observed.query.window_end == START + timedelta(days=7)
    assert observed.query.origin_timezone == saved.territories[0].timezone
    assert observed.snapshot_ids == [saved.snapshot_id]
    assert observed.evidence_ids == ["mapping-e"] and observed.source_ids == ["source-1"]
    assert observed.fetched_at < observed.expires_at
    assert observed.content_hash and observed.cache_key
    assert all(request.url.params["from"] == "c1" for request in requests)
    assert all(request.url.params["to"] == "c2" for request in requests)
    assert all(request.url.host == "api.rasp.yandex-net.ru" for request in requests)
    assert len(observed.departures) == (0 if empty else 1)
    if empty:
        assert "EMPTY_DIRECT_RESPONSE_NOT_NO_ROUTE" in observed.reason_codes
    assert saved.model_dump_json() == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("missing", "PLACE_MAPPING_MISSING"),
        ("ambiguous", "PLACE_MAPPING_AMBIGUOUS"),
        ("snapshot", "MAPPING_SNAPSHOT_NOT_AUTHORIZED"),
        ("scope", "PLACE_OUTSIDE_ACCEPTED_SCOPE"),
        ("unverified", "MAPPING_EVIDENCE_UNVERIFIED"),
        ("evidence_missing", "MAPPING_EVIDENCE_UNVERIFIED"),
        ("source", "MAPPING_SOURCE_NOT_AUTHORIZED"),
        ("timezone", "MAPPING_TIMEZONE_MISMATCH"),
        ("period", "DATE_OUTSIDE_ACCEPTED_COVERAGE"),
        ("evidence_period", "DATE_OUTSIDE_ACCEPTED_COVERAGE"),
        ("naive", "DEPARTURE_TIME_REQUIRES_TIMEZONE"),
        ("collision", "PLACE_MAPPING_COLLISION"),
        ("overflow", "DEPARTURE_TIME_OUT_OF_RANGE"),
    ],
)
async def test_unapproved_mapping_never_performs_http(change, reason):
    saved = snapshot()
    mapped = bindings(saved)
    sources = frozenset({"source-1"})
    snapshots = [saved]
    departure = START
    if change == "missing":
        mapped = mapped[1:]
    elif change == "ambiguous":
        mapped.append(mapped[0].model_copy(update={"provider_code": "c3"}))
    elif change == "snapshot":
        snapshots = []
    elif change == "scope":
        saved.geographic_scope.remove("city-1")
    elif change == "unverified":
        saved.evidence[-1].verified = False
    elif change == "evidence_missing":
        mapped[0] = mapped[0].model_copy(update={"evidence_id": "absent"})
    elif change == "source":
        sources = frozenset()
    elif change == "timezone":
        mapped[0] = mapped[0].model_copy(update={"timezone": "UTC"})
    elif change == "period":
        departure = START + timedelta(days=40)
    elif change == "evidence_period":
        saved.evidence[-1].valid_period.end = START.date() - timedelta(days=1)
    elif change == "naive":
        departure = START.replace(tzinfo=None)
    elif change == "collision":
        mapped[1] = mapped[1].model_copy(update={"provider_code": mapped[0].provider_code})
    elif change == "overflow":
        departure = datetime.max.replace(tzinfo=UTC)

    def no_http(request):
        pytest.fail("Unapproved mapping performed HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_http)) as client:
        gateway = RaspDepartureGateway(RaspProvider(client, api_key="synthetic"), mapped)
        observed = await gateway.get_departures(
            "city-1", "city-2", departure, snapshots=snapshots, source_ids=sources
        )
    assert observed.status == "unknown" and observed.reason_codes == [reason]
    assert observed.departures == [] and observed.fetched_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["disabled", "timeout", "quota", "upstream"])
async def test_provider_errors_are_typed_and_safe(failure):
    saved = snapshot()

    def handle(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("secret upstream message", request=request)
        if failure == "upstream":
            return httpx.Response(503, headers={"Retry-After": "20"}, text="secret upstream body")
        return httpx.Response(200, json=page())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = RaspProvider(
            client,
            api_key=None if failure == "disabled" else "synthetic",
            request_quota=1,
            requests_per_second=1000,
        )
        observed = await RaspDepartureGateway(provider, bindings(saved)).get_departures(
            "city-1", "city-2", START, snapshots=[saved], source_ids=frozenset({"source-1"})
        )
    expected = {
        "disabled": "disabled",
        "timeout": "timeout",
        "quota": "quota_exceeded",
        "upstream": "provider_unavailable",
    }[failure]
    assert observed.errors[0].code == expected
    assert "secret" not in observed.model_dump_json()
    assert observed.accepted_snapshot is False and observed.completeness == "partial"
    if failure != "quota":
        assert observed.status == "unknown"


def test_provider_codes_and_timezones_are_validated_as_trusted_configuration():
    saved = snapshot()
    raw = bindings(saved)[0].model_dump()
    for changes in ({"provider_code": "https://attacker.test"}, {"timezone": "not/a/zone"}):
        with pytest.raises(ValidationError):
            RaspPlaceBinding.model_validate(raw | changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reverse", "conflict"])
async def test_rejected_provider_rows_never_reach_observation_or_cache(failure):
    saved = snapshot()
    place_bindings = [
        binding.model_copy(
            update={"provider_code": "s1" if binding.territory_id == "city-1" else "s2"}
        )
        for binding in bindings(saved)
    ]
    calls = []

    def handle(request):
        calls.append(request)
        rows = [segment()]
        if failure == "reverse":
            rows[0]["from"], rows[0]["to"] = rows[0]["to"], rows[0]["from"]
        else:
            rows.append(segment(arrival="2026-11-10T10:00:00Z", duration=3600))
        return httpx.Response(200, json=page(rows, request=request))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        gateway = RaspDepartureGateway(
            RaspProvider(client, api_key="synthetic", requests_per_second=1000), place_bindings
        )
        for _ in range(2):
            observation = await gateway.get_departures(
                "city-1", "city-2", START, snapshots=[saved], source_ids=frozenset({"source-1"})
            )
            assert observation.status == "unknown"
            assert observation.departures == []
            assert observation.cache_key is None
    assert len(calls) == 2
