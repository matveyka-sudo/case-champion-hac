"""Pipeline utilization is an infrastructure percentage, not a delivery index."""

from dataclasses import dataclass
from math import isfinite

from a2_engine.shared.config import FreightConfig
from a2_engine.shared.results import MetricResult, finite_number


@dataclass(frozen=True)
class PipelineBasis:
    """Normalized quantity unit and covered period of a confirmed connection."""

    product: str
    period: str
    unit: str
    connection_id: str
    direction: str

    def __post_init__(self) -> None:
        for name in ("product", "period", "unit", "connection_id", "direction"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonempty string")


@dataclass(frozen=True)
class PipelineResult:
    utilization_percent: MetricResult
    diagnostics: tuple[str, ...]


def pipeline_utilization(
    volume: float | None,
    capacity: float | None,
    *,
    volume_basis: PipelineBasis,
    capacity_basis: PipelineBasis,
    connection_confirmed: bool,
    config: FreightConfig,
) -> PipelineResult:
    """Require equal normalized bases; preserve values above 100% for QA."""
    v = None if volume is None else finite_number(volume, "volume")
    q = None if capacity is None else finite_number(capacity, "capacity", positive=True)
    if not isinstance(volume_basis, PipelineBasis) or not isinstance(capacity_basis, PipelineBasis):
        raise ValueError("Pipeline quantities require PipelineBasis metadata")
    if not isinstance(connection_confirmed, bool):
        raise ValueError("connection_confirmed must be a boolean")
    if any(
        basis.product not in config.pipeline_products for basis in (volume_basis, capacity_basis)
    ):
        raise ValueError("Pipeline product must belong to the configuration")
    if volume_basis != capacity_basis:
        return PipelineResult(MetricResult(None, "unknown", "INCOMPARABLE_PIPELINE_DATA"), ())
    if not connection_confirmed:
        return PipelineResult(MetricResult(None, "unknown", "CONNECTION_UNCONFIRMED"), ())
    if v is None or q is None:
        return PipelineResult(MetricResult(None, "unknown", "DATA_UNAVAILABLE"), ())
    utilization = (v / q) * 100.0
    if not isfinite(utilization):
        raise ValueError("UTILIZATION_OVERFLOW")
    diagnostics = ("UTILIZATION_ABOVE_100",) if utilization > 100 else ()
    return PipelineResult(MetricResult(utilization, "available"), diagnostics)
