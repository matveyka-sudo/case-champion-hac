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
BATCH_EXTENSION = json.loads(
    (Path(__file__).parent / "fixtures/od_checkpoint_extension.json").read_text()
)
CHECKPOINT_PUBLICATION_EXTENSION = json.loads(
    (Path(__file__).parent / "fixtures/checkpoint_publication_extension.json").read_text()
)
JOB_PROGRESS_EXTENSION = json.loads(
    (Path(__file__).parent / "fixtures/job_progress_extension.json").read_text()
)
SCENARIO_SCOPE_EXTENSION = json.loads(
    (Path(__file__).parent / "fixtures/scenario_scope_extension.json").read_text()
)
RASP_QUOTA_EXTENSION = json.loads(
    (Path(__file__).parent / "fixtures/rasp_quota_extension.json").read_text()
)
PRIVATE_SOURCES_EXTENSION = json.loads(
    (Path(__file__).parent / "fixtures/private_sources_extension.json").read_text()
)
WALK_RUNTIME_EXTENSION = json.loads(
    (Path(__file__).parent / "fixtures/walk_runtime_extension.json").read_text()
)
RESULT_TRANSPORT_EXTENSION = json.loads(
    (Path(__file__).parent / "fixtures/result_transport_extension.json").read_text()
)
RUNTIME_SOURCE_BUDGET_EXTENSION = json.loads(
    (Path(__file__).parent / "fixtures/runtime_source_budget_extension.json").read_text()
)
ANALYSIS_GROUPS_EXTENSION = json.loads(
    (Path(__file__).parent / "fixtures/analysis_groups_extension.json").read_text()
)
DOCUMENT_RAG_EXTENSION = json.loads(
    (Path(__file__).parent / "fixtures/document_rag_extension.json").read_text()
)


ADDRESS_ACCESS_EXTENSION = json.loads(
    (Path(__file__).parent / "fixtures/address_access_extension.json").read_text()
)


def private_sources_counter(expected, kind):
    assert set(PRIVATE_SOURCES_EXTENSION["modules"]) == {
        "db/repositories/private_sources.py",
        "db/repositories/jobs.py",
        "db/repositories/runs.py",
        "db/repositories/scenarios.py",
    }
    removed = collections.Counter(PRIVATE_SOURCES_EXTENSION["removed_" + kind])
    assert removed <= expected
    expected.subtract(removed)
    expected.update(PRIVATE_SOURCES_EXTENSION["added_" + kind])
    return +expected


def scenario_scope_counter(expected, kind):
    removed = collections.Counter(SCENARIO_SCOPE_EXTENSION["removed_" + kind])
    added = collections.Counter(SCENARIO_SCOPE_EXTENSION["added_" + kind])
    assert removed <= expected
    expected.subtract(removed)
    expected.update(added)
    return +expected


def checkpoint_publication_counter(expected, kind):
    assert CHECKPOINT_PUBLICATION_EXTENSION["module"] == "db/repositories/checkpoints.py"
    removed = collections.Counter(CHECKPOINT_PUBLICATION_EXTENSION["removed_" + kind])
    assert removed <= expected
    expected.subtract(removed)
    expected.update(CHECKPOINT_PUBLICATION_EXTENSION["added_" + kind])
    return +expected


def approved_sql_counter(kind):
    expected = private_sources_counter(
        scenario_scope_counter(
            collections.Counter(BASELINE[kind])
            + collections.Counter(BATCH_EXTENSION[kind])
            + collections.Counter(RASP_QUOTA_EXTENSION[kind]),
            kind,
        ),
        kind,
    )
    assert JOB_PROGRESS_EXTENSION["module"] == "db/repositories/job_progress.py"
    expected = checkpoint_publication_counter(expected, kind) + collections.Counter(
        JOB_PROGRESS_EXTENSION[kind]
    )
    assert set(ANALYSIS_GROUPS_EXTENSION["sql_by_module"]) == {
        "db/connection.py",
        "db/repositories/analysis_groups.py",
        "db/repositories/jobs.py",
    }
    for extension in ANALYSIS_GROUPS_EXTENSION["sql_by_module"].values():
        expected.update(extension[kind])
    # Reviewed document RAG change: one replaced FTS query and one added count query.
    assert DOCUMENT_RAG_EXTENSION["module"] == "db/repositories/evidence.py"
    removed = collections.Counter(DOCUMENT_RAG_EXTENSION["removed_" + kind])
    assert removed <= expected
    expected.subtract(removed)
    expected.update(DOCUMENT_RAG_EXTENSION["added_" + kind])
    expected.update(ADDRESS_ACCESS_EXTENSION[kind])
    return +expected


def sql_literals(tree):
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.match(r"^\s*(SELECT|UPDATE|INSERT|DELETE|WITH)\b", node.value)
    ]


def sql_calls(tree):
    # Preserve every inline-query/transaction fingerprint and also cover the
    # progress query's direct module-level constant binding. Its SQL body and
    # positional parameters must both remain part of the reviewed inventory.
    named_sql = {
        node.targets[0].id
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and sql_literals(node.value)
    }
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and (
            node.func.attr == "transaction"
            or (
                node.args
                and (
                    sql_literals(node.args[0])
                    or isinstance(node.args[0], ast.Name)
                    and node.args[0].id in named_sql
                )
            )
        )
    ]


def sql_call_hashes(tree):
    return [
        hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
        for node in sql_calls(tree)
    ]


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
    assert set(ANALYSIS_GROUPS_EXTENSION["signatures"]) == {
        "a2_api.workers.runner:Worker",
        "a2_api.workers.runner:Worker.__init__",
        "a2_api.db.repositories.jobs:JobRepository.enqueue",
        "a2_api.services.analyses:AnalysisService.analyse",
    }
    assert set(JOB_PROGRESS_EXTENSION["signatures"]) == {
        "a2_api.services.analyses:AnalysisService.job",
        "a2_api.services.analyses:AnalysisService._view",
    }
    assert set(RUNTIME_SOURCE_BUDGET_EXTENSION["signatures"]) == {
        "a2_api.core.config:RuntimeSettings",
    }
    assert set(RESULT_TRANSPORT_EXTENSION["signatures"]) == {
        "a2_api.workers.calculation:calculate",
        "a2_api.workers.runner:Worker",
        "a2_api.workers.runner:Worker.__init__",
        "a2_api.core.config:RuntimeSettings",
    }
    assert WALK_RUNTIME_EXTENSION["module"] == "adapters/walk_runtime.py"
    assert set(WALK_RUNTIME_EXTENSION["signatures"]) == {
        "a2_api.adapters.walk_runtime:load_binding",
        "a2_api.adapters.walk_batches:load_approval",
        "a2_api.adapters.walk_batches:preflight",
        "a2_api.adapters.walk_batches:calculate",
        "a2_api.core.walk_observations:observation_directory",
    }
    assert set(PRIVATE_SOURCES_EXTENSION["signatures"]) == {"a2_api.core.jobs:V2JobContext"}
    assert set(DOCUMENT_RAG_EXTENSION["signatures"]) == {"a2_api.assistant.alice:AliceSettings"}
    assert set(BATCH_EXTENSION["signatures"]) == {
        "a2_api.core.jobs:V2JobContext",
        "a2_api.services.analyses:AnalysisService",
        "a2_api.services.analyses:AnalysisService.__init__",
    }
    assert set(SCENARIO_SCOPE_EXTENSION["signatures"]) == {
        "a2_api.db.validation:_validate_scenario_semantics",
        "a2_api.db.scenario_journeys:scenario_journeys",
        "a2_api.db.repositories.jobs:JobRepository._validate_scenario_semantics",
    }
    assert set(RASP_QUOTA_EXTENSION["signatures"]) == {
        "a2_api.providers.rasp:RaspError",
        "a2_api.providers.rasp:RaspError.__init__",
        "a2_api.providers.rasp:RaspIssue",
        "a2_api.providers.rasp:RaspProvider",
        "a2_api.providers.rasp:RaspProvider.__init__",
    }
    for path, signature in (
        BASELINE["signatures"]
        | BATCH_EXTENSION["signatures"]
        | SCENARIO_SCOPE_EXTENSION["signatures"]
        | RASP_QUOTA_EXTENSION["signatures"]
        | PRIVATE_SOURCES_EXTENSION["signatures"]
        | WALK_RUNTIME_EXTENSION["signatures"]
        | RESULT_TRANSPORT_EXTENSION["signatures"]
        | RUNTIME_SOURCE_BUDGET_EXTENSION["signatures"]
        | JOB_PROGRESS_EXTENSION["signatures"]
        | ANALYSIS_GROUPS_EXTENSION["signatures"]
        | DOCUMENT_RAG_EXTENSION["signatures"]
    ).items():
        module, qualified = path.split(":")
        obj = importlib.import_module(module)
        for name in qualified.split("."):
            obj = getattr(obj, name)
        if isinstance(obj, property):
            obj = obj.fget
        assert normalize_signature(str(inspect.signature(obj))) == normalize_signature(signature), (
            path
        )


def test_exported_artifacts_match_baseline_and_approved_contract_extensions(tmp_path):
    export_schemas(tmp_path)
    actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.glob("*.json")}
    # Preserve the historical relocation baseline. Only this reviewed additive
    # contract migration may replace its listed artifact hashes or add schemas.
    extension = json.loads(
        (Path(__file__).parent / "fixtures/score_explanation_schema_extension.json").read_text()
    )
    replaced = {"manifest.json", "openapi.json"} | {
        f"v2.{model}.{mode}.schema.json"
        for model in ("Explanation", "ExplanationContent", "Finding")
        for mode in ("serialization", "validation")
    }
    added = {f"v2.ScoreClaim.{mode}.schema.json" for mode in ("serialization", "validation")}
    assert set(extension["replaced"]) == replaced
    assert set(extension["added"]) == added
    assert replaced <= BASELINE["schemas"].keys()
    assert added.isdisjoint(BASELINE["schemas"])
    expected = BASELINE["schemas"] | extension["replaced"] | extension["added"]
    scenario_extension = json.loads(
        (Path(__file__).parent / "fixtures/scenario_journeys_schema_extension.json").read_text()
    )
    assert set(scenario_extension["replaced"]) == {"manifest.json", "openapi.json"} | {
        f"v2.ScenarioResult.{mode}.schema.json" for mode in ("serialization", "validation")
    }
    expected.update(scenario_extension["replaced"])
    scopes_extension = json.loads(
        (Path(__file__).parent / "fixtures/urban_scopes_schema_extension.json").read_text()
    )
    assert set(scopes_extension["replaced"]) == {"manifest.json", "openapi.json"} | {
        f"v2.Capabilities.{mode}.schema.json" for mode in ("serialization", "validation")
    }
    assert set(scopes_extension["added"]) == {
        f"v2.UrbanPassengerScope.{mode}.schema.json" for mode in ("serialization", "validation")
    }
    assert set(scopes_extension["added"]).isdisjoint(expected)
    expected.update(scopes_extension["replaced"])
    expected.update(scopes_extension["added"])
    leg_extension = json.loads(
        (ROOT / "packages/contracts/tests/fixtures/leg_geometry_schema_extension.json").read_text()
    )
    assert leg_extension["added"] == {}
    assert set(leg_extension["replaced"]) == {"manifest.json", "openapi.json"} | {
        f"v2.{model}.{mode}.schema.json"
        for model in ("Leg", "JourneyResult", "AnalysisResult", "ScenarioResult")
        for mode in ("serialization", "validation")
    }
    expected.update(leg_extension["replaced"])
    groups_extension = json.loads(
        (Path(__file__).parent / "fixtures/evidence_groups_schema_extension.json").read_text()
    )
    assert set(groups_extension["replaced"]) == {"manifest.json", "openapi.json"} | {
        f"v2.{model}.{mode}.schema.json"
        for model in ("AnalysisResult", "ScenarioProposal", "ScenarioResult")
        for mode in ("serialization", "validation")
    }
    assert set(groups_extension["added"]) == {
        f"v2.EvidenceGroup.{mode}.schema.json" for mode in ("serialization", "validation")
    }
    assert set(groups_extension["added"]).isdisjoint(expected)
    expected.update(groups_extension["replaced"])
    expected.update(groups_extension["added"])
    progress_extension = JOB_PROGRESS_EXTENSION["schemas"]
    assert set(progress_extension["replaced"]) == {"manifest.json", "openapi.json"} | {
        f"v2.JobView.{mode}.schema.json" for mode in ("serialization", "validation")
    }
    assert set(progress_extension["added"]) == {
        f"v2.BatchProgress.{mode}.schema.json" for mode in ("serialization", "validation")
    }
    assert set(progress_extension["replaced"]) <= expected.keys()
    assert set(progress_extension["added"]).isdisjoint(expected)
    expected.update(progress_extension["replaced"])
    expected.update(progress_extension["added"])
    snapshot_kinds_extension = json.loads(
        (Path(__file__).parent / "fixtures/snapshot_analysis_kinds_extension.json").read_text()
    )
    assert set(snapshot_kinds_extension) == {"manifest.json", "openapi.json"} | {
        f"v2.{model}.{mode}.schema.json"
        for model in ("Capabilities", "SnapshotAvailability")
        for mode in ("serialization", "validation")
    }
    expected.update(snapshot_kinds_extension)
    analysis_groups = ANALYSIS_GROUPS_EXTENSION["schemas"]
    assert set(analysis_groups["replaced"]) == {"manifest.json", "openapi.json"}
    assert set(analysis_groups["added"]) == {
        f"v2.{model}.{mode}.schema.json"
        for model in ("AnalysisGroupItem", "AnalysisGroupView")
        for mode in ("serialization", "validation")
    }
    assert set(analysis_groups["added"]).isdisjoint(expected)
    expected.update(analysis_groups["replaced"])
    expected.update(analysis_groups["added"])
    demo_extension = json.loads(
        (Path(__file__).parent / "fixtures/backend_demo_schema_extension.json").read_text()
    )
    assert set(demo_extension["replaced"]) == {"manifest.json", "openapi.json"} | {
        f"v2.{model}.{mode}.schema.json"
        for model in ("Capabilities", "FixedParameters", "ScenarioProposal", "ScenarioResult")
        for mode in ("serialization", "validation")
    }
    assert set(demo_extension["added"]) == {
        f"v2.{model}.{mode}.schema.json"
        for model in ("AnalysisPreset", "CargoHandlingChange")
        for mode in ("serialization", "validation")
    }
    assert set(demo_extension["replaced"]) <= expected.keys()
    assert set(demo_extension["added"]).isdisjoint(expected)
    expected.update(demo_extension["replaced"])
    expected.update(demo_extension["added"])
    compact_extension = json.loads(
        (Path(__file__).parent / "fixtures/compact_scopes_schema_extension.json").read_text()
    )
    assert set(compact_extension["replaced"]) == {"manifest.json", "openapi.json"} | {
        f"v2.Capabilities.{mode}.schema.json" for mode in ("serialization", "validation")
    }
    assert set(compact_extension["added"]) == {
        f"v2.CompactAnalysisScope.{mode}.schema.json" for mode in ("serialization", "validation")
    }
    assert set(compact_extension["added"]).isdisjoint(expected)
    expected.update(compact_extension["replaced"])
    expected.update(compact_extension["added"])
    passenger_extension = json.loads(
        (Path(__file__).parent / "fixtures/passenger_schedule_schema_extension.json").read_text()
    )
    assert set(passenger_extension) == {"manifest.json", "openapi.json"}
    expected.update(passenger_extension)
    cargo_extension = json.loads(
        (Path(__file__).parent / "fixtures/urban_cargo_schema_extension.json").read_text()
    )
    assert set(cargo_extension) == {"manifest.json", "openapi.json"} | {
        f"v2.{model}.{mode}.schema.json"
        for model in (
            "AcceptedData",
            "Capabilities",
            "CargoProfile",
            "CompactAnalysisScope",
            "DeliveryCargoProfile",
            "FreightCompatibility",
        )
        for mode in ("serialization", "validation")
    }
    assert set(cargo_extension) <= expected.keys()
    expected.update(cargo_extension)
    assert set(ADDRESS_ACCESS_EXTENSION["schemas"]) == {"manifest.json", "openapi.json"}
    expected.update(ADDRESS_ACCESS_EXTENSION["schemas"])
    assert actual == expected


def test_sql_literals_unchanged_and_confined_to_database_layer():
    strings = []
    for path in SOURCE.rglob("*.py"):
        found = sql_literals(ast.parse(path.read_text()))
        if found:
            assert path.is_relative_to(SOURCE / "db"), path
        strings.extend(found)
    assert BATCH_EXTENSION["module"] == "db/repositories/checkpoints.py"
    assert SCENARIO_SCOPE_EXTENSION["module"] == "db/repositories/runs.py"
    if "additional_modules" in SCENARIO_SCOPE_EXTENSION:
        assert set(SCENARIO_SCOPE_EXTENSION["additional_modules"]) == {
            "db/repositories/scenarios.py"
        }
    assert RASP_QUOTA_EXTENSION["module"] == "db/repositories/rasp_quota.py"
    assert collections.Counter(strings) == approved_sql_counter("sql_literals")


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
        hashes.extend(sql_call_hashes(ast.parse(path.read_text())))
    assert collections.Counter(hashes) == approved_sql_counter("sql_call_hashes")


def test_analysis_group_sql_extensions_remain_in_reviewed_modules():
    for module, extension in ANALYSIS_GROUPS_EXTENSION["sql_by_module"].items():
        tree = ast.parse((SOURCE / module).read_text())
        for kind, actual in (
            ("sql_literals", sql_literals(tree)),
            ("sql_call_hashes", sql_call_hashes(tree)),
        ):
            approved = collections.Counter(extension[kind])
            assert approved <= collections.Counter(actual), module
            if module == "db/repositories/analysis_groups.py":
                assert approved == collections.Counter(actual)


@pytest.mark.parametrize("module", ["checkpoints", "job_progress"])
def test_reviewed_sql_extensions_remain_in_their_owned_modules(module):
    tree = ast.parse((SOURCE / f"db/repositories/{module}.py").read_text())
    for kind, actual in (
        ("sql_literals", sql_literals(tree)),
        ("sql_call_hashes", sql_call_hashes(tree)),
    ):
        expected = (
            checkpoint_publication_counter(collections.Counter(BATCH_EXTENSION[kind]), kind)
            if module == "checkpoints"
            else collections.Counter(JOB_PROGRESS_EXTENSION[kind])
        )
        assert collections.Counter(actual) == expected
        if module == "checkpoints":
            assert (
                sum(collections.Counter(BATCH_EXTENSION[kind]).values())
                == (CHECKPOINT_PUBLICATION_EXTENSION["before_counts"][kind])
            )
            assert len(actual) == CHECKPOINT_PUBLICATION_EXTENSION["after_counts"][kind]
    transactions = collections.Counter(
        hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
        for node in sql_calls(tree)
        if node.func.attr == "transaction"
    )
    assert transactions == (
        CHECKPOINT_PUBLICATION_EXTENSION["unchanged_transaction_call_hashes"]
        if module == "checkpoints"
        else JOB_PROGRESS_EXTENSION["transaction_call_hashes"]
    )
    if module == "checkpoints":
        assert (
            sum(transactions.values())
            == (CHECKPOINT_PUBLICATION_EXTENSION["before_counts"]["transaction_call_hashes"])
            == CHECKPOINT_PUBLICATION_EXTENSION["after_counts"]["transaction_call_hashes"]
        )


@pytest.mark.parametrize(
    ("module", "before", "after", "kind"),
    [
        (
            "analysis_groups",
            "WHERE group_id=$1 AND access_scope=$2",
            "WHERE group_id=$1",
            "sql_literals",
        ),
        (
            "analysis_groups",
            "connection.transaction()",
            'connection.transaction(isolation="serializable")',
            "sql_call_hashes",
        ),
        (
            "checkpoints",
            "WHERE access_scope=$1 AND execution_identity=$2 FOR UPDATE",
            "WHERE access_scope=$1 AND execution_identity=$2 FOR SHARE",
            "sql_literals",
        ),
        (
            "checkpoints",
            "claim.job.job_id,",
            "claim.job.trace_id,",
            "sql_call_hashes",
        ),
        (
            "checkpoints",
            "connection.transaction()",
            'connection.transaction(isolation="serializable")',
            "sql_call_hashes",
        ),
        (
            "job_progress",
            "j.access_scope=$2",
            "j.access_scope=$1",
            "sql_literals",
        ),
        (
            "job_progress",
            "JOB_PROGRESS_SQL, job_id, scope",
            "JOB_PROGRESS_SQL, scope, job_id",
            "sql_call_hashes",
        ),
    ],
)
def test_sql_inventory_rejects_unapproved_mutations(module, before, after, kind):
    source = (SOURCE / f"db/repositories/{module}.py").read_text()
    assert before in source
    mutated = source.replace(before, after, 1)
    extract = sql_literals if kind == "sql_literals" else sql_call_hashes
    original = collections.Counter(extract(ast.parse(source)))
    changed = collections.Counter(extract(ast.parse(mutated)))
    assert changed != original
    expected = approved_sql_counter(kind)
    assert original <= expected
    assert expected - original + changed != expected
