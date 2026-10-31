# Installing Patchyard

## What you need

| | Minimum | Comfortable |
| --- | --- | --- |
| CPU | 2 cores, x86_64 | 4+ cores |
| Memory | 4 GB | 8 GB+ (the 27-device MPLS template wants about 6 GB) |
| Disk | 20 GB free | 40 GB |
| OS | Linux with systemd and Docker Engine | Ubuntu 24.04 LTS (tested), Ubuntu 22.04, Debian 12 |
| Network | internet access during install and the first deploy of each image | |

Patchyard needs a real Linux kernel, either on bare metal or in a VM. Proxmox, VMware, Hyper-V and VirtualBox VMs all work. It doesn't run inside an LXC container or on Docker Desktop (Windows/macOS), because it creates network namespaces, veth cables and kernel VRFs on the host. On Windows, use a Hyper-V or VirtualBox VM running Ubuntu. WSL2 is untested.

Some features depend on kernel modules. The installer loads every module it can find and says which ones are missing. Settings → About shows the same list later.

| Module | Needed for |
| --- | --- |
| `bridge`, `8021q`, `sch_netem`, `dummy`, `nf_tables` | core: switches, VLAN subinterfaces, link speeds and faults, loopbacks, firewalls/ACLs/NAT |
| `vrf` | router VRFs, MPLS L3VPN template |
| `mpls_router`, `mpls_iptunnel` | MPLS/LDP, MPLS L3VPN template |
| `openvswitch` | the Open vSwitch switch engine (RSTP, LACP), campus RSTP + LACP template |

On Ubuntu cloud images and minimal installs, the MPLS and VRF modules are in `linux-modules-extra-$(uname -r)`. The installer adds that package when it's available.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/liamlane/patchyard/main/deploy/install.sh | sudo bash
```

Or from a clone:

```bash
git clone https://github.com/liamlane/patchyard.git && cd patchyard
sudo bash deploy/install.sh
```

The installer does the following:

1. Installs Docker Engine and the compose plugin from docker.com if they're missing. It refuses the snap version of Docker, which containerlab can't use.
2. Loads the kernel modules, and writes `/etc/modules-load.d/patchyard.conf` so they load at boot.
3. Writes `/opt/patchyard/deploy/docker-compose.yml` and `.env`, pulls `ghcr.io/liamlane/patchyard`, and starts it.
4. Creates the admin account (`--admin`, default `admin`): it asks for a password of 10+ characters and turns the login on. With `--yes` it generates a password and prints it. More accounts come from invite links (your name, top right → Admin).
5. With `--domain`: writes a Caddyfile and starts Caddy for HTTPS, opens 80/443 in ufw if it's active, and checks the DNS record.

### Options

| Option | Default | |
| --- | --- | --- |
| `--port N` | 8080 | web UI port |
| `--home DIR` | `/opt/patchyard` | data folder (labs, configs, snapshots, settings, metrics) |
| `--bind ADDR` | `0.0.0.0` | `127.0.0.1` to reach it only through a reverse proxy on the same host |
| `--public` | | hosting for other people: sandboxed lab devices, labs can't reach the internet except package mirrors, login required ([docs/vps.md](vps.md)) |
| `--domain NAME` | | HTTPS on `https://NAME` with Caddy and Let's Encrypt (needs ports 80/443); Patchyard then listens on 127.0.0.1 only |
| `--email ADDR` | | contact address for Let's Encrypt |
| `--admin NAME` | `admin` | name of the admin account the installer creates |
| `--image REF` | `ghcr.io/liamlane/patchyard:latest` | pin a version, e.g. `…/patchyard:0.12.0` |
| `--password-stdin` | | read the admin password from stdin (for automation) |
| `--no-login` | | leave the login off (trusted, isolated networks only) |
| `--no-update-check` | | don't ask GitHub for new releases |
| `-y`, `--yes` | | no questions |

To pass options with the one-liner, use `curl -fsSL …/install.sh | sudo bash -s -- --port 9090`.

Run the installer again at any time: it updates the install in place and never touches your labs.

## Check a host first

```bash
sudo bash tools/patchyard-preflight.sh
```

Each line is OK, WARN or FAIL:

- **FAIL** means Patchyard won't work until it's fixed.
- **WARN** names the feature that won't work.

## Update

```bash
sudo /opt/patchyard/deploy/update.sh
```

This pulls the newest image and restarts the container. Running labs keep running while it restarts.

- **Pin a version:** set `PATCHYARD_IMAGE=ghcr.io/liamlane/patchyard:0.13.0` in `/opt/patchyard/deploy/.env`.
- **New release notice:** Settings → About shows when a new release is out.

## Check it works

```bash
sudo docker exec patchyard python -m app.selftest          # OSPF triangle: about 2 minutes
sudo docker exec patchyard python -m app.selftest --full   # also the small office (firewall, DHCP, DNS, web, PAT)
sudo docker exec patchyard python -m app.selftest --keep   # leave the test labs running to look at
```

The self-test talks to the running app as the first admin. It creates labs from templates, deploys them, waits for OSPF, services and pings, checks them with the same engine as [challenges](challenges.md), then destroys and deletes them. On a server installed with `--public` it also checks the lab egress firewall and the package proxy. Every line should be a ✓, and the exit code is 0 when everything passed.

## Email

Patchyard can send account emails, password reset links and alert digests through any SMTP provider. Set it up in the app: your name → **Admin** → **Email**, then **Save and send test**. The steps, and what to do if the test can't connect, are in [vps.md](vps.md#5-email). Links in emails use the site address you set there, or `https://<--domain>`.

## Reverse proxy

Patchyard uses WebSockets for the consoles and live views. Pass `Upgrade`/`Connection` headers through and use long read timeouts. For example, with nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 1h;
}
```

## Uninstall

```bash
sudo /opt/patchyard/deploy/uninstall.sh                 # keeps labs and settings
sudo /opt/patchyard/deploy/uninstall.sh --destroy-labs  # also removes running lab devices
sudo /opt/patchyard/deploy/uninstall.sh --purge         # also deletes /opt/patchyard (asks first)
```

## Upgrading from netlab

Patchyard was called netlab before 0.12.0. To upgrade, run the 0.12.0 installer, or `./deploy/update.sh` from an updated clone. Either one migrates the old install in place:

- stops the old `netlab` container,
- moves `/opt/netlab` to `/opt/patchyard` and leaves a symlink at `/opt/netlab`, so running labs and old paths keep working,
- keeps your port, and switches the image from `…/netlab` to `…/patchyard`,
- keeps your settings; your old password becomes the `admin` account (sign in as `admin`).

`NETLAB_*` environment variables still work as fallbacks for `PATCHYARD_*`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `couldn't pull ghcr.io/liamlane/patchyard` | network/proxy to ghcr.io; a fork's package must be public or you need `docker login ghcr.io` |
| Web UI doesn't come up | `docker logs patchyard` |
| A deploy fails with a VRF/MPLS error | the module is missing: Settings → About, then `sudo modprobe vrf mpls_router mpls_iptunnel` |
| Open vSwitch switches stay down | `sudo modprobe openvswitch`; Ubuntu: `linux-modules-extra-$(uname -r)` |
| Servers/firewalls come up without services | they install Alpine packages at first start: the host needs to reach `dl-cdn.alpinelinux.org` |
| Clash with an existing 172.31.255.0/24 network | change the management subnet in Settings → Lab defaults, then redeploy |
| Locked out | `sudo docker exec patchyard python -m app.auth reset-password NAME` (prints a new password) |
| Lost phone and recovery codes | `sudo docker exec patchyard python -m app.auth reset-2fa NAME`, then sign in and set two-factor up again |
| Two-factor codes always "wrong" | the phone's clock is off: turn on automatic time; codes are accepted 30 s either side |
| `python -m app.selftest`: 2fa-setup-required | the first admin must set up two-factor sign-in first (sign in once in the browser) |
| Teams channel test fails with HTTP 400/404 | use a Workflows webhook (*Post to a channel when a webhook request is received*); the old Office 365 connector URLs are retired |
| Create or reset an admin from the shell | `echo 'new-password' \| sudo docker exec -i patchyard python -m app.auth create-admin NAME` |
| Email test: "couldn't connect" | the host or port is wrong, or your server's provider blocks outgoing SMTP; try 587 with STARTTLS (port 25 is often blocked) |
| Email test: "refused the username or password" | Microsoft 365 and Gmail need an *app password* (and SMTP AUTH enabled for the mailbox in Microsoft 365) |
| Email test: "refused the From address" | the From address must be the account you sign in with, or one it may send as |
| No "Forgot your password?" link | email must be on, *Forgot password* ticked under Admin → Email, and a site address known |
| "your labs use … of your … MB" | the per-user disk allowance (Admin → Limits & sandbox, or per user under Users → Limits): delete snapshots, server files or labs |
| A user's lab devices can't install packages | the mirror must be in Admin → Limits & sandbox → package mirrors; check `sudo docker logs patchyard \| grep proxy` |
