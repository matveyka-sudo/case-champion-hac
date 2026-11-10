"""Operator-configured API database lifespan and the separate worker entrypoint."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="A2_", extra="ignore", hide_input_in_errors=True)
    contract_version: Literal["2", "legacy"] = "2"
    registry_hook: str | None = None
    assistant_hook: str | None = None
    secure_cookies: bool = False
    database_url: str | None = Field(default=None, repr=False)
    database_schema: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")] = "a2_backend"
    catalog_path: Path | None = None
    code_version: str | None = None
    preflight_hook: str | None = None
    calculation_handler: str | None = None
    job_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 600
    worker_poll_seconds: Annotated[float, Field(gt=0, le=60)] = 0.5
