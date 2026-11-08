"""Indices of one supplied intercity passenger chain, without route search."""

from dataclasses import dataclass
from math import sqrt
from typing import Literal

from a2_engine.shared.config import PassengerConfig
from a2_engine.shared.results import MetricResult, finite_number
from a2_engine.shared.scoring import category_index


@dataclass(frozen=True)
class ReferenceTime:
    reference_id: str
    seconds: float | None
    profile_version: str
    completeness: Literal["full", "partial"]
    applicability_confirmed: bool

    def __post_init__(self) -> None:
        for value in (self.reference_id, self.profile_version):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Reference IDs and versions must be nonempty strings")
        if self.completeness not in ("full", "partial"):
            raise ValueError("Reference completeness must be full or partial")
        if not isinstance(self.applicability_confirmed, bool):
            raise ValueError("applicability_confirmed must be a boolean")
        if self.seconds is not None:
            object.__setattr__(self, "seconds", finite_number(self.seconds, "reference seconds"))


@dataclass(frozen=True)
class PassengerChain:
    """One chain identity; path excludes the initial wait supplied alongside it."""

    chain_id: str
    path_seconds: float | None
    initial_wait_seconds: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.chain_id, str) or not self.chain_id.strip():
            raise ValueError("chain_id must be a nonempty string")
        for name in ("path_seconds", "initial_wait_seconds"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, finite_number(value, name))
        if self.path_seconds == 0:
            raise ValueError("INVALID_PATH_TIME")


@dataclass(frozen=True)
class PassengerResult:
    chain: PassengerChain
    reference: ReferenceTime
    path_index: MetricResult
    waiting_index: MetricResult
    index: MetricResult
    completeness: Literal["full", "partial"]
    diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class CarResult:
    chain_id: str
    reference: ReferenceTime
    path_index: MetricResult
    completeness: Literal["full", "partial"]
    diagnostics: tuple[str, ...]


def _path_index(
    path_seconds: float | None, reference: ReferenceTime, config: PassengerConfig
) -> tuple[MetricResult, tuple[str, ...]]:
    path = None if path_seconds is None else finite_number(path_seconds, "path_seconds")
    if path == 0:
        raise ValueError("INVALID_PATH_TIME")
    diagnostics: tuple[str, ...] = (
        ("PARTIAL_REFERENCE",) if reference.completeness == "partial" else ()
    )
    if reference.profile_version != config.reference_profile:
        return MetricResult(None, "unknown", "REFERENCE_VERSION_MISMATCH"), diagnostics
    if not reference.applicability_confirmed:
        return MetricResult(None, "unknown", "REFERENCE_NOT_APPLICABLE"), diagnostics
    if reference.seconds is None:
        return MetricResult(None, "unknown", "REFERENCE_UNAVAILABLE"), diagnostics
    if path is None:
        return MetricResult(None, "unknown", "DATA_UNAVAILABLE"), diagnostics
    if reference.seconds > path:
        diagnostics += ("REFERENCE_INCONSISTENCY",)
    return MetricResult(100.0 * min(1.0, reference.seconds / path), "available"), diagnostics


def intercity_passenger_index(
    chain: PassengerChain, reference: ReferenceTime, *, config: PassengerConfig
) -> PassengerResult:
    """Return this chain's path/wait/combined indices, with reference coverage."""
    path, diagnostics = _path_index(chain.path_seconds, reference, config)
    wait = category_index(chain.initial_wait_seconds, config.waiting_half_life_seconds)
    if path.value is None:
        index = path
    elif wait.value is None:
        index = wait
    else:
        # Separate roots avoid underflow of the intermediate product for tiny indices.
        index = MetricResult(sqrt(path.value) * sqrt(wait.value), "available")
    completeness: Literal["full", "partial"] = (
        "full" if reference.completeness == "full" and index.value is not None else "partial"
    )
    return PassengerResult(chain, reference, path, wait, index, completeness, diagnostics)


def intercity_car_index(
    chain_id: str, path_seconds: float | None, reference: ReferenceTime, *, config: PassengerConfig
) -> CarResult:
    """Car uses only I_path; no scheduled initial-wait component is invented."""
    if not isinstance(chain_id, str) or not chain_id.strip():
        raise ValueError("chain_id must be a nonempty string")
    path, diagnostics = _path_index(path_seconds, reference, config)
    completeness: Literal["full", "partial"] = (
        "full" if reference.completeness == "full" and path.value is not None else "partial"
    )
    return CarResult(chain_id, reference, path, completeness, diagnostics)
