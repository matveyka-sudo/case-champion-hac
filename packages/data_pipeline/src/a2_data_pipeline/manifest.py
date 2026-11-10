"""Source registry and per-dataset SourceManifest (docs/SOURCE_REGISTRY.md).

The registry assigns every A2 source a role. A dataset manifest records one
concrete fetched file: URL, retrieval time, checksum, licence and QA status.
``registered`` in the registry never means ``accepted`` data.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from .paths import data_dir

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_.:-]*$")]

AnalysisKind = Literal["urban_passenger", "intercity_passenger", "urban_cargo", "intercity_cargo"]
SourceRole = Literal["calculation", "validation", "context", "scenario_basis"]
AccessMethod = Literal["api", "file", "manual"]
LicenseStatus = Literal["permitted", "restricted", "unknown"]
RegistryStatus = Literal["registered", "fetched", "quarantined", "accepted", "unavailable"]
DatasetStatus = Literal["fetched", "parsed", "validated", "quarantined", "accepted"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class License(Model):
    """Declared licence; ``status=unknown`` until terms are checked at import."""

    name: str | None
    url: HttpUrl | None
    status: LicenseStatus
    attribution: str | None = None


class RegistryEntry(Model):
    source_id: Identifier
    a2_ref: str | None = Field(description="S01..S22 in the 07.11 TZ, null for technical additions")
    title: str
    publisher: str
    source_url: HttpUrl
    role: SourceRole
    analysis_kinds: list[AnalysisKind]
    provides: str
    limitations: str
    access_method: AccessMethod
    geography: str
    license: License
    status: RegistryStatus
    datasets: list[Identifier] = Field(default_factory=list)
    notes: str | None = None


class Registry(Model):
    registry_version: Annotated[int, Field(ge=1)]
    updated_at: date
    entries: list[RegistryEntry]

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        ids = [entry.source_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source_id in registry")
        return self

    def get(self, source_id: str) -> RegistryEntry:
        for entry in self.entries:
            if entry.source_id == source_id:
                return entry
        raise KeyError(source_id)


class Checksum(Model):
    algorithm: Literal["sha256"]
    value: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Annotated[int, Field(ge=0)]


class DatasetManifest(Model):
    """SourceManifest of one concrete fetched file or API response set."""

    dataset_id: Identifier
    source_id: Identifier
    source_url: HttpUrl
    retrieved_at: datetime
    publication_date: date | None
    valid_from: date | None
    valid_to: date | None
    geography: str
    license: License
    access_method: AccessMethod
    raw_path: str = Field(description="Path relative to data/raw; raw files are not in git")
    checksum: Checksum
    upstream_checksum: str | None = None
    schema_mapping: dict[str, str]
    coverage: str
    limitations: list[str]
    status: DatasetStatus
    qa_report: str | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at requires an offset")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to precedes valid_from")
        if self.status == "accepted" and self.license.status != "permitted":
            raise ValueError("accepted dataset requires a permitted licence")
        return self


def registry_path() -> Path:
    return data_dir() / "manifests" / "sources.json"


def load_registry(path: Path | None = None) -> Registry:
    return Registry.model_validate_json((path or registry_path()).read_text(encoding="utf-8"))


def dataset_manifest_path(dataset_id: str) -> Path:
    return data_dir() / "manifests" / "datasets" / f"{dataset_id}.json"


def load_dataset_manifest(dataset_id: str) -> DatasetManifest:
    return DatasetManifest.model_validate_json(
        dataset_manifest_path(dataset_id).read_text(encoding="utf-8")
    )


def write_dataset_manifest(manifest: DatasetManifest) -> Path:
    path = dataset_manifest_path(manifest.dataset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump(mode="json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
