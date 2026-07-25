# Rent Radar na DigitalOcean — bot Telegram w Dockerze

Data: 2026-07-25

## Cel

Przenieść skanowanie portali (OLX, Otodom) na serwer, żeby działało niezależnie
od tego, czy laptop jest włączony, i raportowało wyniki na Telegramie.

## Zakres

**W zakresie**

- Kontener Docker z botem Telegram, uruchamiany na tym samym dropletcie co `bot_sct`
- Skan co godzinę w oknie 11:00–21:00 (Europe/Warsaw), 11 rund dziennie
- **Serwer nie używa Google Maps** — odległość w linii prostej z darmowego geokodera
- Powiadomienie tylko wtedy, gdy runda przyniosła nowe ogłoszenia
- Podsumowanie dnia po ostatniej rundzie (21:00)
- Komendy: `/skan`, `/nowe`, `/top`, `/stan`
- `deploy.sh` w konwencji `bot_sct`: idempotentny, przez SSH, `.env` zostaje na serwerze

**Poza zakresem**

- Facebook (Marketplace i grupy) — wymaga zalogowanej sesji Chrome, zostaje lokalnie
- Dashboard wystawiony w internecie — ogłoszenia zawierają numery telefonów; wymagałby
  uwierzytelniania i HTTPS, a wartość jest mała przy dostępie przez `scp`
- Wspólna baza z komputerem — świadoma decyzja: dwa niezależne byty

## Dwie instalacje, dwie bazy

| | Komputer (Mac) | Serwer (droplet) |
|---|---|---|
| Harmonogram | launchd 10:00 / 16:00 / 22:00 | bot, co godzinę 11:00–21:00 |
| Źródła | portale **+ Facebook** | tylko portale |
| Baza | lokalny `offers.db` | `/data/offers.db` w wolumenie |
| Odległość | Google Maps: MPK, rower, pieszo (dokładne minuty) | Nominatim + haversine: **km w linii prostej** |
| Powiadomienia | macOS | Telegram |

### Dlaczego serwer liczy kilometry, a nie minuty

Google Directions to trzy zapytania na ofertę i główny koszt całości. Na serwerze
wystarczy zgrubna miara „jak daleko", więc:

1. adres → współrzędne przez **Nominatim (OpenStreetMap)** — darmowe, bez klucza,
   limit 1 zapytanie/s, wymagany własny User-Agent
2. współrzędne → **haversine** do Zabłocia 43B — czysta matematyka, zero kosztu
3. wynik geokodowania **cache'owany po ulicy** w bazie — przy 177 ofertach było 163
   unikalne adresy, ale z czasem ulice się powtarzają i zapytań ubywa

LLM nie bierze udziału w liczeniu odległości. Model bez dostępu do sieci zgadywałby
współrzędne z pamięci, a to ta sama klasa błędu, która wcześniej dała nam złą cenę
i „datę w dalekiej przyszłości". Skoro liczymy linię prostą, potrzebne są tylko
współrzędne — a te są za darmo i dokładne.

Ograniczenie do zaakceptowania: linia prosta nie zna Wisły ani rozkładu tramwajów.
Kawalerka przy al. Pokoju leży 3,0 km w linii prostej, ale 29 minut MPK, bo trzeba
nadłożyć do mostu. Dlatego km to sito wstępne, a dokładne minuty liczy lokalny Mac
dla ofert, które przejdą przez to sito.

Konsekwencja przyjęta świadomie: oba skanują portale, więc te same ogłoszenia trafią do
obu baz z różnym `first_seen`, a koszt API zostanie podwojony. Gdyby to przeszkadzało,
wystarczy przełącznik `--fb-only` w lokalnym uruchomieniu — nie wchodzi w zakres teraz.

## Architektura

```
droplet
└── docker: rent-radar (jeden proces)
    ├── bot.py — python-telegram-bot, long polling
    │   ├── JobQueue.run_daily × 11 (11:00–21:00)
    │   ├── JobQueue.run_daily 21:05 — podsumowanie dnia
    │   └── handlery komend, dostęp tylko dla OWNER_ID
    └── refresh.run_scan(db_path) -> ScanResult
        ├── collect_portals (z filtrem ceny na liście wyników)
        ├── score_offers.process (LLM: cena/metraż/adres/flagi)
        ├── geo.distance_km (Nominatim + haversine, cache po ulicy)
        └── store (SQLite w /data/offers.db)
```

Harmonogram siedzi w procesie bota (`JobQueue`), nie w cronie — jeden byt do pilnowania,
a restart kontenera odtwarza harmonogram bez utraty stanu, bo stan jest w bazie.

## Zmiany w istniejącym kodzie

- `refresh.py`: wydzielić `run_scan(db_path, ...) -> ScanResult` z `main()`.
  `ScanResult` = `{scanned, new, hits: list[dict], total_in_db, errors}`.
  CLI zostaje bez zmian i woła tę samą funkcję.
- `collect_portals.py`: wyciągać cenę z karty na liście wyników i odrzucać ogłoszenia
  powyżej progu **przed** kosztownym etapem LLM + Maps. Cel: uciąć ~⅓ kosztu.
- `store.py`: ścieżka bazy z parametru/zmiennej środowiskowej zamiast stałej obok pliku.

## Nowe pliki

| Plik | Odpowiedzialność |
|---|---|
| `bot.py` | komendy, harmonogram, formatowanie wiadomości, kontrola dostępu |
| `Dockerfile` | `python:3.12-slim`, zależności, `TZ=Europe/Warsaw`, użytkownik nie-root |
| `compose.yml` | wolumen `./data:/data`, `restart: unless-stopped`, `env_file: .env` |
| `deploy.sh` | wdrożenie przez SSH: Docker jeśli brak, kod, `compose up -d --build`, weryfikacja |
| `.dockerignore` | bez `.venv`, `.git`, danych i wygenerowanych plików |

## Wiadomości

Runda z nowymi ogłoszeniami:

```
🏠 Rent Radar · 16:00

34 nowe ogłoszenia (w bazie: 211)
Pasujące pod profil (≤2200 zł, ≤5 km od Zabłocia): 2

1. 1950 zł · 2,4 km · 28 m²
   Grzegórzki · 👤 właściciel · otwórz →
2. 2100 zł · 3,8 km · 32 m²
   Dębniki · 🏢 agencja (prowizja?) · otwórz →
```

Kilometry to odległość w linii prostej od Zabłocia 43B.

Runda bez nowych ogłoszeń: cisza. Podsumowanie o 21:05: liczba rund, nowych ofert i
trafień z całego dnia oraz najtańsze trafienie.

Komendy: `/skan` (runda na żądanie), `/nowe` (od ostatniej rundy), `/top` (10 najtańszych
w profilu), `/stan` (liczba ofert, czas ostatniej rundy, następna runda).

## Konfiguracja

`.env` na serwerze (nigdy w repo): `BOT_TOKEN`, `OWNER_ID`, `DEEPSEEK_API_KEY`,
`ALERT_BELOW=2200`, `MAX_KM=5`, `DB_PATH=/data/offers.db`.
Klucz Google **nie jedzie na serwer** — nie jest tam do niczego potrzebny.

## Błędy

- Błąd pojedynczego ogłoszenia (padł fetch, LLM zwrócił śmieci) — do logu, runda leci dalej
- Runda wywalona w całości — wiadomość do właściciela z jedną linijką przyczyny
- Brak kluczy przy starcie — kontener nie wstaje, z jasnym komunikatem
- `/skan` w trakcie rundy zaplanowanej — blokada, odpowiedź „runda już trwa"
- Telegram nieosiągalny — runda i tak kończy się zapisem do bazy

## Testy

- Formatowanie wiadomości (bez sieci, na sztucznym `ScanResult`)
- `run_scan` na wstrzykniętych danych — sprawdza deduplikację po URL i liczenie trafień
- Filtr ceny w `collect_portals` na zapisanym HTML-u listy wyników
- Lokalny `docker compose up` przed wdrożeniem; `deploy.sh` odmawia wdrożenia, gdy testy padną

## Otwarte kwestie

- Próg ceny dla wstępnego filtru na liście wyników: 3500 zł dla mieszkań, 2000 zł dla pokoi
  (jak obecnie w wyszukiwaniach) — do potwierdzenia przy implementacji
## Kalibracja progu (zrobiona)

55 ofert z lokalnej bazy, dla których znamy i kilometry w linii prostej, i zmierzone
minuty MPK:

| Próg | Łapie ofert | W tym faktycznie ≤30 min | Gubi dobrych |
|---|---|---|---|
| 3 km | 12 | 11 (92%) | 14 |
| 4 km | 24 | 17 (71%) | 8 |
| 5 km | 33 | 18 (55%) | 7 |
| **6 km** | **46** | **24 (52%)** | **1** |

90% ofert z dojazdem ≤30 min mieści się w 5,4 km w linii prostej.

Wybrane **6 km**: przy alertach pominięta oferta kosztuje więcej niż zerknięcie na
niepotrzebne powiadomienie. Połowa trafień będzie dalej, niż wygląda na mapie —
świadoma cena za to, że prawie nic nie ucieka.

**Pokrycie geokodowania:** Nominatim rozwiązał 55 z 70 adresów (79%). Dla pozostałych
`score_offers` cofa się do poziomu dzielnicy, żeby oferta nie wypadła z alertów tylko
dlatego, że OSM nie zna numeru budynku.
