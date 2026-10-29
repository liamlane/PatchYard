#!/usr/bin/env bash
# Pull the newest Patchyard image and restart it. Running labs are not touched.
#   sudo /opt/patchyard/deploy/update.sh        (or ./deploy/update.sh from a clone)
set -euo pipefail
HOME_DIR="${PATCHYARD_HOME:-/opt/patchyard}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUDO=""; [ "$(id -u)" = 0 ] || SUDO=sudo
# installs from before the rename (netlab in /opt/netlab): the installer migrates them in place
if [ ! -f "$HOME_DIR/deploy/.env" ] && [ -d /opt/netlab ] && [ ! -L /opt/netlab ]; then
  echo "== migrating the netlab install to Patchyard ($HOME_DIR)"
  if [ -f "$HERE/install.sh" ]; then exec $SUDO bash "$HERE/install.sh" --home "$HOME_DIR"; fi
  curl -fsSL https://raw.githubusercontent.com/liamlane/patchyard/main/deploy/install.sh | $SUDO bash -s -- --home "$HOME_DIR"
  exit
fi
[ -f "$HOME_DIR/deploy/.env" ] || { echo "no install in $HOME_DIR (set PATCHYARD_HOME, or run install.sh)"; exit 1; }
# a newer compose file from a clone replaces the installed one
if [ -f "$HERE/docker-compose.yml" ] && [ "$HERE" != "$HOME_DIR/deploy" ]; then $SUDO cp "$HERE/docker-compose.yml" "$HOME_DIR/deploy/"; fi
cd "$HOME_DIR/deploy"
$SUDO docker compose pull -q
$SUDO docker compose up -d --remove-orphans
$SUDO docker image prune -f >/dev/null
PORT=$(sed -n 's/^PATCHYARD_PORT=//p' .env); PORT=${PORT:-8080}
for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:$PORT/api/version" 2>/dev/null && { echo; exit 0; }; sleep 2; done
echo "Patchyard didn't answer on port $PORT: docker logs patchyard"; exit 1
