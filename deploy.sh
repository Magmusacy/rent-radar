#!/usr/bin/env bash
#
# Wdrożenie Rent Radara na serwer z Ubuntu/Debianem (Docker).
#
#   ./deploy.sh root@164.92.161.203
#   ./deploy.sh root@1.2.3.4 -i ~/.ssh/moj_klucz
#
# Idempotentny — można puszczać wielokrotnie. Instaluje tylko to, czego brakuje,
# i nigdy nie nadpisuje .env ani bazy na serwerze.

set -euo pipefail

HOST="${1:-}"
shift || true
SSH_OPTS=("$@")

APP_DIR=/opt/rent-radar

if [[ -z "$HOST" ]]; then
  echo "użycie: ./deploy.sh user@host [dodatkowe-opcje-ssh]" >&2
  exit 1
fi

here() { cd "$(dirname "${BASH_SOURCE[0]}")" && pwd; }
cd "$(here)"

remote() { ssh "${SSH_OPTS[@]}" -o StrictHostKeyChecking=no "$HOST" "$@"; }
step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }

# --- 0. testy przed wysyłką ------------------------------------------------
step "Testy lokalne"
PY=./.venv/bin/python
[[ -x "$PY" ]] || PY=python3
if ! "$PY" -m pytest -q tests >/tmp/rr-tests.log 2>&1; then
  echo "  TESTY PADŁY — nie wdrażam." >&2
  tail -15 /tmp/rr-tests.log >&2
  exit 1
fi
tail -1 /tmp/rr-tests.log | sed 's/^/  /'

step "Sprawdzam połączenie z $HOST"
remote 'echo "  $(hostname) · $(. /etc/os-release && echo "$PRETTY_NAME")"'

# --- 1. Docker -------------------------------------------------------------
step "Docker"
remote '
  set -e
  if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    echo "  jest już: $(docker --version | cut -d, -f1)"
  else
    echo "  instaluję Dockera..."
    export DEBIAN_FRONTEND=noninteractive
    curl -fsSL https://get.docker.com | sh >/dev/null 2>&1
    echo "  zainstalowany: $(docker --version | cut -d, -f1)"
  fi
'

# --- 2. kod ----------------------------------------------------------------
# .env i data/ celowo pominięte: konfiguracja i stan należą do serwera,
# nie do repozytorium.
step "Wysyłam kod do $APP_DIR"
remote "mkdir -p $APP_DIR/data"
tar czf - \
  --exclude=__pycache__ \
  *.py requirements.txt Dockerfile compose.yml tests \
  | remote "tar xzf - -C $APP_DIR && find $APP_DIR -name '._*' -delete"
echo "  wysłane"

# --- 3. konfiguracja -------------------------------------------------------
step "Konfiguracja (.env)"
if remote "test -f $APP_DIR/.env"; then
  echo "  .env już na serwerze — nie ruszam"
elif [[ -f .env ]]; then
  echo "  kopiuję lokalny .env (bez klucza Google — serwer go nie potrzebuje)"
  grep -v '^GOOGLE_MAPS_API_KEY=' .env | remote "cat > $APP_DIR/.env"
else
  echo "  BRAK .env lokalnie i na serwerze — bot się nie uruchomi" >&2
  exit 1
fi
remote "chmod 600 $APP_DIR/.env"

# --- 4. uruchomienie -------------------------------------------------------
step "Budowa obrazu i start"
remote "cd $APP_DIR && docker compose up -d --build 2>&1 | tail -3"

# --- 5. weryfikacja --------------------------------------------------------
step "Sprawdzam, czy wstał"
sleep 8
remote "
  STAN=\$(docker inspect -f '{{.State.Status}}' rent-radar 2>/dev/null || echo brak)
  echo \"  stan: \$STAN\"
  docker logs --tail 5 rent-radar 2>&1 | sed 's/^/  /'
  test \"\$STAN\" = running
"

printf '\n\033[1;32m✓ Wdrożone.\033[0m  Podgląd logów:\n'
echo "  ssh ${SSH_OPTS[*]:-} $HOST 'docker logs -f rent-radar'"
