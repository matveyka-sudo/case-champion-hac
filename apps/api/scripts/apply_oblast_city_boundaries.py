"""Apply reviewed settlement contours to a staged address catalogue."""

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from a2_engine.shared.area import select_area_h
from a2_engine.shared.config import load_urban_config
from prepare_moscow_address_catalog import normalize
from pyproj import Geod, Transformer
from shapely import make_valid
from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.ops import orient, transform
from shapely.prepared import prep

MSK50_ZONE_2 = (
    "+proj=tmerc +lat_0=0 +lon_0=38.48333333333 +k=1 +x_0=2250000 "
    "+y_0=-5712900.566 +ellps=krass +towgs84=23.57,-140.95,-79.8,0,0.35,0.79,-0.22 "
    "+units=m +no_defs"
)


def load_boundary_capture(entry, folder):
    raw = (folder / entry["file"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise ValueError(f"Unexpected boundary snapshot for {entry['city']}")
    return json.loads(raw)


def load_registered_city(entry, folder):
    (feature,) = [
        item
        for item in load_boundary_capture(entry, folder)["features"]
        if item["properties"]["externalKey"] == entry["registry_id"]
    ]
    properties = feature["properties"]
    options = properties["options"]
    boundary = feature["geometry"]
    city = re.escape(entry["city"])
    kind = entry.get("settlement_kind", "city")
    if kind == "city":
        prefix = r"(?:город\s+|г\.\s*)"
    elif kind == "settlement":
        prefix = r"(?:рабочий пос[её]лок\s+|пос[её]лок(?: городского типа)?\s+|р\.?\s*п\.\s*|пгт\.?\s+|п\.\s*)"
    else:
        raise ValueError(f"Unknown settlement kind: {kind}")
    description = f"{options.get('note') or ''} {properties['descr']}"
    if (
        properties["category"] != 36832
        or properties["cadastralDistrictsCode"] != 50
        or options["status"] != "001"
        or options["registration_date"] != entry["registration_date"]
        or not re.search(rf"(?<![\w]){prefix}{city}(?![\w-])", description, re.IGNORECASE)
        or boundary["crs"]["properties"]["name"] != "EPSG:3857"
    ):
        raise ValueError(
            f"Record does not identify an active settlement boundary for {entry['city']}"
        )
    project = Transformer.from_crs(3857, 4326, always_xy=True)
    polygon = transform(project.transform, shape(boundary))
    if (
        polygon.is_empty
        or not polygon.is_valid
        or polygon.geom_type not in {"Polygon", "MultiPolygon"}
    ):
        raise ValueError(f"Invalid registered geometry for {entry['city']}")
    return polygon


def load_approved_plan(entry, folder):
    capture = load_boundary_capture(entry, folder)
    if capture["source_sha256"] != entry["source_sha256"] or entry["crs"] != "MSK-50 zone 2":
        raise ValueError(f"Unexpected coordinate table source for {entry['city']}")
    surfaces = []
    for ring in capture["rings"]:
        if len(ring) < 4 or ring[0] != ring[-1]:
            raise ValueError(f"Unclosed plan contour for {entry['city']}")
        # X is northing and Y is easting in the published table.
        polygon = Polygon([(east, north) for _, north, east in ring])
        if not polygon.is_valid:
            raise ValueError(f"Invalid plan contour for {entry['city']}")
        surfaces.append(polygon)
    surfaces.sort(key=lambda polygon: -polygon.area)
    parents = []
    depths = []
    for index, polygon in enumerate(surfaces):
        parent = next((i for i in range(index - 1, -1, -1) if surfaces[i].contains(polygon)), None)
        parents.append(parent)
        depths.append(0 if parent is None else depths[parent] + 1)
    # Enclosed rings are holes. Islands inside holes are filled again.
    boundary = MultiPolygon(
        [
            Polygon(
                polygon.exterior, [surfaces[i].exterior for i, p in enumerate(parents) if p == j]
            )
            for j, polygon in enumerate(surfaces)
            if depths[j] % 2 == 0
        ]
    )
    if (
        boundary.is_empty
        or not boundary.is_valid
        or abs(boundary.area - entry["published_area_m2"]) > entry["area_tolerance_m2"]
    ):
        raise ValueError(f"Plan contours do not reproduce the published area for {entry['city']}")
    project = Transformer.from_crs(MSK50_ZONE_2, 4326, always_xy=True)
    return transform(project.transform, boundary)


def load_rgis_boundary(entry, folder):
    (feature,) = [
        item
        for item in load_boundary_capture(entry, folder)["features"]
        if item["id"] == entry["feature_id"]
    ]
    polygon = shape(feature["geometry"])
    # Layer 380 distinguishes existing boundaries (1) from planned boundaries (0).
    if (
        entry["rgis_layer"] != 380
        or feature["properties"]["feature_id"] != 1
        or polygon.geom_type not in {"Polygon", "MultiPolygon"}
        or polygon.is_empty
    ):
        raise ValueError(f"Invalid existing RGIS boundary for {entry['city']}")
    if not polygon.is_valid and entry.get("repair_zero_area_lines"):
        # Some published rings double back along a line after rounding.
        repaired = make_valid(polygon)
        if repaired.geom_type == "GeometryCollection":
            parts = [
                part for part in repaired.geoms if part.geom_type in {"Polygon", "MultiPolygon"}
            ]
            lines = [
                part
                for part in repaired.geoms
                if part.geom_type in {"LineString", "MultiLineString"}
            ]
            if (
                len(parts) == 1
                and lines
                and len(parts) + len(lines) == len(repaired.geoms)
                and abs(parts[0].area - polygon.area) <= 1e-8 * polygon.area
            ):
                polygon = parts[0]
    if not polygon.is_valid:
        raise ValueError(f"Invalid RGIS geometry for {entry['city']}")
    return polygon


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("catalogue", type=Path, help="Staged copy of the address catalogue")
    parser.add_argument("captures", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    entries = json.loads(args.manifest.read_text())
    sidecar = args.catalogue.with_name("localities.json")
    localities = json.loads(sidecar.read_text())
    existing = {row["name"] for row in localities}
    geod = Geod(ellps="WGS84")
    bands = load_urban_config().area_h_bands

    with sqlite3.connect(args.catalogue) as db:
        for entry in entries:
            name = entry["city"]
            if name in existing:
                raise ValueError(f"{name} already has an accepted boundary")
            if "registry_id" in entry:
                polygon = load_registered_city(entry, args.captures)
                locality_id = "egrn:" + entry["registry_id"]
            elif "rgis_layer" in entry:
                polygon = load_rgis_boundary(entry, args.captures)
                locality_id = f"rgis:{entry['rgis_layer']}:{entry['feature_id']}"
            else:
                polygon = load_approved_plan(entry, args.captures)
                locality_id = "plan:" + entry["municipality_id"] + ":" + entry["decision"]
            district = db.execute(
                "SELECT geometry,name FROM district WHERE id=?", (entry["municipality_id"],)
            ).fetchone()
            if district is None or not entry["municipality_id"].startswith("oblast-"):
                raise ValueError(f"Unknown municipality for {name}")
            scope = shape(json.loads(district[0]))
            if (
                not polygon.covers(Point(*entry["anchor"]))
                or polygon.intersection(scope).area
                < entry.get("municipality_overlap_min", 0.99) * polygon.area
            ):
                raise ValueError(
                    f"Contour is outside the expected settlement or municipality for {name}"
                )
            parts = [polygon] if polygon.geom_type == "Polygon" else list(polygon.geoms)
            area = (
                abs(geod.geometry_area_perimeter(MultiPolygon([orient(part) for part in parts]))[0])
                / 1e6
            )
            h = select_area_h(area, bands=bands).value
            mask = prep(polygon)
            west, south, east, north = polygon.bounds
            rows = db.execute(
                """SELECT id,label,lon,lat FROM address WHERE region=? AND h_seconds IS NULL
                   AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?""",
                ("Московская область", west, east, south, north),
            )
            updates = []
            for key, label, lon, lat in rows:
                if "pedestrian_access_source" in entry and label.split(", ", 1)[0] not in {
                    name,
                    district[1],
                }:
                    continue
                if mask.covers(Point(lon, lat)):
                    label = f"{name}, {label.split(', ', 1)[1]}"
                    updates.append((label, normalize(f"Московская область {label}"), h, key))
            db.executemany("UPDATE address SET label=?,search=?,h_seconds=? WHERE id=?", updates)
            if "pedestrian_access_source" in entry:
                db.execute(
                    "INSERT INTO metadata VALUES (?,?)",
                    (
                        f"walk-area:{locality_id}",
                        json.dumps(
                            {
                                "geometry": mapping(polygon),
                                "excluded_gates": entry.get("excluded_gates", []),
                            }
                        ),
                    ),
                )
            localities.append(
                {
                    "id": locality_id,
                    "name": name,
                    "geometry": mapping(polygon),
                    "h_seconds": h,
                    "source": entry,
                }
            )
            existing.add(name)
            print(f"{name}: {area:.3f} km², H={h}, {len(updates)} address records")

    temp = sidecar.with_suffix(".tmp")
    temp.write_text(json.dumps(localities, ensure_ascii=False))
    temp.replace(sidecar)


if __name__ == "__main__":
    main()
