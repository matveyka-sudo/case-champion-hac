"""Compatibility surface for legacy services and shared safe domain errors."""

from a2_api.core.errors import DomainError as DomainError
from a2_api.core.errors import unavailable as unavailable
from a2_api.services.legacy import Services as Services
