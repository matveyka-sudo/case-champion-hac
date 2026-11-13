"""One chat call to Alice AI LLM Flash (Yandex AI Studio, OpenAI-compatible API)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from dotenv import dotenv_values
from openai import AsyncOpenAI, DefaultAsyncHttpxClient

logger = logging.getLogger("a2_api.consultant")

SYSTEM = """Ты — консультант приложения Waydex о транспортной доступности.
Отвечай по-русски, коротко и по делу: 1–3 предложения, без вступлений и воды.
Опирайся только на данные расчёта ниже. Называй конкретный район, точку или объект и одно-два нужных числа (минуты, индекс из 100).
Не перечисляй другие показатели, если о них не спросили. Не пиши идентификаторы в квадратных скобках и не используй markdown.
Индекс 0–100: выше — лучше; 80+ отлично, 65–80 хорошо, 50–65 средне, ниже 50 плохо.
Если в данных нет ответа, скажи об этом одной фразой и подскажи, что посчитать или выбрать в приложении (категорию, район, точку на карте).
Если дан черновой ответ системы, его числа верны: можешь улучшить формулировку или дополнить, но не противоречь ему."""


@dataclass(frozen=True)
class ModelSettings:
    api_key: str
    folder_id: str
    model: str
    base_url: str

    @property
    def model_uri(self) -> str:
        if self.model.startswith(("gpt://", "emb://")):
            return self.model
        return f"gpt://{self.folder_id}/{self.model}"


def _value(name: str, default: str = "") -> str:
    file = {k: v for k, v in dotenv_values(".env").items() if v is not None}
    found = os.environ.get(name) or os.environ.get("A2_" + name) or file.get(name) or default
    return found.strip()


def _off(name: str) -> bool:
    return _value(name, "true").lower() in {"0", "false", "no", "off"}


def consultant_enabled() -> bool:
    """CONSULTANT_ENABLED=false brings back the previous assistant."""
    return not _off("CONSULTANT_ENABLED")


def settings_from_env() -> ModelSettings | None:
    """YANDEX_API_KEY and YANDEX_FOLDER_ID enable the model; nothing else gates it."""
    value = _value
    if _off("CONSULTANT_MODEL_ENABLED"):
        return None
    api_key, folder_id = value("YANDEX_API_KEY"), value("YANDEX_FOLDER_ID")
    if not api_key or not folder_id:
        return None
    return ModelSettings(
        api_key=api_key,
        folder_id=folder_id,
        model=value("YANDEX_MODEL", "aliceai-llm-flash"),
        base_url=value("YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1"),
    )


class ModelClient:
    """After a failure the model is skipped for a minute, so answers stay fast."""

    def __init__(
        self, settings: ModelSettings, *, timeout: float = 20, pause_seconds: float = 60
    ) -> None:
        self.settings = settings
        self.timeout = timeout
        self.pause_seconds = pause_seconds
        self._down_until = 0.0
        self._client = AsyncOpenAI(
            api_key=settings.api_key,
            project=settings.folder_id,
            organization="",
            base_url=settings.base_url,
            max_retries=0,
            timeout=timeout,
            http_client=DefaultAsyncHttpxClient(follow_redirects=False, timeout=timeout),
        )

    @property
    def model_id(self) -> str:
        name = self.settings.model.split("://", 1)[-1]
        parts = [part for part in name.split("/") if part]
        if "://" in self.settings.model and len(parts) > 1:
            parts = parts[1:]
        return parts[0] if parts else "model"

    async def ask(self, question: str, context: str, draft: str | None) -> str | None:
        if time.monotonic() < self._down_until:
            return None
        user = f"Данные расчёта:\n{context}\n\nВопрос пользователя: {question}"
        if draft:
            user += f"\n\nЧерновой ответ системы: {draft}"
        try:
            response = await self._client.chat.completions.create(
                model=self.settings.model_uri,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                ],
                max_tokens=400,
                temperature=0.2,
                stream=False,
            )
        except Exception as error:  # the consultant falls back to its own answer
            logger.warning("consultant_model_failed error_type=%s", type(error).__name__)
            self._down_until = time.monotonic() + self.pause_seconds
            return None
        if not response.choices:
            return None
        text = (response.choices[0].message.content or "").strip()
        return text or None

    async def aclose(self) -> None:
        await self._client.close()
