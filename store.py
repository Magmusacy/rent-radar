#!/usr/bin/env python3
"""SQLite store for scored offers, keyed by listing URL.

The point of this file is the difference between two dates:

  listed_date  — when the portal says the listing went up (often missing, and a
                 listing can be "refreshed" to look new)
  first_seen   — when *we* first pulled it into the database (always known,
                 never rewritten)

`first_seen` is what makes "new since the last scan" answerable, and the URL
primary key is what stops the same offer being counted twice across scans.

    python store.py --import oferty_krakow_wszystkie.csv   # one-off migration
    python store.py --stats
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

import geo

# In Docker the database lives on a mounted volume, hence the override.
DB_PATH = Path(os.environ.get("DB_PATH") or Path(__file__).parent / "offers.db")

# Everything score_offers.py produces, plus our own bookkeeping up front.
COLUMNS = [
    "url", "first_seen", "last_seen", "times_seen",
    "source", "seller", "listed_date", "days_listed",
    "total_price", "price_check", "stated_totals", "price_per_m2", "area_m2",
    "type", "shared_rooms", "street", "district",
    "distance_km", "commute_min", "transfers", "walk_to_stops_min", "bike_min", "bike_km",
    "walk_all_way_min", "walk_all_way_km", "commute_score", "avg_commute_min",
    "condition_1_10", "amenities", "red_flags", "price_note",
    "reject", "summary", "title", "source_excerpt",
]
MUTABLE = [c for c in COLUMNS if c not in ("url", "first_seen", "times_seen")]


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cols = ",\n  ".join(
        "url TEXT PRIMARY KEY" if c == "url"
        else "times_seen INTEGER NOT NULL DEFAULT 1" if c == "times_seen"
        else f"{c} TEXT"
        for c in COLUMNS
    )
    conn.execute(f"CREATE TABLE IF NOT EXISTS offers (\n  {cols}\n)")
    # a database created by an older version is missing newer columns — add them
    have = {r["name"] for r in conn.execute("PRAGMA table_info(offers)")}
    for c in COLUMNS:
        if c not in have:
            conn.execute(f"ALTER TABLE offers ADD COLUMN {c} TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_first_seen ON offers(first_seen)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_price ON offers(total_price)")
    geo.ensure_cache(conn)  # geocoding cache shares the file, so it survives too
    conn.commit()
    return conn


def known_urls(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT url FROM offers")}


def upsert(conn: sqlite3.Connection, rows: list, seen_at: str | None = None) -> tuple:
    """Insert new offers, refresh the ones we already had. Returns (new, updated)."""
    seen_at = seen_at or datetime.now().isoformat(timespec="seconds")
    known = known_urls(conn)
    new = updated = 0
    for r in rows:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        values = {c: ("" if r.get(c) is None else str(r.get(c))) for c in COLUMNS}
        values.update(url=url, first_seen=seen_at, last_seen=seen_at, times_seen=1)
        placeholders = ", ".join(f":{c}" for c in COLUMNS)
        # first_seen is deliberately absent from the UPDATE clause — it must never move
        updates = ", ".join(f"{c} = excluded.{c}" for c in MUTABLE)
        conn.execute(
            f"INSERT INTO offers ({', '.join(COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(url) DO UPDATE SET {updates}, times_seen = offers.times_seen + 1",
            values,
        )
        if url in known:
            updated += 1
        else:
            new += 1
    conn.commit()
    return new, updated


def all_rows(conn: sqlite3.Connection) -> list:
    """Every offer as a plain dict, with days_known derived from first_seen."""
    out = []
    today = date.today()
    for r in conn.execute("SELECT * FROM offers"):
        d = dict(r)
        try:
            d["days_known"] = (today - datetime.fromisoformat(d["first_seen"]).date()).days
        except (ValueError, TypeError):
            d["days_known"] = ""
        out.append(d)
    return out


def stats(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "offers": q("SELECT COUNT(*) FROM offers"),
        "found_today": q("SELECT COUNT(*) FROM offers WHERE date(first_seen) = date('now','localtime')"),
        "found_3d": q("SELECT COUNT(*) FROM offers WHERE date(first_seen) >= date('now','localtime','-3 day')"),
        "scans_seen_twice_plus": q("SELECT COUNT(*) FROM offers WHERE times_seen > 1"),
        "first_scan": q("SELECT MIN(first_seen) FROM offers") or "—",
    }


def main():
    ap = argparse.ArgumentParser(description="Offer database maintenance")
    ap.add_argument("--import", dest="csv_file", help="import an already-scored CSV")
    ap.add_argument("--seen-at", help="timestamp to record as first_seen for the import")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    conn = connect()
    if args.csv_file:
        with open(args.csv_file, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        new, updated = upsert(conn, rows, args.seen_at)
        print(f"zaimportowano: {new} nowych, {updated} zaktualizowanych")
    if args.stats or not args.csv_file:
        for k, v in stats(conn).items():
            print(f"{k:24} {v}")


if __name__ == "__main__":
    main()
