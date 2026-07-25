"""Distance maths and the geocoding cache — no network involved."""
import sqlite3

import geo

ZABLOCIE = (50.0479, 19.9581)
KAZIMIERZ = (50.0510, 19.9450)


def test_haversine_matches_known_distance():
    # ~1 km apart in central Krakow; tolerance covers coordinate rounding
    assert 0.8 < geo.haversine_km(ZABLOCIE, KAZIMIERZ) < 1.3


def test_haversine_is_zero_for_same_point():
    assert geo.haversine_km(ZABLOCIE, ZABLOCIE) == 0


def test_haversine_is_symmetric():
    assert geo.haversine_km(ZABLOCIE, KAZIMIERZ) == geo.haversine_km(KAZIMIERZ, ZABLOCIE)


def test_cache_hit_avoids_the_network(monkeypatch):
    conn = sqlite3.connect(":memory:")
    geo.ensure_cache(conn)
    conn.execute("INSERT INTO geocache VALUES ('Topolowa 30, Kraków', 50.06, 19.95, '', '')")
    conn.commit()

    def explode(*_args, **_kwargs):
        raise AssertionError("cache miss — poszło zapytanie do Nominatim")

    monkeypatch.setattr(geo, "_throttled_get", explode)
    assert geo.geocode("Topolowa 30, Kraków", conn) == (50.06, 19.95)


def test_cached_miss_is_not_retried(monkeypatch):
    """A street Nominatim cannot resolve is remembered as unresolvable."""
    conn = sqlite3.connect(":memory:")
    geo.ensure_cache(conn)
    calls = []

    def one_empty_result(params):
        calls.append(params)
        return []

    monkeypatch.setattr(geo, "_throttled_get", one_empty_result)
    assert geo.geocode("Nieistniejąca 999, Kraków", conn) is None
    assert geo.geocode("Nieistniejąca 999, Kraków", conn) is None
    assert len(calls) == 1
