"""Inject accepted catalogs, engine adapters and persistent jobs at this boundary.

The default backend is deliberately unavailable. It neither loads development
fixtures nor acknowledges jobs that no worker can execute.
"""

from typing import Literal

from a2_contracts.api import (
    AnalysisRequest,
    Job,
    JobAccepted,
    Place,
)
from a2_contracts.dataset import Coverage, Destination, Evidence, Source
from a2_contracts.journeys import JourneyRequest, JourneyResult
from a2_contracts.profiles import ProfileConfig
from a2_contracts.regions import StudyArea
from a2_contracts.results import ProfileResult
from a2_contracts.scenarios import Comparison, ScenarioRequest

from a2_api.core.errors import unavailable


class Services:
    """Adapters must not execute CPU-heavy routing in these async methods.

    Configure catalog reads and bounded process/worker dispatch here once ML1,
    ML2 and persistent storage are connected. Errors must be safe for users.
    """

    async def profiles(self) -> list[ProfileConfig]:
        raise unavailable()

    async def study_areas(self) -> list[StudyArea]:
        raise unavailable()

    async def places(self, bundle_id: str, query: str | None) -> list[Place]:
        raise unavailable()

    async def destinations(self, bundle_id: str) -> list[Destination]:
        raise unavailable()

    async def coverage(self, bundle_id: str) -> Coverage:
        raise unavailable()

    async def sources(self, bundle_id: str) -> list[Source]:
        raise unavailable()

    async def evidence(self, bundle_id: str) -> list[Evidence]:
        raise unavailable()

    async def evidence_record(self, evidence_id: str, bundle_id: str) -> Evidence:
        raise unavailable()

    async def journeys(self, request: JourneyRequest) -> JourneyResult:
        raise unavailable()

    async def analyse(
        self, request: AnalysisRequest, idempotency_key: str, trace_id: str
    ) -> JobAccepted:
        raise unavailable()

    async def scenario(
        self, request: ScenarioRequest, idempotency_key: str, trace_id: str
    ) -> JobAccepted:
        raise unavailable()

    async def job(self, job_id: str) -> Job:
        raise unavailable()

    async def run(self, run_id: str) -> ProfileResult | Comparison:
        raise unavailable()

    async def export(self, run_id: str, format: Literal["json", "csv"]) -> tuple[str, bytes]:
        raise unavailable()
