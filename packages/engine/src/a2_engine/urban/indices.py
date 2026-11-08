"""Pure urban passenger calculations. All durations are in seconds."""

from collections.abc import Mapping, Sequence
from math import frexp, fsum, ldexp

from a2_engine.shared.config import AreaHBand, UrbanConfig, validate_bands
from a2_engine.shared.results import MetricResult, finite_number, validate_index


def select_h_seconds(area_km2: float | None, *, bands: Sequence[AreaHBand]) -> MetricResult:
    """Select H from administrative area; unknown area never receives a default."""
    validate_bands(bands)
    if area_km2 is None:
        return MetricResult(None, "unknown", "AREA_UNAVAILABLE")
    area = finite_number(area_km2, "area_km2", positive=True)
    for band in bands:
        if band.max_area_km2 is None or area <= band.max_area_km2:
            return MetricResult(band.h_seconds, "available")
    raise AssertionError("Validated bands must cover every positive area")


def is_low_accessibility(index: MetricResult, *, threshold: float) -> bool | None:
    """Strict comparison; unavailable indices have no boolean classification."""
    validate_index(index)
    limit = finite_number(threshold, "threshold")
    if limit > 100:
        raise ValueError("threshold must be within 0..100")
    return None if index.value is None else index.value < limit


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    # Power-of-two scaling avoids overflow without rounding ordinary input weights.
    _, exponent = frexp(max(weights))
    scaled = [ldexp(weight, -exponent) for weight in weights]
    denominator = fsum(scaled)
    mean = fsum(value * weight for value, weight in zip(values, scaled, strict=True)) / denominator
    # Correct floating-point excursions outside the mathematical convex hull.
    # This is a numeric bound, not UI decimal rounding.
    return min(max(values), max(min(values), mean))


def point_index(
    category_indices: Mapping[str, MetricResult],
    selected_categories: Mapping[str, str],
    *,
    config: UrbanConfig,
) -> MetricResult:
    """Full index for ONE mode, with category ID -> requested priority.

    Unselected known categories may be present. Missing selected values must
    be supplied explicitly as unknown MetricResult values, rather than omitted.
    """
    if not isinstance(selected_categories, Mapping) or not selected_categories:
        raise ValueError("selected_categories must be a nonempty category-to-priority mapping")
    if not isinstance(category_indices, Mapping):
        raise ValueError("category_indices must be a mapping")
    if any(key not in config.categories for key in selected_categories):
        raise ValueError("Selected category IDs must belong to the configuration")
    if any(
        not isinstance(priority, str) or priority not in config.priority_weights
        for priority in selected_categories.values()
    ):
        raise ValueError("Selected priorities must belong to the configuration")
    if set(category_indices) - config.categories:
        raise ValueError("Input category IDs must belong to the configuration")
    if set(selected_categories) - set(category_indices):
        raise ValueError("Every selected category requires an explicit index result")
    for result in category_indices.values():
        validate_index(result)
    chosen = [category_indices[key] for key in selected_categories]
    if any(result.value is None for result in chosen):
        return MetricResult(None, "unknown", "CATEGORY_UNAVAILABLE")
    values = [result.value for result in chosen if result.value is not None]
    weights = [config.priority_weights[priority] for priority in selected_categories.values()]
    return MetricResult(_weighted_mean(values, weights), "available")


def point_indices_by_mode(
    category_indices_by_mode: Mapping[str, Mapping[str, MetricResult]],
    selected_categories: Mapping[str, str],
    *,
    config: UrbanConfig,
) -> dict[str, MetricResult]:
    """Evaluate supplied modes independently; never average transport modes."""
    if (
        not isinstance(category_indices_by_mode, Mapping)
        or not category_indices_by_mode
        or set(category_indices_by_mode) - config.modes
    ):
        raise ValueError("Supply a nonempty mapping of configured urban modes")
    return {
        mode: point_index(indices, selected_categories, config=config)
        for mode, indices in category_indices_by_mode.items()
    }


def territory_index(
    point_indices: Mapping[str, MetricResult],
    populations: Mapping[str, float | None],
) -> MetricResult:
    """Full ONE-mode population mean from original cells/prepared cell shares.

    Keys identify the same original cells or prepared shares in both mappings.
    Spatial preparation, coverage certification and deduplication are upstream.
    """
    if not isinstance(point_indices, Mapping) or not isinstance(populations, Mapping):
        raise ValueError("point_indices and populations must be mappings")
    if set(point_indices) != set(populations):
        raise ValueError("Point index and population IDs must match exactly")
    if any(not isinstance(key, str) or not key.strip() for key in point_indices):
        raise ValueError("Point IDs must be nonempty strings")
    weights = {}
    for key, result in point_indices.items():
        validate_index(result)
        population = populations[key]
        weights[key] = (
            None if population is None else finite_number(population, f"population.{key}")
        )
    if any(weight is None for weight in weights.values()):
        return MetricResult(None, "unknown", "POPULATION_UNAVAILABLE")
    positive = {key: weight for key, weight in weights.items() if weight is not None and weight > 0}
    if not positive:
        return MetricResult(None, "not_applicable", "ZERO_WEIGHT")
    if any(point_indices[key].value is None for key in positive):
        return MetricResult(None, "unknown", "POINT_INDEX_UNAVAILABLE")
    values = [point_indices[key].value for key in positive]
    return MetricResult(
        _weighted_mean([value for value in values if value is not None], list(positive.values())),
        "available",
    )
