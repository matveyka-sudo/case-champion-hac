"""Operator-configured PostgreSQL implementation of A2_ASSISTANT_BUDGET_HOOK."""

from decimal import Decimal

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from a2_api.analysis_service import AnalysisService
from a2_api.deployment_budget import PostgresDeploymentBudget, _valid_config


class BudgetSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="", extra="ignore", hide_input_in_errors=True
    )

    yandex_folder_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    a2_alice_currency: str = Field(pattern=r"^[A-Z]{3}$")
    a2_alice_tariff_id: str = Field(min_length=1, max_length=100)
    a2_alice_budget_limit: Decimal = Field(gt=0, allow_inf_nan=False)
    a2_alice_token_limit: int = Field(gt=0, le=2**63 - 1)

    @property
    def model_uri(self) -> str:
        return f"gpt://{self.yandex_folder_id}/aliceai-llm-flash"


def load_budget_settings() -> BudgetSettings:
    try:
        settings = BudgetSettings()  # type: ignore[call-arg]  # populated by BaseSettings
        if not _valid_config(
            settings.a2_alice_budget_limit,
            settings.a2_alice_token_limit,
            settings.model_uri,
            settings.a2_alice_currency,
            settings.a2_alice_tariff_id,
        ):
            raise ValueError
        return settings
    except (ValidationError, ValueError):
        raise ValueError("Alice AI budget configuration is invalid") from None


def build_budget(service: AnalysisService) -> PostgresDeploymentBudget:
    """The hook never inserts a budget row or enables LLM calls by itself."""
    settings = load_budget_settings()
    if service.repository is None:
        raise ValueError("Alice AI budget configuration requires PostgreSQL storage")
    return PostgresDeploymentBudget(
        service.repository.pool,
        model_uri=settings.model_uri,
        limit_amount=settings.a2_alice_budget_limit,
        token_limit=settings.a2_alice_token_limit,
        currency=settings.a2_alice_currency,
        tariff_id=settings.a2_alice_tariff_id,
    )
