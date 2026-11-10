"""Relocation regressions: contracts, raw SQL, imports, application and patch seams."""

import ast
import collections
import hashlib
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from a2_api.api.application import create_app
from a2_api.exports.schemas import export_schemas
from a2_api.services.analyses import AnalysisService
from fastapi.testclient import TestClient

SOURCE = Path(__file__).parents[1] / "src/a2_api"
ROOT = SOURCE.parents[3]
BASELINE = json.loads((Path(__file__).parent / "fixtures/refactor_baseline.json").read_text())
RETIRED_ROOT_MODULES = frozenset(
    "alice analysis_service api_v2 app budget_cli budget_hook configuration "
    "deployment_budget env_settings evidence_search export job_services jobs "
    "product_registry run_exports runtime storage tool_services worker".split()
)


def test_root_package_contains_only_canonical_subpackages():
    expected = {
        "adapters",
        "__init__.py",
        "py.typed",
        "api",
        "assistant",
        "core",
        "db",
        "exports",
        "providers",
        "services",
        "workers",
    }
    assert {path.name for path in SOURCE.iterdir() if path.name != "__pycache__"} == expected


def test_retired_root_imports_and_executable_config_do_not_return():
    tracked = subprocess.check_output(
        ["git", "ls-files", "--", "*.py"], cwd=ROOT, text=True
    ).splitlines()
    for name in tracked:
        path = ROOT / name
        if not path.is_file():
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
                if node.module == "a2_api":
                    assert not RETIRED_ROOT_MODULES.intersection(
                        alias.name for alias in node.names
                    ), path
            assert all(
                not (
                    module.startswith("a2_api.") and module.split(".", 2)[1] in RETIRED_ROOT_MODULES
                )
                for module in modules
            ), path
    old_path = re.compile(r"\ba2_api\.(?:" + "|".join(sorted(RETIRED_ROOT_MODULES)) + r")\b")
    text_suffixes = {".py", ".md", ".json", ".yml", ".yaml", ".sh", ".toml", ".mjs", ".ts", ".tsx"}
    tracked_text = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    for name in tracked_text:
        path = ROOT / name
        if not path.is_file() or not (
            path.suffix in text_suffixes
            or path.name in {".env.example", "Dockerfile"}
            or path.name.endswith(".Dockerfile")
        ):
            continue
        assert old_path.search(path.read_text()) is None, name


def normalize_signature(value):
    value = re.sub(r" at 0x[0-9a-f]+(?=>)", "", value)
    value = re.sub(r"a2_api(?:\.[a-z_][a-z0-9_]*)*\.", "", value)
    # A set default has no order; repr changes across interpreter hash seeds.
    return re.sub(
        r"frozenset\((\{[^}]*\})\)",
        lambda m: "frozenset(" + repr(sorted(ast.literal_eval(m[1]))) + ")",
        value,
    )


def test_all_185_existing_signatures_and_defaults():
    for path, signature in BASELINE["signatures"].items():
        module, qualified = path.split(":")
        obj = importlib.import_module(module)
        for name in qualified.split("."):
            obj = getattr(obj, name)
        if isinstance(obj, property):
            obj = obj.fget
        assert normalize_signature(str(inspect.signature(obj))) == normalize_signature(signature), (
            path
        )


def test_all_293_exported_artifacts_remain_byte_identical(tmp_path):
    export_schemas(tmp_path)
    actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.glob("*.json")}
    assert actual == BASELINE["schemas"]


def test_sql_literals_unchanged_and_confined_to_database_layer():
    strings = []
    for path in SOURCE.rglob("*.py"):
        found = [
            n.value
            for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and re.match(r"^\s*(SELECT|UPDATE|INSERT|DELETE|WITH)\b", n.value)
        ]
        if found:
            assert path.is_relative_to(SOURCE / "db"), path
        strings.extend(found)
    assert collections.Counter(strings) == BASELINE["sql_literals"]


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("db.repositories.jobs", "JobRepository"),
        ("core.jobs", "V2JobContext"),
        ("services.analyses", "AnalysisService"),
        ("services.jobs", "QueuedServices"),
        ("core.catalog", "CatalogConfig"),
        ("services.catalog", "CatalogServices"),
        ("core.registry", "ProductRegistry"),
        ("core.config", "RuntimeSettings"),
        ("db.connection", "open_pool"),
        ("api.bootstrap", "build_app"),
        ("api.application", "create_app"),
        ("api.legacy", "create_app"),
        ("db.repositories.evidence", "EvidenceRepository"),
        ("db.repositories.budget", "PostgresDeploymentBudget"),
        ("assistant.tools", "SavedTools"),
        ("assistant.budget", "build_budget"),
        ("assistant.budget_cli", "run_command"),
        ("exports.results", "export_run"),
        ("exports.schemas", "export_schemas"),
        ("assistant.alice", "AliceSettings"),
    ],
)
def test_public_implementations_live_in_canonical_modules(module, name):
    obj = getattr(importlib.import_module("a2_api." + module), name)
    assert obj.__module__ == "a2_api." + module


def test_request_local_services_and_routers_are_not_shared_between_apps():
    class Tagged(AnalysisService):
        def __init__(self, tag):
            super().__init__()
            self.tag = tag

        def capabilities(self):
            result = super().capabilities()
            for item in result.analyses:
                item.reason_code = self.tag
            return result

    first, second = create_app(Tagged("FIRST")), create_app(Tagged("SECOND"))
    for app in (first, second):
        routes = [(r.path, method) for r in app.routes for method in getattr(r, "methods", ())]
        assert len(routes) == len(set(routes))
    with TestClient(first) as a, TestClient(second) as b:
        assert a.get("/api/v1/capabilities").json()["analyses"][0]["reason_code"] == "FIRST"
        assert b.get("/api/v1/capabilities").json()["analyses"][0]["reason_code"] == "SECOND"
        assert a.get("/api/v1/capabilities").json()["analyses"][0]["reason_code"] == "FIRST"
        assert (
            a.post(
                "/api/v1/analyses", headers={"Origin": "https://foreign.invalid"}, json={}
            ).status_code
            == 403
        )


@pytest.mark.asyncio
async def test_canonical_worker_calculator_patch_controls_execution(monkeypatch):
    from a2_api.workers import runner

    injected = AsyncMock(return_value="sentinel")
    monkeypatch.setattr(runner, "calculate", injected)
    worker = runner.Worker(object(), handler="trusted:handler", code_version="v1")
    assert await worker._calculate("claim", "trusted:handler", 3) == "sentinel"
    injected.assert_awaited_once_with("claim", "trusted:handler", 3)


def test_database_and_config_do_not_depend_on_http_services_or_orm():
    for domain in ("db", "core"):
        for path in (SOURCE / domain).rglob("*.py"):
            for n in ast.walk(ast.parse(path.read_text())):
                if isinstance(n, ast.ImportFrom):
                    assert n.level == 0, path
                    if n.module:
                        assert not n.module.startswith(
                            ("a2_api.api", "a2_api.services", "fastapi", "sqlalchemy")
                        ), (path, n.module)
                if isinstance(n, ast.Import):
                    assert not any(
                        a.name.startswith(
                            ("fastapi", "sqlalchemy", "a2_api.api", "a2_api.services")
                        )
                        for a in n.names
                    ), path
    for path in SOURCE.rglob("*.py"):
        for n in ast.walk(ast.parse(path.read_text())):
            if isinstance(n, ast.ImportFrom):
                assert n.level == 0, path


def test_fresh_canonical_import_orders_outside_checkout(tmp_path):
    modules = [
        "a2_api.api.application",
        "a2_api.db.repositories.jobs",
        "a2_api.assistant.alice",
        "a2_api.workers.cli",
        "a2_api.exports.schemas",
        "a2_api.services.analyses",
        "a2_api.api.bootstrap",
    ]
    env = dict(os.environ)
    root = SOURCE.parents[3]
    env["PYTHONPATH"] = os.pathsep.join(
        str(root / p) for p in ("apps/api/src", "packages/contracts/src", "packages/assistant/src")
    )
    script = 'import importlib,json,sys;[importlib.import_module(n) for n in json.loads(sys.argv[1])];from a2_api.api.bootstrap import build_app;assert build_app().openapi()["info"]["version"]=="2.0.0"'
    for order in (modules, list(reversed(modules))):
        subprocess.run(
            [sys.executable, "-c", script, json.dumps(order)],
            cwd=tmp_path,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )


def test_sql_parameter_binding_and_transaction_options_unchanged():
    hashes = []
    for path in SOURCE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            sql = node.args and any(
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and re.match(r"^\s*(SELECT|UPDATE|INSERT|DELETE|WITH)\b", value.value)
                for value in ast.walk(node.args[0])
            )
            if node.func.attr == "transaction" or sql:
                hashes.append(
                    hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
                )
    assert collections.Counter(hashes) == BASELINE["sql_call_hashes"]
