"""Capture dated Yandex search pages with transfers using the shared quota ledger."""

import argparse
import asyncio
import json
import os
from datetime import UTC, date, datetime, timedelta
from itertools import permutations
from pathlib import Path

from a2_api.providers.rasp import RaspError
from a2_api.providers.rasp_runtime import open_rasp_provider
from a2_data_pipeline.rasp_bindings import load_bindings

CITIES = ("moscow", "kaliningrad", "novorossiysk", "krasnodar")
SELECTED_RAIL = {
    ("moscow", "kaliningrad"): ("029Ч", "s2000006", "s9623137"),
    ("kaliningrad", "moscow"): ("030Ч", "s9623137", "s2000006"),
    ("moscow", "krasnodar"): ("104В", "s2000003", "s9613602"),
    ("krasnodar", "moscow"): ("104Ж", "s9613602", "s2000003"),
    ("moscow", "novorossiysk"): ("172М", "s2000003", "s9613022"),
    ("novorossiysk", "moscow"): ("126С", "s9613022", "s2000003"),
}


async def capture_threads(output: Path) -> None:
    async with open_rasp_provider() as provider:
        for path in sorted(output.glob("20??-??-??-*.json")):
            search = json.loads(path.read_text())
            pair = search["origin"], search["destination"]
            if pair not in SELECTED_RAIL:
                continue
            number, start, end = SELECTED_RAIL[pair]
            target = output / f"thread-selected-{path.name}"
            if target.exists():
                continue
            row = next(
                (
                    row
                    for page in search["pages"]
                    for row in page["segments"]
                    if row.get("has_transfers") is False
                    and row["thread"]["transport_type"] == "train"
                    and row["thread"]["number"] == number
                    and row["from"]["code"] == start
                    and row["to"]["code"] == end
                ),
                None,
            )
            if row is None:
                print(path.name, "SELECTED_TRAIN_NOT_FOUND", flush=True)
                continue
            try:
                thread = await provider.dated_thread(
                    row["thread"]["uid"], date.fromisoformat(row["start_date"])
                )
            except RaspError as exc:
                print(path.name, exc.code, flush=True)
                if exc.code in {"quota_exceeded", "quota_unavailable", "unauthorized"}:
                    return
                continue
            staging = target.with_suffix(".tmp")
            staging.write_text(
                json.dumps(
                    {
                        "origin": pair[0],
                        "destination": pair[1],
                        "query": search["date"],
                        "observed_at": datetime.now(UTC).isoformat(),
                        "row": row,
                        "thread": thread,
                    },
                    ensure_ascii=False,
                )
            )
            os.replace(staging, target)
            print(path.name, number, len(thread["stops"]), flush=True)


async def capture(start: date, days: int, output: Path) -> None:
    _, places = load_bindings("rasp-bindings-2026-11-09.1")
    codes = {row.territory_id: row.provider_code for row in places}
    codes["krasnodar"] = "c35"
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    async with open_rasp_provider() as provider:
        for delta in range(days):
            day = start + timedelta(days=delta)
            for left, right in permutations(CITIES, 2):
                if {left, right} == {"novorossiysk", "krasnodar"}:
                    continue
                target = output / f"{day}-{left}-{right}.json"
                if target.exists():
                    continue
                try:
                    pages = await provider.dated_journeys(codes[left], codes[right], day)
                except RaspError as exc:
                    print(day, left, right, exc.code, flush=True)
                    if exc.code in {"quota_exceeded", "quota_unavailable", "unauthorized"}:
                        return
                    continue
                staging = target.with_suffix(".tmp")
                staging.write_text(
                    json.dumps(
                        {
                            "origin": left,
                            "destination": right,
                            "date": day.isoformat(),
                            "observed_at": datetime.now(UTC).isoformat(),
                            "pages": pages,
                        },
                        ensure_ascii=False,
                    )
                )
                os.replace(staging, target)
                print(day, left, right, sum(len(p["segments"]) for p in pages), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--threads", action="store_true")
    parser.add_argument("--days", type=int, choices=range(1, 9), default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.threads:
        asyncio.run(capture_threads(args.output))
    else:
        if args.start is None:
            parser.error("--start is required for search capture")
        asyncio.run(capture(args.start, args.days, args.output))
