"""Configured empty scenario inventory must not inherit generic templates."""

import pytest
from a2_api.adapters import product_runtime
from a2_api.api.bootstrap import build_app
from a2_api.core.config import RuntimeSettings
from a2_api.core.registry import ProductRegistry
from a2_api.services.analyses import DEFAULT_TEMPLATES


@pytest.mark.parametrize("templates", [frozenset(), frozenset({"additional_departure"}), None])
def test_operator_registry_controls_published_scenario_templates(monkeypatch, templates):
    registry = ProductRegistry()
    if templates is not None:
        registry.scenario_template_ids = templates
    monkeypatch.setattr(product_runtime, "registry", lambda: registry)
    app = build_app(RuntimeSettings(registry_hook="a2_api.adapters.product_runtime:registry"))
    assert app.state.services.registered_template_ids == (
        DEFAULT_TEMPLATES if templates is None else templates
    )
