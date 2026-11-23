"""Local Soryuka Three.js visual preferences.

One existing SQLite database, no new services, no Cloud.ru requests.
These cosmetic settings do not modify the protected personality canon.
"""
from __future__ import annotations

import json
import math

from .soul_store import SoulStore, StoreError, now

DEFAULTS = {"fog": 0.0008, "light": 1.0, "speed": 1.0}
RANGES = {"fog": (0.0, 0.003, 0.00005),
          "light": (0.2, 2.0, 0.1),
          "speed": (0.25, 2.0, 0.05)}
SCHEMA = """
CREATE TABLE IF NOT EXISTS mirror_graphics_settings (
 name TEXT PRIMARY KEY,
 value_json TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
"""


def validate_graphics(values: object) -> dict[str, float]:
    if not isinstance(values, dict) or set(values) != set(DEFAULTS):
        raise StoreError("Graphics settings must include fog, light and speed")
    accepted = {}
    for name, (minimum, maximum, step) in RANGES.items():
        value = values[name]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise StoreError(f"Invalid graphics setting: {name}")
        if not minimum <= value <= maximum:
            raise StoreError(f"Out-of-range graphics setting: {name}")
        snapped = round((value - minimum) / step) * step + minimum
        accepted[name] = round(max(minimum, min(maximum, snapped)), 8)
    return accepted


def read_graphics(store: SoulStore) -> dict:
    with store.db() as conn:
        conn.executescript(SCHEMA)
        row = conn.execute(
            "SELECT value_json,updated_at FROM mirror_graphics_settings WHERE name='scene'"
        ).fetchone()
    if not row:
        return {"settings": dict(DEFAULTS), "source": "defaults"}
    try:
        saved = json.loads(row["value_json"])
        settings = validate_graphics(saved)
    except (ValueError, TypeError, KeyError):
        return {"settings": dict(DEFAULTS), "source": "defaults"}
    return {"settings": settings, "source": "sqlite", "updated_at": row["updated_at"]}


def write_graphics(store: SoulStore, values: object) -> dict:
    settings = validate_graphics(values)
    timestamp = now()
    with store.db() as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            """INSERT INTO mirror_graphics_settings(name,value_json,updated_at)
               VALUES('scene',?,?)
               ON CONFLICT(name) DO UPDATE SET
                   value_json=excluded.value_json,updated_at=excluded.updated_at""",
            (json.dumps(settings, separators=(",", ":")), timestamp)
        )
    return {"settings": settings, "source": "sqlite", "updated_at": timestamp}
