"""Inject accepted catalogs, engine adapters and persistent jobs at this boundary.

The default backend is deliberately unavailable. It neither loads development
fixtures nor acknowledges jobs that no worker can execute.
"""

from a2_contracts.api import (
    ErrorCode,
    FieldError,
)


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
