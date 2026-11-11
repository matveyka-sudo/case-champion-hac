"""Operator-owned static truck execution policy, never accepted from HTTP clients."""

from typing import Literal

from a2_contracts.v2.base import ContractModel
from pydantic import Field, HttpUrl, field_validator


class TruckRoutingBinding(ContractModel):
    schema_version: Literal["waydex-truck-routing/1"]
    endpoint_url: HttpUrl
    graph_directory_env: str = Field(pattern=r"^A2_TRUCK_[A-Z0-9_]+_GRAPH_DIR$")
    graph_manifest_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    engine_version: Literal["3.5.1"]
    tileset_last_modified: int = Field(strict=True, ge=0)
    max_pairs: int = Field(strict=True, gt=0, le=1024)
    max_seconds: int = Field(strict=True, gt=0, le=240)
    requests_per_second: int = Field(default=10, strict=True, ge=1, le=20)
    request_timeout_seconds: int = Field(default=8, strict=True, ge=1, le=15)

    @field_validator("endpoint_url")
    @classmethod
    def endpoint_without_credentials(cls, value: HttpUrl) -> HttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("TRUCK_ENDPOINT_MUST_NOT_CONTAIN_SECRETS_OR_QUERY")
        return value
