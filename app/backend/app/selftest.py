"""Release self-test: deploy real labs on this server, check they work, clean up.

    sudo docker exec patchyard python -m app.selftest            # OSPF triangle (5 devices, ~2 min)
    sudo docker exec patchyard python -m app.selftest --full     # + small office (firewall, DHCP, DNS, web, PAT)
    sudo docker exec patchyard python -m app.selftest --keep     # leave the labs running to look at

It talks to the running app over HTTP on localhost as the first admin, so it exercises
everything a user would: templates, deploy (sandboxed on a --public server), FRR, package
installs through the lab proxy, DHCP/DNS/HTTP services, the IOS CLI, challenge checks,
the egress firewall and destroy. Exit code 0 = every step passed.
"""
import json, os, sys, time, urllib.request, urllib.error

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


def step(name, fn):
    t = time.time()
    try:
        out = fn()
        RESULTS.append((True, name, out or ""))
        print(f"  {GREEN}✓{END} {name} {DIM}{out or ''} ({time.time() - t:.0f}s){END}", flush=True)
        return True
    except Exception as e:  # noqa: BLE001
        RESULTS.append((False, name, str(e)))
        print(f"  {RED}✗ {name}: {e}{END}", flush=True)
        return False


def wait(fn, timeout, every=5, what="condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            r = fn()
            if r:
                return r
        except Fail as e:
            last = e
        time.sleep(every)
    raise Fail(f"timed out after {timeout}s waiting for {what}" + (f" ({last})" if last else ""))


def run_lab(c: Client, tid: str, checks: list[dict], settle: int, keep: bool):
    name = f"selftest-{tid}-{int(time.time()) % 100000}"
    print(f"\n{tid} → {name}")
    ok = step("create from template", lambda: c.call("POST", f"/api/templates/{tid}", {"name": name}) and "")
    if not ok:
        return
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

        def running():
            st = c.call("GET", f"/api/labs/{name}/status")
            states = st.get("nodes") or {}
            if len(states) < want:
                return False
            bad = {k: v for k, v in states.items() if (v.get("state") if isinstance(v, dict) else v) != "running"}
            return False if bad else f"{len(states)} devices running"
        step("every device running", lambda: wait(running, 180, what="devices"))
        print(f"  {DIM}… letting routing and services settle ({settle}s){END}", flush=True)
        last = {}

        def scored():
            r = c.call("POST", f"/api/labs/{name}/challenge/check")
            last.update(r)
            return r if r["pct"] == 100 else False
        try:
            time.sleep(settle)
            wait(scored, 240, every=15, what="every check to pass")
        except Fail:
            pass
        for r in last.get("results", []):
            RESULTS.append((r["ok"], f"check: {r['label']}", r["detail"]))
            print(f"  {GREEN + '✓' if r['ok'] else RED + '✗'}{END} {r['label']} {DIM}{r['detail']}{END}")
        if not last:
            RESULTS.append((False, "checks", "never ran"))
    finally:
        if not keep:
            step("destroy", lambda: c.call("POST", f"/api/labs/{name}/destroy") and "")
            step("delete", lambda: c.call("DELETE", f"/api/labs/{name}") and "")
        else:
            print(f"  {DIM}kept: {name}{END}")


def main(argv):
    keep, full = "--keep" in argv, "--full" in argv
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
    ], settle=40, keep=keep)
    if full:
        run_lab(c, "small-office", [
            {"type": "service", "node": "srv1", "service": s} for s in ("dhcp", "dns", "http", "ftp", "tftp", "syslog", "ntp")
        ] + [
            {"type": "dhcp", "node": "pc1", "subnet": "192.168.10.0/24"},
            {"type": "http", "node": "pc1", "url": "http://intranet.office.lab", "match": "intranet"},
            {"type": "http", "node": "pc2", "url": "http://www.example.net", "match": "example"},
            {"type": "ping", "node": "pc1", "target": "198.51.100.10"},
        ], settle=60, keep=keep)
    bad = [r for r in RESULTS if not r[0]]
    print(f"\n{GREEN if not bad else RED}{len(RESULTS) - len(bad)}/{len(RESULTS)} passed{END}")
    for _, n, d in bad:
        print(f"  {RED}✗{END} {n}: {d}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
