"""Common versioned inputs for the three model cities."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from random import Random

from a2_engine import (
    PhysicalDeparture,
    PhysicalService,
    Timetable,
    TimetableEvent,
    TimetableRun,
    TimetableScope,
    TransportModeBinding,
)
from a2_engine.intercity.analysis.models import PublicMode
from a2_engine.routing.timetable_models import TimetableFootpath, TimetableTransfer
from a2_engine.scenario.identity import scenario_hash
from a2_engine.scenario.models import (
    ScenarioBaseline,
    ScenarioFeasibilityInputs,
    ScenarioOperation,
    ScenarioResourceBlock,
    ScenarioTimetable,
)

SEED = 20261109
VERSION = "ml1-synthetic-v1"
START = datetime(2026, 11, 9, 8, tzinfo=UTC)
DAY = START.date()
PROOF = (f"{VERSION}:seed:{SEED}",)
CONFIG_PATH = Path(__file__).resolve().parents[4] / "config/product.json"
CATEGORIES = (
    "adult_polyclinic",
    "child_polyclinic",
    "kindergarten",
    "school",
    "college_university",
    "rail_terminal",
    "bus_terminal",
    "airport",
    "shopping_center",
)
PRIORITIES = dict(zip(CATEGORIES, ("low", "medium", "high") * 3, strict=True))


@dataclass(frozen=True)
class City:
    id: str
    number: int
    area: int
    h: int
    population: tuple[int, ...]

    @property
    def snapshot(self) -> str:
        return f"{VERSION}:{SEED}:{self.id}"


random = Random(SEED)
CITIES = tuple(
    City(name, i, area, h, tuple(random.randint(20, 140) for _ in range(6)))
    for i, (name, area, h) in enumerate(
        (("moscow", 1500, 3600), ("kaliningrad", 200, 1800), ("novorossiysk", 400, 2400))
    )
)
PAIRS = tuple((a, b) for a in CITIES for b in CITIES if a != b)


def config_hash() -> str:
    return sha256(CONFIG_PATH.read_bytes()).hexdigest()


def clock(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def run(
    snapshot: str,
    trip: str,
    stops: tuple[tuple[str, int], ...],
    kind: str,
) -> TimetableRun:
    return TimetableRun(
        snapshot,
        VERSION,
        trip,
        DAY,
        "UTC",
        kind,
        tuple(
            TimetableEvent(i, node, clock(at), clock(at), True, True, PROOF)
            for i, (node, at) in enumerate(stops)
        ),
        True,
        PROOF,
    )


def identity(service: TimetableRun, evidence: tuple[str, ...] = PROOF) -> PhysicalService:
    return PhysicalService(
        f"physical:{service.trip_id}",
        (service.run_id,),
        tuple(
            PhysicalDeparture(event.sequence, f"{service.trip_id}:{event.sequence}", evidence)
            for event in service.events[:-1]
            if event.pickup
        ),
        evidence,
    )


def dataset(
    snapshot: str,
    runs: tuple[TimetableRun, ...],
    transfers: tuple[TimetableTransfer, ...],
    footpaths: tuple[TimetableFootpath, ...] = (),
    *,
    mode: PublicMode,
) -> ScenarioTimetable:
    nodes = {e.node_id for r in runs for e in r.events}
    nodes.update(node for p in footpaths for node in (p.from_node_id, p.to_node_id))
    table = Timetable(
        TimetableScope(
            snapshot,
            f"{snapshot}:graph",
            f"{snapshot}:calendar",
            f"{snapshot}:timetable",
            f"{snapshot}:footpaths",
            f"{snapshot}:transfers",
            "UTC",
            clock(-3600),
            clock(10 * 86400),
            frozenset(nodes),
            True,
            True,
            True,
            True,
            True,
            PROOF,
        ),
        runs,
        footpaths,
        transfers,
    )
    return ScenarioTimetable(
        f"{snapshot}:network",
        table,
        (TransportModeBinding(runs[0].transport_type, mode, PROOF),),
        tuple(identity(r) for r in runs),
    )


def resource_proof(
    baseline: ScenarioBaseline,
    operation: ScenarioOperation,
    block: ScenarioResourceBlock,
    changed_ids: tuple[str, ...],
) -> ScenarioFeasibilityInputs:
    return ScenarioFeasibilityInputs(
        baseline.baseline_id,
        scenario_hash(operation),
        (block,),
        changed_ids,
        True,
        True,
        True,
        block.available_from,
        block.available_to,
        PROOF,
    )
