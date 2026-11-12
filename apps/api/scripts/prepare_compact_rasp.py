"""Prepare bounded, temporary intercity observations outside the analysis worker."""

import argparse
import asyncio
import json
import os
import tempfile
from datetime import UTC, date, datetime, time
from itertools import permutations
from pathlib import Path
from zoneinfo import ZoneInfo

from a2_api.core.rasp_quota import QuotaUnavailable
from a2_api.providers.rasp import RaspError, Transport
from a2_api.providers.rasp_runtime import load_settings, open_rasp_provider
from a2_contracts.common import canonical_hash
from a2_data_pipeline.rasp_bindings import load_bindings
from a2_data_pipeline.runtime_inputs import _publication_lock

CITIES = ("moscow", "kaliningrad", "novorossiysk")
MODES: tuple[Transport, ...] = ("plane", "train", "suburban", "bus", "water", "helicopter")


async def prepare(day: date, cache: Path) -> int:
    settings = load_settings()
    if not settings.live_enabled or settings.api_key is None:
        raise RaspError("disabled")
    codes, places = load_bindings("rasp-bindings-2026-11-09.1")
    bindings = {row.territory_id: row for row in places if row.territory_id in CITIES}
    by_code: dict[str, set[str]] = {}
    for code in codes:
        by_code.setdefault(code.provider_code, set()).add(code.node_id)
    failures = 0
    with _publication_lock(cache / "compact-rasp"):
        now = datetime.now(UTC)
        for path in cache.glob("compact-rasp-*.json"):
            try:
                expires_at = datetime.fromisoformat(json.loads(path.read_bytes())["expires_at"])
            except (OSError, ValueError, KeyError):
                expires_at = now
            if expires_at <= now:
                path.unlink()

        async with open_rasp_provider(settings) as provider:
            for origin, destination in permutations(CITIES, 2):
                if origin not in bindings or destination not in bindings:
                    print(origin, destination, "STATION_BINDING_UNAVAILABLE")
                    failures += 1
                    continue
                left, right = bindings[origin], bindings[destination]
                identity = canonical_hash([origin, destination, day.isoformat(), list(MODES)])
                target = cache / f"compact-rasp-{identity}.json"
                if target.exists():
                    print(origin, destination, "CACHE_HIT")
                    continue
                start = datetime.combine(day, time.min, ZoneInfo(left.timezone))
                try:
                    rows = await provider.intercity_departures(
                        left.provider_code,
                        right.provider_code,
                        start,
                        origin_timezone=left.timezone,
                        transport_types=MODES,
                    )
                except RaspError as exc:
                    print(origin, destination, exc.code)
                    failures += 1
                    continue
                notices = await provider.copyright()
                departures = []
                unresolved = 0
                for row in rows.departures:
                    a = by_code.get(row.from_station.code, set()) & set(left.node_ids)
                    b = by_code.get(row.to_station.code, set()) & set(right.node_ids)
                    if len(a) != 1 or len(b) != 1:
                        unresolved += 1
                        continue
                    departures.append(
                        {
                            **row.model_dump(mode="json"),
                            "origin_node_id": next(iter(a)),
                            "destination_node_id": next(iter(b)),
                        }
                    )
                payload = {
                    **rows.model_dump(mode="json", exclude={"departures"}),
                    "schema_version": "waydex-compact-rasp-capture/1",
                    "origin_id": origin,
                    "destination_id": destination,
                    "departures": departures,
                    "unresolved_station_rows": unresolved,
                    "copyright": notices.model_dump(mode="json"),
                    "limitations": [
                        "DIRECT_PROVIDER_OBSERVATIONS",
                        "PHYSICAL_EXECUTION_REVIEW_REQUIRED",
                    ],
                }
                # Modeled journeys are admitted only after canonical normalization,
                # reviewed physical identity and the private source permission checks.
                with tempfile.NamedTemporaryFile(dir=cache, delete=False) as staging:
                    staged = Path(staging.name)
                    try:
                        staging.write(
                            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
                        )
                        staging.flush()
                        os.replace(staged, target)
                    finally:
                        staged.unlink(missing_ok=True)
                print(origin, destination, len(departures), "mapped rows")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    cache = args.cache.resolve()
    if any((p / ".git").exists() for p in (cache, *cache.parents)):
        raise SystemExit("RASP_CACHE_MUST_BE_OUTSIDE_REPOSITORY")
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    if cache.stat().st_uid != os.getuid() or cache.stat().st_mode & 0o077:
        raise SystemExit("RASP_CACHE_REQUIRES_OWNER_ONLY_ACCESS")
    try:
        failures = asyncio.run(prepare(args.date, cache))
    except QuotaUnavailable:
        raise SystemExit("RASP_CONFIGURATION_OR_SHARED_QUOTA_UNAVAILABLE") from None
    except RaspError as exc:
        raise SystemExit(f"RASP_{exc.code.upper()}") from None
    if failures:
        raise SystemExit("RASP_CAPTURE_PARTIAL")


if __name__ == "__main__":
    main()
