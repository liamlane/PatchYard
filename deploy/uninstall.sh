#!/usr/bin/env bash
# Remove Patchyard. Keeps your labs and settings unless you pass --purge.
#   sudo ./uninstall.sh [--purge] [--destroy-labs]
#     --destroy-labs  also stop and remove every running lab device (containerlab containers)
#     --purge         also delete the data folder (labs, snapshots, settings, metrics): no undo
set -euo pipefail
HOME_DIR="${PATCHYARD_HOME:-/opt/patchyard}"
PURGE=0; LABS=0
for a in "$@"; do case "$a" in --purge) PURGE=1 ;; --destroy-labs) LABS=1 ;; *) echo "unknown option $a"; exit 2 ;; esac; done
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
if [ "$LABS" = 1 ]; then
  ids=$(docker ps -aq --filter label=containerlab)
  [ -n "$ids" ] && docker rm -f $ids >/dev/null && echo "== removed $(echo "$ids" | wc -l) lab containers"
  docker network rm clab-mgmt >/dev/null 2>&1 || true
fi
if [ -f "$HOME_DIR/deploy/docker-compose.yml" ]; then (cd "$HOME_DIR/deploy" && docker compose down) || true; else docker rm -f patchyard >/dev/null 2>&1 || true; fi
rm -f /etc/modules-load.d/patchyard.conf
echo "== Patchyard container removed"
if [ "$PURGE" = 1 ]; then
  printf 'Delete %s with every lab, snapshot and setting? Type DELETE to confirm: ' "$HOME_DIR"
  read -r c </dev/tty || c=""
  if [ "$c" = DELETE ]; then rm -rf "$HOME_DIR"; [ -L /opt/netlab ] && rm -f /opt/netlab; echo "== $HOME_DIR deleted"; else echo "== kept $HOME_DIR"; fi
else
  echo "   Labs and settings are still in $HOME_DIR (run install.sh again to bring Patchyard back)."
fi
[ "$LABS" = 1 ] || echo "   Lab devices that were running are still running: docker ps --filter label=containerlab"
