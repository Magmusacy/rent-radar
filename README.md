# Rent Radar

> Score any address by its public-transit commute, and let an LLM triage rental listings for you, in any city.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code style: PEP 8](https://img.shields.io/badge/code%20style-pep8-orange.svg)](https://peps.python.org/pep-0008/)

Two small, configurable CLI tools for apartment hunting in **any city**:

1. **`commute.py`**: for a given address, shows how long it takes to reach
   each of your destinations by public transit at a target time, with exact
   line numbers, vehicle types and transfer counts, then prints a 0-10 score.
2. **`analyze_listings.py`**: takes a list of listing URLs, scrapes each page,
   uses an LLM to extract structured data (price, area, rooms, condition,
   amenities, red flags), adds commute times, and writes everything to a CSV.

Everything configurable lives in one `config.json`, and Google Maps covers
public transit worldwide.

## Contents

- [Rent Radar](#rent-radar)
  - [Contents](#contents)
  - [Quick start](#quick-start)
  - [Configuration](#configuration)
    - [Using a different city](#using-a-different-city)
    - [Using a different LLM](#using-a-different-llm)
  - [API keys](#api-keys)
  - [Usage](#usage)
    - [`commute.py`: score one address](#commutepy-score-one-address)
    - [`commute.py`: compare and rank several addresses](#commutepy-compare-and-rank-several-addresses)
      - [Example output](#example-output)
    - [`analyze_listings.py`: batch-analyze listing URLs](#analyze_listingspy-batch-analyze-listing-urls)
  - [Project layout](#project-layout)
  - [How it works](#how-it-works)
  - [Requirements](#requirements)
  - [License](#license)

---

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create your config and secrets from the templates
cp config.example.json config.json
cp .env.example .env

# 3. Edit config.json (destinations, city, time) and .env (API keys)

# 4. Run
python commute.py "14 Some Street"
```

---

## Configuration

Everything user-tunable lives in **`config.json`** (copy it from
`config.example.json`).

```jsonc
{
  "city": "Kraków",          // appended to addresses that don't already include it
  "region": "pl",            // Google region bias (optional, ISO country code)
  "language": "en",          // language of Google's route descriptions

  "destinations": {          // add/remove freely: work, gym, partner, parents...
    "Work":       "Main Square, Kraków",
    "University": "AGH University, Kraków",
    "Gym":        "Some Gym, Kraków"
  },

  "transit_modes": ["bus", "tram", "subway", "rail"],

  "schedule": {
    "hour": 9,
    "minute": 0,
    "mode": "arrival"        // "arrival" = be there by HH:MM, "departure" = leave at HH:MM
  },

  "scoring": {
    "minutes_divisor": 5.0,  // every 5 min of average time = -1 point
    "transfer_penalty": 0.4, // each transfer = -0.4 points
    "max_commute_min": 40,   // listing rejected above this commute
    "max_walk_min": 20,      // listing flagged above this walking time
    "min_condition": 4       // listing rejected below this 1-10 condition
  },

  "listings": {
    "currency": "PLN",
    "llm_provider": "deepseek",
    "llm_model": "deepseek-chat",
    "llm_base_url": "https://api.deepseek.com",
    "llm_api_key_env": "DEEPSEEK_API_KEY",
    "request_delay_sec": 1.5,
    "extraction_language": "English"
  }
}
```

### Using a different city

Set `city`, `region`, the `destinations` addresses and `transit_modes` for your
area (e.g. `["subway", "bus", "rail"]` in New York). That's it.

### Using a different LLM

The listing analyzer talks to any OpenAI-compatible endpoint. Point
`llm_base_url`, `llm_model` and `llm_api_key_env` at your provider (DeepSeek,
OpenAI, a local server, …) and store the key under that env-var name in `.env`.

---

## API keys

Put keys in **`.env`** (copied from `.env.example`):

| Key | Used by | Where |
|-----|---------|-------|
| `GOOGLE_MAPS_API_KEY` | both tools | <https://console.cloud.google.com/>, enable **Directions API** + **Geocoding API** |
| `DEEPSEEK_API_KEY` (or your chosen name) | `analyze_listings.py` | <https://platform.deepseek.com/> |

Google gives $200 of free credit per month; Directions API costs $0.005 per
request, so a typical search costs nothing.

---

## Usage

### `commute.py`: score one address

```bash
python commute.py "14 Some Street"
python commute.py                 # prompts for an address
```

### `commute.py`: compare and rank several addresses

```bash
python commute.py --compare
```

Enter addresses line by line; an empty line ends input. You get a per-address
breakdown plus a final ranking.

#### Example output


```
================================================================
 Address: 14 Some Street, Kraków
 Day:     Monday 2026-05-18
 Goal:    arrive by 09:00
================================================================

→ Work
────────────────────────────────────────────────────────────────
  ⏱  Time:       24 min  (1 transfer)
  🚶 Walking:    6 min
  🕗 Departure:  08:36
  🏁 Arrival:    09:00
  🚊🚌  Route:
     1. tram 8  →  Borek Fałęcki
        Kapelanka  →  Dworzec Główny Zachód  (12 min)
     2. bus 304  →  Dworzec Główny
        Dworzec Główny Zachód  →  Main Square  (3 min)

================================================================
 SUMMARY
================================================================
  Total commute time:    78 min
  Average time:          26.0 min
  Direct routes:         2/3
  Total transfers:       1

  🏆 SCORE: 4.4/10  -  average location
```

### `analyze_listings.py`: batch-analyze listing URLs

```bash
cp listings.example.txt listings.txt   # then paste your own URLs
python analyze_listings.py             # listings.txt -> listings.csv
python analyze_listings.py my.txt -o results.csv
python analyze_listings.py --limit 3   # test on the first 3
```

Output CSV columns include: `street`, `district`, `total_price`, `area_m2`,
`price_per_m2`, `condition_1_10`, `amenities`, `red_flags`, `avg_commute_min`,
`commute_score`, per-destination commute/walk times, a `reject` column with
reasons to skip, and a one-line `summary` verdict.

---

## Project layout

```
config.py             # loads config.json into typed dataclasses
config.example.json   # config template (copy to config.json)
commute.py            # transit/bike/walking routes + score
analyze_listings.py   # scrape + LLM extraction + commute -> CSV
geo.py                # free geocoding (OpenStreetMap) + straight-line distance
collect_portals.py    # gathers listing URLs from OLX and Otodom search pages
score_offers.py       # URLs *or* raw text -> LLM + distance -> CSV
store.py              # SQLite store, keyed by URL, remembers when each offer was first seen
refresh.py            # one sweep: collect -> score what's new -> store -> rebuild outputs
make_dashboard.py     # CSV -> self-contained interactive HTML dashboard
bot.py                # Telegram bot: hourly schedule + /skan /nowe /top /stan
Dockerfile            # server image (no Google key needed)
compose.yml           # container definition, /data volume
deploy.sh             # one-command deploy to a Docker host over SSH
.env.example          # API key template (copy to .env)
listings.example.txt  # URL list template (copy to listings.txt)
requirements.txt
```

---

## How it works

- **Commute**: queries Google Directions in `transit` mode for the next weekday
  at your target time, parses each leg (walking vs. transit), counts transfers,
  and scores the address from average time + transfer penalty.
- **Listings**: downloads the visible page text (works on any site), asks the
  LLM to fill a strict JSON schema, computes commutes for the extracted address,
  and applies your thresholds to produce a reject/keep verdict.

---

## Running it as a server (Docker + Telegram)

The laptop and the server run the same code with different jobs:

| | Laptop | Server |
|---|---|---|
| Schedule | launchd, three times a day | in-process, hourly 11:00–21:00 |
| Sources | portals **+ Facebook** (needs a logged-in browser) | portals only |
| Distance | Google Maps: transit, bike, walking — exact minutes | OpenStreetMap + haversine: **straight-line km** |
| Alerts | macOS notification | Telegram message |
| Database | `offers.db` next to the code | `/data/offers.db` on a volume |

The server deliberately gets **no Google Maps key**. Directions costs three calls
per offer, and for a first-pass filter "how far, roughly" is enough — so `geo.py`
geocodes through Nominatim (free, no key) and computes the great-circle distance.
Exact minutes stay on the laptop, for the handful of offers that pass the filter.

### Deploy

```bash
cp .env.example .env      # fill in BOT_TOKEN, OWNER_ID, DEEPSEEK_API_KEY
./deploy.sh root@YOUR_DROPLET_IP
```

`deploy.sh` is idempotent — run it as often as you like. It runs the test suite
first and refuses to deploy if anything fails, installs Docker if the host lacks
it, copies the code (never `.env`, never the database), strips the Google key on
the way, then `docker compose up -d --build` and verifies the container is
running.

```bash
ssh root@YOUR_DROPLET_IP 'docker logs -f rent-radar'      # follow the logs
ssh root@YOUR_DROPLET_IP 'cd /opt/rent-radar && docker compose restart'
```

### Telegram

Get a token from [@BotFather](https://t.me/BotFather) and your numeric user id
from [@userinfobot](https://t.me/userinfobot), then put both in `.env`:

```ini
BOT_TOKEN=123456:AA...
OWNER_ID=123456789        # only this user may issue commands
ALERT_BELOW=2200          # total monthly price, PLN
MAX_KM=5                  # straight-line distance from the commute target
```

The bot writes only when a sweep found something new, plus a digest after the
last sweep of the day. Commands: `/skan` (sweep now), `/nowe` (found today),
`/top` (cheapest matches), `/stan` (database and schedule).

### Tests

```bash
pip install -r requirements-dev.txt
pytest -q tests
```

---

## Requirements

- Python 3.10+
- See `requirements.txt` (`googlemaps`, `python-dotenv`, `requests`,
  `beautifulsoup4`, `openai`, `python-telegram-bot`)
- Docker only for the server deployment

## License

MIT. See [LICENSE](LICENSE).
