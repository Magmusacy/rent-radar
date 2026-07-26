#!/usr/bin/env python3
"""One scheduled sweep: collect portal listings, score only the new ones, refresh the dashboard.

    python refresh.py                 # normal run
    python refresh.py --dry-run       # show how many new listings, score nothing

Designed to be run from cron/launchd a couple of times a day. Every URL it has
already scored lives in offers.db (see store.py), so each run costs API calls
only for listings that actually appeared since last time — and the database
remembers when each offer was first seen, which is what "new" really means.

Facebook is not part of this: its feed needs a logged-in browser, so FB offers
stay in fb_items.json and are re-scored only when that file changes.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    # launchd and cron start with a bare environment, so the keys have to be read
    # here too — without this the laptop silently drops to the server's
    # no-Google-Maps mode and loses its commute times.
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

import collect_portals
import store
from config import CONFIG

# Falls back to the first destination in config.json (also gitignored).
CONFIG_DEST = next(iter(CONFIG.destinations.values()), "")

ROOT = Path(__file__).parent
PY = str(ROOT / ".venv/bin/python") if (ROOT / ".venv/bin/python").exists() else sys.executable
MASTER = ROOT / "oferty_krakow_wszystkie.csv"
FILTERED = ROOT / "oferty_krakow_30min.csv"
FB_ITEMS = ROOT / "fb_items.json"
# Your own address does not belong in a public repository — set DEST_ADDRESS in .env.
DEST = os.environ.get("DEST_ADDRESS") or CONFIG_DEST
# The alert profile. Overridable from .env so the server and the laptop can differ.
ALERT_BELOW = float(os.environ.get("ALERT_BELOW", 2200))   # total price, PLN
MAX_COMMUTE = float(os.environ.get("MAX_COMMUTE", 30))     # minutes, when Maps is available
# 6 km calibrated against 55 offers with measured commutes: it keeps all but one
# of the sub-30-minute offers, at the cost of roughly half the alerts being
# further away than they look on a map. Missing a flat costs more than a glance.
MAX_KM = float(os.environ.get("MAX_KM", 6))                # straight-line km, when it is not
SCRATCH = ROOT / ".refresh"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] {msg}", flush=True)


def read_csv(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def write_outputs(rows: list) -> tuple:
    """Master CSV + <=30 min CSV, both sorted by total price; then both dashboards."""
    def price(r):
        p = as_float(r.get("total_price"))
        return (p is None, p or 0)

    rows.sort(key=price)
    for i, r in enumerate(rows, 1):
        r["num"] = i
        r.setdefault("first_seen", "")
        r.setdefault("days_known", "")
    near = [r for r in rows
            if (c := as_float(r.get("commute_min"))) is not None and c <= MAX_COMMUTE]
    for i, r in enumerate(near, 1):
        r["num"] = i

    lead = ["num", "first_seen", "days_known"]
    fields = lead + [c for c in rows[0].keys() if c not in lead]
    for path, data in ((MASTER, rows), (FILTERED, near)):
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(data)

    subprocess.run([PY, str(ROOT / "make_dashboard.py"), str(FILTERED),
                    "-o", str(ROOT / "dashboard.html")], check=True, cwd=ROOT)
    subprocess.run([PY, str(ROOT / "make_dashboard.py"), str(MASTER),
                    "-o", str(ROOT / "dashboard_wszystkie.html")], check=True, cwd=ROOT)
    return len(rows), len(near)


def notify(title: str, body: str):
    """Best-effort macOS notification; silently skipped elsewhere.

    ensure_ascii=False matters: AppleScript has no idea what \\u017c means, so
    escaped Polish characters make the whole script a syntax error.
    """
    quote = lambda s: json.dumps(s, ensure_ascii=False)
    try:
        subprocess.run(
            ["osascript", "-e",
             f"display notification {quote(body)} with title {quote(title)} sound name \"Ping\""],
            check=False, timeout=10)
    except Exception:
        pass


def is_hit(row: dict) -> bool:
    """Does this offer match the profile worth being woken up for?

    Two worlds, one rule: the laptop knows exact transit minutes, the server only
    knows straight-line kilometres. Whichever number exists is the one we judge.
    """
    price = as_float(row.get("total_price"))
    if price is None or price > ALERT_BELOW:
        return False
    minutes = as_float(row.get("commute_min"))
    if minutes is not None:
        return minutes <= MAX_COMMUTE
    km = as_float(row.get("distance_km"))
    return km is not None and km <= MAX_KM


@dataclass
class ScanResult:
    """What one sweep did — everything the bot needs to write a message."""
    listed: int = 0          # offers seen on the portals
    scored: int = 0          # offers actually put through the LLM
    added: int = 0           # rows new to the database
    updated: int = 0         # rows that were already known
    total: int = 0           # size of the database afterwards
    hits: list = field(default_factory=list)
    error: str = ""
    started_at: str = ""
    finished_at: str = ""


def run_scan(include_fb: bool = False, dry_run: bool = False,
             use_maps: bool | None = None, workers: int = 8) -> ScanResult:
    """One full sweep: collect, score what is new, store, rebuild outputs.

    use_maps=None decides by environment: with a Google key we compute exact
    commute minutes, without one we fall back to straight-line kilometres.
    """
    result = ScanResult(started_at=datetime.now().isoformat(timespec="seconds"))
    if use_maps is None:
        use_maps = bool(os.environ.get("GOOGLE_MAPS_API_KEY"))

    SCRATCH.mkdir(exist_ok=True)
    portals_file = SCRATCH / "portals.json"
    conn = store.connect()

    try:
        sys.argv = ["collect_portals", str(portals_file)]
        collect_portals.main()
        listings = json.loads(portals_file.read_text(encoding="utf-8"))
        result.listed = len(listings)

        seen = store.known_urls(conn)
        fresh = [x for x in listings if x["url"] not in seen]
        log(f"{len(listings)} ogłoszeń na portalach, {len(fresh)} nowych od ostatniego przebiegu")

        if include_fb and FB_ITEMS.exists():
            fb = json.loads(FB_ITEMS.read_text(encoding="utf-8"))
            fresh += fb
            log(f"+ {len(fb)} pozycji z Facebooka")

        if not fresh or dry_run:
            log("nic nowego" if not fresh else "--dry-run: kończę bez odpytywania API")
            result.total = len(store.known_urls(conn))
            result.finished_at = datetime.now().isoformat(timespec="seconds")
            return result

        items_file, out_file = SCRATCH / "new_items.json", SCRATCH / "new_scored.csv"
        items_file.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
        cmd = [PY, str(ROOT / "score_offers.py"), str(items_file),
               "-o", str(out_file), "--dest", DEST, "--workers", str(workers)]
        if not use_maps:
            cmd.append("--no-maps")
        subprocess.run(cmd, check=True, cwd=ROOT)

        new_rows = read_csv(out_file)
        result.scored = len(new_rows)
        result.added, result.updated = store.upsert(conn, new_rows)
        log(f"baza: +{result.added} nowych, {result.updated} odświeżonych")

        # Everything downstream is rendered from the database, so the CSVs and the
        # dashboards always agree with it.
        all_rows = store.all_rows(conn)
        result.total = len(all_rows)
        write_outputs(all_rows)
        result.hits = [r for r in new_rows if is_hit(r)]
    except Exception as e:  # a broken sweep must not take the bot down with it
        result.error = f"{type(e).__name__}: {e}"
        log(f"BŁĄD: {result.error}")
    finally:
        conn.close()
        result.finished_at = datetime.now().isoformat(timespec="seconds")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-fb", action="store_true",
                    help="also re-score fb_items.json (only needed after you edit it)")
    ap.add_argument("--no-maps", action="store_true",
                    help="skip Google Maps; use straight-line distance instead")
    args = ap.parse_args()

    result = run_scan(include_fb=args.include_fb, dry_run=args.dry_run,
                      use_maps=False if args.no_maps else None)
    if result.error:
        sys.exit(1)
    if not result.scored:
        return

    log(f"dodano {result.added} ofert · łącznie w bazie {result.total} · trafień: {len(result.hits)}")
    for r in result.hits:
        reach = f"{r['commute_min']} min" if r.get("commute_min") else f"{r.get('distance_km')} km"
        log(f"  ★ {r['total_price']} zł · {reach} · {r['street']} · {r['url']}")
    if result.hits:
        cheapest = min(result.hits, key=lambda r: as_float(r["total_price"]))
        notify(f"Rent Radar: {len(result.hits)} nowych trafień",
               f"od {cheapest['total_price']} zł — {cheapest['street']}")


if __name__ == "__main__":
    main()
