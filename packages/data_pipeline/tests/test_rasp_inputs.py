"""Synthetic private cache fixtures only; no provider requests or accepted artifacts."""

import copy
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from a2_data_pipeline import rasp_inputs as module

NOW = datetime(2026, 11, 10, 19, tzinfo=UTC)


def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Any]]:
    stations = [
        module.Station(
            "test-origin",
            "test-city-a",
            "Synthetic A",
            "Synthetic city A",
            "Europe/Moscow",
            37,
            55,
            "s100",
        ),
        module.Station(
            "test-dest",
            "test-city-b",
            "Synthetic B",
            "Synthetic city B",
            "Europe/Moscow",
            38,
            54,
            "s200",
        ),
    ]
    monkeypatch.setattr(module, "_stations", lambda _: (stations, {"synthetic": "a" * 64}))

    def write(name: str, value: Any) -> dict[str, Any]:
        raw = json.dumps(value).encode()
        (tmp_path / name).write_bytes(raw)
        return {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}

    row = {
        "thread": {"uid": "synthetic-thread", "transport_type": "train"},
        "from": {"code": "s100"},
        "to": {"code": "s200"},
        "departure": "2026-11-11T01:00:00+03:00",
        "arrival": "2026-11-12T01:00:00+03:00",
        "duration": 86400,
        "start_date": "2026-11-10",
        "has_transfers": False,
    }
    page = {
        "search": {"from": {"code": "s100"}, "to": {"code": "s200"}, "date": "2026-11-11"},
        "pagination": {"offset": 0, "limit": 100, "total": 1},
        "segments": [row],
        "interval_segments": [],
    }
    envelope = {
        "schema_version": "waydex-rasp-observation/1",
        "source_url": module.SOURCE,
        "origin_node_id": "test-origin",
        "destination_node_id": "test-dest",
        "departure_at": "2026-11-10T21:00:00Z",
        "window_end_at": "2026-11-11T21:00:00Z",
        "origin_timezone": "Europe/Moscow",
        "transport_type": "train",
        "result_timezone": "UTC",
        "transfers": False,
        "retrieved_at": "2026-11-10T18:00:00Z",
        "expires_at": "2026-11-10T20:00:00+00:00",
        "synthetic": True,
        "pages": [
            {
                **write("page.json", page),
                "requested_date": "2026-11-11",
                "offset": 0,
                "limit": 100,
                "fetched_at": "2026-11-10T18:01:00Z",
                "reservation_id": "synthetic-reservation",
                "http_status": 200,
                "request_fingerprint": module.request_fingerprint(
                    module.SOURCE, "s100", "s200", date(2026, 11, 11), 0, 100
                ),
            }
        ],
        "usage_authorization": write(
            "usage.json",
            {
                "schema_version": "waydex-rasp-cache-usage/1",
                "temporary_service_cache_authorized": True,
                "authorization_ref": "TEST_ONLY_USAGE_REVIEW",
                "approved_by": "test_only",
                "purge_policy_ref": "TEST_ONLY_PURGE_ON_EXPIRY",
                "attribution": module.ATTRIBUTION,
                "attribution_url": "https://rasp.yandex.ru/",
                "desktop_copyright_banner_required": True,
                "expires_at": "2026-11-10T20:00:00+00:00",
            },
        ),
        "quota_authorization": write(
            "quota.json",
            {
                "schema_version": "waydex-rasp-quota-export/1",
                "hard_daily_limit": 500,
                "allowance": 500,
                "already_used": 0,
                "baseline_ref": "TEST_ONLY_BASELINE",
                "quota_timezone": "Europe/Moscow",
                "quota_day": "2026-11-10",
                "exclusive_usage_attested": True,
                "blocked": False,
                "ledger_scope_id": "TEST_ONLY_LEDGER",
                "reservations": [
                    {
                        "reservation_id": "synthetic-reservation",
                        "request_fingerprint": module.request_fingerprint(
                            module.SOURCE, "s100", "s200", date(2026, 11, 11), 0, 100
                        ),
                        "quota_day": "2026-11-10",
                        "reserved_at": "2026-11-10T18:00:30Z",
                        "day_reserved_total": 1,
                        "reserved_before_send": True,
                    }
                ],
            },
        ),
        "physical_identities": write(
            "identities.json",
            {
                "schema_version": "waydex-rasp-rail-identities/1",
                "records": [
                    {
                        "thread_uid": "synthetic-thread",
                        "service_date": "2026-11-10",
                        "physical_trip_id": "TEST_ONLY_PHYSICAL_TRAIN",
                        "confirmed": True,
                        "review_ref": "TEST_ONLY_IDENTITY_REVIEW",
                    }
                ],
            },
        ),
    }
    envelope["copyright"] = write(
        "copyright.json",
        {
            "copyright": {
                "url": "https://rasp.yandex.ru/",
                "text": "TEST_ONLY_NOTICE",
                "logo_vm": "TEST_ONLY_BANNER_OPAQUE_HTML",
            }
        },
    )
    path = tmp_path / "envelope.json"
    path.write_text(json.dumps(envelope))
    return path, envelope


def normalize(path: Path, envelope: dict[str, Any]) -> module.NormalizedRasp:
    path.write_text(json.dumps(envelope))
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    return module.normalize_rasp(path, checksum, now=NOW)


def replace_document(path: Path, envelope: dict[str, Any], key: str, value: Any) -> None:
    receipt = envelope[key] if key != "page" else envelope["pages"][0]
    raw = json.dumps(value).encode()
    (path.parent / receipt["path"]).write_bytes(raw)
    receipt.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))


def test_dated_source_over24h_boundary_clocks_and_unknowns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, envelope = fixture(tmp_path, monkeypatch)
    out = normalize(path, envelope)
    assert out.report["synthetic"] is True
    assert len(out.timetable.runs) == len(out.accepted_data.scheduled_services) == 1
    service = out.accepted_data.scheduled_services[0]
    assert service.stop_times[0].departure_seconds == 25 * 3600
    assert service.stop_times[1].arrival_seconds == 49 * 3600
    assert service.service_dates[0].isoformat() == "2026-11-10"
    assert not out.timetable.scope.schedule_complete
    assert not out.timetable.scope.calendar_complete
    assert not out.timetable.scope.footpaths_complete
    assert not out.timetable.scope.transfers_complete
    assert not out.timetable.scope.continuations_complete
    assert out.report["pagination_complete_for_query"] is True
    assert not out.accepted_data.reference_edges and not out.accepted_data.confirmed_absence_scope
    assert out.report["minimum_proven"] is out.report["index_ready"] is False
    with pytest.raises(ValueError, match="SYNTHETIC"):
        module.publish_private(out, tmp_path / "never-published", now=NOW)
    assert not (tmp_path / "never-published").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "expired",
        "unaware",
        "timezone",
        "window",
        "quota",
        "usage",
        "hash",
        "station",
        "duration",
        "duplicate_json",
        "identity",
    ],
)
def test_reject_source_authority_and_binding_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    path, envelope = fixture(tmp_path, monkeypatch)
    if mutation == "expired":
        envelope["expires_at"] = "2026-11-10T18:30:00Z"
    elif mutation == "unaware":
        envelope["retrieved_at"] = "2026-11-10T18:00:00"
    elif mutation == "timezone":
        envelope["origin_timezone"] = "UTC"
    elif mutation == "window":
        envelope["window_end_at"] = "2027-01-01T00:00:00Z"
    elif mutation in {"quota", "usage"}:
        key = mutation + "_authorization"
        value = json.loads((tmp_path / envelope[key]["path"]).read_bytes())
        if mutation == "quota":
            value["reservations"][0]["day_reserved_total"] = 501
        else:
            value["temporary_service_cache_authorized"] = False
        replace_document(path, envelope, key, value)
    elif mutation == "hash":
        (tmp_path / "page.json").write_text("{}")
    elif mutation == "duplicate_json":
        raw = b'{"x":1,"x":2}'
        (tmp_path / "page.json").write_bytes(raw)
        envelope["pages"][0].update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    elif mutation == "identity":
        value = json.loads((tmp_path / "identities.json").read_bytes())
        value["records"] *= 2
        replace_document(path, envelope, "physical_identities", value)
    else:
        value = json.loads((tmp_path / "page.json").read_bytes())
        if mutation == "station":
            value["segments"][0]["to"]["code"] = "s999"
        else:
            value["segments"][0]["duration"] = 1
        replace_document(path, envelope, "page", value)
    with pytest.raises((ValueError, KeyError)):
        normalize(path, envelope)


def test_unconfirmed_physical_identity_is_not_confirmed_from_uid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, envelope = fixture(tmp_path, monkeypatch)
    replace_document(
        path,
        envelope,
        "physical_identities",
        {"schema_version": "waydex-rasp-rail-identities/1", "records": []},
    )
    out = normalize(path, envelope)
    assert not out.timetable.runs and not out.accepted_data.scheduled_services
    assert out.report["excluded_rows"] == {"PHYSICAL_EXECUTION_IDENTITY_UNCONFIRMED": 1}


@pytest.mark.parametrize("conflict", [False, True])
def test_reviewed_physical_duplicates_deduplicate_or_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict: bool
) -> None:
    path, envelope = fixture(tmp_path, monkeypatch)
    page = json.loads((tmp_path / "page.json").read_bytes())
    page["segments"].append(copy.deepcopy(page["segments"][0]))
    page["pagination"]["total"] = 2
    if conflict:
        page["segments"][1]["arrival"] = "2026-11-12T02:00:00+03:00"
        page["segments"][1]["duration"] += 3600
    replace_document(path, envelope, "page", page)
    if conflict:
        with pytest.raises(ValueError, match="CONFLICTING_PHYSICAL"):
            normalize(path, envelope)
    else:
        assert len(normalize(path, envelope).timetable.runs) == 1


def test_incomplete_pagination_and_empty_observation_never_prove_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, envelope = fixture(tmp_path, monkeypatch)
    page = json.loads((tmp_path / "page.json").read_bytes())
    page["pagination"]["total"] = 20
    replace_document(path, envelope, "page", page)
    out = normalize(path, envelope)
    assert out.report["pagination_complete_for_query"] is False
    page["segments"] = []
    page["pagination"]["total"] = 0
    replace_document(path, envelope, "page", page)
    out = normalize(path, envelope)
    assert not out.timetable.runs and not out.accepted_data.confirmed_absence_scope


def test_directory_candidate_has_real_esr_bindings_without_codes_in_report() -> None:
    # This read-only check uses only public directory records. Provider caches
    # are not required in CI; the production resolver additionally verifies them.
    from a2_data_pipeline.intercity_nodes import load_nodes

    nodes = {n.node_id: n for n in load_nodes("russia-intercity-nodes-2026-11-09.2")}
    assert nodes["node-wd-q21844902"].territory_ids == ["moscow"]
    assert nodes["node-wd-q3728238"].territory_ids == ["novorossiysk"]
    assert all(nodes[key].esr_codes for key in ("node-wd-q21844902", "node-wd-q3728238"))


@pytest.mark.parametrize(
    "mutation",
    [
        "fingerprint",
        "posthoc",
        "quota_day",
        "baseline",
        "boolean_count",
        "blocked",
        "copyright",
        "path_escape",
    ],
)
def test_receipt_cannot_retroactively_authorize_or_rebind_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    path, envelope = fixture(tmp_path, monkeypatch)
    if mutation == "fingerprint":
        envelope["pages"][0]["request_fingerprint"] = "f" * 64
    elif mutation == "path_escape":
        envelope["pages"][0]["path"] = "../outside-private-root.json"
    elif mutation == "copyright":
        replace_document(
            path,
            envelope,
            "copyright",
            {"copyright": {"url": "https://unrelated.invalid/", "text": "x", "logo_vm": "x"}},
        )
    else:
        quota = json.loads((tmp_path / "quota.json").read_bytes())
        record = quota["reservations"][0]
        if mutation == "posthoc":
            record["reserved_at"] = "2026-11-10T18:02:00Z"
        elif mutation == "quota_day":
            record["quota_day"] = "2026-11-09"
        elif mutation == "baseline":
            quota["already_used"] = 500
        elif mutation == "boolean_count":
            record["day_reserved_total"] = True
        else:
            quota["blocked"] = True
        replace_document(path, envelope, "quota_authorization", quota)
    with pytest.raises(ValueError):
        normalize(path, envelope)


def test_expired_cache_consumer_refuses_before_reading_any_source_documents(tmp_path: Path) -> None:
    manifest = {
        "schema_version": "waydex-private-rasp-cache/1",
        "expires_at": "2026-11-10T18:00:00Z",
    }
    raw = json.dumps(manifest).encode()
    (tmp_path / "manifest.json").write_bytes(raw)
    with pytest.raises(ValueError, match="EXPIRED"):
        module.load_private(tmp_path, hashlib.sha256(raw).hexdigest(), now=NOW)
    with pytest.raises(ValueError, match="MANIFEST_HASH"):
        module.load_private(tmp_path, "0" * 64, now=NOW)


def test_physical_departure_identity_survives_new_cache_episode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, envelope = fixture(tmp_path, monkeypatch)
    first = normalize(path, envelope)
    envelope["retrieved_at"] = "2026-11-10T18:00:01Z"
    second = normalize(path, envelope)
    assert first.timetable.runs[0].run_id != second.timetable.runs[0].run_id
    assert (
        first.physical_services[0].physical_service_id
        == second.physical_services[0].physical_service_id
    )
    assert (
        first.physical_services[0].departures[0].departure_id
        == second.physical_services[0].departures[0].departure_id
    )


def test_service_clock_roundtrip_rejects_lossy_dst_fold() -> None:
    with pytest.raises(ValueError, match="UNREPRESENTABLE"):
        module.service_seconds(
            datetime.fromisoformat("2026-11-01T01:30:00-05:00"),
            date(2026, 11, 1),
            "America/New_York",
        )
    assert (
        module.service_seconds(
            datetime.fromisoformat("2026-11-01T01:30:00-04:00"),
            date(2026, 11, 1),
            "America/New_York",
        )
        == 5400
    )


def test_internal_envelope_schema_is_the_published_version() -> None:
    from a2_data_pipeline.paths import data_dir

    actual = json.loads(
        (data_dir() / "manifests/schemas/private-rasp-observation-v1.json").read_bytes()
    )
    assert actual == module.ObservationEnvelope.model_json_schema()


@pytest.mark.parametrize("raw", [b'{"value":NaN}', b'{"value":1e9999}', b'{"value":Infinity}'])
def test_nonfinite_source_numbers_are_rejected(raw: bytes) -> None:
    with pytest.raises(ValueError, match="NONFINITE"):
        module._json(raw)


def test_cli_never_echoes_private_malformed_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise KeyError("TEST_ONLY_PRIVATE_VALUE")

    monkeypatch.setattr(module, "normalize_rasp", fail)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rasp_inputs",
            "--envelope",
            "private.json",
            "--sha256",
            "0" * 64,
            "--output",
            "private-output",
        ],
    )
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "TEST_ONLY_PRIVATE_VALUE" not in output.err + output.out
    assert "PRIVATE_RASP_INPUT_VALIDATION_FAILED" in output.err


def test_frozen_input_validity_must_be_rechecked_at_publication_and_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, envelope = fixture(tmp_path, monkeypatch)
    out = normalize(path, envelope)
    policy = out.cache_policy
    policy.assert_valid(now=NOW)
    # The frozen in-memory policy expires independently of filesystem deletion.
    (tmp_path / "envelope.json").unlink()
    with pytest.raises(ValueError, match="PRIVATE_SOURCE_VALIDITY_EXPIRED"):
        policy.assert_valid(now=datetime(2026, 11, 10, 20, tzinfo=UTC))
    assert policy.usage_scope == "temporary_provider_service_cache"
    assert policy.authorization_ref == "TEST_ONLY_USAGE_REVIEW"
