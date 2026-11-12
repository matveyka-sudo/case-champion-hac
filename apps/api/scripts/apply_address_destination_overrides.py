"""Add reviewed pedestrian destinations to a staged address catalogue."""

import argparse
import json
import sqlite3
from pathlib import Path

from prepare_moscow_address_catalog import normalize
from shapely.geometry import Point, shape


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("catalogue", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    localities = {
        row["id"]: (shape(row["geometry"]), row["h_seconds"])
        for row in json.loads(args.catalogue.with_name("localities.json").read_text())
    }
    entries = json.loads(args.manifest.read_text())
    with sqlite3.connect(args.catalogue.resolve().as_uri() + "?mode=rw", uri=True) as db:
        (source,) = db.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
        for entry in entries:
            key = entry["id"]
            if entry["routing_extract"] != source:
                raise ValueError(f"Destination {key} was reviewed against a different extract")
            lon, lat = entry["coordinates"]
            boundary, h = localities[entry["locality_id"]]
            if "pedestrian_exit" in entry or "medical_walk_municipality" in entry:
                if entry["category"] != "hospital" or boundary.covers(Point(lon, lat)):
                    raise ValueError(f"Invalid external hospital {key}")
                scope_key = f"walk-area:{entry['locality_id']}"
                (scope_raw,) = db.execute(
                    "SELECT value FROM metadata WHERE key=?", (scope_key,)
                ).fetchone()
                scope = json.loads(scope_raw)
                if municipality := entry.get("medical_walk_municipality"):
                    (geometry,) = db.execute(
                        "SELECT geometry FROM district WHERE id=?", (municipality,)
                    ).fetchone()
                    walk_area = shape(json.loads(geometry))
                    if not walk_area.covers(Point(lon, lat)) or not walk_area.intersects(boundary):
                        raise ValueError(f"Invalid municipal approach for {key}")
                    entry["medical_walk_geometry"] = json.loads(geometry)
                hospitals = {item["id"]: item for item in scope.get("reviewed_hospitals", [])}
                hospitals[key] = entry
                scope["reviewed_hospitals"] = list(hospitals.values())
                db.execute(
                    "UPDATE metadata SET value=? WHERE key=?", (json.dumps(scope), scope_key)
                )
                print(f"{key}: {entry['name']}")
                continue
            if not boundary.covers(Point(lon, lat)):
                raise ValueError(f"Destination {key} is outside its reviewed locality")
            if "address_label" in entry:
                label = entry["address_label"]
                region = "Московская область"
                address = (key, label, normalize(f"{region} {label}"), region, lon, lat, h)
                existing = db.execute("SELECT * FROM address WHERE id=?", (key,)).fetchone()
                if existing is not None and existing != address:
                    raise ValueError(f"Conflicting address {key}")
                if existing is None:
                    db.execute("INSERT INTO address VALUES(?,?,?,?,?,?,?)", address)
                db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES (?,?)",
                    (f"address:{key}", json.dumps(entry, ensure_ascii=False, sort_keys=True)),
                )
                print(f"{key}: {label}")
                continue
            row = (key, entry["category"], entry["name"], lon, lat, entry["location"], "")
            existing = db.execute(
                "SELECT id,category,name,lon,lat,location,line FROM destination WHERE id=?",
                (key,),
            ).fetchall()
            if existing and existing != [row]:
                raise ValueError(f"Conflicting destination {key}")
            if not existing:
                db.execute("INSERT INTO destination VALUES(?,?,?,?,?,?,?)", row)
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?,?)",
                (f"destination:{key}", json.dumps(entry, ensure_ascii=False, sort_keys=True)),
            )
            print(f"{key}: {entry['name']}")


if __name__ == "__main__":
    main()
