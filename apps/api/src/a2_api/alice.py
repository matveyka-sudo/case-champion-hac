"""Compatible Alice hook, preserving the legacy SDKTransport injection point."""

from a2_assistant import FlashAssistant, SDKTransport

from a2_api.assistant.alice import AliceSettings as AliceSettings
from a2_api.assistant.alice import _build_assistant
from a2_api.assistant.alice import _deployment_budget as _deployment_budget
from a2_api.services.analyses import AnalysisService


def build_assistant(service: AnalysisService) -> FlashAssistant:
    return _build_assistant(service, SDKTransport)
