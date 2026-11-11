"""Shared H selection; the public urban function keeps its canonical owner."""

from collections.abc import Sequence

from a2_engine.shared.config import AreaHBand, validate_bands
from a2_engine.shared.results import MetricResult, finite_number


def select_area_h(area_km2: float | None, *, bands: Sequence[AreaHBand]) -> MetricResult:
    validate_bands(bands)
    if area_km2 is None:
        return MetricResult(None, "unknown", "AREA_UNAVAILABLE")
    area = finite_number(area_km2, "area_km2", positive=True)
    for band in bands:
        if band.max_area_km2 is None or area <= band.max_area_km2:
            return MetricResult(band.h_seconds, "available")
    raise AssertionError("Validated bands must cover every positive area")
