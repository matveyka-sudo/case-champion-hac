"""Closed-form checks for the finite fixtures, independent of engine scoring/search."""

from collections.abc import Callable
from dataclasses import dataclass
from math import exp2, fsum, sqrt
from statistics import mean
from typing import Literal

from ml1_demo.model import City

UrbanMode = Literal["walk", "private_car", "public_transport"]
Stage = Literal["before", "coordinate", "additional"]


def index(seconds: float, half_life: float) -> float:
    return 100 * exp2(-seconds / half_life)


def urban_time(
    city: City, mode: UrbanMode, point: int, category: int, stage: Stage = "before"
) -> int:
    """The earlier of the two same-category objects always has the shorter exit."""
    if mode == "walk":
        return 3600 + 120 * point + 60 * city.number + 30 * category
    if mode == "private_car":
        return 630 + 15 * point + 60 * city.number + 60 * category
    if point >= 3:
        arrival = 2160 if stage == "coordinate" else 1920
    else:
        arrival = 2640 if stage == "before" else 2160
    return arrival + 60 * city.number + 30 * category


def point_index(city: City, mode: UrbanMode, point: int, stage: Stage = "before") -> float:
    values = (
        index(urban_time(city, mode, point, category, stage), city.h) * (1 + category % 3)
        for category in range(9)
    )
    return fsum(values) / 18


def population_mean(
    population: tuple[int, ...], scorer: Callable[[int], float], start: int = 0, end: int = 6
) -> float:
    return fsum(population[i] * scorer(i) for i in range(start, end)) / sum(population[start:end])


def urban_index(city: City, mode: UrbanMode, stage: Stage = "before") -> float:
    return population_mean(city.population, lambda i: point_index(city, mode, i, stage))


@dataclass(frozen=True)
class PassengerExpected:
    initial_wait: int
    movement: int
    transfer_wait: int
    path: int
    total: int
    reference: int
    path_index: float
    waiting_index: float
    index: float


def passenger(origin: City, destination: City, *, after: bool = False) -> PassengerExpected:
    n = origin.number * 3 + destination.number
    # Hand-summed two ride segments, one transfer, and one first departure.
    wait = 1800 + 120 * n
    move = 9000 + 720 * n
    transfer = (1200 if after else 2400) + 60 * n
    path = move + transfer
    ref = 8700 + 720 * n
    ip, iw = 100 * ref / path, index(wait, 43200)
    return PassengerExpected(wait, move, transfer, path, wait + path, ref, ip, iw, sqrt(ip * iw))


@dataclass(frozen=True)
class DeliveryExpected:
    initial_wait: int
    movement: int
    handling: int
    transshipment: int
    transfer_wait: int
    total: int
    urgency: str
    half_life: int
    index: float


def delivery(origin: City, destination: City, *, after: bool = False) -> DeliveryExpected:
    n = origin.number * 3 + destination.number
    move, handling = 57600 + 1800 * n, 18000 + 300 * n
    transfer = 3600 if after else 7200
    total = 3600 + move + handling + transfer
    urgency, hours = (("urgent", 24), ("ordinary", 72), ("nonurgent", 744))[(n - 1) % 3]
    return DeliveryExpected(
        3600,
        move,
        handling,
        handling - 14400,
        transfer,
        total,
        urgency,
        3600 * hours,
        index(total, 3600 * hours),
    )


def urban_cargo_times(city: City) -> tuple[int, ...]:
    base = 1050 + 120 * city.number
    return base, base + 180, base + 360


def urban_cargo_index(city: City) -> float:
    return mean(index(t, city.h) for t in urban_cargo_times(city))
