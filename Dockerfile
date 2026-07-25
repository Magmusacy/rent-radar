# Rent Radar — the Telegram bot that sweeps rental portals.
# No Google Maps here: distances come from OpenStreetMap + haversine (see geo.py).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Warsaw \
    DB_PATH=/data/offers.db

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so editing code does not invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# Nothing here needs root; /data is the volume holding the database and outputs.
RUN useradd --create-home --uid 10001 radar \
    && mkdir -p /data \
    && chown -R radar:radar /app /data
USER radar

CMD ["python", "bot.py"]
