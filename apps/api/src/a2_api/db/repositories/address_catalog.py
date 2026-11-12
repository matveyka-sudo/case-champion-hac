"""Read-only SQLite queries for the published address catalogue."""

import json
import math
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast

MAX_CANDIDATES = 12
RADIUS_DEGREES = 0.20


def catalogue_path() -> Path:
    return Path(
        os.getenv("A2_ADDRESS_CATALOGUE", "data/processed/moscow-address/catalog.sqlite")
    ).resolve()


def catalogue() -> closing[sqlite3.Connection]:
    db = sqlite3.connect(catalogue_path().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return closing(db)


def search_rows(db: sqlite3.Connection, parts: list[str]) -> list[sqlite3.Row]:
    rows = db.execute(
        """SELECT id,label,region,lat,lon,h_seconds FROM address
           WHERE NOT EXISTS (
               SELECT 1 FROM json_each(?) WHERE instr(' ' || address.search || ' ', value) = 0
           )
           ORDER BY label,id LIMIT 12""",
        (json.dumps(parts),),
    ).fetchall()
    return cast(list[sqlite3.Row], rows)


def find_address(db: sqlite3.Connection, address_id: str) -> sqlite3.Row | None:
    row = db.execute(
        "SELECT id,label,region,lat,lon,h_seconds FROM address WHERE id=?",
        (address_id,),
    ).fetchone()
    return cast(sqlite3.Row | None, row)


def metadata(db: sqlite3.Connection, key: str) -> str:
    return cast(str, db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0])


def district_rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = db.execute("SELECT id,name,geometry FROM district ORDER BY name").fetchall()
    return cast(list[sqlite3.Row], rows)


def restricted_walk_areas(db: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        json.loads(row[0])
        for row in db.execute("SELECT value FROM metadata WHERE key GLOB 'walk-area:*'")
    ]


def candidate_rows(
    db: sqlite3.Connection, lat: float, lon: float, category: str, *, limit: int = MAX_CANDIDATES
) -> list[sqlite3.Row]:
    # Longitude degree length varies with latitude.
    longitude_scale = math.cos(math.radians(lat))
    rows = db.execute(
        """SELECT id,name,lat,lon,location,line FROM destination
           WHERE category=? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
           ORDER BY ((lat-?)*(lat-?))+?*((lon-?)*(lon-?)),
                    CASE location WHEN 'mapped_entrance' THEN 0 ELSE 1 END, id LIMIT ?""",
        (
            category,
            lat - RADIUS_DEGREES,
            lat + RADIUS_DEGREES,
            lon - RADIUS_DEGREES / longitude_scale,
            lon + RADIUS_DEGREES / longitude_scale,
            lat,
            lat,
            longitude_scale**2,
            lon,
            lon,
            limit,
        ),
    ).fetchall()
    return cast(list[sqlite3.Row], rows)
