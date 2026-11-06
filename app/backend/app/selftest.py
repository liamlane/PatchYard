"""Release self-test: deploy real labs on this server, check they work, clean up.

    sudo docker exec patchyard python -m app.selftest            # OSPF triangle (5 devices, ~2 min)
    sudo docker exec patchyard python -m app.selftest --full     # + small office (firewall, DHCP, DNS, web, PAT) and ISP/Internet devices
    sudo docker exec patchyard python -m app.selftest --keep     # leave the labs running to look at
    sudo docker exec patchyard python -m app.selftest --quick    # retry failing checks for 90 s instead of 4 min

Every phase prints progress with the elapsed time. After a short head start the checks run every
10 s until they all pass, so a healthy lab finishes as soon as it's ready (about 30 s for the OSPF
triangle). Checks that keep failing are retried for up to 4 minutes (90 s with --quick), which is
why a broken lab takes longer; the failing devices' permissions, processes, addresses and logs are
printed at the end.

It talks to the running app over HTTP on localhost as the first admin, so it exercises
everything a user would: templates, deploy (sandboxed on a --public server), FRR, package
installs through the lab proxy, DHCP/DNS/HTTP services, the IOS CLI, challenge checks,
the egress firewall and destroy. Exit code 0 = every step passed.
"""
import json, os, subprocess, sys, threading, time, urllib.request, urllib.error

BASE = f"http://127.0.0.1:{os.environ.get('PATCHYARD_PORT') or os.environ.get('NETLAB_PORT') or 8080}"
GREEN, RED, DIM, END = ("\033[32m", "\033[31m", "\033[2m", "\033[0m") if sys.stdout.isatty() else ("", "", "", "")


class Fail(Exception):
    pass


class Client:
    def __init__(self):
        from . import auth, users
        self.cookie = ""
        if users.any_users():
            tok, _ = auth.make_session(auth.first_admin())
            self.cookie = f"{auth.COOKIE}={tok}"

    def call(self, method, path, body=None, timeout=900):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"content-type": "application/json", "cookie": self.cookie})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail")
            except ValueError:
                detail = e.reason
            raise Fail(f"{method} {path}: {e.code} {detail}")


RESULTS = []
T0 = time.time()


def say(msg):
    """A progress line with the time since the self-test started."""
    t = int(time.time() - T0)
    print(f"  {DIM}[{t // 60:02d}:{t % 60:02d}] {msg}{END}", flush=True)


class Ticker:
    """Prints 'still working' every `every` seconds while a long call runs, so nothing looks stuck."""
    def __init__(self, what, every=10):
        self.what, self.every, self.stop = what, every, threading.Event()

    def __enter__(self):
        t0 = time.time()
        def run():
            while not self.stop.wait(self.every):
                say(f"{self.what}… {time.time() - t0:.0f}s")
        self.th = threading.Thread(target=run, daemon=True)
        self.th.start()
        return self

    def __exit__(self, *a):
        self.stop.set()


def step(name, fn, tick=True):
    t = time.time()
    try:
        if tick:
            with Ticker(name):
                out = fn()
        else:
            out = fn()
        RESULTS.append((True, name, out or ""))
        print(f"  {GREEN}✓{END} {name} {DIM}{out or ''} ({time.time() - t:.0f}s){END}", flush=True)
        return True
    except Exception as e:  # noqa: BLE001
        RESULTS.append((False, name, str(e)))
        print(f"  {RED}✗ {name}: {e} ({time.time() - t:.0f}s){END}", flush=True)
        return False


def wait(fn, timeout, every=5, what="condition", progress=None):
    t0, end = time.time(), time.time() + timeout
    last = None
    while time.time() < end:
        try:
            r = fn()
            if r:
                return r
        except Fail as e:
            last = e
        if progress:
            say(f"{what}: {progress()} · {time.time() - t0:.0f}s of {timeout}s")
        time.sleep(every)
    raise Fail(f"timed out after {timeout}s waiting for {what}" + (f" ({last})" if last else ""))


def _sh(*cmd, timeout=15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return f"({e})"


def diagnose(lab: str, nodes: list[str]):
    """What a failing device looks like from the inside: permissions, processes, addresses, its log."""
    print(f"\n  {RED}diagnostics{END} {DIM}(paste these when reporting a failure){END}")
    for n in nodes:
        c = f"clab-{lab}-{n}"
        print(f"  ── {n} ({c})")
        print("    " + _sh("docker", "inspect", c, "--format",
                            "state={{.State.Status}} privileged={{.HostConfig.Privileged}} caps={{.HostConfig.CapAdd}} "
                            "mem={{.HostConfig.Memory}} restarts={{.RestartCount}}"))
        for label, cmd in (("processes", ["ps", "-o", "comm="]), ("addresses", ["ip", "-br", "addr"]),
                           ("/etc/frr", ["ls", "-la", "/etc/frr"]), ("daemons", ["sh", "-c", "grep -E '^(zebra|ospfd|bgpd|ldpd)=' /etc/frr/daemons 2>/dev/null"])):
            out = _sh("docker", "exec", c, *cmd)
            print(f"    {label}: " + " · ".join(x.strip() for x in out.splitlines()[:25] if x.strip()))
        log = _sh("docker", "logs", "--tail", "15", c)
        for ln in log.splitlines()[-15:]:
            print(f"    log | {ln[:200]}")


def run_lab(c: Client, tid: str, checks: list[dict], settle: int, keep: bool, retry: int = 240, build=None, after_up=None, extra=None):
    """build(c, name) makes the lab (default: from template tid); after_up(c, name) runs once every device is up."""
    name = f"selftest-{tid}-{int(time.time()) % 100000}"
    print(f"\n{tid} → {name}")
    if build:
        ok = step("create lab", lambda: build(c, name) or "")
    else:
        ok = step("create from template", lambda: c.call("POST", f"/api/templates/{tid}", {"name": name}) and "")
    if not ok:
        return
    say(f"{tid}: create → deploy → wait for devices → {settle}s head start → checks every 10s until they pass (up to {retry}s) → destroy")
    try:
        lab = c.call("GET", f"/api/labs/{name}")
        lab["challenge"] = {"title": "self-test", "instructions": "", "feedback": "full", "checks": checks}
        step("attach checks", lambda: c.call("PUT", f"/api/labs/{name}", lab) and f"{len(checks)} checks")

        def deploy():
            r = c.call("POST", f"/api/labs/{name}/deploy")
            if not r.get("ok"):
                raise Fail("containerlab: " + r.get("output", "")[-600:])
            return "deployed"
        if not step("deploy", deploy):
            return

        want = len(lab["nodes"])
        seen = {"n": 0}

        def running():
            st = c.call("GET", f"/api/labs/{name}/status")
            states = st.get("nodes") or {}
            seen["n"] = sum(1 for v in states.values() if (v.get("state") if isinstance(v, dict) else v) == "running")
            if len(states) < want:
                return False
            bad = {k: v for k, v in states.items() if (v.get("state") if isinstance(v, dict) else v) != "running"}
            return False if bad else f"{len(states)} devices running"
        step("every device running", lambda: wait(running, 180, what="devices up", progress=lambda: f"{seen['n']}/{want} running"), tick=False)
        if after_up:
            step("set up", lambda: after_up(c, name))

        # a short head start, then the checks run every 10 s until they all pass: a healthy lab is
        # usually ready in 15-30 s (OSPF adjacencies, packages installing on servers)
        if settle:
            say(f"giving routing and services a {settle}s head start")
            time.sleep(settle)
        last = {}
        tries = {"n": 0}

        def scored():
            tries["n"] += 1
            t = time.time()
            with Ticker(f"running {len(checks)} checks (attempt {tries['n']})"):
                r = c.call("POST", f"/api/labs/{name}/challenge/check")
            last.clear(); last.update(r)
            res = r.get("results", [])
            bad = [x["label"] for x in res if not x["ok"]]
            say(f"attempt {tries['n']}: {len(res) - len(bad)}/{len(res)} passing ({time.time() - t:.0f}s)"
                + (f" · waiting on: {'; '.join(bad[:3])}{' …' if len(bad) > 3 else ''}" if bad else ""))
            return r if r["pct"] == 100 else False
        try:
            wait(scored, retry, every=10, what="every check to pass")
        except Fail:
            say(f"gave up after {retry}s of retries")
        for r in last.get("results", []):
            RESULTS.append((r["ok"], f"check: {r['label']}", r["detail"]))
            print(f"  {GREEN + '✓' if r['ok'] else RED + '✗'}{END} {r['label']} {DIM}{r['detail']}{END}")
        if not last:
            RESULTS.append((False, "checks", "never ran"))
        failing = sorted({x["node"] for x, r in zip(checks, last.get("results", [])) if not r["ok"]} if last else set())
        if failing:
            diagnose(name, failing)
        if extra:
            for label, fn in extra:
                step(label, lambda fn=fn: fn(c, name))
    finally:
        if not keep:
            step("destroy", lambda: c.call("POST", f"/api/labs/{name}/destroy") and "")
            step("delete", lambda: c.call("DELETE", f"/api/labs/{name}") and "")
        else:
            print(f"  {DIM}kept: {name}{END}")


ISP_LAB = {
    "nodes": [{"id": "inet1", "type": "internet", "x": 300, "y": 0, "props": {"asn": "64496", "pool": "203.0.113.0/24"}},
              {"id": "isp1", "type": "isp", "x": 300, "y": 150, "props": {"asn": "64501", "pool": "81.10.0.0/16", "ports": {"eth1": {"mode": "dhcp"}}}},
              {"id": "pc1", "type": "pc", "x": 150, "y": 300, "props": {"ip_mode": "dhcp"}},
              {"id": "r1", "type": "router", "x": 450, "y": 300, "props": {"chassis": "router-2s"}}],
    "links": [{"id": "l1", "a": "pc1", "a_if": "eth1", "b": "isp1", "b_if": "eth1"},
              {"id": "l2", "a": "r1", "a_if": "eth1", "b": "isp1", "b_if": "eth2"},
              {"id": "l3", "a": "isp1", "a_if": "eth3", "b": "inet1", "b_if": "eth1"}],
}
ISP_CHECKS = [
    {"type": "bgp_up", "node": "isp1", "count": 1, "label": "isp1 peers with inet1 over BGP"},
    {"type": "dhcp", "node": "pc1", "subnet": "81.10.0.8/29", "label": "pc1 got a broadband address from isp1"},
    {"type": "ping", "node": "pc1", "target": "8.8.8.8", "label": "pc1 can ping 8.8.8.8 (on inet1)"},
    {"type": "http", "node": "pc1", "url": "http://www.example.com", "match": "www.example.com", "label": "pc1 can open www.example.com"},
    {"type": "http", "node": "pc1", "url": "http://whatismyip.example.com", "match": "81.10.0.", "label": "whatismyip shows pc1's ISP address"},
    {"type": "route", "node": "r1", "prefix": "0.0.0.0/0", "protocol": "static", "label": "r1 has a default route to isp1 (Configure for me)"},
    {"type": "ping", "node": "r1", "target": "1.1.1.1", "label": "r1 can ping 1.1.1.1"},
]


def _build_isp(c, name):
    c.call("POST", "/api/labs", {"name": name})
    lab = c.call("GET", f"/api/labs/{name}")
    lab.update(ISP_LAB)
    c.call("PUT", f"/api/labs/{name}", lab)
    return "pc1 on DHCP, r1 on a static /30, isp1 peering with inet1"


def _configure_r1(c, name):
    def go():
        r = c.call("POST", f"/api/labs/{name}/nodes/isp1/isp/configure", {"port": "eth2"})
        if not r.get("ok"):
            raise Fail("; ".join(r.get("errors") or ["failed"]))
        return "r1 configured from the ISP's port page"
    return wait(go, 60, every=5, what="r1's CLI to accept the ISP settings")


def _tm(c, name):
    c.call("POST", f"/api/labs/{name}/history/record")
    pts = c.call("GET", f"/api/labs/{name}/history")["points"]
    if not pts:
        raise Fail("nothing recorded")
    return f"{len(pts)} change point(s), last at {time.strftime('%H:%M:%S', time.localtime(pts[-1]['ts']))}"


def _pcap(c, name):
    lab = c.call("GET", f"/api/labs/{name}")
    link = lab["links"][0]["id"]
    req = urllib.request.Request(f"{BASE}/api/labs/{name}/links/{link}/pcap?seconds=3", headers={"cookie": c.cookie})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    if data[:4] not in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"):
        raise Fail(f"not a pcap ({len(data)} bytes)")
    return f"{len(data)} bytes of pcap in 3 s"


def main(argv):
    keep, full = "--keep" in argv, "--full" in argv
    retry = 90 if "--quick" in argv else 240
    c = Client()
    print(f"Patchyard self-test against {BASE}")
    v = c.call("GET", "/api/version")
    print(f"version {v.get('version')} ({v.get('commit') or 'local'})")
    sysinfo = c.call("GET", "/api/system")
    missing = [f["needed_for"] for f in (sysinfo.get("features") or {}).values() if not f.get("ok")]
    if missing:
        print(f"{RED}host is missing kernel features for: {', '.join(missing)}{END}")
    st = c.call("GET", "/api/auth/state")
    print(f"sandbox {'ON' if st.get('sandbox') else 'off'} · login {'on' if st.get('enabled') else 'off'} · email {'on' if st.get('mail') else 'off'}")
    if st.get("sandbox"):
        import subprocess
        def fw():
            r = subprocess.run(["nft", "list", "table", "ip", "patchyard"], capture_output=True, text=True)
            if r.returncode or "lab egress" not in r.stdout:
                raise Fail("host table ip patchyard is missing: " + (r.stderr or r.stdout)[:200])
            return "ip patchyard loaded"
        step("lab egress firewall", fw)
        def proxy():
            import socket
            port = int(c.call("GET", "/api/settings").get("sandbox", {}).get("proxy_port", 3128))
            with socket.create_connection(("127.0.0.1", port), timeout=3):
                return f"listening on :{port}"
        step("package proxy", proxy)
    run_lab(c, "ospf-triangle", [
        {"type": "ospf_full", "node": "r1", "count": 2}, {"type": "ospf_full", "node": "r3", "count": 2},
        {"type": "route", "node": "r1", "prefix": "192.168.3.0/24", "protocol": "ospf"},
        {"type": "ios_show", "node": "r1", "command": "show ip interface brief", "match": "10.0.12.1"},
        {"type": "service", "node": "srv1", "service": "http"}, {"type": "service", "node": "srv1", "service": "dns"},
        {"type": "ping", "node": "pc1", "target": "192.168.3.10"},
        {"type": "http", "node": "pc1", "url": "http://www.lab", "match": "www.lab"},
    ], settle=10, keep=keep, retry=retry, extra=[("time machine records the lab", _tm), ("Wireshark capture of a cable", _pcap)])
    if full:
        run_lab(c, "small-office", [
            {"type": "service", "node": "srv1", "service": s} for s in ("dhcp", "dns", "http", "ftp", "tftp", "syslog", "ntp")
        ] + [
            {"type": "dhcp", "node": "pc1", "subnet": "192.168.10.0/24"},
            {"type": "http", "node": "pc1", "url": "http://intranet.office.lab", "match": "intranet"},
            {"type": "http", "node": "pc2", "url": "http://www.example.net", "match": "example"},
            {"type": "ping", "node": "pc1", "target": "198.51.100.10"},
        ], settle=15, keep=keep, retry=retry)
        run_lab(c, "isp-internet", ISP_CHECKS, settle=15, keep=keep, retry=retry, build=_build_isp, after_up=_configure_r1)
    bad = [r for r in RESULTS if not r[0]]
    t = int(time.time() - T0)
    print(f"\n{GREEN if not bad else RED}{len(RESULTS) - len(bad)}/{len(RESULTS)} passed{END} {DIM}in {t // 60}m {t % 60:02d}s{END}")
    for _, n, d in bad:
        print(f"  {RED}✗{END} {n}: {d}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
