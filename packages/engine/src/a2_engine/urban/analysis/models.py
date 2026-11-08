"""analysis / models — canonical urban implementation."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from a2_engine.shared.results import MetricResult
from a2_engine.urban.journeys import UrbanJourneyTiming
from a2_engine.urban.models import (
    Completeness,
    UrbanArea,
    UrbanContext,
    UrbanCoverage,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanThreshold,
)
from a2_engine.urban.selection import SelectedUrbanObject


@dataclass(frozen=True)
class UrbanCategoryResult:
    point_id: str
    category: str
    passenger_mode: str
    best: SelectedUrbanObject | None
    objects: tuple[SelectedUrbanObject, ...]
    rejected: tuple[UrbanJourneyTiming, ...]
    index: MetricResult
    count: MetricResult
    threshold: UrbanThreshold | None
    coverage: UrbanCoverage | None
    minimum_proven: bool
    completeness: Completeness
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class UrbanPointResult:
    point: UrbanPoint
    passenger_mode: str
    categories: tuple[UrbanCategoryResult, ...]
    index: MetricResult
    completeness: Completeness


@dataclass(frozen=True)
class UrbanTerritoryResult:
    passenger_mode: str
    points: tuple[UrbanPointResult, ...]
    index: MetricResult
    completeness: Completeness
    ranking_eligible: bool
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class UrbanAnalysis:
    context: UrbanContext
    area: UrbanArea
    selected_categories: Mapping[str, str]
    h_seconds: MetricResult
    journeys: tuple[UrbanJourneyTiming, ...]
    modes: tuple[UrbanTerritoryResult, ...]
    population_coverage: UrbanPopulationCoverage
    status: Literal["available", "partial", "unknown"]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "selected_categories", MappingProxyType(dict(self.selected_categories))
        )
