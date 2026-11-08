"""Trusted Chat Completions transport. No native completion, SDK retries or env loading.

Official docs checked 2026-11-08:
https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models
https://aistudio.yandex.ru/ru/docs/ai-studio/operations/generation/completions-basic
https://aistudio.yandex.ru/ru/docs/ai-studio/operations/generation/completions-function
Flash tool support and project billing still require an authorized real smoke.
"""

import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Annotated, Literal, Protocol, cast

from openai import APIStatusError, APITimeoutError, AsyncOpenAI, DefaultAsyncHttpxClient
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr


class FlashConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    folder_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")] | None = None
    base_url: Literal["https://ai.api.cloud.yandex.net/v1"] = "https://ai.api.cloud.yandex.net/v1"
    model_id: Literal["aliceai-llm-flash"] = "aliceai-llm-flash"
    tools_enabled: bool = Field(default=False, strict=True)
    max_model_calls: Annotated[int, Field(strict=True, ge=1, le=4)] = 4
    max_tool_calls: Annotated[int, Field(strict=True, ge=1, le=8)] = 8
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=2000)] = 2000
    max_input_tokens: Annotated[int, Field(strict=True, ge=1024, le=30000)] = 12000
    per_call_timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 30
    total_timeout_seconds: Annotated[float, Field(gt=0, le=90)] = 90

    @property
    def model_uri(self) -> str:
        return f"gpt://{self.folder_id}/{self.model_id}"


class ModelToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Annotated[str, Field(min_length=1, max_length=128)]
    name: Annotated[str, Field(pattern=r"^[a-z_]{1,64}$")]
    arguments: Annotated[str, Field(max_length=8000)]


class ModelReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: Annotated[str, Field(max_length=16000)] | None
    tool_calls: list[ModelToolCall] = Field(default_factory=list, max_length=32)
    input_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None
    output_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None
    finish_reason: str


class ModelFailure(Exception):
    def __init__(
        self, code: str, *, status_code: int | None = None, retry_after: float | None = None
    ) -> None:
        self.code = (
            code
            if code in {"disabled", "timeout", "provider_error", "invalid_response"}
            else "provider_error"
        )
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(f"Flash transport: {self.code}")


class ModelTransport(Protocol):
    live: bool

    async def complete(
        self,
        messages: list[dict[str, JsonValue]],
        tools: list[dict[str, JsonValue]],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> ModelReply: ...


class SDKTransport:
    """Low-level adapter, invoked by FlashAssistant only after durable reservation.

    The caller owns injected HTTP client lifecycle through aclose(). This raw
    transport is not a spending gate; production entrypoints must use the core.
    SDK version is supplied by the workspace lock, not a new dependency here.
    """

    live = True

    def __init__(
        self, config: FlashConfig, *, http_client: DefaultAsyncHttpxClient | None = None
    ) -> None:
        self.config = config
        self._client: AsyncOpenAI | None = None
        if config.api_key is not None and config.api_key.get_secret_value() and config.folder_id:
            client = (
                http_client
                if http_client is not None
                else DefaultAsyncHttpxClient(
                    follow_redirects=False, timeout=config.per_call_timeout_seconds
                )
            )
            client.follow_redirects = False
            self._client = AsyncOpenAI(
                api_key=config.api_key.get_secret_value(),
                project=config.folder_id,
                organization="",
                base_url=config.base_url,
                max_retries=0,
                http_client=client,
                timeout=config.per_call_timeout_seconds,
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def complete(
        self,
        messages: list[dict[str, JsonValue]],
        tools: list[dict[str, JsonValue]],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> ModelReply:
        if self._client is None:
            raise ModelFailure("disabled")
        if model != self.config.model_uri or not 0 < max_tokens <= 2000 or not 0 < timeout <= 30:
            raise ModelFailure("invalid_response")
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=cast(list[ChatCompletionMessageParam], messages),
                tools=cast(list[ChatCompletionToolParam], tools) if tools else [],
                tool_choice="auto" if tools else "none",
                max_tokens=max_tokens,
                timeout=timeout,
                stream=False,
            )
            if len(response.choices) != 1:
                raise ModelFailure("invalid_response")
            choice = response.choices[0]
            calls = []
            for call in choice.message.tool_calls or []:
                if call.type != "function":
                    raise ModelFailure("invalid_response")
                calls.append(
                    ModelToolCall(
                        id=call.id, name=call.function.name, arguments=call.function.arguments
                    )
                )
            return ModelReply(
                content=choice.message.content,
                tool_calls=calls,
                input_tokens=response.usage.prompt_tokens if response.usage else None,
                output_tokens=response.usage.completion_tokens if response.usage else None,
                finish_reason=choice.finish_reason,
            )
        except APIStatusError as exc:
            retry_after = _retry_after(exc.response.headers.get("Retry-After"))
            raise ModelFailure(
                "provider_error", status_code=exc.status_code, retry_after=retry_after
            ) from None
        except APITimeoutError:
            raise ModelFailure("timeout") from None
        except Exception:
            raise ModelFailure("invalid_response") from None


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (
                parsedate_to_datetime(value).astimezone(UTC) - datetime.now(UTC)
            ).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None
