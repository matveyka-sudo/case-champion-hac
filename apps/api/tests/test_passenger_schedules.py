import json
from datetime import UTC, datetime

from a2_api.api.v2.passenger_schedules import read_schedule


def test_prepared_schedule_exposes_observations_only_until_expiry(tmp_path):
    path = tmp_path / "compact-rasp-test.json"
    path.write_text(
        json.dumps(
            {
                "origin_id": "moscow",
                "destination_id": "kaliningrad",
                "fetched_at": "2026-11-12T07:00:00Z",
                "expires_at": "2026-11-12T09:00:00Z",
                "completed_dates": ["2026-11-12"],
                "departures": [],
                "unmapped_departures": [
                    {
                        "service_uid": "test-flight",
                        "transport_type": "plane",
                        "from_station": {"code": "s100", "title": "Тестовый аэропорт A"},
                        "to_station": {"code": "s200", "title": "Тестовый аэропорт B"},
                        "departure_at": "2026-11-12T19:00:00Z",
                        "arrival_at": "2026-11-12T21:00:00Z",
                        "duration_seconds": 7200,
                    }
                ],
                "copyright": {
                    "logo_vm": '<iframe src="//yandex.st/rasp/media/apicc/copyright_vert_mono.html"></iframe>'
                },
            }
        )
    )
    now = datetime(2026, 11, 12, 8, tzinfo=UTC)
    result = read_schedule(tmp_path, "moscow", "kaliningrad", now)
    assert result is not None
    assert result.accepted_snapshot is False
    assert result.departures[0].from_station.code == "s100"
    assert read_schedule(tmp_path, "kaliningrad", "moscow", now) is None
    assert read_schedule(tmp_path, "moscow", "kaliningrad", result.expires_at) is None
