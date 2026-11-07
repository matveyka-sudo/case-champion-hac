"""Inject accepted catalogs, engine adapters and persistent jobs at this boundary.

The default backend is deliberately unavailable. It neither loads development
fixtures nor acknowledges jobs that no worker can execute.
"""

from a2_contracts.api import (
    AnalysisRequest,
    ErrorCode,
    FieldError,
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


class DomainError(Exception):
    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        field_errors: list[FieldError] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.field_errors = field_errors or []


def unavailable() -> DomainError:
    return DomainError(
        503,
        "engine_unavailable",
        "Accepted data or calculation service unavailable",
        retryable=True,
    )


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
