# Rent Radar — bot Telegram skanujący portale z ogłoszeniami.
# Bez Google Maps: odległości liczone z OpenStreetMap + haversine (patrz geo.py).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Warsaw \
    DB_PATH=/data/offers.db

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Zależności osobno, żeby zmiana kodu nie unieważniała warstwy z pipem.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# Kontener nie potrzebuje roota; /data to wolumen z bazą i wynikami.
RUN useradd --create-home --uid 10001 radar \
    && mkdir -p /data \
    && chown -R radar:radar /app /data
USER radar

CMD ["python", "bot.py"]
