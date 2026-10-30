"""Subprocess and docker helpers. Every command is passed as an argv list (no shell)."""
import asyncio, ipaddress, re

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
IF_RE = re.compile(r"^[a-zA-Z0-9._-]{1,15}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


def check_name(n: str) -> str:
    if not NAME_RE.match(n or ""):
        raise ValueError(f"invalid name '{n}': use lowercase letters, digits and dashes")
    return n


def check_if(n: str) -> str:
    if not IF_RE.match(n or ""):
        raise ValueError(f"invalid interface '{n}'")
    return n


def check_ip(v: str, cidr=False) -> str:
    (ipaddress.ip_interface if cidr else ipaddress.ip_address)(v)
    return v


def check_host(v: str) -> str:
    if not HOST_RE.match(v or ""):
        raise ValueError(f"invalid host name '{v}'")
    return v


GEN = [0]          # bumped by any command that starts/stops/changes containers (netinfo's cache key)
_MUTATE = {"run", "rm", "start", "stop", "kill", "restart", "pause", "unpause", "create", "deploy", "destroy", "network", "container"}


async def run(*args, input: bytes | None = None, timeout: float = 60, cwd=None):
    mut = len(args) > 1 and str(args[0]).rsplit("/", 1)[-1] in ("docker", "containerlab", "clab") and str(args[1]) in _MUTATE
    try:
        return await _run(args, input, timeout, cwd)
    finally:
        if mut:
            GEN[0] += 1


async def _run(args, input, timeout, cwd):
    try:
        p = await asyncio.create_subprocess_exec(
            *args, cwd=cwd,
            stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        return 127, "", f"{args[0]}: command not found"
    try:
        out, err = await asyncio.wait_for(p.communicate(input), timeout)
    except asyncio.TimeoutError:
        try:
            p.kill()
            await asyncio.wait_for(p.wait(), 5)          # reap it, no zombies
        except (ProcessLookupError, asyncio.TimeoutError):
            pass
        return 124, "", f"timed out after {timeout}s"
    return p.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def cname(lab: str, node: str) -> str:
    return f"clab-{lab}-{node}"


async def dexec(lab, node, *cmd, input: bytes | None = None, timeout: float = 30):
    flags = ["-i"] if input is not None else []
    return await run("docker", "exec", *flags, cname(lab, node), *cmd, input=input, timeout=timeout)


async def dsh(lab, node, script: str, timeout: float = 30):
    return await dexec(lab, node, "sh", "-c", script, timeout=timeout)


async def container_states(lab: str) -> dict:
    """{node: state} for one lab, by containerlab's labels (a name prefix would mix up lab 'a' and lab 'a-b')."""
    from . import netinfo
    return {node: c["state"] for (l, node), c in (await netinfo.containers()).items() if l == lab}


async def container_pid(lab, node) -> int | None:
    rc, out, _ = await run("docker", "inspect", "-f", "{{.State.Pid}}", cname(lab, node))
    try:
        pid = int(out.strip())
        return pid or None
    except ValueError:
        return None
