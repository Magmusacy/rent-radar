#!/usr/bin/env bash
#
# Deploy Rent Radar to an Ubuntu/Debian host with Docker.
#
#   ./deploy.sh root@164.92.161.203
#   ./deploy.sh root@1.2.3.4 -i ~/.ssh/my_key
#
# Idempotent — run it as often as you like. It installs only what is missing and
# never overwrites the server's .env or its database.

set -euo pipefail

HOST="${1:-}"
shift || true
SSH_OPTS=("$@")

APP_DIR=/opt/rent-radar

if [[ -z "$HOST" ]]; then
  echo "usage: ./deploy.sh user@host [extra-ssh-options]" >&2
  exit 1
fi

here() { cd "$(dirname "${BASH_SOURCE[0]}")" && pwd; }
cd "$(here)"

remote() { ssh "${SSH_OPTS[@]}" -o StrictHostKeyChecking=no "$HOST" "$@"; }
step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }

# --- 0. tests before anything leaves the machine ---------------------------
step "Running tests"
PY=./.venv/bin/python
[[ -x "$PY" ]] || PY=python3
if ! "$PY" -m pytest -q tests >/tmp/rr-tests.log 2>&1; then
  echo "  TESTS FAILED — not deploying." >&2
  tail -15 /tmp/rr-tests.log >&2
  exit 1
fi
tail -1 /tmp/rr-tests.log | sed 's/^/  /'

step "Checking the connection to $HOST"
remote 'echo "  $(hostname) · $(. /etc/os-release && echo "$PRETTY_NAME")"'

# --- 1. Docker -------------------------------------------------------------
step "Docker"
remote '
  set -e
  if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    echo "  already installed: $(docker --version | cut -d, -f1)"
  else
    echo "  installing Docker..."
    export DEBIAN_FRONTEND=noninteractive
    curl -fsSL https://get.docker.com | sh >/dev/null 2>&1
    echo "  installed: $(docker --version | cut -d, -f1)"
  fi
'

# --- 2. code ---------------------------------------------------------------
# .env and data/ are deliberately excluded: configuration and state belong to
# the server, not to the repository.
step "Uploading code to $APP_DIR"
remote "mkdir -p $APP_DIR/data"
tar czf - \
  --exclude=__pycache__ \
  *.py requirements.txt Dockerfile compose.yml tests \
  | remote "tar xzf - -C $APP_DIR && find $APP_DIR -name '._*' -delete"
echo "  uploaded"

# --- 3. configuration ------------------------------------------------------
step "Configuration (.env)"
if remote "test -f $APP_DIR/.env"; then
  echo "  server already has one — leaving it alone"
elif [[ -f .env ]]; then
  echo "  copying the local .env (minus the Google key — the server has no use for it)"
  grep -v '^GOOGLE_MAPS_API_KEY=' .env | remote "cat > $APP_DIR/.env"
else
  echo "  no .env here and none on the server — the bot cannot start" >&2
  exit 1
fi
remote "chmod 600 $APP_DIR/.env"

# --- 4. start --------------------------------------------------------------
step "Building the image and starting"
remote "cd $APP_DIR && docker compose up -d --build 2>&1 | tail -3"

# --- 5. verify -------------------------------------------------------------
step "Checking that it came up"
sleep 8
remote "
  STATE=\$(docker inspect -f '{{.State.Status}}' rent-radar 2>/dev/null || echo missing)
  echo \"  state: \$STATE\"
  docker logs --tail 5 rent-radar 2>&1 | sed 's/^/  /'
  test \"\$STATE\" = running
"

printf '\n\033[1;32m✓ Deployed.\033[0m  Follow the logs with:\n'
echo "  ssh ${SSH_OPTS[*]:-} $HOST 'docker logs -f rent-radar'"
