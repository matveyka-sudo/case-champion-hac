"""od / models — canonical urban implementation."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from functools import cached_property

from a2_engine.routing.road import road_hash
from a2_engine.urban.models import UrbanPoint
from a2_engine.urban.tau.models import UrbanODPair, UrbanODScope, UrbanTauDay


@dataclass(frozen=True)
class UrbanODTask:
    plan_id: str
    scope: UrbanODScope
    profile_hash: str
    departure_at: datetime
    origin: UrbanPoint
    destination: UrbanPoint

    @property
    def task_id(self) -> str:
        return road_hash(
            [
                self.plan_id,
                self.profile_hash,
                self.departure_at.isoformat(),
                self.origin.point_id,
                self.destination.point_id,
            ]
        )


@dataclass(frozen=True)
class UrbanODPlan:
    scope: UrbanODScope
    points: tuple[UrbanPoint, ...]
    week_start: date
    reference_at: datetime
    calendar: tuple[UrbanTauDay, ...]
    departures: tuple[datetime, ...]
    profile_hash: str
    evidence_refs: tuple[str, ...]

    @cached_property
    def plan_id(self) -> str:
        return road_hash(
            [
                self.scope,
                self.points,
                self.week_start.isoformat(),
                self.reference_at.isoformat(),
                self.calendar,
                [d.isoformat() for d in self.departures],
                self.profile_hash,
                self.evidence_refs,
            ]
        )

    def tasks(self) -> Iterator[UrbanODTask]:
        for departure in self.departures:
            for origin in self.points:
                for destination in self.points:
                    if origin.point_id == destination.point_id:
                        continue
                    # Only a confirmed zero product permits skipping routing.
                    if origin.population == 0 or destination.population == 0:
                        continue
                    yield UrbanODTask(
                        self.plan_id, self.scope, self.profile_hash, departure, origin, destination
                    )

    def batches(self, size: int) -> Iterator[tuple[UrbanODTask, ...]]:
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ValueError("POSITIVE_OD_BATCH_SIZE_REQUIRED")
        batch = []
        for task in self.tasks():
            batch.append(task)
            if len(batch) == size:
                yield tuple(batch)
                batch = []
        if batch:
            yield tuple(batch)


@dataclass(frozen=True)
class UrbanODObservation:
    task: UrbanODTask
    pair: UrbanODPair
    best_known_seconds: float | None = None

    def __post_init__(self) -> None:
        if (self.pair.origin_point_id, self.pair.destination_point_id) != (
            self.task.origin.point_id,
            self.task.destination.point_id,
        ):
            raise ValueError("OD_OBSERVATION_PAIR_MISMATCH")
        if self.best_known_seconds is not None:
            from a2_engine.shared.results import finite_number

            finite_number(self.best_known_seconds, "OD best known")
