#!/usr/bin/env python3
"""Run a real walk job through Nginx; retain a private session for restart checks."""

import argparse
import http.cookiejar
import json
import time
import uuid
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5190")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument(
        "--existing", action="store_true", help="Verify the saved analysis after restart"
    )
    args = parser.parse_args()
    folder = args.evidence_dir.resolve()
    folder.mkdir(exist_ok=True)
    cookies = http.cookiejar.LWPCookieJar(str(folder / "session.cookies"))
    if args.existing:
        cookies.load(ignore_discard=True)
    client = build_opener(HTTPCookieProcessor(cookies))

    def request(path, body=None):
        headers = {"Origin": args.base_url, "Idempotency-Key": str(uuid.uuid4())}
        if body is not None:
            headers["Content-Type"] = "application/json"
        with client.open(
            Request(
                args.base_url + path,
                data=json.dumps(body).encode() if body is not None else None,
                headers=headers,
            ),
            timeout=30,
        ) as response:
            return json.load(response)

    started = time.monotonic()
    if args.existing:
        accepted = json.loads((folder / "accepted.json").read_text())
    else:
        request("/health")
        capabilities = request("/api/v1/capabilities")
        (folder / "capabilities.json").write_text(json.dumps(capabilities, indent=2))
        accepted = request(
            "/api/v1/analyses",
            {
                "schema_version": "2",
                "analysis_kind": "urban_passenger",
                "city_id": "kaliningrad",
                "passenger_mode": "walk",
                "categories": ["school"],
                "priorities": {"school": "medium"},
                "departure_at": "2026-11-10T09:00:00+02:00",
                "traffic_mode": "static",
            },
        )
        (folder / "accepted.json").write_text(json.dumps(accepted, indent=2))
        cookies.save(ignore_discard=True)
        Path(cookies.filename).chmod(0o600)
    while True:
        job = request("/api/v1/jobs/" + accepted["job_id"])
        if job["status"] == "succeeded":
            break
        if job["status"] in ("failed", "cancelled") or time.monotonic() - started > 90:
            raise RuntimeError("Real walk job did not succeed: " + json.dumps(job))
        time.sleep(0.25)
    result = request("/api/v1/analyses/" + job["analysis_id"])
    if not result["journeys"] or result["status"] != "partial":
        raise RuntimeError("Expected an observed route with explicit partial coverage")
    if any(score["value"] is not None for score in result["scores"]):
        raise RuntimeError("Bounded walking pilot must retain unknown scores")
    (folder / ("restored-result.json" if args.existing else "result.json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    if (
        args.existing
        and result["result_hash"] != json.loads((folder / "result.json").read_text())["result_hash"]
    ):
        raise RuntimeError("Persisted result changed after restart")
    print(
        json.dumps(
            {
                "job_id": job["job_id"],
                "analysis_id": job["analysis_id"],
                "status": job["status"],
                "analysis_status": result["status"],
                "journeys": len(result["journeys"]),
                "result_hash": result["result_hash"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "restored": args.existing,
            }
        )
    )


if __name__ == "__main__":
    main()
