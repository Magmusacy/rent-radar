#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
import sys
import time
from typing import Any, Dict, Optional

import requests
from bs4 import BeautifulSoup

try:
    from openai import OpenAI
except ImportError:
    print("Missing 'openai' library. Install it: pip install openai")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import googlemaps

from config import CONFIG
from commute import find_route, next_weekday_at, score_location


HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

EXTRACT_SYSTEM_PROMPT = (
    "You are an expert in residential rental listings in {city}. "
    "From the listing text, extract precise, structured data. "
    "Respond ONLY with valid JSON matching the schema, with no extra text and "
    "no markdown fences. Write all free-text fields in {language}."
)

EXTRACT_USER_TEMPLATE = """JSON schema (all fields required; use null or an empty list when something cannot be determined):
{{
  "street": "string or null: full address '<street> <number>, {city}' if a number is given, otherwise '<street>, {city}'. If no street is given at all, use district + '{city}'",
  "district": "string or null: neighborhood/district name",
  "total_price": "number or null: TOTAL monthly cost in {currency}: rent + admin/maintenance fee + ALL utilities (electricity, water, gas, internet, heating, waste). If the listing states them separately, SUM them. If utilities are estimated ('approx. 300'), include that amount. Never report rent alone when utilities are known.",
  "area_m2": "number or null: area of the offered room/flat in m², number only",
  "shared_rooms": "integer or null: how many rooms in the WHOLE flat are shared (i.e. number of flatmates + 1). Studio = 1 (you live alone). A room in a 2-room flat = 2. A room in a 3-room flat = 3. A whole 2-room flat rented entirely = 1 (no flatmates). Only whether there are flatmates matters.",
  "type": "string: one of: studio, shared_room, flat_2room, flat_3room, flat_4room_plus",
  "condition_1_10": "integer 1-10: condition/standard rating (1=needs renovation, 5=ordinary, 8=freshly renovated, 10=premium apartment). If unknown, use 5",
  "amenities": ["list from the text: washer, dishwasher, balcony, air conditioning, garage, internet, furniture, appliances"],
  "red_flags": ["list of suspicious items or empty: e.g. 'no interior photos', 'coal heating', 'suspiciously low price', 'tenants restrictions', 'deposit > 2x rent'"],
  "price_note": "string: one sentence: is the price per m² fair for the local {city} rental market",
  "summary": "string: ONE sentence verdict with the overall take: is the listing worth a look and why (consider price, condition, location, any catches). Start with 'Worth it' / 'Probably worth' / 'Average' / 'Probably skip' / 'Skip'."
}}

LISTING TEXT:
\"\"\"
{text}
\"\"\""""


def fetch_offer_text(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"  ! Could not fetch page: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    main = soup.find("main") or soup.body or soup
    for tag in main(["script", "style", "noscript", "nav", "header", "footer", "svg"]):
        tag.decompose()
    text = main.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:8000]


def extract_offer_data(client: OpenAI, text: str) -> Optional[Dict[str, Any]]:
    lc = CONFIG.listings
    system_prompt = EXTRACT_SYSTEM_PROMPT.format(
        city=CONFIG.city or "the city",
        language=lc.extraction_language,
    )
    user_prompt = EXTRACT_USER_TEMPLATE.format(
        city=CONFIG.city or "the city",
        currency=lc.currency,
        text=text,
    )
    try:
        resp = client.chat.completions.create(
            model=lc.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=800,
        )
        return json.loads(resp.choices[0].message.content)
    except json.JSONDecodeError as e:
        print(f"  ! LLM returned invalid JSON: {e}")
        return None
    except Exception as e:
        print(f"  ! LLM error: {e}")
        return None


def commute_for_address(gmaps, address: str, target) -> Dict[str, Any]:
    address = CONFIG.ensure_city(address)
    routes = {name: find_route(gmaps, address, dest, target, CONFIG.schedule.mode)
              for name, dest in CONFIG.destinations.items()}
    valid = [r for r in routes.values() if r is not None]
    if not valid:
        return {"routes": routes, "avg_min": None,
                "transfers": None, "score": 0.0, "max_min": None}
    return {
        "routes": routes,
        "avg_min": round(sum(r.duration_min for r in valid) / len(valid), 1),
        "max_min": max(r.duration_min for r in valid),
        "transfers": sum(r.transfers for r in valid),
        "score": round(score_location(routes), 2),
    }


def verdict(data: Dict[str, Any], commute: Optional[Dict[str, Any]]) -> str:
    s = CONFIG.scoring
    reasons = []
    if commute and commute["max_min"] is not None and commute["max_min"] > s.max_commute_min:
        reasons.append(f"commute {commute['max_min']} min > {s.max_commute_min}")
    if not commute or commute["avg_min"] is None:
        reasons.append("no route")
    if commute:
        far_walks = [f"{name} {r.walk_min} min" for name, r in commute["routes"].items()
                     if r is not None and r.walk_min > s.max_walk_min]
        if far_walks:
            reasons.append("a lot of walking: " + ", ".join(far_walks))
    condition = data.get("condition_1_10")
    if isinstance(condition, (int, float)) and condition < s.min_condition:
        reasons.append(f"poor condition ({condition}/10)")
    flags = data.get("red_flags") or []
    if flags:
        reasons.append("red flags: " + ", ".join(flags))
    return "; ".join(reasons)


def build_row(i: int, url: str, data: Dict[str, Any],
              commute: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    row: Dict[str, Any] = {"num": i, "url": url}
    row["street"] = data.get("street") or ""
    row["district"] = data.get("district") or ""
    row["total_price"] = data.get("total_price") or ""
    row["area_m2"] = data.get("area_m2") or ""
    row["shared_rooms"] = data.get("shared_rooms") or ""
    row["type"] = data.get("type") or ""
    row["condition_1_10"] = data.get("condition_1_10") or ""
    row["amenities"] = "; ".join(data.get("amenities") or [])
    row["red_flags"] = "; ".join(data.get("red_flags") or [])
    row["price_note"] = data.get("price_note") or ""
    row["summary"] = data.get("summary") or ""

    try:
        p = float(data.get("total_price") or 0)
        a = float(data.get("area_m2") or 0)
        row["price_per_m2"] = round(p / a, 2) if (p and a) else ""
    except (TypeError, ValueError):
        row["price_per_m2"] = ""

    if commute:
        row["avg_commute_min"] = commute["avg_min"] if commute["avg_min"] is not None else ""
        row["transfers"] = commute["transfers"] if commute["transfers"] is not None else ""
        row["commute_score"] = commute["score"]
        for name in CONFIG.destinations:
            r = commute["routes"].get(name)
            row[f"commute_{name}_min"] = r.duration_min if r else ""
            row[f"walk_{name}_min"] = r.walk_min if r else ""

    row["reject"] = verdict(data, commute)
    return row


def main():
    parser = argparse.ArgumentParser(description="Analyze property listings → CSV")
    parser.add_argument("input", nargs="?", default="listings.txt",
                        help="File with URLs (default listings.txt)")
    parser.add_argument("-o", "--output", default="listings.csv",
                        help="Output CSV file (default listings.csv)")
    parser.add_argument("--delay", type=float, default=CONFIG.listings.request_delay_sec,
                        help="Delay in seconds between listings")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only the first N listings (for testing)")
    args = parser.parse_args()

    llm_key_env = CONFIG.listings.llm_api_key_env
    llm_key = os.environ.get(llm_key_env, "")
    gmaps_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not llm_key:
        print(f"MISSING {llm_key_env}. Add it to .env:  {llm_key_env}=...")
        print(f"LLM base URL: {CONFIG.listings.llm_base_url}")
        sys.exit(1)
    if not gmaps_key:
        print("MISSING GOOGLE_MAPS_API_KEY in .env.")
        sys.exit(1)
    if not os.path.exists(args.input):
        print(f"No such file: {args.input}")
        sys.exit(1)

    with open(args.input, encoding="utf-8") as f:
        urls = [ln.strip() for ln in f
                if ln.strip() and ln.strip().startswith("http")]
    if args.limit:
        urls = urls[:args.limit]
    if not urls:
        print("The file contains no URLs.")
        sys.exit(1)

    print(f"Found {len(urls)} URLs. Working...\n")

    client = OpenAI(api_key=llm_key, base_url=CONFIG.listings.llm_base_url)
    gmaps = googlemaps.Client(key=gmaps_key)
    target = next_weekday_at(CONFIG.schedule.hour, CONFIG.schedule.minute)

    commute_cols = [f"commute_{name}_min" for name in CONFIG.destinations]
    walk_cols = [f"walk_{name}_min" for name in CONFIG.destinations]

    fieldnames = [
        "num", "url",
        "street", "district",
        "total_price", "area_m2", "shared_rooms", "type", "price_per_m2",
        "condition_1_10", "amenities", "red_flags", "price_note",
        "avg_commute_min", "transfers", "commute_score",
    ] + commute_cols + walk_cols + ["reject", "summary"]

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url[:90]}…")

            text = fetch_offer_text(url)
            if not text:
                writer.writerow({"num": i, "url": url})
                f.flush()
                continue

            data = extract_offer_data(client, text)
            if not data:
                writer.writerow({"num": i, "url": url})
                f.flush()
                continue

            street = data.get("street") or ""
            price = data.get("total_price")
            area = data.get("area_m2")
            print(f"   → {street or '???'} | {price or '?'} {CONFIG.listings.currency} (incl. utilities) | "
                  f"{area or '?'} m² | {data.get('shared_rooms') or '?'}-person flat")

            commute = commute_for_address(gmaps, street, target) if street else None
            if commute and commute["avg_min"] is not None:
                print(f"   → avg commute {commute['avg_min']} min, "
                      f"score {commute['score']}/10")

            writer.writerow(build_row(i, url, data, commute))
            f.flush()
            time.sleep(args.delay)

    print(f"\n✓ Done. Results: {args.output}")


if __name__ == "__main__":
    main()
