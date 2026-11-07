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
for name, method in c["methodologies"].items():
    assert method == {"status": "pending", "version": None, "parameters": None}, name
print("Product scope valid; all score methodologies are pending.")
