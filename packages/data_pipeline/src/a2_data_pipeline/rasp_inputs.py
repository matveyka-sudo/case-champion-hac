"""Hash-bound, private, expiring direct-rail observations. Makes no HTTP calls.

API thread UID is not automatically a confirmed physical execution. Each used
row requires a separately reviewed physical identity receipt. Cached episodes
are immutable until expiry; they are never public dataset publications.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from a2_contracts.v2.dataset import AcceptedData
from a2_engine import (
    PhysicalDeparture,
    PhysicalService,
    Timetable,
    TimetableEvent,
    TimetableRun,
    TimetableScope,
)
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, TypeAdapter

from .intercity_nodes import load_nodes
from .paths import data_dir
from .rasp_bindings import load_bindings
from .runtime_inputs import _publish_directory
from .territories import load_directory

SOURCE = "https://api.rasp.yandex.net/v3.0/search/"
ATTRIBUTION = "Данные предоставлены сервисом Яндекс.Расписания"
RULES = "rasp-direct-rail-cache-v1"


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class PrivateCachePolicy(Model):
    schema_version: Literal["waydex-private-source-validity/1"]
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    usage_scope: Literal["temporary_provider_service_cache"]
    authorization_ref: str = Field(min_length=1)
    purge_policy_ref: str = Field(min_length=1)

    def assert_valid(self, *, now: datetime) -> None:
        """Run at job admission, publication and every cache reuse, even if frozen."""
        if now.tzinfo is None or not self.observed_at <= now < self.expires_at:
            raise ValueError("PRIVATE_SOURCE_VALIDITY_EXPIRED_OR_NOT_YET_VALID")


class FileReceipt(Model):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=1, le=2 * 1024 * 1024)


class PageReceipt(FileReceipt):
    http_status: Literal[200]
    requested_date: date
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    fetched_at: AwareDatetime
    reservation_id: str = Field(min_length=1)
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ObservationEnvelope(Model):
    schema_version: Literal["waydex-rasp-observation/1"]
    source_url: Literal[
        "https://api.rasp.yandex.net/v3.0/search/", "https://api.rasp.yandex-net.ru/v3.0/search/"
    ]
    origin_node_id: str
    destination_node_id: str
    departure_at: AwareDatetime
    window_end_at: AwareDatetime
    origin_timezone: str
    transport_type: Literal["train"]
    result_timezone: Literal["UTC"]
    transfers: StrictBool
    retrieved_at: AwareDatetime
    expires_at: AwareDatetime
    pages: tuple[PageReceipt, ...] = Field(min_length=1, max_length=32)
    usage_authorization: FileReceipt
    quota_authorization: FileReceipt
    physical_identities: FileReceipt
    copyright: FileReceipt
    synthetic: StrictBool = False


@dataclass(frozen=True)
class Station:
    node_id: str
    territory_id: str
    name: str
    territory_name: str
    timezone: str
    lon: float
    lat: float
    provider_code: str


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def request_fingerprint(
    source_url: str, from_code: str, to_code: str, day: date, offset: int, limit: int
) -> str:
    """Same public v1 recipe as BACKEND; credentials are never input to this hash."""
    url = urlsplit(source_url)
    params = {
        "from": from_code,
        "to": to_code,
        "format": "json",
        "lang": "ru_RU",
        "system": "yandex",
        "date": day.isoformat(),
        "result_timezone": "UTC",
        "transfers": "false",
        "offset": str(offset),
        "limit": str(limit),
        "transport_types": "train",
    }
    return _digest(
        {
            "schema_version": "waydex-rasp-wire-request/1",
            "method": "GET",
            "host": url.hostname,
            "path": url.path,
            "params": dict(sorted(params.items())),
        }
    )


def service_seconds(value: datetime, day: date, timezone: str) -> int:
    """Wall-clock service seconds, preserving >24h; reject ambiguous lossy folds."""
    zone = ZoneInfo(timezone)
    seconds = int(
        (
            value.astimezone(zone).replace(tzinfo=None) - datetime.combine(day, time())
        ).total_seconds()
    )
    if seconds < 0 or (datetime.combine(day, time(), zone) + timedelta(seconds=seconds)).astimezone(
        UTC
    ) != value.astimezone(UTC):
        raise ValueError("UNREPRESENTABLE_LOCAL_SERVICE_CLOCK")
    return seconds


def _json(raw: bytes) -> Any:
    def finite(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("NONFINITE_JSON")
        return result

    def unique(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    return json.loads(
        raw,
        parse_float=finite,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")),
    )


def _read(root: Path, ref: FileReceipt) -> Any:
    path = (root / ref.path).resolve()
    if Path(ref.path).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("PRIVATE_REFERENCE_OUTSIDE_ROOT")
    with path.open("rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) != ref.size_bytes or hashlib.sha256(raw).hexdigest() != ref.sha256:
        raise ValueError("PRIVATE_SOURCE_HASH_OR_SIZE_MISMATCH")
    return _json(raw)


def _stations(ids: tuple[str, str]) -> tuple[list[Station], dict[str, str]]:
    pinned = {
        "directories/russia-intercity-nodes-2026-11-09.2/nodes.json": "9f5b57eca5ddaee82efa8300cb01c0dffa1f81940759181dae19f58c1bc6cbdf",
        "directories/russia-territories-2026-11-09.1/manifest.json": "c168dc5104a8babccacb921593fb8c8a99eb691b369da3d78b11e4f17476bd6b",
        "processed/yandex_rasp/rasp-bindings-2026-11-09.1/manifest.json": "bc12decf35208b4c68424c9bd9a9259a0ff521104a5f06c2c68a1bf1611c54b5",
    }
    if any(
        hashlib.sha256((data_dir() / name).read_bytes()).hexdigest() != sha
        for name, sha in pinned.items()
    ):
        raise ValueError("ACCEPTED_STATION_DIRECTORY_OR_BINDINGS_CHANGED")
    nodes = {n.node_id: n for n in load_nodes("russia-intercity-nodes-2026-11-09.2")}
    bindings = {n.node_id: n for n in load_bindings("rasp-bindings-2026-11-09.1")[0]}
    places = {t.territory_id: t for t in load_directory("russia-territories-2026-11-09.1")}
    rows = []
    for key in ids:
        node, binding = nodes[key], bindings[key]
        if (
            node.kind != "rail_station"
            or binding.basis != "esr_code"
            or len(node.territory_ids) != 1
            or not node.esr_codes
            or re.fullmatch(r"s[0-9]+", binding.provider_code) is None
        ):
            raise ValueError("EXACT_ESR_RAIL_STATION_BINDING_REQUIRED")
        territory = places[node.territory_ids[0]]
        rows.append(
            Station(
                key,
                territory.territory_id,
                node.name,
                territory.name,
                territory.timezone,
                node.lon,
                node.lat,
                binding.provider_code,
            )
        )
    paths = {
        "nodes": "directories/russia-intercity-nodes-2026-11-09.2/nodes.json",
        "territories": "directories/russia-territories-2026-11-09.1/territories.json",
        "bindings": "processed/yandex_rasp/rasp-bindings-2026-11-09.1/manifest.json",
    }
    return rows, {
        k: hashlib.sha256((data_dir() / v).read_bytes()).hexdigest() for k, v in paths.items()
    }


@dataclass(frozen=True)
class NormalizedRasp:
    accepted_data: AcceptedData
    timetable: Timetable
    report: dict[str, Any]
    copyright: dict[str, Any]
    physical_services: tuple[PhysicalService, ...] = ()

    @property
    def cache_policy(self) -> PrivateCachePolicy:
        return PrivateCachePolicy.model_validate(self.report["cache_policy"])


def normalize_rasp(envelope_path: Path, expected_sha256: str, *, now: datetime) -> NormalizedRasp:
    with envelope_path.open("rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("OBSERVATION_ENVELOPE_HASH_OR_SIZE_MISMATCH")
    envelope = ObservationEnvelope.model_validate(_json(raw))
    root = envelope_path.parent
    copyright = _read(root, envelope.copyright)
    notice = copyright.get("copyright", {})
    if (
        notice.get("url") not in {"http://rasp.yandex.ru/", "https://rasp.yandex.ru/"}
        or not isinstance(notice.get("text"), str)
        or not notice["text"].strip()
        or not any(
            isinstance(notice.get(k), str) and notice[k].strip()
            for k in ("logo_vm", "logo_vd", "logo_hy", "logo_hd", "logo_vy", "logo_hm")
        )
    ):
        raise ValueError("COPYRIGHT_BANNER_TEXT_AND_LINK_CAPTURE_REQUIRED")
    if now.tzinfo is None or not envelope.retrieved_at <= now < envelope.expires_at:
        raise ValueError("PRIVATE_CACHE_EXPIRED_OR_NOT_YET_VALID")
    if (
        not envelope.departure_at
        < envelope.window_end_at
        <= envelope.departure_at + timedelta(days=7)
    ):
        raise ValueError("BOUNDED_DEPARTURE_WINDOW_REQUIRED")
    stations, lineage = _stations((envelope.origin_node_id, envelope.destination_node_id))
    origin, destination = stations
    if (
        origin.territory_id == destination.territory_id
        or origin.provider_code == destination.provider_code
    ):
        raise ValueError("DISTINCT_INTERCITY_STATIONS_REQUIRED")
    if envelope.origin_timezone != origin.timezone:
        raise ValueError("ORIGIN_IANA_TIMEZONE_MISMATCH")
    if envelope.transfers is not False:
        raise ValueError("DIRECT_RAIL_QUERY_REQUIRED")
    usage = _read(root, envelope.usage_authorization)
    if (
        usage.get("schema_version") != "waydex-rasp-cache-usage/1"
        or usage.get("temporary_service_cache_authorized") is not True
        or not usage.get("authorization_ref")
        or not usage.get("approved_by")
        or not usage.get("purge_policy_ref")
        or usage.get("attribution") != ATTRIBUTION
        or usage.get("attribution_url") != "https://rasp.yandex.ru/"
        or usage.get("desktop_copyright_banner_required") is not True
        or datetime.fromisoformat(usage["expires_at"]) != envelope.expires_at
    ):
        raise ValueError("REVIEWED_TEMPORARY_CACHE_USAGE_AUTHORIZATION_REQUIRED")
    quota = _read(root, envelope.quota_authorization)
    if (
        quota.get("schema_version") != "waydex-rasp-quota-export/1"
        or quota.get("hard_daily_limit") != 500
        or not quota.get("ledger_scope_id")
        or type(quota.get("allowance")) is not int
        or not 1 <= quota["allowance"] <= 500
        or type(quota.get("already_used")) is not int
        or not 0 <= quota["already_used"] <= quota["allowance"]
        or not quota.get("baseline_ref")
        or not quota.get("quota_timezone")
        or quota.get("exclusive_usage_attested") is not True
        or quota.get("blocked") is not False
    ):
        raise ValueError("SHARED_500_DAILY_QUOTA_RECEIPT_REQUIRED")
    reservations = {r["reservation_id"]: r for r in quota["reservations"]}
    if len(reservations) != len(quota["reservations"]):
        raise ValueError("DUPLICATE_QUOTA_RESERVATION")
    identities = _read(root, envelope.physical_identities)
    if identities.get("schema_version") != "waydex-rasp-rail-identities/1":
        raise ValueError("REVIEWED_PHYSICAL_EXECUTION_IDENTITIES_REQUIRED")
    physical = {(r["thread_uid"], r["service_date"]): r for r in identities["records"]}
    if len(physical) != len(identities["records"]):
        raise ValueError("DUPLICATE_PHYSICAL_IDENTITY_RECEIPT")
    snapshot_id = ("test_only:" if envelope.synthetic else "rasp-cache:") + expected_sha256[:24]
    graph = RULES + ":" + _digest(lineage)[:24]
    evidence_id = "rasp-observation:" + expected_sha256[:24]
    records: dict[tuple[str, date], dict[str, Any]] = {}
    pages: dict[date, dict[int, tuple[int, int]]] = {}
    reasons = {
        "DIRECT_RAIL_OBSERVATIONS_ONLY",
        "PROVIDER_NETWORK_COVERAGE_UNKNOWN",
        "REFERENCE_UNAVAILABLE",
        "TRANSFERS_UNKNOWN",
        "TEMPORARY_PRIVATE_CACHE_ONLY",
    }
    skipped: dict[str, int] = {}
    used_reservations: set[str] = set()
    used_counters: set[tuple[str, int]] = set()
    duplicate_rows = 0

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    def stamp(value: str) -> datetime:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.microsecond:
            raise ValueError("AWARE_WHOLE_SECOND_SOURCE_EVENT_REQUIRED")
        return result.astimezone(UTC)

    for ref in envelope.pages:
        expected_request = request_fingerprint(
            envelope.source_url,
            origin.provider_code,
            destination.provider_code,
            ref.requested_date,
            ref.offset,
            ref.limit,
        )
        if ref.request_fingerprint != expected_request:
            raise ValueError("PAGE_REQUEST_FINGERPRINT_MISMATCH")
        reservation = reservations.get(ref.reservation_id)
        if (
            not reservation
            or type(reservation.get("day_reserved_total")) is not int
            or reservation.get("day_reserved_total", 501) > quota["allowance"]
            or reservation.get("day_reserved_total", 0) <= quota["already_used"]
            or reservation.get("reserved_before_send") is not True
            or reservation.get("request_fingerprint") != ref.request_fingerprint
            or ref.reservation_id in used_reservations
        ):
            raise ValueError("PRE_REQUEST_SHARED_QUOTA_RESERVATION_REQUIRED")
        reserved_at = datetime.fromisoformat(reservation["reserved_at"])
        if reserved_at.tzinfo is None or reserved_at > ref.fetched_at:
            raise ValueError("POSTHOC_QUOTA_RESERVATION_FORBIDDEN")
        if (
            reserved_at.astimezone(ZoneInfo(quota["quota_timezone"])).date().isoformat()
            != reservation["quota_day"]
            or quota.get("quota_day") != reservation["quota_day"]
        ):
            raise ValueError("QUOTA_DAY_BINDING_MISMATCH")
        used_reservations.add(ref.reservation_id)
        counter = (reservation["quota_day"], reservation["day_reserved_total"])
        if counter in used_counters:
            raise ValueError("DUPLICATE_DAILY_QUOTA_COUNTER")
        used_counters.add(counter)
        if not envelope.retrieved_at <= ref.fetched_at < envelope.expires_at:
            raise ValueError("PAGE_RETRIEVAL_OUTSIDE_CACHE_EPISODE")
        page = _read(root, ref)
        if (
            page["search"]["from"]["code"],
            page["search"]["to"]["code"],
            page["search"]["date"],
        ) != (origin.provider_code, destination.provider_code, ref.requested_date.isoformat()):
            raise ValueError("RESPONSE_REQUEST_STATION_OR_DATE_MISMATCH")
        first = envelope.departure_at.astimezone(ZoneInfo(origin.timezone)).date()
        last = (
            (envelope.window_end_at - timedelta(microseconds=1))
            .astimezone(ZoneInfo(origin.timezone))
            .date()
        )
        if not first <= ref.requested_date <= last:
            raise ValueError("PAGE_DATE_OUTSIDE_QUERY_WINDOW")
        p = page["pagination"]
        rows = page["segments"]
        if (
            any(type(p[k]) is not int for k in ("offset", "limit", "total"))
            or (p["offset"], p["limit"]) != (ref.offset, ref.limit)
            or not 0 <= len(rows) <= ref.limit
            or p["total"] < ref.offset + len(rows)
        ):
            raise ValueError("INVALID_SOURCE_PAGINATION")
        date_pages = pages.setdefault(ref.requested_date, {})
        if ref.offset in date_pages or (
            date_pages and p["total"] != next(iter(date_pages.values()))[1]
        ):
            raise ValueError("DUPLICATE_PAGE_OR_CHANGED_TOTAL")
        date_pages[ref.offset] = (len(rows), p["total"])
        if page.get("interval_segments"):
            reasons.add("INTERVAL_SERVICES_UNRESOLVED")
        for row in rows:
            if (
                not isinstance(row["thread"].get("uid"), str)
                or not 1 <= len(row["thread"]["uid"]) <= 100
            ):
                raise ValueError("SOURCE_THREAD_UID_INVALID")
            if row.get("has_transfers") is not False or row["thread"]["transport_type"] != "train":
                skip("TRANSFER_OR_NON_RAIL_ROW")
                continue
            if (row["from"]["code"], row["to"]["code"]) != (
                origin.provider_code,
                destination.provider_code,
            ):
                raise ValueError("SEGMENT_STATION_BINDING_MISMATCH")
            departure, arrival = stamp(row["departure"]), stamp(row["arrival"])
            if (
                arrival <= departure
                or type(row["duration"]) not in (int, float)
                or (arrival - departure).total_seconds() != row["duration"]
            ):
                raise ValueError("SOURCE_DURATION_OR_EVENT_ORDER_MISMATCH")
            if departure.astimezone(ZoneInfo(origin.timezone)).date() != ref.requested_date:
                raise ValueError("SOURCE_DEPARTURE_DATE_MISMATCH")
            if not envelope.departure_at <= departure < envelope.window_end_at:
                skip("OUTSIDE_DEPARTURE_WINDOW")
                continue
            identity = physical.get((row["thread"]["uid"], row.get("start_date")))
            if (
                not identity
                or identity.get("confirmed") is not True
                or not identity.get("review_ref")
                or not isinstance(identity.get("physical_trip_id"), str)
                or not identity["physical_trip_id"].strip()
            ):
                skip("PHYSICAL_EXECUTION_IDENTITY_UNCONFIRMED")
                continue
            service_date = date.fromisoformat(identity["service_date"])
            departure_seconds = service_seconds(departure, service_date, origin.timezone)
            arrival_seconds = service_seconds(arrival, service_date, origin.timezone)
            trip_id = "rasp-rail:" + _digest(identity["physical_trip_id"])[:24]
            record: dict[str, Any] = {
                "trip_id": trip_id,
                "service_date": service_date,
                "departure": departure,
                "arrival": arrival,
                "departure_seconds": departure_seconds,
                "arrival_seconds": arrival_seconds,
            }
            key = (trip_id, service_date)
            if key in records and records[key] != record:
                raise ValueError("CONFLICTING_PHYSICAL_EXECUTION_DUPLICATE")
            if key in records:
                duplicate_rows += 1
            records[key] = record
    completed = []
    for day, parts in pages.items():
        offset = 0
        total = next(iter(parts.values()))[1]
        for start, (count, _) in sorted(parts.items()):
            if start != offset:
                break
            offset += count
        if offset == total:
            completed.append(day.isoformat())
    all_days = []
    day = first
    while day <= last:
        all_days.append(day.isoformat())
        day += timedelta(days=1)
    source_refs = [evidence_id]
    evidence = {
        "evidence_id": evidence_id,
        "source_id": "yandex_rasp",
        "source_version": snapshot_id,
        "url": envelope.source_url,
        "retrieved_at": envelope.retrieved_at.isoformat(),
        "valid_period": {"start": first.isoformat(), "end": last.isoformat()},
        "license": "restricted:temporary-authorized-service-cache-only",
        "content_hash": expected_sha256,
        "verified": True,
    }
    catalog_id = "rasp-station-directory:" + _digest(lineage)[:24]
    catalog = {
        **evidence,
        "evidence_id": catalog_id,
        "source_id": "rasp_station_directory",
        "source_version": _digest(lineage),
        "url": "https://www.wikidata.org/",
        "content_hash": _digest(lineage),
        "license": "CC0-1.0; restricted private provider bindings",
    }
    payload: dict[str, Any] = {
        "schema_version": "2",
        "snapshot_id": snapshot_id,
        "source_versions": {"yandex_rasp": snapshot_id, "rasp_station_directory": _digest(lineage)},
        "geographic_scope": [origin.territory_id, destination.territory_id],
        "valid_period": {"start": first.isoformat(), "end": last.isoformat()},
        "timezone": origin.timezone,
        "provenance": {
            "graph_version": graph,
            "geometry_version": _digest(lineage),
            "calendar_version": snapshot_id,
            "population_version": None,
        },
        "qa": {
            "acceptance_state": "accepted",
            "accepted_by": "test_only" if envelope.synthetic else usage["approved_by"],
            "accepted_at": envelope.retrieved_at.isoformat(),
            "checks": [
                "source_hashes",
                "exact_esr_station_binding",
                "dated_events",
                "reviewed_execution_identity",
                "private_expiring_cache",
            ],
        },
        "evidence": [evidence, catalog],
        "registry_evidence": [],
        "territories": [
            {
                "territory_id": s.territory_id,
                "level": "city",
                "name": s.territory_name,
                "timezone": s.timezone,
                "geometry": {
                    "geometry_id": "directory-record:" + s.territory_id,
                    "version": _digest(lineage),
                    "bbox": None,
                    "source_refs": [catalog_id],
                },
                "area_km2": None,
                "source_refs": [catalog_id],
            }
            for s in stations
        ],
        "access_nodes": [
            {
                "node_id": s.node_id,
                "kind": "rail_station",
                "territory_id": s.territory_id,
                "location": {"lon": s.lon, "lat": s.lat},
                "source_refs": [catalog_id],
            }
            for s in stations
        ],
        "scheduled_services": [],
        **{
            k: []
            for k in (
                "population_cells",
                "sample_points",
                "destinations",
                "transfers",
                "freight_sites",
                "freight_nodes",
                "freight_compatibility",
                "pipeline_connections",
                "reference_edges",
                "minimum_transfers",
                "traffic_profiles",
                "category_ownership",
                "coverage_by_mode_date_category",
                "confirmed_absence_scope",
            )
        },
    }
    runs = []
    for record in sorted(records.values(), key=lambda r: (r["service_date"], r["trip_id"])):
        service_id = record["trip_id"] + ":" + record["service_date"].isoformat()
        payload["scheduled_services"].append(
            {
                "service_id": service_id,
                "trip_id": record["trip_id"],
                "mode": "rail",
                "timezone": origin.timezone,
                "service_dates": [record["service_date"].isoformat()],
                "trip_identity_confirmed": True,
                "stop_times": [
                    {
                        "node_id": origin.node_id,
                        "sequence": 0,
                        "arrival_seconds": record["departure_seconds"],
                        "departure_seconds": record["departure_seconds"],
                        "pickup": True,
                        "dropoff": False,
                    },
                    {
                        "node_id": destination.node_id,
                        "sequence": 1,
                        "arrival_seconds": record["arrival_seconds"],
                        "departure_seconds": record["arrival_seconds"],
                        "pickup": False,
                        "dropoff": True,
                    },
                ],
                "source_refs": source_refs,
            }
        )
        runs.append(
            TimetableRun(
                snapshot_id,
                "yandex_rasp",
                record["trip_id"],
                record["service_date"],
                origin.timezone,
                "train",
                (
                    TimetableEvent(
                        0,
                        origin.node_id,
                        record["departure"],
                        record["departure"],
                        True,
                        False,
                        tuple(source_refs),
                    ),
                    TimetableEvent(
                        1,
                        destination.node_id,
                        record["arrival"],
                        record["arrival"],
                        False,
                        True,
                        tuple(source_refs),
                    ),
                ),
                True,
                tuple(source_refs),
            )
        )
    end = max([envelope.window_end_at, *[r["arrival"] for r in records.values()]])
    scope = TimetableScope(
        snapshot_id,
        graph,
        snapshot_id,
        snapshot_id,
        "not-collected",
        "not-collected",
        origin.timezone,
        envelope.departure_at,
        end,
        frozenset(s.node_id for s in stations),
        False,
        False,
        False,
        False,
        False,
        tuple(source_refs),
        tuple(sorted(reasons)),
    )
    report = {
        "schema_version": "waydex-rasp-normalization-report/1",
        "synthetic": envelope.synthetic,
        "envelope_sha256": expected_sha256,
        "lineage_sha256": lineage,
        "source_retrieved_at": envelope.retrieved_at.isoformat(),
        "requested_departure_at": envelope.departure_at.isoformat(),
        "requested_window_end_at": envelope.window_end_at.isoformat(),
        "origin_timezone": envelope.origin_timezone,
        "page_observations": [p.model_dump(mode="json") for p in envelope.pages],
        "cache_expires_at": envelope.expires_at.isoformat(),
        "cache_policy": PrivateCachePolicy(
            schema_version="waydex-private-source-validity/1",
            observed_at=envelope.retrieved_at,
            expires_at=envelope.expires_at,
            usage_scope="temporary_provider_service_cache",
            authorization_ref=usage["authorization_ref"],
            purge_policy_ref=usage["purge_policy_ref"],
        ).model_dump(mode="json"),
        "queried_dates": sorted(d.isoformat() for d in pages),
        "completed_dates": sorted(completed),
        "pagination_complete_for_query": sorted(completed) == all_days,
        "dated_executions": len(runs),
        "duplicate_physical_rows_removed": duplicate_rows,
        "excluded_rows": skipped,
        "calendar_semantics": "only explicitly observed service dates",
        "service_clock": "local_service_midnight_v1",
        "endpoint_semantics": "origin pickup-only departure boundary and destination dropoff-only arrival boundary; coincident boundary timestamps do not assert station dwell",
        "boundary_geometry_available": False,
        "reference_complete": False,
        "minimum_proven": False,
        "index_ready": False,
        "reasons": sorted(reasons),
    }
    physical_services = tuple(
        PhysicalService(
            "physical-rail:" + _digest((r.trip_id, r.service_date.isoformat()))[:24],
            (r.run_id,),
            (
                PhysicalDeparture(
                    0,
                    "physical-departure:"
                    + _digest(
                        (
                            r.trip_id,
                            r.service_date.isoformat(),
                            r.events[0].node_id,
                            r.events[0].departure_at.isoformat(),
                        )
                    )[:24],
                    tuple(source_refs),
                ),
            ),
            tuple(source_refs),
        )
        for r in runs
    )
    return NormalizedRasp(
        AcceptedData.model_validate(payload),
        Timetable(scope, tuple(runs), (), ()),
        report,
        copyright,
        physical_services,
    )


def publish_private(inputs: NormalizedRasp, target: Path, *, now: datetime) -> dict[str, Any]:
    inputs.cache_policy.assert_valid(now=now)
    if (
        inputs.report["synthetic"]
        or inputs.accepted_data.snapshot_id.startswith("test_only:")
        or now.tzinfo is None
        or now >= datetime.fromisoformat(inputs.report["cache_expires_at"])
    ):
        raise ValueError("SYNTHETIC_OR_EXPIRED_CACHE_PUBLICATION_FORBIDDEN")
    if target.resolve().is_relative_to(data_dir().resolve()) or any(
        (p / ".git").exists() for p in target.resolve().parents
    ):
        raise ValueError("PRIVATE_PROVIDER_CACHE_MUST_BE_OUTSIDE_DATA_TREE")
    documents = {
        "accepted-data.json": inputs.accepted_data.model_dump(mode="json"),
        "timetable.json": asdict(inputs.timetable),
        "normalization-report.json": inputs.report,
        "copyright.json": inputs.copyright,
        "physical-services.json": [asdict(s) for s in inputs.physical_services],
    }
    payloads = {
        k: (
            json.dumps(
                v,
                default=lambda x: sorted(x) if isinstance(x, frozenset) else x.isoformat(),
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        for k, v in documents.items()
    }
    manifest = {
        "schema_version": "waydex-private-rasp-cache/1",
        "snapshot_id": inputs.accepted_data.snapshot_id,
        "expires_at": inputs.report["cache_expires_at"],
        "redistribution": "prohibited",
        "cache_policy": inputs.cache_policy.model_dump(mode="json"),
        "files": {
            k: {"sha256": hashlib.sha256(v).hexdigest(), "size_bytes": len(v)}
            for k, v in payloads.items()
        },
    }
    payloads["manifest.json"] = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()

    def verify(path: Path) -> None:
        if {p.name for p in path.iterdir()} != set(payloads) or any(
            (path / k).read_bytes() != v for k, v in payloads.items()
        ):
            raise ValueError("PRIVATE_CACHE_IMMUTABLE")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".rasp-cache-") as folder:
        stage = Path(folder) / "payload"
        stage.mkdir()
        for k, v in payloads.items():
            (stage / k).write_bytes(v)
        _publish_directory(stage, target, verify)
    return manifest


def load_private(target: Path, expected_manifest_sha256: str, *, now: datetime) -> NormalizedRasp:
    """Consumers must perform this wall-clock expiry check on every cache load."""
    with (target / "manifest.json").open("rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_manifest_sha256:
        raise ValueError("PRIVATE_CACHE_MANIFEST_HASH_MISMATCH")
    manifest = _json(raw)
    if (
        manifest.get("schema_version") != "waydex-private-rasp-cache/1"
        or now.tzinfo is None
        or now >= datetime.fromisoformat(manifest["expires_at"])
    ):
        raise ValueError("PRIVATE_CACHE_EXPIRED_OR_INVALID")
    policy = PrivateCachePolicy.model_validate(manifest["cache_policy"])
    policy.assert_valid(now=now)
    docs = {}
    for name in (
        "accepted-data.json",
        "timetable.json",
        "normalization-report.json",
        "copyright.json",
        "physical-services.json",
    ):
        with (target / name).open("rb") as stream:
            content = stream.read(32 * 1024 * 1024 + 1)
        ref = manifest["files"][name]
        if (
            len(content) > 32 * 1024 * 1024
            or len(content) != ref["size_bytes"]
            or hashlib.sha256(content).hexdigest() != ref["sha256"]
        ):
            raise ValueError("PRIVATE_CACHE_DOCUMENT_HASH_OR_SIZE_MISMATCH")
        docs[name] = _json(content)
    accepted = AcceptedData.model_validate(docs["accepted-data.json"])
    report = docs["normalization-report.json"]
    if (
        accepted.snapshot_id != manifest["snapshot_id"]
        or report["synthetic"]
        or report["cache_expires_at"] != manifest["expires_at"]
        or now < datetime.fromisoformat(report["source_retrieved_at"])
        or report["cache_policy"] != manifest["cache_policy"]
    ):
        raise ValueError("PRIVATE_CACHE_BINDING_MISMATCH")
    table = TypeAdapter(Timetable).validate_python(docs["timetable.json"])
    if (
        table.scope.snapshot_id != accepted.snapshot_id
        or table.scope.graph_version != accepted.provenance.graph_version
        or table.scope.calendar_version != accepted.provenance.calendar_version
    ):
        raise ValueError("PRIVATE_CACHE_TIMETABLE_SOURCE_MISMATCH")
    return NormalizedRasp(
        accepted,
        table,
        report,
        docs["copyright.json"],
        TypeAdapter(tuple[PhysicalService, ...]).validate_python(docs["physical-services.json"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--envelope", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.schema:
        print(json.dumps(ObservationEnvelope.model_json_schema(), indent=2))
        return
    if not args.envelope or not args.sha256 or not args.output:
        parser.error("--envelope --sha256 --output required")
    try:
        inputs = normalize_rasp(args.envelope, args.sha256, now=datetime.now(UTC))
        print(json.dumps(publish_private(inputs, args.output, now=datetime.now(UTC)), indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Do not echo private upstream bodies, codes or arbitrary malformed input.
        reason = (
            str(exc)
            if re.fullmatch(r"[A-Z][A-Z0-9_]+", str(exc))
            else "PRIVATE_RASP_INPUT_VALIDATION_FAILED"
        )
        parser.exit(2, reason + "\n")


if __name__ == "__main__":
    main()
