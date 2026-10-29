#!/usr/bin/env bash
# Patchyard installer: sets up Docker, kernel modules and the Patchyard container on a Linux host.
#
#   curl -fsSL https://raw.githubusercontent.com/liamlane/patchyard/main/deploy/install.sh | sudo bash
#   sudo ./deploy/install.sh [options]            # from a clone of the repo
#
# Options:
#   --port N            web UI port (default 8080)
#   --home DIR          data folder: labs, settings, metrics (default /opt/patchyard)
#   --image REF         container image (default ghcr.io/liamlane/patchyard:latest)
#   --bind ADDR         listen address (default 0.0.0.0; 127.0.0.1 = only via a local reverse proxy)
#   --public            hosting for other people (a VPS): sandboxed lab devices, no internet from
#                       labs except package mirrors, login required; implies --bind 127.0.0.1 with --domain
#   --domain NAME       serve https://NAME with a Let's Encrypt certificate (Caddy, ports 80/443)
#   --email ADDR        contact address for Let's Encrypt (recommended with --domain)
#   --admin NAME        admin account name (default admin)
#   --password-stdin    read the admin password from stdin instead of asking
#   --no-login          leave the login off (anyone who can reach the port can use it; not with --public)
#   --no-update-check   don't check GitHub for new releases
#   -y, --yes           don't ask questions (generates an admin password and prints it)
#
# Safe to run again: it updates the install in place and never touches your labs.
# Supported: Ubuntu 22.04/24.04 and Debian 12 (tested path); Fedora, RHEL/Rocky/Alma 9 and Arch
# on a best-effort basis. x86_64 only for now.
set -euo pipefail

PORT=8080
HOME_DIR=/opt/patchyard
IMAGE=ghcr.io/liamlane/patchyard:latest
BIND=0.0.0.0
LOGIN=ask
PUBLIC=0
DOMAIN=""
EMAIL=""
ADMIN="admin"
PW_STDIN=0
UPDATE_CHECK=1
YES=0
REPO_RAW="https://raw.githubusercontent.com/liamlane/patchyard/main"

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift ;;
    --home) HOME_DIR="$2"; shift ;;
    --image) IMAGE="$2"; shift ;;
    --bind) BIND="$2"; shift ;;
    --public) PUBLIC=1 ;;
    --domain) DOMAIN="$2"; shift ;;
    --email) EMAIL="$2"; shift ;;
    --admin) ADMIN="$2"; shift ;;
    --password-stdin) PW_STDIN=1 ;;
    --no-login) LOGIN=off ;;
    --no-update-check) UPDATE_CHECK=0 ;;
    -y|--yes) YES=1 ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "unknown option $1 (see --help)"; exit 2 ;;
  esac
  shift
done

say()  { printf '\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '   \033[33mwarn\033[0m  %s\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root (sudo)"
[ "$(uname -s)" = Linux ] || die "Patchyard needs a Linux host (a VM is fine)"
case "$(uname -m)" in
  x86_64|amd64) ;;
  *) die "this build is x86_64 only for now ($(uname -m) detected)" ;;
esac
[[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "bad --port $PORT"
case "$HOME_DIR" in /*) ;; *) die "--home must be an absolute path" ;; esac
[[ "$ADMIN" =~ ^[a-z][a-z0-9_-]{2,23}$ ]] || die "--admin: 3-24 lowercase letters, digits, _ or -"
if [ -n "$DOMAIN" ]; then
  [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "--domain: a DNS name like lab.example.com"
  BIND=127.0.0.1
fi
if [ "$PUBLIC" = 1 ]; then
  [ "$LOGIN" = off ] && die "--public needs the login: drop --no-login"
  [ -z "$DOMAIN" ] && [ "$BIND" = 0.0.0.0 ] && warn "--public without --domain: Patchyard will be plain HTTP on port $PORT. Put HTTPS in front of it."
fi

. /etc/os-release 2>/dev/null || true
DISTRO="${ID:-unknown}"; LIKE="${ID_LIKE:-}"
family() {
  case " $DISTRO $LIKE " in
    *" ubuntu "*|*" debian "*) echo debian ;;
    *" fedora "*|*" rhel "*|*" centos "*|*" rocky "*|*" almalinux "*) echo rhel ;;
    *" arch "*) echo arch ;;
    *) echo other ;;
  esac
}
FAM=$(family)
say "Host: ${PRETTY_NAME:-$DISTRO} ($(uname -r)), family: $FAM"
[ "$FAM" = debian ] || warn "best-effort platform: the tested path is Ubuntu/Debian"

# ---------------------------------------------------------------- packages
pkg_install() {
  case "$FAM" in
    debian) DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$@" >/dev/null ;;
    rhel) dnf install -y -q "$@" >/dev/null ;;
    arch) pacman -Sy --noconfirm --needed "$@" >/dev/null ;;
    *) return 1 ;;
  esac
}
say "Base tools"
command -v curl >/dev/null || { [ "$FAM" = debian ] && apt-get update -q >/dev/null; pkg_install curl ca-certificates || die "install curl first"; }
ok "curl"

# ---------------------------------------------------------------- docker
say "Docker"
if command -v snap >/dev/null && snap list docker >/dev/null 2>&1; then
  die "Docker is installed as a snap; containerlab can't work with it. Remove it (snap remove docker) and run this again: Docker Engine will be installed from docker.com."
fi
if ! command -v docker >/dev/null; then
  case "$FAM" in
    debian|rhel) curl -fsSL https://get.docker.com | sh >/dev/null ;;
    arch) pkg_install docker docker-compose ;;
    *) die "install Docker Engine and the compose plugin yourself, then run this again" ;;
  esac
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker info >/dev/null 2>&1 || die "the Docker daemon isn't running"
docker compose version >/dev/null 2>&1 || {
  case "$FAM" in debian|rhel) pkg_install docker-compose-plugin ;; arch) pkg_install docker-compose ;; esac
  docker compose version >/dev/null 2>&1 || die "the docker compose plugin is missing"
}
DOCKER_ROOT=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
ok "docker $(docker version -f '{{.Server.Version}}'), compose $(docker compose version --short), root $DOCKER_ROOT"

# ---------------------------------------------------------------- kernel modules
say "Kernel modules"
MODS="bridge 8021q sch_netem dummy vrf mpls_router mpls_iptunnel openvswitch nf_tables"
missing=""
for m in $MODS; do modprobe "$m" 2>/dev/null || missing="$missing $m"; done
if [ -n "$missing" ] && [ "$FAM" = debian ] && apt-cache show "linux-modules-extra-$(uname -r)" >/dev/null 2>&1; then
  pkg_install "linux-modules-extra-$(uname -r)" || true
  still=""; for m in $missing; do modprobe "$m" 2>/dev/null || still="$still $m"; done; missing="$still"
fi
loaded=""
for m in $MODS; do case " $missing " in *" $m "*) ;; *) loaded="$loaded $m" ;; esac; done
printf '%s\n' $loaded > /etc/modules-load.d/patchyard.conf
ok "loaded now and at boot:$loaded"
for m in $missing; do
  case "$m" in
    mpls_router|mpls_iptunnel) warn "$m unavailable: MPLS/LDP and the MPLS L3VPN template won't work" ;;
    openvswitch) warn "openvswitch unavailable: the Open vSwitch switch engine (RSTP/LACP) won't work" ;;
    vrf) warn "vrf unavailable: router VRFs won't work" ;;
    sch_netem) warn "sch_netem unavailable: link speeds and fault injection won't work" ;;
    *) warn "$m unavailable" ;;
  esac
done
rm -f /etc/modules-load.d/netlab.conf

# ---------------------------------------------------------------- migrate from netlab
if [ -d /opt/netlab ] && [ ! -L /opt/netlab ] && [ ! -e "$HOME_DIR" ]; then
  say "Moving the old netlab install to $HOME_DIR"
  if [ -f /opt/netlab/deploy/docker-compose.yml ]; then (cd /opt/netlab/deploy && docker compose down) || true; fi
  systemctl disable --now netlab >/dev/null 2>&1 || true
  old_img=$(sed -n 's/^NETLAB_IMAGE=//p' /opt/netlab/deploy/.env 2>/dev/null || true)
  [ -n "$old_img" ] && [ "$IMAGE" = ghcr.io/liamlane/patchyard:latest ] && IMAGE=$(echo "$old_img" | sed 's#/netlab:#/patchyard:#')
  old_port=$(sed -n 's/^NETLAB_PORT=//p' /opt/netlab/deploy/.env 2>/dev/null || true)
  [ -n "$old_port" ] && PORT="$old_port"
  mv /opt/netlab "$HOME_DIR"
  ln -s "$HOME_DIR" /opt/netlab        # running lab containers and old paths keep working
  ok "labs, snapshots, settings and metrics moved; /opt/netlab now points at $HOME_DIR"
fi

# re-runs keep the choices made last time unless overridden
if [ -f "$HOME_DIR/deploy/.env" ]; then
  prev() { sed -n "s/^$1=//p" "$HOME_DIR/deploy/.env"; }
  [ -z "$DOMAIN" ] && DOMAIN=$(prev PATCHYARD_DOMAIN)
  [ -z "$EMAIL" ] && EMAIL=$(prev PATCHYARD_ACME_EMAIL)
  [ "$PUBLIC" = 0 ] && [ "$(prev PATCHYARD_SANDBOX)" = 1 ] && PUBLIC=1
  [ -n "$DOMAIN" ] && BIND=127.0.0.1
fi

# ---------------------------------------------------------------- compose project
say "Patchyard in $HOME_DIR"
mkdir -p "$HOME_DIR/labs" "$HOME_DIR/deploy"
# run from a clone: use the files next to this script; piped from curl: download them
HERE=/nonexistent
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ] && [ "$(basename "${BASH_SOURCE[0]}")" = install.sh ]; then
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
if [ -f "$HERE/docker-compose.yml" ]; then
  cp "$HERE/docker-compose.yml" "$HOME_DIR/deploy/docker-compose.yml"
else
  curl -fsSL "$REPO_RAW/deploy/docker-compose.yml" -o "$HOME_DIR/deploy/docker-compose.yml"
fi
# HTTPS front end (used when COMPOSE_PROFILES=https): written here so the values are literal
{
  echo "# written by install.sh: HTTPS for Patchyard (Let's Encrypt). Re-run the installer to change it."
  [ -n "$EMAIL" ] && printf '{\n\temail %s\n}\n\n' "$EMAIL"
  printf '%s {\n' "${DOMAIN:-localhost}"
  printf '\tencode zstd gzip\n\treverse_proxy 127.0.0.1:%s\n' "$PORT"
  printf '\theader {\n\t\tStrict-Transport-Security "max-age=31536000"\n\t\tX-Content-Type-Options nosniff\n\t\tX-Frame-Options DENY\n\t\tReferrer-Policy same-origin\n\t\t-Server\n\t}\n'
  printf '}\n'
} > "$HOME_DIR/deploy/Caddyfile"
cat > "$HOME_DIR/deploy/.env" <<ENV
PATCHYARD_IMAGE=$IMAGE
PATCHYARD_PORT=$PORT
PATCHYARD_HOME=$HOME_DIR
PATCHYARD_BIND=$BIND
PATCHYARD_UPDATE_CHECK=$UPDATE_CHECK
PATCHYARD_DOCKER_ROOT=$DOCKER_ROOT
PATCHYARD_SANDBOX=$PUBLIC
PATCHYARD_DOMAIN=$DOMAIN
PATCHYARD_ACME_EMAIL=$EMAIL
COMPOSE_PROFILES=$([ -n "$DOMAIN" ] && echo https)
ENV
chmod 600 "$HOME_DIR/deploy/.env"
if [ -n "$DOMAIN" ]; then
  say "HTTPS for $DOMAIN"
  for p in 80 443; do
    if ss -ltnH "sport = :$p" 2>/dev/null | grep -q . && ! docker ps --format '{{.Names}}' | grep -qx patchyard-caddy; then
      die "port $p is already in use; Caddy needs 80 and 443 (stop the other web server, or install without --domain)"
    fi
  done
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ok "ufw: opened 80 and 443"
  fi
  RES=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)
  MINE=$(curl -fsS -4 -m 5 https://api.ipify.org 2>/dev/null || true)
  if [ -z "$RES" ]; then warn "$DOMAIN doesn't resolve yet: point an A record at this server; the certificate follows automatically"
  elif [ -n "$MINE" ] && [ "$RES" != "$MINE" ]; then warn "$DOMAIN points at $RES, but this server is $MINE"
  else ok "$DOMAIN -> $RES"; fi
fi
cd "$HOME_DIR/deploy"
docker compose pull -q || die "couldn't pull $IMAGE (is the package public, or are you logged in to ghcr.io?)"
docker compose up -d
printf '   waiting for the web app'
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/api/version" >/dev/null 2>&1; then break; fi
  printf '.'; sleep 2
done
echo
curl -fsS "http://127.0.0.1:$PORT/api/version" >/dev/null 2>&1 || die "it didn't come up: docker logs patchyard"
ok "$(curl -fsS "http://127.0.0.1:$PORT/api/version")"

# ---------------------------------------------------------------- login
if [ "$LOGIN" != off ] && [ "$(docker exec patchyard python -m app.auth status 2>/dev/null)" != on ]; then
  say "Admin account ($ADMIN)"
  PW=""
  if [ "$PW_STDIN" = 1 ]; then
    read -r PW
  elif [ "$YES" = 0 ] && { exec 3</dev/tty; } 2>/dev/null; then
    while :; do
      printf 'Choose a password for %s (10+ characters, empty = generate one): ' "$ADMIN" >/dev/tty; read -rs PW <&3; echo >/dev/tty
      [ -z "$PW" ] && break
      printf 'Again: ' >/dev/tty; read -rs PW2 <&3; echo >/dev/tty
      [ "$PW" = "$PW2" ] && [ ${#PW} -ge 10 ] && break
      echo "   didn't match or too short, try again" >/dev/tty
    done
    exec 3<&-
  fi
  GEN=0
  if [ -z "$PW" ]; then PW=$(LC_ALL=C tr -dc 'A-Za-z0-9' < <(head -c 512 /dev/urandom)); PW=${PW:0:16}; GEN=1; fi
  printf '%s\n' "$PW" | docker exec -i patchyard python -m app.auth create-admin "$ADMIN" >/dev/null || die "couldn't create the admin account"
  if [ "$GEN" = 1 ]; then ok "login on. User $ADMIN, generated password: $PW   (change it under your name, top right)"; else ok "login on as $ADMIN"; fi
  unset PW PW2
elif [ "$LOGIN" = off ]; then
  warn "login left off: anyone who can reach port $PORT controls this host's Docker"
fi

# ---------------------------------------------------------------- done
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
say "Done"
if [ -n "$DOMAIN" ]; then echo "   Open https://$DOMAIN"; else echo "   Open http://${IP:-<this-host>}:$PORT"; fi
[ "$PUBLIC" = 1 ] && echo "   Invite people from Admin (top right) > Invites; each gets their own account and labs."
echo "   Update:     sudo $HOME_DIR/deploy/update.sh   (or run this installer again)"
echo "   Uninstall:  curl -fsSL $REPO_RAW/deploy/uninstall.sh | sudo bash"
for f in update.sh uninstall.sh; do
  if [ -f "$HERE/$f" ]; then cp "$HERE/$f" "$HOME_DIR/deploy/$f"; else curl -fsSL "$REPO_RAW/deploy/$f" -o "$HOME_DIR/deploy/$f" || true; fi
  chmod +x "$HOME_DIR/deploy/$f" 2>/dev/null || true
done
[ "$BIND" = 0.0.0.0 ] && echo "   Patchyard controls this host's Docker: keep it on a trusted network or behind a reverse proxy with authentication."
if [ "$PUBLIC" = 1 ]; then
  echo "   Public server checklist: firewall everything but 22/80/443 (e.g. ufw allow OpenSSH 80/tcp 443/tcp && ufw enable),"
  echo "   SSH keys only, automatic security updates (unattended-upgrades)."
fi
