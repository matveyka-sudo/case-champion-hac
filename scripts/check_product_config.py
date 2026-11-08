"""Validate the agreed scope; this script does not calculate scores."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
c = json.loads((root / "config/product.json").read_text())
assert c["intracity_cities"] == ["Москва", "Калининград", "Новороссийск"]
assert set(c["analysis_kinds"]) == {
    "urban_passenger", "intercity_passenger", "urban_cargo", "intercity_cargo"
}
assert c["urban_modes"] == ["walk", "public_transport", "private_car"]
assert len(c["categories"]) == len(set(c["categories"])) == 9
assert c["time_step_minutes"] == 15 and c["grid_spacing_m"] == 1000
assert c["priority_levels"] == ["low", "medium", "high"]
assert c["pipeline_products"] == ["oil", "oil_products", "gas"]
assert "pipeline" in c["cargo_modes"]
assert set(c["methodologies"]) == set(c["analysis_kinds"])
assert c["spec_version"] == "2026-11-08.1"
assert c["schema_version"] == "2"
assert c["methodology_version"] == "a2-methodology-v3"
assert c["priority_weights"] == {"low": 1, "medium": 2, "high": 3}
for name, method in c["methodologies"].items():
    assert method["status"] == "approved", name
    assert method["version"] == c["methodology_version"], name
    assert isinstance(method["parameters"], dict) and method["parameters"], name
urban = c["methodologies"]["urban_passenger"]["parameters"]
assert urban["area_h_bands"] == [
    {"max_area_km2": 100, "h_minutes": 20},
    {"max_area_km2": 300, "h_minutes": 30},
    {"max_area_km2": 1000, "h_minutes": 40},
    {"max_area_km2": None, "h_minutes": 60},
]
assert urban["low_accessibility_below"] == 50
assert urban["max_total_wait_hours"] == 4
assert urban["tau_times"] == ["08:00", "12:00", "18:00", "22:00"]
assert urban["tau_days"] == 7
pax = c["methodologies"]["intercity_passenger"]["parameters"]
assert pax == {
    "reference": "A", "reference_profile": "edge-calibrated-v1",
    "departure_window_days": 7, "initial_wait_half_life_hours": 12,
}
cargo = c["methodologies"]["intercity_cargo"]["parameters"]
assert cargo["departure_window_days"] == 31
assert cargo["delivery_half_life_hours"] == {"urgent": 24, "ordinary": 72, "nonurgent": 744}
assert cargo["pipeline_in_total"] is False
assert c["assistant"]["model"] == "aliceai-llm-flash"
assert c["assistant"]["api"] == "chat_completions"
assert c["assistant"]["base_url"] == "https://ai.api.cloud.yandex.net/v1"
assert c["assistant"]["enabled_by_default"] is False
assert c["traffic_modes"] == ["static", "typical", "live", "historical"]
assert c["traffic_default"] == "static"
for filename in ("PRODUCT_SPEC.md", "FORMULAS.md", "IMPLEMENTATION.md", "ASSISTANT.md"):
    assert (root / "docs" / filename).is_file(), filename
print("Product specification valid: approved methodology v3, Alice Flash, explicit data modes.")
