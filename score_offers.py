#!/usr/bin/env python3
"""Score offers that come from anywhere (portal URLs or raw text) into one CSV.

Unlike `analyze_listings.py`, which only takes a file of URLs it can fetch, this
script accepts a JSON file of items:

    [{"source": "olx",  "url": "https://...", "title": "..."},
     {"source": "fb",   "url": "https://...", "title": "...",
      "text": "raw text of a Facebook post, because FB pages cannot be fetched"}]

Items with "text" skip the HTTP fetch and go straight into the same LLM
extraction + Google Maps commute scoring the rest of the toolkit uses.
Output is a CSV sorted by total price, ready for a spreadsheet.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import googlemaps
from openai import OpenAI

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import geo
import store
from config import CONFIG
from commute import bicycling_route, next_weekday_at, walking_route
from analyze_listings import (
    build_row,
    commute_for_address,
    extract_offer_data,
    fetch_offer_text,
)

# "= 1590 zł", "łącznie 1750 zł", "koszt całkowity: 1550 PLN" — the totals a
# listing states outright, used to catch the LLM mis-adding the components.
TOTAL_PATTERNS = [
    r"=\s*(\d[\d  ]{2,7})\s*(?:zł|zl|pln)",
    r"(?:łącznie|lacznie|razem|koszt całkowity|całkowity koszt|total cost)\D{0,15}"
    r"(\d[\d  ]{2,7})\s*(?:zł|zl|pln)",
]


PL_MONTHS = {m: i for i, m in enumerate(
    ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca",
     "sierpnia", "września", "października", "listopada", "grudnia"], 1)}


def listed_date(text: str):
    """When the listing went up: OLX prints 'Dodane\\n06 lipca 2026',
    Otodom 'Ostatnia aktualizacja: 25.07.2026'. Returns (iso_date, days_ago)."""
    found = None
    # OLX prints either a full date or a relative "dzisiaj / wczoraj o 11:35"
    m = re.search(r"Dodane\s*\n?\s*(dzisiaj|wczoraj)", text, re.IGNORECASE)
    if m:
        found = date.today() - timedelta(days=0 if m.group(1).lower() == "dzisiaj" else 1)
    if not found:
        m = re.search(r"Dodane\s*\n?\s*(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})", text, re.IGNORECASE)
        if m and m.group(2).lower() in PL_MONTHS:
            found = date(int(m.group(3)), PL_MONTHS[m.group(2).lower()], int(m.group(1)))
    if not found:
        m = re.search(r"(?:aktualizacja|Dodano)\D{0,3}(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
        if m:
            found = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    if not found:
        return "", ""
    return found.isoformat(), (date.today() - found).days


AGENCY_HINTS = [
    "biuro nieruchomości", "biuro nieruchomosci", "pośrednik", "posrednik",
    "agent nieruchomości", "nr w biurze", "prowizja biura", "prowizja agenta",
    "prowizja pośrednika", "licencjonowany", "nieruchomości sp.",
]
PRIVATE_HINTS = [
    "bez pośredników", "bez posrednikow", "bezpośrednio od właściciela",
    "od właściciela", "od wlasciciela", "wynajmuję jako właściciel",
    "prywatnie", "pośrednikom dziękuję", "posrednikom dziekuje",
    "wynajmuje jako właściciel", "prowizji brak", "prowizja: brak", "prowizja 0",
]


def seller_type(text: str) -> str:
    """'agencja' / 'prywatny' / '' — who is actually renting the place out.

    The portals' own field wins; free-text hints are the fallback, and the
    'no middlemen' phrasing beats agency keywords because an agency does not
    advertise itself as commission-free."""
    low = text.lower()
    m = re.search(r"typ ogłoszeniodawcy\s*:?\s*\n?\s*([^\n]{3,30})", low)
    if m:
        v = m.group(1)
        if "biuro" in v or "deweloper" in v or "agencj" in v:
            return "agencja"
        if "prywat" in v:
            return "prywatny"
    if re.search(r"\bosoba prywatna\b|\bprywatne\b", low):
        return "prywatny"
    if re.search(r"\bfirma\b", low) and "biuro" in low:
        return "agencja"
    if any(h in low for h in PRIVATE_HINTS):
        return "prywatny"
    if any(h in low for h in AGENCY_HINTS):
        return "agencja"
    return ""


def largest_amount_in_text(text: str):
    """The biggest plausible rent-sized figure the listing mentions.

    Most posts never write a total — they list components ("2000 odstępne,
    czynsz 493, prąd 78"). The sum has to be at least as large as the largest
    component, so anything below it means the model dropped or misread a number.
    """
    amounts = []
    for m in re.finditer(r"(\d[\d  ]{2,7})\s*(?:zł|zl|pln)", text, re.IGNORECASE):
        try:
            v = int(re.sub(r"[  ]", "", m.group(1)))
        except ValueError:
            continue
        if 400 <= v <= 15000:      # below: utilities; above: deposits, sale prices
            amounts.append(v)
    return max(amounts) if amounts else None


def totals_in_text(text: str) -> set:
    found = set()
    for pat in TOTAL_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            try:
                found.add(int(re.sub(r"[  ]", "", m.group(1))))
            except ValueError:
                continue
    return {v for v in found if 300 <= v <= 20000}


def process(item, client, gmaps, target, index, geo_conn=None, destination=None):
    """Score one offer.

    gmaps=None is the server's mode: no Google key, so instead of exact transit
    minutes the row gets a straight-line distance_km from geo.py.
    """
    url = item.get("url", "")
    text = item.get("text")
    if not text:
        text = fetch_offer_text(url)
    if not text:
        return None
    if item.get("title"):
        text = f"{item['title']}\n\n{text}"

    data = extract_offer_data(client, text[:8000])
    if not data:  # the model occasionally returns truncated or empty JSON
        data = extract_offer_data(client, text[:8000])
    if not data:
        return None

    street = data.get("street") or ""
    commute = commute_for_address(gmaps, street, target) if (street and gmaps) else None
    row = build_row(index, url, data, commute)
    row["source"] = item.get("source", "")
    row["title"] = (item.get("title") or "")[:120]
    row["listed_date"], row["days_listed"] = listed_date(text)
    row["seller"] = seller_type(text)

    # Walking legs inside the transit trip are NOT the door-to-door walk; keep both,
    # under names that say which is which.
    for name in CONFIG.destinations:
        row["walk_to_stops_min"] = row.pop(f"walk_{name}_min", "")
        row["commute_min"] = row.pop(f"commute_{name}_min", "")
    if street:
        origin = CONFIG.ensure_city(street)
        dest = destination or list(CONFIG.destinations.values())[0]
        if gmaps:
            walk = walking_route(gmaps, origin, dest)
            if walk:
                row["walk_all_way_min"], row["walk_all_way_km"] = walk
            bike = bicycling_route(gmaps, origin, dest)
            if bike:
                row["bike_min"], row["bike_km"] = bike
        else:
            km = geo.distance_km(origin, dest, geo_conn)
            if km is None and data.get("district"):
                # Nominatim resolves roughly 4 in 5 street addresses; falling back to
                # the district keeps the rest from silently dropping out of alerts
                km = geo.distance_km(CONFIG.ensure_city(data["district"]), dest, geo_conn)
            row["distance_km"] = km

    # Cross-check the LLM's arithmetic against totals written in the listing itself.
    stated = totals_in_text(text)
    price = row.get("total_price")
    row["stated_totals"] = "; ".join(str(v) for v in sorted(stated))
    # keep the words the numbers came from, so any row can be audited without re-fetching
    row["source_excerpt"] = re.sub(r"\s+", " ", text)[:400]
    biggest = largest_amount_in_text(text)
    if stated and isinstance(price, (int, float)) and price:
        if not any(abs(price - v) <= max(20, 0.02 * v) for v in stated):
            row["price_check"] = f"tekst podaje: {', '.join(str(v) for v in sorted(stated))}"
    elif isinstance(price, (int, float)) and price and biggest and price < 0.95 * biggest:
        row["price_check"] = f"suma niższa niż największa kwota w ogłoszeniu ({biggest} zł)"
    elif not price and biggest:
        row["price_check"] = f"brak ceny mimo kwot w treści (największa: {biggest} zł)"
    if row.get("price_check"):
        row["reject"] = "; ".join(filter(None, [row.get("reject"), "cena do weryfikacji"]))

    reach = (f"commute={row.get('commute_min', '?')}" if gmaps
             else f"{row.get('distance_km', '?')} km")
    print(f"  · {row['source']:9} {str(row['total_price'] or '?'):>6} PLN  "
          f"{(row['street'] or '?')[:45]:45} {reach}"
          f"{'  ⚠ ' + row['price_check'] if row.get('price_check') else ''}")
    return row


def main():
    ap = argparse.ArgumentParser(description="Score offers from JSON -> CSV")
    ap.add_argument("input", help="JSON file with items")
    ap.add_argument("-o", "--output", default="offers.csv")
    ap.add_argument("--dest", default=None,
                    help="Single commute destination, e.g. 'Zabłocie 43B, Kraków'. "
                         "Defaults to config.json destinations.")
    ap.add_argument("--max-min", type=int, default=None,
                    help="Drop offers whose commute exceeds this many minutes")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-maps", action="store_true",
                    help="skip Google Maps entirely; record straight-line km instead "
                         "(what the server does — no Google key needed)")
    args = ap.parse_args()

    llm_key = os.environ.get(CONFIG.listings.llm_api_key_env, "")
    gmaps_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not llm_key:
        print(f"Missing {CONFIG.listings.llm_api_key_env} in .env")
        sys.exit(1)
    if not gmaps_key and not args.no_maps:
        print("Missing GOOGLE_MAPS_API_KEY in .env (or pass --no-maps)")
        sys.exit(1)

    if args.dest:
        CONFIG.destinations = {"Zablocie": args.dest}

    items = json.loads(open(args.input, encoding="utf-8").read())
    if args.limit:
        items = items[:args.limit]
    destination = list(CONFIG.destinations.values())[0]
    mode = "straight-line km" if args.no_maps else "Google Maps"
    print(f"{len(items)} items, target: {destination} ({mode})\n")

    client = OpenAI(api_key=llm_key, base_url=CONFIG.listings.llm_base_url)
    gmaps = None if args.no_maps else googlemaps.Client(key=gmaps_key)
    geo_conn = store.connect() if args.no_maps else None
    target = next_weekday_at(CONFIG.schedule.hour, CONFIG.schedule.minute)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(
            lambda p: process(p[1], client, gmaps, target, p[0],
                              geo_conn=geo_conn, destination=destination),
            enumerate(items, 1),
        ))
    rows = [r for r in rows if r]

    # drop duplicates that appear on several portals at once
    seen, unique = set(), []
    for r in rows:
        key = (str(r.get("street", "")).lower(), r.get("total_price"), r.get("area_m2"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    rows = unique

    if args.max_min is not None:
        kept = []
        for r in rows:
            c = r.get("avg_commute_min")
            if isinstance(c, (int, float)) and c <= args.max_min:
                kept.append(r)
        print(f"\n{len(kept)}/{len(rows)} within {args.max_min} min commute")
        rows = kept

    def price_key(r):
        try:
            return float(r.get("total_price") or 10 ** 9)
        except (TypeError, ValueError):
            return 10 ** 9

    rows.sort(key=price_key)
    for i, r in enumerate(rows, 1):
        r["num"] = i

    fieldnames = [
        "num", "source", "seller", "listed_date", "days_listed",
        "total_price", "price_check", "stated_totals",
        "price_per_m2", "area_m2", "type", "shared_rooms", "street", "district",
        "distance_km", "commute_min", "transfers", "walk_to_stops_min", "bike_min", "bike_km",
        "walk_all_way_min", "walk_all_way_km", "commute_score", "avg_commute_min",
        "condition_1_10", "amenities", "red_flags", "price_note",
        "reject", "summary", "title", "url", "source_excerpt",
    ]

    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"\n✓ {len(rows)} offers -> {args.output}")


if __name__ == "__main__":
    main()
