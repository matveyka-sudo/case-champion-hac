"""Local settings aliases with fail-closed conflict detection."""

import os
from pathlib import Path
from typing import Any, ClassVar

from dotenv import dotenv_values
from pydantic_settings import BaseSettings


class WaydexSettings(BaseSettings):
    alias_pairs: ClassVar[tuple[tuple[str, str], ...]] = ()

    def __init__(self, **values: Any) -> None:
        env_file = values.get("_env_file", ".env")
        from_file = dotenv_values(Path(env_file)) if isinstance(env_file, (str, Path)) else {}
        for current, legacy in self.alias_pairs:
            current_value = os.environ.get(current, from_file.get(current))
            legacy_value = os.environ.get(legacy, from_file.get(legacy))
            if (
                current_value is not None
                and legacy_value is not None
                and current_value != legacy_value
            ):
                raise ValueError(f"Conflicting settings: {current} and {legacy}")
        super().__init__(**values)
