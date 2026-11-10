"""Server-owned, expiring source permissions; separate from cache freshness."""

from datetime import datetime
from typing import Literal, Self

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import ContractModel, Identifier, Timestamp
from a2_data_pipeline.rasp_inputs import PrivateCachePolicy
from pydantic import Field, TypeAdapter, model_validator


class PrivateSourceDescriptor(ContractModel):
    source_kind: Literal["yandex_rasp"]
    episode_id: Identifier
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshot_id: Identifier
    snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy: PrivateCachePolicy
    policy_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    copyright_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def bound_policy(self) -> Self:
        if (
            self.episode_id != "rasp:" + self.manifest_sha256
            or self.policy_hash != canonical_hash(self.policy.model_dump(mode="json"))
            or self.policy.observed_at >= self.policy.expires_at
            or len(self.snapshot_id) > 200
            or len(self.policy.authorization_ref) > 1000
            or len(self.policy.purge_policy_ref) > 1000
        ):
            raise ValueError("PRIVATE_SOURCE_POLICY_BINDING_MISMATCH")
        return self

    @property
    def content_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class PrivateSourceGrant(ContractModel):
    grant_id: Identifier
    episode_id: Identifier
    access_scope: Identifier
    authorization_ref: Identifier
    approved_by: Identifier
    valid_from: Timestamp
    valid_until: Timestamp
    external_model_use: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def bounded_interval(self) -> Self:
        if self.valid_from >= self.valid_until or any(
            len(value) > 1000
            for value in (
                self.grant_id,
                self.episode_id,
                self.access_scope,
                self.authorization_ref,
                self.approved_by,
            )
        ):
            raise ValueError("PRIVATE_SOURCE_GRANT_INTERVAL_INVALID")
        return self

    @property
    def content_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class PrivateSourceBinding(ContractModel):
    source: PrivateSourceDescriptor
    grant: PrivateSourceGrant
    grant_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def exact_grant(self) -> Self:
        if (
            self.grant.episode_id != self.source.episode_id
            or self.grant.authorization_ref != self.source.policy.authorization_ref
            or self.grant_hash != self.grant.content_hash
            or self.grant.valid_from < self.source.policy.observed_at
            or self.grant.valid_until > self.source.policy.expires_at
        ):
            raise ValueError("PRIVATE_SOURCE_GRANT_BINDING_MISMATCH")
        return self

    @property
    def valid_until(self) -> datetime:
        return min(self.grant.valid_until, self.source.policy.expires_at)

    def assert_access(self, scope: str, now: datetime) -> None:
        self.source.policy.assert_valid(now=now)
        if self.grant.access_scope != scope or not self.grant.valid_from <= now < self.valid_until:
            raise ValueError("PRIVATE_SOURCE_PERMISSION_UNAVAILABLE")


PRIVATE_BINDINGS = TypeAdapter(tuple[PrivateSourceBinding, ...])


def validate_bindings(value: object) -> tuple[PrivateSourceBinding, ...]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 8:
        raise ValueError("PRIVATE_SOURCE_BINDING_INVENTORY_INVALID")
    bindings = PRIVATE_BINDINGS.validate_python(value)
    if not 1 <= len(bindings) <= 8 or len({b.source.episode_id for b in bindings}) != len(bindings):
        raise ValueError("PRIVATE_SOURCE_BINDING_INVENTORY_INVALID")
    return bindings
