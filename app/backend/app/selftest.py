"""Release self-test: deploy real labs on this server, check they work, clean up.

    sudo docker exec patchyard python -m app.selftest            # OSPF triangle (5 devices, ~2 min)
    sudo docker exec patchyard python -m app.selftest --full     # + small office (firewall, DHCP, DNS, web, PAT)
    sudo docker exec patchyard python -m app.selftest --keep     # leave the labs running to look at
    sudo docker exec patchyard python -m app.selftest --quick    # retry failing checks for 90 s instead of 4 min

Every phase prints progress with the elapsed time. A healthy run takes about 2 minutes: deploy,
40 s for OSPF to converge, then the checks (they pass on the first or second attempt). Checks that
keep failing are retried every 15 s for up to 4 minutes, which is why a broken lab takes longer;
the failing devices' permissions, processes, addresses and logs are printed at the end.

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


def run_lab(c: Client, tid: str, checks: list[dict], settle: int, keep: bool, retry: int = 240):
    name = f"selftest-{tid}-{int(time.time()) % 100000}"
    print(f"\n{tid} → {name}")
    ok = step("create from template", lambda: c.call("POST", f"/api/templates/{tid}", {"name": name}) and "")
    if not ok:
        return
    say(f"{tid}: create → deploy → wait for devices → settle {settle}s → checks (retried for up to {retry}s) → destroy")
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

        # routing needs about 40 s (OSPF dead interval); services need their packages installed first
        for left in range(settle, 0, -10):
            say(f"letting routing and services settle: {left}s left")
            time.sleep(min(10, left))
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
            wait(scored, retry, every=15, what="every check to pass")
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
    finally:
        if not keep:
            step("destroy", lambda: c.call("POST", f"/api/labs/{name}/destroy") and "")
            step("delete", lambda: c.call("DELETE", f"/api/labs/{name}") and "")
        else:
            print(f"  {DIM}kept: {name}{END}")


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
    ], settle=40, keep=keep, retry=retry)
    if full:
        run_lab(c, "small-office", [
            {"type": "service", "node": "srv1", "service": s} for s in ("dhcp", "dns", "http", "ftp", "tftp", "syslog", "ntp")
        ] + [
            {"type": "dhcp", "node": "pc1", "subnet": "192.168.10.0/24"},
            {"type": "http", "node": "pc1", "url": "http://intranet.office.lab", "match": "intranet"},
            {"type": "http", "node": "pc2", "url": "http://www.example.net", "match": "example"},
            {"type": "ping", "node": "pc1", "target": "198.51.100.10"},
        ], settle=60, keep=keep, retry=retry)
    bad = [r for r in RESULTS if not r[0]]
    t = int(time.time() - T0)
    print(f"\n{GREEN if not bad else RED}{len(RESULTS) - len(bad)}/{len(RESULTS)} passed{END} {DIM}in {t // 60}m {t % 60:02d}s{END}")
    for _, n, d in bad:
        print(f"  {RED}✗{END} {n}: {d}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
