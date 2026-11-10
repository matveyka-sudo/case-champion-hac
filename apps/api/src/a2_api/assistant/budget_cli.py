"""Admin-only aggregate Alice budget diagnostics and explicit ceiling updates."""

import argparse
import asyncio
import json

import asyncpg  # type: ignore[import-untyped]

from a2_api.assistant.budget import load_budget_settings
from a2_api.core.config import RuntimeSettings
from a2_api.db.connection import open_pool
from a2_api.db.repositories.budget import configure_budget, read_budget_status


async def run_command(command: str, pool: asyncpg.Pool) -> dict[str, object]:
    if command == "set-limit":
        settings = load_budget_settings()
        await configure_budget(
            pool,
            limit_amount=settings.a2_alice_budget_limit,
            token_limit=settings.a2_alice_token_limit,
            model_uri=settings.model_uri,
            currency=settings.a2_alice_currency,
            tariff_id=settings.a2_alice_tariff_id,
        )
    elif command != "status":
        raise ValueError("Unsupported budget command")
    snapshot = await read_budget_status(pool)
    if snapshot is None:
        return {"status": "unconfigured"}
    return {
        "status": "blocked" if snapshot.blocked else "configured",
        "currency": snapshot.currency,
        "tariff_id": snapshot.tariff_id,
        "limit_amount": str(snapshot.limit_amount),
        "charged_amount": str(snapshot.charged_amount),
        "held_amount": str(snapshot.held_amount),
        "available_amount": str(snapshot.available_amount),
        "token_limit": snapshot.token_limit,
        "charged_tokens": snapshot.charged_tokens,
        "held_tokens": snapshot.held_tokens,
        "available_tokens": snapshot.available_tokens,
        "reservation_count": snapshot.reservation_count,
    }


async def _execute(command: str) -> dict[str, object]:
    pool = await open_pool(RuntimeSettings(_env_file=".env"))  # type: ignore[call-arg]
    try:
        return await run_command(command, pool)
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "set-limit"])
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(_execute(args.command)), sort_keys=True))
    except Exception:
        parser.exit(1, "Budget command failed; check database, migrations and configuration\n")


if __name__ == "__main__":
    main()
