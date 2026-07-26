"""The geocode cache is hit by eight worker threads at once — it must survive that.

Regression test: the first scheduled sweep died with
sqlite3.InterfaceError ("bad parameter or other API misuse") because one
connection was shared across the pool without serialising the calls.
"""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import geo


def test_cache_survives_parallel_workers(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    geo.ensure_cache(conn)

    def fake_lookup(params):
        return [{"lat": "50.05", "lon": "19.94", "display_name": params["q"]}]

    monkeypatch.setattr(geo, "_throttled_get", fake_lookup)

    addresses = [f"Ulica {i}, Kraków" for i in range(40)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda a: geo.geocode(a, conn), addresses))

    assert all(r == (50.05, 19.94) for r in results)
    cached = conn.execute("SELECT COUNT(*) FROM geocache").fetchone()[0]
    assert cached == len(addresses)
