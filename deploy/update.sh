#!/usr/bin/env bash
# Pull the newest netlab image and restart it. Running labs are not touched.
set -euo pipefail
cd /opt/netlab/deploy
sudo docker compose pull
sudo docker compose up -d
sudo docker image prune -f >/dev/null
curl -fsS http://127.0.0.1:${NETLAB_PORT:-8080}/api/version; echo
