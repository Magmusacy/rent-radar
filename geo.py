#!/usr/bin/env python3
"""Free geocoding and straight-line distance — the server's substitute for Google Maps.

Google Directions gives exact minutes but costs three calls per offer. On the
server we only need "how far is this, roughly", so:

    address -> coordinates   via Nominatim (OpenStreetMap, free, no key)
    coordinates -> km        via haversine (pure maths, no cost)

Results are cached by address in the same SQLite file as the offers, so a street
that shows up in ten listings is geocoded once.

An LLM is deliberately not involved. Without web access it would be recalling
coordinates from memory, and a confidently wrong distance is worse than none.

Straight-line distance ignores rivers and tram lines: al. Pokoju is 3.0 km from
Zabłocie as the crow flies but 29 minutes by tram, because the Vistula forces a
detour to the bridge. Treat km as a coarse sieve; exact minutes come from
commute.py where they matter.
"""
from __future__ import annotations

import math
import sqlite3
import threading
import time
from typing import Optional, Tuple

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy requires a real identifying User-Agent and at most
# one request per second. Both are honoured here.
USER_AGENT = "rent-radar/1.0 (personal apartment search)"
MIN_INTERVAL_S = 1.1

_throttle_lock = threading.Lock()
_last_request = 0.0

Coords = Tuple[float, float]


def ensure_cache(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS geocache ("
        " query TEXT PRIMARY KEY, lat REAL, lon REAL, resolved TEXT, fetched_at TEXT)"
    )
    conn.commit()


def _throttled_get(params: dict) -> list:
    global _last_request
    with _throttle_lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
    r = requests.get(NOMINATIM_URL, params=params,
                     headers={"User-Agent": USER_AGENT}, timeout=25)
    r.raise_for_status()
    return r.json()


def geocode(address: str, conn: Optional[sqlite3.Connection] = None,
            country: str = "pl") -> Optional[Coords]:
    """Address -> (lat, lon). Cached; returns None when nothing matches."""
    query = " ".join(address.split()).strip()
    if not query:
        return None

    if conn is not None:
        row = conn.execute("SELECT lat, lon FROM geocache WHERE query = ?", (query,)).fetchone()
        if row is not None:
            lat, lon = row[0], row[1]
            return (lat, lon) if lat is not None else None

    try:
        hits = _throttled_get({"q": query, "format": "json", "limit": 1,
                               "countrycodes": country})
    except Exception as e:
        print(f"  ! geocoding failed for {query!r}: {e}")
        return None

    coords = (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None
    if conn is not None:
        # a miss is cached too, so a bad address is not retried on every scan
        conn.execute(
            "INSERT OR REPLACE INTO geocache (query, lat, lon, resolved, fetched_at)"
            " VALUES (?, ?, ?, ?, datetime('now'))",
            (query, coords[0] if coords else None, coords[1] if coords else None,
             hits[0].get("display_name", "")[:200] if hits else ""),
        )
        conn.commit()
    return coords


def haversine_km(a: Coords, b: Coords) -> float:
    """Great-circle distance between two (lat, lon) pairs, in kilometres."""
    radius = 6371.0
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat = lat2 - lat1
    dlon = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def distance_km(address: str, destination: str,
                conn: Optional[sqlite3.Connection] = None) -> Optional[float]:
    """Straight-line kilometres between two addresses, or None if either fails."""
    origin = geocode(address, conn)
    target = geocode(destination, conn)
    if not origin or not target:
        return None
    return round(haversine_km(origin, target), 1)


if __name__ == "__main__":
    import sys

    dest = "Zabłocie 43B, Kraków"
    for addr in sys.argv[1:] or ["Topolowa 30, Kraków", "Krowoderskich Zuchów, Kraków"]:
        print(f"{addr:<40} {distance_km(addr, dest)} km")
