"""Deterministic wire schemas from the actual models and FastAPI router."""

import argparse
import hashlib
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from a2_contracts import api, common, dataset, journeys, profiles, regions, results, scenarios
from a2_contracts.common import ContractModel

from .app import create_app
from .configuration import CatalogConfig

MODULES = (api, common, dataset, journeys, profiles, regions, results, scenarios)


def model_registry() -> dict[str, type[ContractModel]]:
    models = {
        name: model
        for module in MODULES
        for name, model in inspect.getmembers(module, inspect.isclass)
        if issubclass(model, ContractModel)
        and model is not ContractModel
        and model.__module__ == module.__name__
    }
    models["CatalogConfig"] = CatalogConfig
    return models


def export_schemas(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, object] = {"openapi.json": create_app().openapi()}
    for name, model in sorted(model_registry().items()):
        for mode in ("validation", "serialization"):
            artifacts[f"{name}.{mode}.schema.json"] = model.model_json_schema(mode=mode)
    hashes: dict[str, str] = {}
    for filename, document in sorted(artifacts.items()):
        content = (
            json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            + "\n"
        ).encode("utf-8")
        (output / filename).write_bytes(content)
        hashes[filename] = hashlib.sha256(content).hexdigest()
    (output / "manifest.json").write_text(
        json.dumps({"contract_version": "0.1.0", "sha256": hashes}, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("contracts/generated"))
    parser.add_argument("--check", action="store_true", help="Report stale exports without writing")
    args = parser.parse_args()
    if args.check:
        with TemporaryDirectory(prefix="a2-schema-check-") as temp:
            generated = Path(temp)
            export_schemas(generated)
            expected = {p.name: p.read_bytes() for p in generated.glob("*.json")}
            actual = {p.name: p.read_bytes() for p in args.output.glob("*.json")}
            if actual != expected:
                changed = sorted(
                    name
                    for name in expected.keys() | actual.keys()
                    if expected.get(name) != actual.get(name)
                )
                parser.exit(1, "Stale schema exports: " + ", ".join(changed) + "\n")
        print("Schema exports match canonical models and API")
    else:
        export_schemas(args.output)
        print(f"Exported schemas to {args.output}")


if __name__ == "__main__":
    main()
