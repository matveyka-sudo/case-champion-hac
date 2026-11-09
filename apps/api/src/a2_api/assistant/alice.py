"""Opt-in Alice AI factory for the existing A2_ASSISTANT_HOOK boundary."""

import importlib
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Literal, cast

from a2_assistant import DeploymentBudget, FlashAssistant, FlashConfig, SDKTransport
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from a2_api.assistant.tools import SavedTools
from a2_api.services.analyses import AnalysisService


class AliceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="", extra="ignore", hide_input_in_errors=True
    )

    llm_enabled: bool = False
    yandex_api_key: SecretStr | None = None
    yandex_folder_id: str | None = None
    yandex_model: Literal["aliceai-llm-flash"] = "aliceai-llm-flash"
    yandex_base_url: Literal["https://ai.api.cloud.yandex.net/v1"] = (
        "https://ai.api.cloud.yandex.net/v1"
    )
    a2_assistant_budget_hook: str | None = None
    a2_assistant_tools_enabled: bool = False
    a2_alice_billing_currency_confirmed: bool = False


def _deployment_budget(path: str, service: AnalysisService) -> DeploymentBudget:
    module, separator, name = path.partition(":")
    if not separator or not module or not name:
        raise ValueError("A2_ASSISTANT_BUDGET_HOOK must be a trusted module:function")
    try:
        factory = getattr(importlib.import_module(module), name)
        if not callable(factory):
            raise TypeError
        budget = cast(Callable[[AnalysisService], DeploymentBudget], factory)(service)
        amount = budget.limit_amount
        if (
            budget.durable is not True
            or not isinstance(amount, Decimal)
            or not amount.is_finite()
            or amount <= 0
            or re.fullmatch(r"[A-Z]{3}", budget.currency) is None
            or not callable(budget.reserve)
            or not callable(budget.record_usage)
        ):
            raise ValueError
    except Exception:
        raise ValueError("A2_ASSISTANT_BUDGET_HOOK must provide a valid durable budget") from None
    return budget


def _build_assistant(
    service: AnalysisService, transport_factory: Callable[[FlashConfig], SDKTransport]
) -> FlashAssistant:
    """Construct the existing assistant; no paid call without all operator gates."""
    try:
        settings = AliceSettings()
        config = FlashConfig(
            api_key=settings.yandex_api_key,
            folder_id=settings.yandex_folder_id,
            model_id=settings.yandex_model,
            base_url=settings.yandex_base_url,
            tools_enabled=settings.a2_assistant_tools_enabled,
        )
    except ValidationError:
        raise ValueError(
            "Alice AI configuration is invalid; check model, URL and folder ID"
        ) from None

    enabled = (
        settings.llm_enabled
        and settings.a2_alice_billing_currency_confirmed
        and bool(config.api_key is not None and config.api_key.get_secret_value())
    )
    if enabled and config.folder_id is None:
        raise ValueError("Alice AI configuration requires YANDEX_FOLDER_ID")
    # A configured key never opens a live SDK client without an explicit shared ledger.
    budget = (
        _deployment_budget(settings.a2_assistant_budget_hook, service)
        if enabled and settings.a2_assistant_budget_hook
        else None
    )
    transport = transport_factory(config) if budget is not None else None
    return FlashAssistant(
        config, transport=transport, tool_backend=SavedTools(service), deployment_budget=budget
    )


def build_assistant(service: AnalysisService) -> FlashAssistant:
    return _build_assistant(service, SDKTransport)
