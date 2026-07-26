#!/usr/bin/env python3
"""Turn a scored-offers CSV into a self-contained interactive HTML dashboard.

    python make_dashboard.py oferty_krakow_30min.csv -o dashboard.html

The output is one file with the data embedded — no server, no CDN, no network.
Open it in a browser, filter, sort, click through to the offers.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

try:
    # DEST_ADDRESS lives in .env, so a standalone run names the same place a
    # scheduled one does
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

SOURCE_LABELS = {
    "olx": "OLX",
    "otodom": "Otodom",
    "fb-group": "Facebook",
    "fb-marketplace": "Facebook",
}


def num(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def load(path: Path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        raw = list(csv.DictReader(fh))
    for r in raw:
        rows.append({
                "price": num(r.get("total_price")),
                "commute": num(r.get("commute_min") or r.get("avg_commute_min")),
                # walking legs inside the transit trip (to the stop, from the stop)
                "walkStops": num(r.get("walk_to_stops_min") or r.get("walk_Zablocie_min")),
                # door-to-door on foot, the whole way
                "walkAll": num(r.get("walk_all_way_min")),
                "walkKm": num(r.get("walk_all_way_km")),
                "bike": num(r.get("bike_min")),
                "bikeKm": num(r.get("bike_km")),
                "priceCheck": r.get("price_check") or "",
                "days": num(r.get("days_listed")),
                "listed": r.get("listed_date") or "",
                # when *we* first pulled it in — always known, unlike the portal's date
                "found": num(r.get("days_known")),
                "foundAt": (r.get("first_seen") or "")[:16].replace("T", " "),
                "area": num(r.get("area_m2")),
                "ppm": num(r.get("price_per_m2")),
                "transfers": num(r.get("transfers")),
                "condition": num(r.get("condition_1_10")),
                "type": r.get("type") or "",
                # how many rooms the whole flat has — the thing you want to know
                # when the offer is one room inside it
                "rooms": num(r.get("shared_rooms")),
                "street": r.get("street") or "",
                "district": r.get("district") or "",
                "source": SOURCE_LABELS.get(r.get("source", ""), r.get("source", "")),
                "seller": r.get("seller") or "",
                "origin": r.get("source", ""),
                "flags": r.get("red_flags") or "",
                "amenities": r.get("amenities") or "",
                "note": r.get("price_note") or "",
                "summary": r.get("summary") or "",
                "url": r.get("url") or "",
            })
    return [r for r in rows if r["price"] is not None and r["commute"] is not None]


TEMPLATE = """<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rent Radar — __TITLE__</title>
<style>
  :root {
    color-scheme: light;
    --surface-0: #f4f3f0;
    --surface-1: #fcfcfb;
    --border:    #dedcd6;
    --grid:      #ebe9e4;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #77756f;
    --series-1: #2a78d6;
    --series-2: #eb6834;
    --series-3: #1baf7a;
    --good: #0ca30c;
    --warning: #fab219;
    --critical: #d03b3b;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
      color-scheme: dark;
      --surface-0: #121211;
      --surface-1: #1a1a19;
      --border:    #35352f;
      --grid:      #2a2a27;
      --text-primary:   #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted:     #97968c;
      --series-1: #3987e5;
      --series-2: #d95926;
      --series-3: #199e70;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --surface-0: #121211;
    --surface-1: #1a1a19;
    --border:    #35352f;
    --grid:      #2a2a27;
    --text-primary:   #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted:     #97968c;
    --series-1: #3987e5;
    --series-2: #d95926;
    --series-3: #199e70;
  }

  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 20px;
    background: var(--surface-0); color: var(--text-primary);
    font: 14px/1.5 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  /* the table carries ~15 columns — give it the whole window, not a reading column */
  .wrap { max-width: min(2000px, 98vw); margin: 0 auto; }
  h1 { font-size: 20px; margin: 0 0 2px; letter-spacing: -0.01em; }
  .sub { color: var(--text-secondary); margin: 0 0 20px; font-size: 13px; }
  .card {
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px; margin-bottom: 16px;
  }

  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 16px; }
  .kpi { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
  .kpi .label { color: var(--text-secondary); font-size: 12px; }
  .kpi .value { font-size: 26px; font-weight: 600; letter-spacing: -0.02em; margin-top: 2px; }
  .kpi .value small { font-size: 13px; font-weight: 400; color: var(--text-muted); }

  .filters { display: flex; flex-wrap: wrap; gap: 16px 22px; align-items: flex-end; }
  .f { display: flex; flex-direction: column; gap: 4px; }
  .f label { font-size: 12px; color: var(--text-secondary); }
  .f input[type=range] { width: 170px; accent-color: var(--series-1); }
  .f select, .f input[type=search] {
    background: var(--surface-0); color: var(--text-primary);
    border: 1px solid var(--border); border-radius: 6px; padding: 5px 8px; font: inherit; font-size: 13px;
  }
  .f input[type=search] { width: 190px; }
  .chips { display: flex; gap: 6px; }
  .chip {
    display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
    border: 1px solid var(--border); border-radius: 999px; padding: 4px 11px 4px 8px;
    font-size: 12.5px; color: var(--text-secondary); background: var(--surface-0); user-select: none;
  }
  .chip[aria-pressed="true"] { color: var(--text-primary); border-color: var(--text-muted); }
  .chip[aria-pressed="false"] { opacity: 0.45; }
  .chip:disabled { opacity: 0.25; cursor: default; }
  .chip .dot { width: 9px; height: 9px; border-radius: 50%; }
  .chip .dot.sq { border-radius: 2px; }
  .chip .dot.tri { border-radius: 1px; transform: rotate(45deg) scale(0.86); }
  .reset { margin-left: auto; background: none; border: 1px solid var(--border); color: var(--text-secondary);
           border-radius: 6px; padding: 6px 12px; font: inherit; font-size: 13px; cursor: pointer; }

  .charthead { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 2px; }
  .charthead h2 { font-size: 14px; margin: 0; font-weight: 600; }
  .charthead .hint { font-size: 12px; color: var(--text-muted); }
  svg { display: block; width: 100%; height: auto; overflow: visible; }
  .axis-line { stroke: var(--border); stroke-width: 1; }
  .grid-line { stroke: var(--grid); stroke-width: 1; }
  .tick { fill: var(--text-muted); font-size: 11px; }
  .axis-title { fill: var(--text-secondary); font-size: 11.5px; }
  .mark { stroke: var(--surface-1); stroke-width: 2; cursor: pointer; }
  .mark:hover { stroke: var(--text-primary); }

  #tip {
    position: fixed; pointer-events: none; opacity: 0; transition: opacity .1s;
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 10px; font-size: 12.5px; max-width: 260px; z-index: 9;
    box-shadow: 0 6px 20px rgba(0,0,0,.16);
  }
  #tip b { display: block; font-size: 13px; margin-bottom: 2px; }
  #tip .row { color: var(--text-secondary); }

  table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
  .tablewrap { overflow-x: auto; }
  th, td { text-align: left; padding: 6px 9px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
  th { position: sticky; top: 0; background: var(--surface-1); cursor: pointer; font-size: 12px;
       color: var(--text-secondary); font-weight: 600; border-bottom: 1px solid var(--border); }
  th[data-dir]::after { content: " ↕"; color: var(--text-muted); }
  th[data-dir="asc"]::after { content: " ↑"; color: var(--text-primary); }
  th[data-dir="desc"]::after { content: " ↓"; color: var(--text-primary); }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.wide { white-space: normal; min-width: 240px; max-width: 420px; color: var(--text-secondary); }
  tbody tr:hover { background: var(--surface-0); }
  a { color: var(--series-1); }
  .src { display: inline-flex; align-items: center; gap: 6px; }
  .src .dot { width: 8px; height: 8px; border-radius: 50%; }
  .src .dot.sq { border-radius: 2px; }
  .src .dot.tri { border-radius: 1px; transform: rotate(45deg) scale(0.86); }
  .flag { color: var(--critical); }
  .muted { color: var(--text-muted); }
  .legendnote { font-size: 12px; color: var(--text-muted); margin-top: 8px; }
  .tabs { display: flex; gap: 8px; margin: 0 0 16px; }
  .tab {
    display: inline-block; padding: 7px 14px; border-radius: 8px; font-size: 13px;
    border: 1px solid var(--border); background: var(--surface-1);
    color: var(--text-secondary); text-decoration: none;
  }
  .tab.active { background: var(--series-1); border-color: var(--series-1); color: #fff; font-weight: 600; }
  .tab:not(.active):hover { color: var(--text-primary); border-color: var(--text-muted); }
  .fresh { color: var(--good); font-weight: 600; }
  .empty { padding: 30px; text-align: center; color: var(--text-muted); }
  footer { color: var(--text-muted); font-size: 12px; margin-top: 6px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Rent Radar — Kraków</h1>
  <p class="sub">__SUBTITLE__</p>

  <nav class="tabs">
    <span class="tab active">__THIS_LABEL__</span>
    <a class="tab" href="__PEER_FILE__">__PEER_LABEL__ →</a>
  </nav>

  <div class="kpis" id="kpis"></div>

  <div class="card">
    <div class="filters">
      <div class="f">
        <label>Źródło</label>
        <div class="chips" id="sourceChips"></div>
      </div>
      <div class="f">
        <label>Od kogo</label>
        <div class="chips" id="sellerChips"></div>
      </div>
      <div class="f">
        <label>Znalezione przez skaner</label>
        <div class="chips" id="foundChips"></div>
      </div>
      <div class="f">
        <label>Maks. cena: <b id="priceLabel"></b></label>
        <input type="range" id="price" min="0" max="100" value="100">
      </div>
      <div class="f">
        <label>Maks. dojazd: <b id="commuteLabel"></b></label>
        <input type="range" id="commute" min="0" max="100" value="100">
      </div>
      <div class="f">
        <label for="age">Dodane w ciągu</label>
        <select id="age">
          <option value="">dowolnie</option>
          <option value="1">1 dnia</option>
          <option value="3">3 dni</option>
          <option value="7">7 dni</option>
          <option value="14">14 dni</option>
        </select>
      </div>
      <div class="f">
        <label>Rodzaj</label>
        <div class="chips" id="typeChips"></div>
      </div>
      <div class="f">
        <label for="q">Szukaj (adres, dzielnica)</label>
        <input type="search" id="q" placeholder="np. Podgórze">
      </div>
      <button class="reset" id="reset">Wyczyść</button>
    </div>
  </div>

  <div class="card">
    <div class="charthead">
      <h2>Cena a dojazd na __DEST__</h2>
      <span class="hint">każdy punkt to oferta · najedź po szczegóły · kliknij, by otworzyć ogłoszenie</span>
    </div>
    <svg id="scatter" viewBox="0 0 900 380" role="img" aria-label="Wykres: cena całkowita względem czasu dojazdu"></svg>
  </div>

  <div class="card">
    <div class="charthead">
      <h2 id="tableTitle">Oferty</h2>
      <span class="hint">kliknij nagłówek, by posortować</span>
    </div>
    <div class="tablewrap">
      <table>
        <thead><tr id="thead"></tr></thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
    <div class="empty" id="empty" hidden>Żadna oferta nie pasuje do filtrów.</div>
    <p class="legendnote">
      <b>Dojazd MPK</b> — drzwi w drzwi komunikacją miejską (przyjazd na 9:00 w dzień roboczy).
      <b>w tym pieszo</b> — dojście do przystanku i od przystanku, część tego dojazdu.
      <b>Rowerem</b> — cała trasa na rowerze, drzwi w drzwi.
      <b>Całkiem pieszo</b> — cała trasa na piechotę, bez tramwaju.
      <b>Dodane</b> — ile dni temu ogłoszenie się pojawiło (OLX: data dodania, Otodom: ostatnia
      aktualizacja). „—" znaczy, że portal jej nie podał.
      <b>Od kogo</b> — z pola „typ ogłoszeniodawcy" na portalu, a gdy go brak, z treści
      („bez pośredników" → właściciel, „prowizja biura" → agencja). „—" znaczy, że nie dało się
      ustalić; przy agencji licz się z jednorazową, bezzwrotną prowizją.
      <b>Znalezione</b> — data i godzina, o której nasz skaner zobaczył ofertę po raz pierwszy
      (z bazy `offers.db`). Skanujemy kilka razy dziennie, więc liczy się godzina, nie sam dzień;
      to jest wiarygodna miara „nowe od ostatniego skanu", bo nie da się jej odświeżyć.
      <b>⚠</b> przy cenie — kwota z ogłoszenia nie zgadza się z wyliczoną, sprawdź kolumnę
      <code>stated_totals</code> w CSV.
    </p>
  </div>

  <footer>__FOOTER__</footer>
</div>
<div id="tip" role="tooltip"></div>

<script>
const DATA = __DATA__;
const SERIES = [
  { key: "OLX",      color: "var(--series-1)", shape: "circle" },
  { key: "Otodom",   color: "var(--series-2)", shape: "square" },
  { key: "Facebook", color: "var(--series-3)", shape: "triangle" },
];
const COLOR = Object.fromEntries(SERIES.map(s => [s.key, s.color]));
const SHAPE = Object.fromEntries(SERIES.map(s => [s.key, s.shape]));
const SHAPE_CLASS = { circle: "", square: "sq", triangle: "tri" };

const zl = v => v == null ? "—" : Math.round(v).toLocaleString("pl-PL") + " zł";
const mn = v => v == null ? "—" : Math.round(v) + " min";
const median = a => { if (!a.length) return null; const s=[...a].sort((x,y)=>x-y); const m=s.length>>1;
                      return s.length%2 ? s[m] : (s[m-1]+s[m])/2; };

const maxPrice = Math.max(...DATA.map(d => d.price));
const maxCommute = Math.max(...DATA.map(d => d.commute));
const state = {
  sources: new Set(SERIES.map(s => s.key)),
  price: maxPrice, commute: maxCommute, types: null, q: "", age: null, found: null, seller: null,
  sort: { col: "price", dir: "asc" },
};

/* ---------- filters ---------- */
const chipsEl = document.getElementById("sourceChips");
SERIES.forEach(s => {
  const b = document.createElement("button");
  b.className = "chip"; b.type = "button"; b.setAttribute("aria-pressed", "true");
  b.innerHTML = `<span class="dot ${SHAPE_CLASS[s.shape]}" style="background:${s.color}"></span>${s.key}`;
  b.onclick = () => {
    const on = b.getAttribute("aria-pressed") === "true";
    b.setAttribute("aria-pressed", String(!on));
    on ? state.sources.delete(s.key) : state.sources.add(s.key);
    render();
  };
  chipsEl.appendChild(b);
});

const SELLER_OPTS = [
  { label: "wszyscy", value: null },
  { label: "👤 właściciel", value: "prywatny" },
  { label: "🏢 agencja", value: "agencja" },
];
const sellerChips = document.getElementById("sellerChips");
SELLER_OPTS.forEach(o => {
  const b = document.createElement("button");
  b.className = "chip"; b.type = "button";
  b.setAttribute("aria-pressed", String(o.value === state.seller));
  b.textContent = o.label;
  b.onclick = () => {
    state.seller = o.value;
    [...sellerChips.children].forEach((c, i) =>
      c.setAttribute("aria-pressed", String(SELLER_OPTS[i].value === state.seller)));
    render();
  };
  sellerChips.appendChild(b);
});

// single-select segmented control over first_seen (our own discovery date)
// The most recent sweep, so "what came in just now" is one click away.
// Declared before the chips that name it — a const used above its declaration
// throws, and one such error takes the whole dashboard down.
const LAST_SCAN = DATA.reduce((best, d) => (d.foundAt > best ? d.foundAt : best), "");
const LAST_SCAN_LABEL = LAST_SCAN ? LAST_SCAN.slice(11, 16) : "";

const FOUND_OPTS = [
  { label: "wszystkie", value: null },
  { label: `ostatni skan ${LAST_SCAN_LABEL}`, value: "last" },
  { label: "dzisiaj", value: 0 },
  { label: "ostatnie 3 dni", value: 3 },
];
const foundChips = document.getElementById("foundChips");
FOUND_OPTS.forEach(o => {
  const b = document.createElement("button");
  b.className = "chip"; b.type = "button";
  b.setAttribute("aria-pressed", String(o.value === state.found));
  b.textContent = o.label;
  b.onclick = () => {
    state.found = o.value;
    [...foundChips.children].forEach((c, i) =>
      c.setAttribute("aria-pressed", String(FOUND_OPTS[i].value === state.found)));
    render();
  };
  foundChips.appendChild(b);
});

// The extractor's vocabulary, grouped into the four things you actually choose
// between. Counts come from the data, so an empty bucket says so.
const TYPE_OPTS = [
  { label: "wszystkie", types: null },
  { label: "👥 pokój", types: ["shared_room"] },
  { label: "🏠 kawalerka", types: ["studio", "flat_1room"] },
  { label: "🛏 2 pokoje", types: ["flat_2room"] },
  { label: "🛏 3+ pokoje", types: ["flat_3room", "flat_4room_plus"] },
];
const typeChips = document.getElementById("typeChips");
TYPE_OPTS.forEach(o => {
  const n = o.types ? DATA.filter(d => o.types.includes(d.type)).length : DATA.length;
  const b = document.createElement("button");
  b.className = "chip"; b.type = "button";
  b.setAttribute("aria-pressed", String(o.types === state.types));
  b.innerHTML = `${o.label} <span class="muted">${n}</span>`;
  b.disabled = n === 0;
  b.onclick = () => {
    state.types = o.types;
    [...typeChips.children].forEach((c, i) =>
      c.setAttribute("aria-pressed", String(TYPE_OPTS[i].types === state.types)));
    render();
  };
  typeChips.appendChild(b);
});

// A room is never just "a room": renting one in a 2-room flat and in a 6-room
// flat are different lives, so say which whenever the listing revealed it.
const TYPE_NAMES = {
  studio: "kawalerka", flat_1room: "1 pokój", flat_2room: "2 pokoje",
  flat_3room: "3 pokoje", flat_4room_plus: "4+ pokoi",
};
const typeLabel = d => {
  if (d.type === "shared_room") {
    return d.rooms ? `pokój w ${d.rooms}-pok. <span class="muted">(${d.rooms - 1} współlok.)</span>`
                   : 'pokój <span class="muted">(? pok.)</span>';
  }
  return TYPE_NAMES[d.type] || (d.type || "—").replace(/_/g, " ");
};

const priceEl = document.getElementById("price");
const commuteEl = document.getElementById("commute");
priceEl.min = 500; priceEl.max = Math.ceil(maxPrice / 100) * 100; priceEl.step = 50; priceEl.value = priceEl.max;
commuteEl.min = 5; commuteEl.max = Math.ceil(maxCommute); commuteEl.step = 1; commuteEl.value = commuteEl.max;

priceEl.oninput = () => { state.price = +priceEl.value; render(); };
commuteEl.oninput = () => { state.commute = +commuteEl.value; render(); };
document.getElementById("age").onchange = e => { state.age = e.target.value ? +e.target.value : null; render(); };
document.getElementById("q").oninput = e => { state.q = e.target.value.toLowerCase().trim(); render(); };
document.getElementById("reset").onclick = () => {
  state.sources = new Set(SERIES.map(s => s.key));
  [...chipsEl.children].forEach(c => c.setAttribute("aria-pressed", "true"));
  state.price = +priceEl.max; priceEl.value = priceEl.max;
  state.commute = +commuteEl.max; commuteEl.value = commuteEl.max;
  state.types = null;
  [...typeChips.children].forEach((c, i) =>
    c.setAttribute("aria-pressed", String(TYPE_OPTS[i].types === null)));
  state.q = ""; document.getElementById("q").value = "";
  state.age = null; document.getElementById("age").value = "";
  state.found = null;
  [...foundChips.children].forEach((c, i) =>
    c.setAttribute("aria-pressed", String(FOUND_OPTS[i].value === null)));
  state.seller = null;
  [...sellerChips.children].forEach((c, i) =>
    c.setAttribute("aria-pressed", String(SELLER_OPTS[i].value === null)));
  render();
};

const visible = () => DATA.filter(d =>
  state.sources.has(d.source) &&
  d.price <= state.price &&
  d.commute <= state.commute &&
  (state.types === null || state.types.includes(d.type)) &&
  // unknown posting date is kept out when filtering by freshness — an offer we
  // cannot date is not evidence of a fresh one
  (state.age == null || (d.days != null && d.days <= state.age)) &&
  (state.found == null
    // one sweep writes its rows within a few minutes, so compare on the hour
    ? true
    : state.found === "last"
      ? d.foundAt.slice(0, 13) === LAST_SCAN.slice(0, 13)
      : d.found != null && d.found <= state.found) &&
  (state.seller == null || d.seller === state.seller) &&
  (!state.q || (d.street + " " + d.district).toLowerCase().includes(state.q))
);

const age = d => d.days == null ? "—"
  : d.days <= 0 ? '<span class="fresh">dziś</span>'
  : d.days === 1 ? '<span class="fresh">wczoraj</span>'
  : d.days + " dni";

// With several sweeps a day, "dziś" says nothing — the hour is the useful part.
const foundLabel = d => {
  if (d.found == null || !d.foundAt) return "—";
  const time = d.foundAt.slice(11, 16);
  if (d.found <= 0) return `<span class="fresh">dziś ${time}</span>`;
  if (d.found === 1) return `wczoraj ${time}`;
  const [y, m, day] = d.foundAt.slice(0, 10).split("-");
  return `${day}.${m} ${time}`;
};


/* ---------- KPI row ---------- */
function kpis(rows) {
  const prices = rows.map(r => r.price), commutes = rows.map(r => r.commute);
  const cheapest = rows.length ? rows.reduce((a, b) => a.price <= b.price ? a : b) : null;
  const items = [
    ["Ofert po filtrach", rows.length, ""],
    ["Mediana ceny", zl(median(prices)), ""],
    ["Najtańsza", cheapest ? zl(cheapest.price) : "—", cheapest ? cheapest.district || cheapest.street : ""],
    ["Mediana dojazdu", mn(median(commutes)), "na __DEST__"],
  ];
  document.getElementById("kpis").innerHTML = items.map(([l, v, s]) =>
    `<div class="kpi"><div class="label">${l}</div><div class="value">${v}${s ? ` <small>${s}</small>` : ""}</div></div>`
  ).join("");
}

/* ---------- scatter ---------- */
const svg = document.getElementById("scatter");
const W = 900, H = 380, M = { t: 14, r: 16, b: 44, l: 62 };
const tip = document.getElementById("tip");

function markPath(shape, x, y, r) {
  if (shape === "square") return `<rect x="${x - r}" y="${y - r}" width="${2 * r}" height="${2 * r}" rx="1.5"`;
  if (shape === "triangle") return `<polygon points="${x},${y - r - 1} ${x + r + 1},${y + r} ${x - r - 1},${y + r}"`;
  return `<circle cx="${x}" cy="${y}" r="${r}"`;
}

// round the axis top up to a readable step, so ticks land on 1000 zł / 5 min — and
// keep the top close to the data, so the plot never wastes half its width
function niceScale(rawMax, maxTicks) {
  const mag = Math.pow(10, Math.floor(Math.log10(rawMax)));
  for (const m of [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10]) {
    const step = m * mag;
    if (rawMax / step <= maxTicks) return { step, max: Math.ceil(rawMax / step) * step };
  }
  return { step: rawMax, max: rawMax };
}

function scatter(rows) {
  const xs = niceScale(Math.max(10, ...rows.map(d => d.commute)) * 1.02, 7);
  const ys = niceScale(Math.max(1000, ...rows.map(d => d.price)) * 1.04, 6);
  const xMax = xs.max, yMax = ys.max;
  const yTicks = Math.round(yMax / ys.step), xTicks = Math.round(xMax / xs.step);
  const px = v => M.l + (v / xMax) * (W - M.l - M.r);
  const py = v => H - M.b - (v / yMax) * (H - M.t - M.b);

  let out = "";
  for (let i = 0; i <= yTicks; i++) {
    const v = (yMax / yTicks) * i, y = py(v);
    out += `<line class="grid-line" x1="${M.l}" y1="${y}" x2="${W - M.r}" y2="${y}"/>`;
    out += `<text class="tick" x="${M.l - 10}" y="${y + 4}" text-anchor="end">${Math.round(v).toLocaleString("pl-PL")}</text>`;
  }
  for (let i = 0; i <= xTicks; i++) {
    const v = (xMax / xTicks) * i, x = px(v);
    out += `<text class="tick" x="${x}" y="${H - M.b + 18}" text-anchor="middle">${Math.round(v)}</text>`;
  }
  out += `<line class="axis-line" x1="${M.l}" y1="${H - M.b}" x2="${W - M.r}" y2="${H - M.b}"/>`;
  out += `<text class="axis-title" x="${(W + M.l) / 2}" y="${H - 6}" text-anchor="middle">czas dojazdu (min)</text>`;
  out += `<text class="axis-title" x="${-((H - M.b + M.t) / 2)}" y="14" transform="rotate(-90)" text-anchor="middle">koszt całkowity (zł/mies.)</text>`;

  rows.forEach((d, i) => {
    const r = d.area ? Math.max(4.5, Math.min(11, Math.sqrt(d.area) * 1.15)) : 5;
    out += markPath(SHAPE[d.source], px(d.commute), py(d.price), r) +
      ` class="mark" fill="${COLOR[d.source]}" data-i="${i}"></${SHAPE[d.source] === "circle" ? "circle" : SHAPE[d.source] === "square" ? "rect" : "polygon"}>`;
  });

  // legend — identity is never colour-alone: dot shape + text label
  let lx = M.l;
  out += `<g transform="translate(0,${M.t - 6})">`;
  SERIES.forEach(s => {
    out += markPath(s.shape, lx + 5, 4, 5) + ` fill="${COLOR[s.key]}" stroke="var(--surface-1)" stroke-width="2"></${s.shape === "circle" ? "circle" : s.shape === "square" ? "rect" : "polygon"}>`;
    out += `<text class="tick" x="${lx + 16}" y="8">${s.key}</text>`;
    lx += 22 + s.key.length * 7.2;
  });
  out += `</g>`;

  svg.innerHTML = out;

  svg.querySelectorAll(".mark").forEach(el => {
    const d = rows[+el.dataset.i];
    el.onmousemove = e => {
      tip.innerHTML =
        `<b>${d.street || d.district || "—"}</b>` +
        `<div class="row">${zl(d.price)} · ${mn(d.commute)}${d.area ? " · " + d.area + " m²" : ""}</div>` +
        `<div class="row">${typeLabel(d)} · ${d.source}</div>` +
        (d.flags ? `<div class="row flag">${d.flags}</div>` : "");
      tip.style.opacity = 1;
      const pad = 14;
      tip.style.left = Math.min(e.clientX + pad, innerWidth - tip.offsetWidth - 8) + "px";
      tip.style.top = Math.min(e.clientY + pad, innerHeight - tip.offsetHeight - 8) + "px";
    };
    el.onmouseleave = () => { tip.style.opacity = 0; };
    el.onclick = () => { if (d.url) open(d.url, "_blank", "noopener"); };
  });
}

/* ---------- table ---------- */
const COLS = [
  { key: "price",    label: "Cena",      num: true,
    fmt: d => zl(d.price) + (d.priceCheck ? ` <span class="flag" title="${d.priceCheck}">⚠</span>` : "") },
  { key: "days",     label: "Dodane",    num: true,  fmt: age },
  { key: "foundAt",  label: "Znalezione", num: true,
    fmt: d => `<span title="${d.foundAt}">${foundLabel(d)}</span>` },
  { key: "commute",  label: "Dojazd MPK", num: true, fmt: d => mn(d.commute) },
  { key: "walkStops", label: "w tym pieszo", num: true, fmt: d => mn(d.walkStops) },
  { key: "bike",     label: "Rowerem",   num: true,
    fmt: d => d.bike == null ? "—" : mn(d.bike) + (d.bikeKm ? ` <span class="muted">(${d.bikeKm} km)</span>` : "") },
  { key: "walkAll",  label: "Całkiem pieszo", num: true,
    fmt: d => d.walkAll == null ? "—" : mn(d.walkAll) + (d.walkKm ? ` <span class="muted">(${d.walkKm} km)</span>` : "") },
  { key: "area",     label: "m²",        num: true,  fmt: d => d.area ?? "—" },
  { key: "ppm",      label: "zł/m²",     num: true,  fmt: d => d.ppm ? Math.round(d.ppm) : "—" },
  { key: "type",     label: "Rodzaj",    num: false, fmt: typeLabel },
  { key: "street",   label: "Adres",     num: false, fmt: d => d.street || "—" },
  { key: "district", label: "Dzielnica", num: false, fmt: d => d.district || "—" },
  { key: "seller",   label: "Od kogo",   num: false,
    fmt: d => d.seller === "prywatny" ? "👤 właściciel"
      : d.seller === "agencja" ? '🏢 agencja <span class="muted">(prowizja?)</span>'
      : '<span class="muted">—</span>' },
  { key: "source",   label: "Źródło",    num: false,
    fmt: d => `<span class="src"><span class="dot ${SHAPE_CLASS[SHAPE[d.source]]}" style="background:${COLOR[d.source]}"></span>${d.source}</span>` },
  { key: "summary",  label: "Werdykt",   num: false, wide: true,
    fmt: d => (d.flags ? `<span class="flag">⚑ ${d.flags}</span><br>` : "") + d.summary },
  { key: "url",      label: "Link",      num: false,
    fmt: d => d.url ? `<a href="${d.url}" target="_blank" rel="noopener">otwórz →</a>` : "" },
];

const thead = document.getElementById("thead");
thead.innerHTML = COLS.map(c => `<th data-col="${c.key}">${c.label}</th>`).join("");
thead.querySelectorAll("th").forEach(th => {
  th.onclick = () => {
    const col = th.dataset.col;
    if (col === "url" || col === "summary") return;
    state.sort = { col, dir: state.sort.col === col && state.sort.dir === "asc" ? "desc" : "asc" };
    render();
  };
});

function table(rows) {
  const { col, dir } = state.sort, sign = dir === "asc" ? 1 : -1;
  const sorted = [...rows].sort((a, b) => {
    const x = a[col], y = b[col];
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === "number" ? x - y : String(x).localeCompare(String(y), "pl")) * sign;
  });
  thead.querySelectorAll("th").forEach(th =>
    th.dataset.dir = th.dataset.col === col ? dir : (th.dataset.col === "url" || th.dataset.col === "summary" ? "" : "any"));
  document.getElementById("tbody").innerHTML = sorted.map(d =>
    "<tr>" + COLS.map(c => `<td class="${c.num ? "num" : ""}${c.wide ? " wide" : ""}">${c.fmt(d)}</td>`).join("") + "</tr>"
  ).join("");
  document.getElementById("empty").hidden = sorted.length > 0;
  document.getElementById("tableTitle").textContent = `Oferty (${sorted.length})`;
}

/* ---------- render ---------- */
function render() {
  document.getElementById("priceLabel").textContent = zl(state.price);
  document.getElementById("commuteLabel").textContent = mn(state.commute);
  const rows = visible();
  kpis(rows);
  scatter(rows);
  table(rows);
}
render();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="Scored offers CSV -> interactive HTML dashboard")
    ap.add_argument("input", nargs="?", default="oferty_krakow_30min.csv")
    ap.add_argument("-o", "--output", default="dashboard.html")
    ap.add_argument("--title", default=None)
    # The commute target is personal data — it comes from the environment, not
    # from the source of a public repository.
    ap.add_argument("--dest", default=os.environ.get("DEST_ADDRESS", "miejsce pracy"))
    args = ap.parse_args()

    rows = load(Path(args.input))
    if not rows:
        raise SystemExit(f"{args.input}: no rows with both a price and a commute time")

    prices = sorted(r["price"] for r in rows)
    title = args.title or f"{len(rows)} ofert"
    lo, hi = (f"{int(p):,}".replace(",", " ") for p in (prices[0], prices[-1]))
    subtitle = (f"{len(rows)} ofert z OLX, Otodom i Facebooka · "
                f"ceny {lo} – {hi} zł/mies. (wraz z mediami) · "
                f"czas dojazdu liczony komunikacją miejską na {args.dest}")
    footer = (f"Wygenerowane z {Path(args.input).name} przez make_dashboard.py · "
              f"koszty i oceny pochodzą z automatycznej analizy treści ogłoszeń — zweryfikuj przed kontaktem.")

    # the two views link to each other, so the page has a "show me everything" button
    out = Path(args.output)
    if "wszystkie" in out.stem:
        this_label = f"Wszystkie oferty ({len(rows)})"
        peer_file, peer_label = out.name.replace("_wszystkie", ""), "Tylko ≤30 min do pracy"
    else:
        this_label = f"≤30 min do pracy ({len(rows)})"
        peer_file, peer_label = f"{out.stem}_wszystkie{out.suffix}", "Wszystkie oferty, bez limitu dojazdu"

    html = (TEMPLATE
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__TITLE__", title)
            .replace("__DEST__", args.dest)
            .replace("__SUBTITLE__", subtitle)
            .replace("__THIS_LABEL__", this_label)
            .replace("__PEER_FILE__", peer_file)
            .replace("__PEER_LABEL__", peer_label)
            .replace("__FOOTER__", footer))
    Path(args.output).write_text(html, encoding="utf-8")
    print(f"✓ {len(rows)} ofert -> {args.output}")


if __name__ == "__main__":
    main()
