"""Operator-configured API database lifespan and the separate worker entrypoint."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field
from pydantic_settings import SettingsConfigDict

from a2_api.core.env_settings import WaydexSettings


class RuntimeSettings(WaydexSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="A2_",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )
    alias_pairs = (
        ("DATABASE_URL", "A2_DATABASE_URL"),
        ("DATABASE_SCHEMA", "A2_DATABASE_SCHEMA"),
        ("ASSISTANT_HOOK", "A2_ASSISTANT_HOOK"),
    )
    contract_version: Literal["2", "legacy"] = "2"
    registry_hook: str | None = None
    assistant_hook: str | None = Field(
        default=None, validation_alias=AliasChoices("ASSISTANT_HOOK", "A2_ASSISTANT_HOOK")
    )
    secure_cookies: bool = False
    database_url: str | None = Field(
        default=None, repr=False, validation_alias=AliasChoices("DATABASE_URL", "A2_DATABASE_URL")
    )
    database_schema: Annotated[
        str,
        Field(
            pattern=r"^[a-z][a-z0-9_]{0,62}$",
            validation_alias=AliasChoices("DATABASE_SCHEMA", "A2_DATABASE_SCHEMA"),
        ),
    ] = "a2_backend"
    catalog_path: Path | None = None
    code_version: str | None = None
    preflight_hook: str | None = None
    calculation_handler: str | None = None
    job_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 600
    worker_poll_seconds: Annotated[float, Field(gt=0, le=60)] = 0.5
    worker_result_decoded_mib: Annotated[int, Field(ge=16, le=32)] = 16
    runtime_source_budget_mib: Annotated[int, Field(ge=96, le=160)] = 96
