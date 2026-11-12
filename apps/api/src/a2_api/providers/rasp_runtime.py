"""Explicit central-ledger wiring; never falls back to a release-local database."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncpg  # type: ignore[import-untyped]
import httpx
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import SettingsConfigDict

from a2_api.core.env_settings import WaydexSettings
from a2_api.core.rasp_quota import QuotaUnavailable
from a2_api.db.repositories.rasp_quota import PostgresRaspQuota
from a2_api.providers.rasp import RaspError, RaspProvider
from a2_api.providers.rasp_capture import CaptureCallback


class RaspSettings(WaydexSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RASP_",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )
    live_enabled: bool = False
    api_key: SecretStr | None = Field(default=None, repr=False)
    quota_database_url: SecretStr = Field(repr=False)
    quota_database_schema: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    account_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,128}$")
    quota_timezone: str
    observation_cache_dir: Path | None = None

    @field_validator("quota_timezone")
    @classmethod
    def iana_zone(cls, value: str) -> str:
        ZoneInfo(value)
        return value


def load_settings() -> RaspSettings:
    try:
        return RaspSettings()  # type: ignore[call-arg] # explicit operator environment
    except (ValidationError, ValueError, KeyError):
        raise QuotaUnavailable() from None


@asynccontextmanager
async def open_quota_pool(settings: RaspSettings) -> AsyncIterator[asyncpg.Pool]:
    """The distinct central URL/schema are mandatory, including for administration."""
    try:
        pool = await asyncpg.create_pool(
            settings.quota_database_url.get_secret_value().replace(
                "postgresql+asyncpg://", "postgresql://", 1
            ),
            min_size=1,
            max_size=5,
            timeout=5,
            command_timeout=5,
            server_settings={
                "search_path": settings.quota_database_schema,
                "application_name": "waydex-rasp-quota",
            },
        )
        if pool is None:
            raise QuotaUnavailable()
    except Exception:
        raise QuotaUnavailable() from None
    try:
        yield pool
    finally:
        await pool.close()


@asynccontextmanager
async def open_rasp_provider(
    settings: RaspSettings | None = None,
    *,
    transport: httpx.MockTransport | None = None,
    on_page_capture: CaptureCallback | None = None,
    cache_ttl_seconds: float = 300,
) -> AsyncIterator[RaspProvider]:
    """Owned nonretrying HTTP transport; injection is limited to offline mocks."""
    config = settings or load_settings()
    if not config.live_enabled or config.api_key is None:
        raise RaspError("disabled")
    if transport is not None and type(transport) is not httpx.MockTransport:
        raise ValueError("Only an offline MockTransport may replace the owned HTTP transport")
    selected = transport or httpx.AsyncHTTPTransport(
        retries=0,
        trust_env=False,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
    )
    async with (
        open_quota_pool(config) as pool,
        httpx.AsyncClient(
            transport=selected,
            trust_env=False,
            follow_redirects=False,
            auth=None,
            event_hooks={},
        ) as client,
    ):
        yield RaspProvider(
            client,
            api_key=config.api_key,
            wire_quota=PostgresRaspQuota(
                pool, account_id=config.account_id, quota_timezone=config.quota_timezone
            ),
            on_page_capture=on_page_capture,
            cache_ttl_seconds=cache_ttl_seconds,
        )
