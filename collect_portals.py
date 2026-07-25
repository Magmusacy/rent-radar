#!/usr/bin/env python3
"""Collect rental listing URLs from OLX and Otodom search pages (Krakow)."""
import json
import re
import sys
import time

import requests

H = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9",
}

OLX_SEARCHES = [
    # flats, max 3500 PLN
    "https://www.olx.pl/nieruchomosci/mieszkania/wynajem/krakow/?search%5Bfilter_float_price%3Ato%5D=3500&search%5Border%5D=created_at%3Adesc",
    # rooms, max 2000 PLN
    "https://www.olx.pl/nieruchomosci/stancje-pokoje/krakow/?search%5Bfilter_float_price%3Ato%5D=2000&search%5Border%5D=created_at%3Adesc",
]

OTODOM_SEARCHES = [
    "https://www.otodom.pl/pl/wyniki/wynajem/mieszkanie/malopolskie/krakow/krakow/krakow?priceMax=3500&by=LATEST&direction=DESC",
    "https://www.otodom.pl/pl/wyniki/wynajem/pokoj/malopolskie/krakow/krakow/krakow?priceMax=2000&by=LATEST&direction=DESC",
]

PAGES = 2  # how many result pages per search


def get(url):
    r = requests.get(url, headers=H, timeout=30)
    r.raise_for_status()
    return r.text


def olx_urls(html):
    out = []
    # hrefs carry a ?search_reason=… suffix, so stop at the query string
    for m in re.finditer(r'href="(/d/oferta/[^"]+?)(?:\?|")', html):
        out.append("https://www.olx.pl" + m.group(1))
    # OLX also proxies otodom offers
    for m in re.finditer(r'href="(https://www\.otodom\.pl/pl/oferta/[^"?]+)', html):
        out.append(m.group(1))
    return out


def otodom_urls(html):
    out = []
    for m in re.finditer(r'"/pl/oferta/([a-zA-Z0-9\-]+)"', html):
        out.append("https://www.otodom.pl/pl/oferta/" + m.group(1))
    return out


def main():
    seen, rows = set(), []

    def add(urls, kind):
        new = 0
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            rows.append({"source": "otodom" if "otodom.pl" in u else kind, "url": u})
            new += 1
        return new

    for base in OLX_SEARCHES:
        for page in range(1, PAGES + 1):
            url = base + (f"&page={page}" if page > 1 else "")
            try:
                found = olx_urls(get(url))
            except Exception as e:
                print(f"! OLX p{page} -> {e}", file=sys.stderr)
                continue
            print(f"OLX p{page} {base[40:75]} -> {len(found)} raw / {add(found, 'olx')} new")
            time.sleep(1.0)

    for base in OTODOM_SEARCHES:
        for page in range(1, PAGES + 1):
            url = base + (f"&page={page}" if page > 1 else "")
            try:
                found = otodom_urls(get(url))
            except Exception as e:
                print(f"! Otodom p{page} -> {e}", file=sys.stderr)
                continue
            print(f"OTODOM p{page} {base[40:75]} -> {len(found)} raw / {add(found, 'otodom')} new")
            time.sleep(1.0)

    out = sys.argv[1] if len(sys.argv) > 1 else "portals.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"\n{len(rows)} unique listing URLs -> {out}")


if __name__ == "__main__":
    main()
