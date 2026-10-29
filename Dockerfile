# Patchyard: one image with the web app, containerlab and every host tool it drives.
# Built and published to ghcr.io by .github/workflows/build.yml on every push to main.

# ---- frontend ----
FROM node:20-bookworm-slim AS ui
WORKDIR /ui
COPY app/frontend/package.json app/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY app/frontend/ ./
RUN npm run build

# ---- runtime ----
FROM python:3.12-slim-bookworm
ARG CLAB_VERSION=0.82.0
ARG DOCKER_CLI_VERSION=27.5.1
ARG PATCHYARD_VERSION=dev
ARG PATCHYARD_COMMIT=
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PATCHYARD_HOME=/opt/patchyard PATCHYARD_PORT=8080 \
    PATCHYARD_VERSION=${PATCHYARD_VERSION} PATCHYARD_COMMIT=${PATCHYARD_COMMIT}

# host-side tools: ip/tc/bridge, iptables, nft + conntrack (router ACL/NAT), dnsmasq (DHCP relay), tcpdump, nsenter, modprobe, iperf3, ping
RUN apt-get update && apt-get install -y --no-install-recommends \
      iproute2 iptables nftables conntrack dnsmasq-base tcpdump util-linux kmod iperf3 iputils-ping traceroute tftp-hpa curl ca-certificates procps openssh-client \
 && rm -rf /var/lib/apt/lists/*

# Docker CLI (talks to the host daemon through the mounted socket) and containerlab, pinned
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
      | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
 && curl -fsSL https://get.containerlab.dev | bash -s -- -v "${CLAB_VERSION}" \
 && containerlab version && docker --version

WORKDIR /opt/patchyard-app/backend
COPY app/backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY app/backend/ ./
COPY --from=ui /ui/dist /opt/patchyard-app/frontend/dist

LABEL org.opencontainers.image.title="Patchyard" \
      org.opencontainers.image.description="Self-hosted Packet Tracer-style network lab (containerlab + FRR)" \
      org.opencontainers.image.source="https://github.com/liamlane/patchyard" \
      org.opencontainers.image.licenses="LicenseRef-Patchyard-Personal-Use" \
      org.opencontainers.image.version="${PATCHYARD_VERSION}" org.opencontainers.image.revision="${PATCHYARD_COMMIT}"
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS "http://127.0.0.1:${PATCHYARD_PORT:-${NETLAB_PORT:-8080}}/api/version" || exit 1
CMD ["sh", "-c", "exec uvicorn app.main:app --host ${PATCHYARD_BIND:-0.0.0.0} --port ${PATCHYARD_PORT:-${NETLAB_PORT:-8080}} --proxy-headers"]
