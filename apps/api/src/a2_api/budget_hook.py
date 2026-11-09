"""Operator-configured PostgreSQL implementation of ASSISTANT_BUDGET_HOOK."""

from decimal import Decimal

from pydantic import AliasChoices, Field, ValidationError
from pydantic_settings import SettingsConfigDict

from a2_api.analysis_service import AnalysisService
from a2_api.deployment_budget import PostgresDeploymentBudget, _valid_config
from a2_api.env_settings import WaydexSettings


class BudgetSettings(WaydexSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )
    alias_pairs = (
        ("ALICE_CURRENCY", "A2_ALICE_CURRENCY"),
        ("ALICE_TARIFF_ID", "A2_ALICE_TARIFF_ID"),
        ("ALICE_BUDGET_LIMIT", "A2_ALICE_BUDGET_LIMIT"),
        ("ALICE_TOKEN_LIMIT", "A2_ALICE_TOKEN_LIMIT"),
    )

    yandex_folder_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    alice_currency: str = Field(
        pattern=r"^[A-Z]{3}$", validation_alias=AliasChoices("ALICE_CURRENCY", "A2_ALICE_CURRENCY")
    )
    alice_tariff_id: str = Field(
        min_length=1,
        max_length=100,
        validation_alias=AliasChoices("ALICE_TARIFF_ID", "A2_ALICE_TARIFF_ID"),
    )
    alice_budget_limit: Decimal = Field(
        gt=0,
        allow_inf_nan=False,
        validation_alias=AliasChoices("ALICE_BUDGET_LIMIT", "A2_ALICE_BUDGET_LIMIT"),
    )
    alice_token_limit: int = Field(
        gt=0,
        le=2**63 - 1,
        validation_alias=AliasChoices("ALICE_TOKEN_LIMIT", "A2_ALICE_TOKEN_LIMIT"),
    )

    @property
    def model_uri(self) -> str:
        return f"gpt://{self.yandex_folder_id}/aliceai-llm-flash"


def load_budget_settings() -> BudgetSettings:
    try:
        settings = BudgetSettings()  # type: ignore[call-arg]  # populated by BaseSettings
        if not _valid_config(
            settings.alice_budget_limit,
            settings.alice_token_limit,
            settings.model_uri,
            settings.alice_currency,
            settings.alice_tariff_id,
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
        limit_amount=settings.alice_budget_limit,
        token_limit=settings.alice_token_limit,
        currency=settings.alice_currency,
        tariff_id=settings.alice_tariff_id,
    )
