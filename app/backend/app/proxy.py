"""Package proxy for sandboxed labs: lab devices can't reach the internet, except package
mirrors through this (CONNECT for HTTPS, absolute-URI GET for HTTP).

Only clients from the sandbox management pool are served, only hosts in sandbox.proxy_allow
(or their subdomains) on ports 80/443 are reached. The host firewall (sandbox.firewall)
also drops connections to the port from anywhere else.
"""
import asyncio, ipaddress, logging
from . import settings

log = logging.getLogger("patchyard.proxy")
_SERVER = None
MAX_HEAD = 16384


def allowed_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    for a in settings.get("sandbox.proxy_allow", []) or []:
        a = a.lower().lstrip(".")
        if host == a or host.endswith("." + a):
            return True
    return False


def _from_lab(peer: str) -> bool:
    try:
        return ipaddress.ip_address(peer) in ipaddress.ip_network(settings.get("sandbox.mgmt_pool", "172.30.0.0/16"))
    except ValueError:
        return False


async def _pipe(r, w):
    try:
        while True:
            b = await asyncio.wait_for(r.read(65536), timeout=300)
            if not b:
                break
            w.write(b)
            await w.drain()
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    finally:
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass


async def _reply(w, code, text):
    w.write(f"HTTP/1.1 {code} {text}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
    try:
        await w.drain()
    finally:
        w.close()


async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter):
    peer = (w.get_extra_info("peername") or ("", 0))[0]
    if not _from_lab(peer):
        w.close()
        return
    try:
        head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), timeout=20)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        w.close()
        return
    if len(head) > MAX_HEAD:
        return await _reply(w, 431, "Request Header Fields Too Large")
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, ver = lines[0].split(" ", 2)
    except ValueError:
        return await _reply(w, 400, "Bad Request")
    if method == "CONNECT":
        host, _, port = target.rpartition(":")
        port = int(port) if port.isdigit() else 0
        rest = b""
    else:
        if not target.startswith("http://"):
            return await _reply(w, 400, "Bad Request")
        hp, _, path = target[7:].partition("/")
        host, _, port = hp.partition(":")
        port = int(port) if port.isdigit() else 80
        hdrs = [l for l in lines[1:] if l and not l.lower().startswith(("proxy-", "connection:", "keep-alive:"))]
        rest = (f"{method} /{path} {ver}\r\n" + "\r\n".join(hdrs + ["Connection: close"]) + "\r\n\r\n").encode("latin-1")
    host = host.strip("[]")
    if port not in (80, 443) or not allowed_host(host):
        log.info("proxy refused %s -> %s:%s", peer, host, port)
        return await _reply(w, 403, "Forbidden by Patchyard (only package mirrors are reachable from labs)")
    try:
        ur, uw = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=15)
    except (OSError, asyncio.TimeoutError):
        return await _reply(w, 502, "Bad Gateway")
    if method == "CONNECT":
        w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await w.drain()
    else:
        uw.write(rest)
        await uw.drain()
    await asyncio.gather(_pipe(r, uw), _pipe(ur, w))


async def start():
    global _SERVER
    if _SERVER or not settings.get("sandbox.enabled", False):
        return
    port = int(settings.get("sandbox.proxy_port", 3128))
    try:
        _SERVER = await asyncio.start_server(handle, "0.0.0.0", port, limit=MAX_HEAD * 2)
        log.info("package proxy on :%s", port)
    except OSError as e:
        log.warning("package proxy: %s", e)


async def stop():
    global _SERVER
    if _SERVER:
        _SERVER.close()
        _SERVER = None
