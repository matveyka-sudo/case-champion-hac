"""Explicit operator policy for cumulative runtime source validation bytes."""

from a2_api.core.config import RuntimeSettings

DEFAULT_RUNTIME_SOURCE_BYTES = 96 * 1024 * 1024
MAX_RUNTIME_SOURCE_BYTES = 160 * 1024 * 1024


def runtime_source_budget_bytes() -> int:
    return RuntimeSettings().runtime_source_budget_mib * 1024 * 1024
