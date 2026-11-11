"""Operator-only explicit daily initialization/status/export; performs no HTTP."""

import argparse
import asyncio
import json
import os
from datetime import date
from pathlib import Path

from a2_api.core.rasp_quota import QuotaUnavailable, credential_fingerprint
from a2_api.db.repositories.rasp_quota import export_reservations, initialize_day, read_status
from a2_api.providers.rasp_runtime import RaspSettings, load_settings, open_quota_pool


async def run_command(settings: RaspSettings, args: argparse.Namespace) -> dict[str, object]:
    async with open_quota_pool(settings) as pool:
        if args.command == "initialize-day":
            if settings.api_key is None or any(
                value is None
                for value in (args.day, args.allowance, args.already_used, args.baseline_ref)
            ):
                raise QuotaUnavailable()
            await initialize_day(
                pool,
                account_id=settings.account_id,
                credential_fingerprint=credential_fingerprint(settings.api_key.get_secret_value()),
                quota_timezone=settings.quota_timezone,
                quota_day=date.fromisoformat(args.day),
                allowance=args.allowance,
                already_used=args.already_used,
                baseline_ref=args.baseline_ref,
                exclusive_usage_attested=args.exclusive_managed_usage_attested,
            )
        elif args.command == "export":
            if args.day is None:
                raise QuotaUnavailable()
            return await export_reservations(
                pool,
                account_id=settings.account_id,
                quota_day=date.fromisoformat(args.day),
                reservation_ids=args.reservation_id,
            )
        elif args.command != "status":
            raise ValueError("Unknown quota command")
        return await read_status(pool, account_id=settings.account_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "initialize-day", "export"])
    parser.add_argument("--day")
    parser.add_argument("--allowance", type=int)
    parser.add_argument("--already-used", type=int)
    parser.add_argument("--baseline-ref")
    parser.add_argument("--exclusive-managed-usage-attested", action="store_true")
    parser.add_argument("--reservation-id", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = asyncio.run(run_command(load_settings(), args))
        encoded = json.dumps(result, sort_keys=True, indent=2) + "\n"
        if args.output is not None:
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as target:
                target.write(encoded)
        else:
            print(encoded, end="")
    except Exception:
        parser.exit(
            1, "Quota command failed; confirm central ledger, explicit baseline and migrations\n"
        )


if __name__ == "__main__":
    main()
