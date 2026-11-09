"""Optional read-only Flash explanations; importing this package performs no I/O."""

from .core import DeploymentBudget, FlashAssistant, FlashConfig, render_explanation
from .tools import AccessScope, ToolBackend
from .transport import SDKTransport

__all__ = [
    "AccessScope",
    "DeploymentBudget",
    "FlashAssistant",
    "FlashConfig",
    "SDKTransport",
    "ToolBackend",
    "render_explanation",
]
