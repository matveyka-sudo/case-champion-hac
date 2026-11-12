"""Read temporary provider observations independently of accepted analysis inputs."""

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import AwareDatetime, BaseModel, ValidationError

from a2_api.core.rasp_quota import QuotaUnavailable
from a2_api.providers.rasp import RaspDeparture
from a2_api.providers.rasp_runtime import load_settings

router = APIRouter()
City = Literal["moscow", "kaliningrad", "novorossiysk"]
_LOGO_PATH = "//yandex.st/rasp/media/apicc/copyright_vert_mono.html"


class PassengerScheduleObservation(BaseModel):
    source: Literal["yandex_rasp"] = "yandex_rasp"
    accepted_snapshot: Literal[False] = False
    fetched_at: AwareDatetime
    expires_at: AwareDatetime
    completed_dates: list[date]
    departures: list[RaspDeparture]
    copyright_banner_url: Literal["https://yandex.st/rasp/media/apicc/copyright_vert_mono.html"] = (
        "https:" + _LOGO_PATH
    )


def read_schedule(
    cache: Path,
    origin: City,
    destination: City,
    now: datetime,
) -> PassengerScheduleObservation | None:
    try:
        stat = cache.stat()
        if not cache.is_dir() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            return None
    except OSError:
        return None
    latest = None
    for path in cache.glob("compact-rasp-*.json"):
        try:
            if path.is_symlink():
                continue
            with path.open("rb") as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                continue
            data = json.loads(raw)
            if (data["origin_id"], data["destination_id"]) != (origin, destination):
                continue
            if f'src="{_LOGO_PATH}"' not in (data["copyright"].get("logo_vm") or ""):
                continue
            rows = [
                {k: v for k, v in row.items() if k not in ("origin_node_id", "destination_node_id")}
                for row in (*data["departures"], *data.get("unmapped_departures", []))
            ]
            candidate = PassengerScheduleObservation(
                fetched_at=data["fetched_at"],
                expires_at=data["expires_at"],
                completed_dates=data["completed_dates"],
                departures=rows,
            )
            if candidate.fetched_at <= now < candidate.expires_at and (
                latest is None or candidate.fetched_at > latest.fetched_at
            ):
                latest = candidate
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            continue
    return latest


@router.get("/passenger-schedules")
def passenger_schedules(origin: City, destination: City) -> list[PassengerScheduleObservation]:
    try:
        settings = load_settings()
    except QuotaUnavailable:
        return []
    if origin == destination or not settings.live_enabled or settings.observation_cache_dir is None:
        return []
    observation = read_schedule(
        settings.observation_cache_dir, origin, destination, datetime.now(UTC)
    )
    return [observation] if observation is not None else []
