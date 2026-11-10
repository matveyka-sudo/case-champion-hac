"""districts / models — canonical urban implementation."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from a2_engine.shared.results import MetricResult, finite_number
from a2_engine.urban.analysis.models import UrbanAnalysis
from a2_engine.urban.models import UrbanContext, identifier, references


@dataclass(frozen=True)
class UrbanCellAllocation:
    cell_id: str
    population: float | None
    allocated_population: Decimal | None
    residual_population: Decimal | None
    documented_precision: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class UrbanDistrictPopulation:
    district_id: str
    population: Decimal | None
    excluded_population: Decimal | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class UrbanDistrictComparison:
    context: UrbanContext
    passenger_mode: str
    category: str | None
    selected_categories: tuple[tuple[str, str], ...]
    h_seconds: MetricResult
    catalog_version: str
    distribution_version: str
    grid_versions: frozenset[str]
    district_boundary_versions: tuple[tuple[str, str], ...]
    expected_district_ids: tuple[str, ...]
    administrative_level: str = "district"


@dataclass(frozen=True)
class UrbanDistrictValue:
    district_id: str
    index: MetricResult
    deficit: MetricResult
    ranking_eligible: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class UrbanDistrictRank:
    district_id: str
    index: float
    rank: int  # Competition ranking: 1,1,3; subset positions if incomplete.


@dataclass(frozen=True)
class UrbanOriginalCell:
    cell_id: str
    city_id: str
    grid_version: str
    population: float | None
    population_year: int | None
    population_method: str | None
    point_id: str | None
    excluded: bool
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("cell_id", "city_id", "grid_version"):
            identifier(getattr(self, name), name)
        if not isinstance(self.excluded, bool):
            raise ValueError("EXPLICIT_CELL_EXCLUSION_REQUIRED")
        if self.point_id is not None:
            identifier(self.point_id, "point_id")
        if self.excluded and self.point_id is not None:
            raise ValueError("EXCLUDED_CELL_HAS_NO_REPRESENTATIVE_POINT")
        if self.population is not None:
            object.__setattr__(
                self, "population", finite_number(self.population, "cell population")
            )
            if (
                isinstance(self.population_year, bool)
                or not isinstance(self.population_year, int)
                or self.population_year < 1
                or self.population_method is None
            ):
                raise ValueError("CELL_POPULATION_BASIS_REQUIRED")
        if self.population_method is not None:
            identifier(self.population_method, "population_method")
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs, required=True))


@dataclass(frozen=True)
class UrbanDistrict:
    district_id: str
    city_id: str
    name: str
    boundary_version: str
    evidence_refs: tuple[str, ...]
    administrative_level: str = "district"

    def __post_init__(self) -> None:
        for name in ("district_id", "city_id", "name", "boundary_version"):
            identifier(getattr(self, name), name)
        if self.administrative_level != "district":
            raise ValueError("DISTRICT_LEVEL_REQUIRED")
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs, required=True))


@dataclass(frozen=True)
class UrbanDistrictShare:
    cell_id: str
    district_id: str
    population: float | None  # Absolute persons, not population fraction or area share.
    evidence_refs: tuple[str, ...]
    area_share: float | None = None  # Retained for audit; never used as a weight.

    def __post_init__(self) -> None:
        identifier(self.cell_id, "cell_id")
        identifier(self.district_id, "district_id")
        if self.population is not None:
            object.__setattr__(
                self, "population", finite_number(self.population, "district population")
            )
        if self.area_share is not None:
            area = finite_number(self.area_share, "area_share")
            if area > 1:
                raise ValueError("INVALID_AREA_SHARE")
            object.__setattr__(self, "area_share", area)
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs, required=True))


@dataclass(frozen=True)
class UrbanSharePrecision:
    cell_quantum: float
    share_quantum: float
    evidence_refs: tuple[str, ...]
    # Supported rule: city cells rounded to nearest quantum; shares floored.
    rule: str = "cell_nearest_share_floor"

    def __post_init__(self) -> None:
        if self.rule != "cell_nearest_share_floor":
            raise ValueError("UNSUPPORTED_SHARE_PRECISION_RULE")
        for name in ("cell_quantum", "share_quantum"):
            object.__setattr__(self, name, finite_number(getattr(self, name), name, positive=True))
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs, required=True))


@dataclass(frozen=True)
class UrbanDistrictRanking:
    scope: UrbanDistrictComparison
    entries: tuple[UrbanDistrictRank, ...]
    excluded: tuple[tuple[str, tuple[str, ...]], ...]
    complete: bool
    positions_scope: str  # all_expected_districts | available_subset


@dataclass(frozen=True)
class UrbanDistrictCoverage:
    context: UrbanContext
    catalog_version: str
    distribution_version: str
    grid_versions: frozenset[str]
    district_boundary_versions: Mapping[str, str]
    expected_district_ids: frozenset[str]
    expected_cell_ids: frozenset[str]
    complete_district_ids: frozenset[str]
    original_cells_complete: bool
    evidence_refs: tuple[str, ...]
    precision: UrbanSharePrecision | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.context, UrbanContext) or not isinstance(
            self.original_cells_complete, bool
        ):
            raise ValueError("DISTRICT_CONTEXT_AND_COVERAGE_REQUIRED")
        for name in ("catalog_version", "distribution_version"):
            identifier(getattr(self, name), name)
        for name in (
            "grid_versions",
            "expected_district_ids",
            "expected_cell_ids",
            "complete_district_ids",
        ):
            object.__setattr__(self, name, frozenset(references(getattr(self, name))))
        boundaries = dict(self.district_boundary_versions)
        for key, value in boundaries.items():
            identifier(key, "district ID")
            identifier(value, "boundary version")
        if (
            set(boundaries) != self.expected_district_ids
            or self.complete_district_ids - self.expected_district_ids
        ):
            raise ValueError("DISTRICT_COVERAGE_ROSTER_MISMATCH")
        object.__setattr__(self, "district_boundary_versions", MappingProxyType(boundaries))
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs, required=True))


@dataclass(frozen=True)
class UrbanDistrictMetric:
    scope: UrbanDistrictComparison
    city_index: MetricResult
    districts: tuple[UrbanDistrictValue, ...]
    gap: MetricResult
    ranking: UrbanDistrictRanking


@dataclass(frozen=True)
class UrbanDistrictInputs:
    original_cells: tuple[UrbanOriginalCell, ...]
    district_catalog: tuple[UrbanDistrict, ...]
    population_shares: tuple[UrbanDistrictShare, ...]
    coverage: UrbanDistrictCoverage


@dataclass(frozen=True)
class UrbanDistrictModeResult:
    passenger_mode: str
    overall: UrbanDistrictMetric
    categories: tuple[UrbanDistrictMetric, ...]


@dataclass(frozen=True)
class UrbanDistrictAnalysis:
    source_analysis: UrbanAnalysis
    original_cells: tuple[UrbanOriginalCell, ...]
    district_catalog: tuple[UrbanDistrict, ...]
    population_shares: tuple[UrbanDistrictShare, ...]
    coverage: UrbanDistrictCoverage
    cell_qa: tuple[UrbanCellAllocation, ...]
    district_populations: tuple[UrbanDistrictPopulation, ...]
    city_population: Decimal | None
    allocated_population: Decimal | None
    residual_population: Decimal | None
    excluded_population: Decimal | None
    modes: tuple[UrbanDistrictModeResult, ...]
    evidence_refs: tuple[str, ...]
