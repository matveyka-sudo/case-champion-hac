"""Compatibility imports; implementation lives in a2_api.api.application."""

from a2_api.api.application import (
    API_CONTRACT_VERSION as API_CONTRACT_VERSION,
)
from a2_api.api.application import (
    create_app as create_app,
)
from a2_api.api.application import (
    error_response as error_response,
)
from a2_api.api.dependencies import IdempotencyKey as IdempotencyKey
