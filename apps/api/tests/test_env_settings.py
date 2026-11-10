"""Offline checks for the documented local configuration names and precedence."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from a2_api.assistant.alice import AliceSettings, build_assistant
from a2_api.assistant.budget import BudgetSettings
from a2_api.core.config import RuntimeSettings
from a2_api.services.analyses import AnalysisService


def test_local_dotenv_uses_current_names_and_keeps_live_disabled(monkeypatch, tmp_path):
    template = (Path(__file__).resolve().parents[3] / ".env.example").read_text()
    assert "LLM_BUDGET_RUB" not in template
    (tmp_path / ".env").write_text(
        template.replace(
            "DATABASE_URL=postgresql://a2:a2_local_dev@127.0.0.1:5432/a2",
            "DATABASE_URL=postgresql://a2:private@127.0.0.1:5432/a2",
        ).replace("YANDEX_FOLDER_ID=\n", "YANDEX_FOLDER_ID=synthetic-folder\n")
    )
    monkeypatch.chdir(tmp_path)
    for name in (
        "DATABASE_URL",
        "A2_DATABASE_URL",
        "ASSISTANT_HOOK",
        "A2_ASSISTANT_HOOK",
        "ASSISTANT_BUDGET_HOOK",
        "A2_ASSISTANT_BUDGET_HOOK",
    ):
        monkeypatch.delenv(name, raising=False)
    runtime = RuntimeSettings()
    alice = AliceSettings()
    budget = BudgetSettings()
    assert runtime.database_url == "postgresql://a2:private@127.0.0.1:5432/a2"
    assert runtime.database_schema == "a2_backend"
    assert runtime.assistant_hook == "a2_api.assistant.alice:build_assistant"
    assert alice.assistant_budget_hook == "a2_api.assistant.budget:build_budget"
    assert alice.assistant_tools_enabled is False
    assert alice.alice_billing_currency_confirmed is False
    assert budget.alice_budget_limit == 10 and budget.alice_token_limit == 50000
    assert build_assistant(AnalysisService()).transport is None
    assert "private" not in repr(runtime)


@pytest.mark.parametrize(
    "current,legacy,settings_type",
    [
        ("DATABASE_URL", "A2_DATABASE_URL", RuntimeSettings),
        ("ASSISTANT_HOOK", "A2_ASSISTANT_HOOK", RuntimeSettings),
        ("ASSISTANT_TOOLS_ENABLED", "A2_ASSISTANT_TOOLS_ENABLED", AliceSettings),
        ("ALICE_BUDGET_LIMIT", "A2_ALICE_BUDGET_LIMIT", BudgetSettings),
    ],
)
def test_conflicting_aliases_fail_without_values(
    monkeypatch, tmp_path, current, legacy, settings_type
):
    (tmp_path / ".env").write_text(f"{current}=canonical-private\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(current, raising=False)
    monkeypatch.setenv(legacy, "legacy-private")
    with pytest.raises(ValueError, match="Conflicting settings") as error:
        settings_type()
    assert "private" not in str(error.value)


def test_legacy_names_remain_readable_without_conflict(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("A2_DATABASE_URL", "postgresql://legacy@localhost/a2")
    monkeypatch.setenv("A2_ASSISTANT_HOOK", "a2_api.assistant.alice:build_assistant")
    assert RuntimeSettings().database_url == "postgresql://legacy@localhost/a2"
    assert RuntimeSettings().assistant_hook == "a2_api.assistant.alice:build_assistant"


def test_alembic_reads_local_dotenv(database, tmp_path):
    _, schema, url = database
    root = Path(__file__).resolve().parents[3]
    (tmp_path / ".env").write_text(f"DATABASE_URL={url}\nDATABASE_SCHEMA={schema}\n")
    env = os.environ.copy()
    for name in ("DATABASE_URL", "A2_DATABASE_URL", "DATABASE_SCHEMA", "A2_DATABASE_SCHEMA"):
        env.pop(name, None)
    env["PYTHONPATH"] = os.pathsep.join(
        str(root / path)
        for path in ("packages/contracts/src", "apps/api/src", "packages/assistant/src")
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(root / "apps/api/alembic.ini"), "current"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.splitlines()[-3:]
    assert "0004_alice_budget" in result.stdout
