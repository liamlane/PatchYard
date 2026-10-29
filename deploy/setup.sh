#!/usr/bin/env bash
# Kept for installs that followed the old instructions (./deploy/setup.sh IMAGE). Use install.sh.
exec bash "$(cd "$(dirname "$0")" && pwd)/install.sh" ${1:+--image "$1"} "${@:2}"
