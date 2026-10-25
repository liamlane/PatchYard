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


async def run(*args, input: bytes | None = None, timeout: float = 60, cwd=None):
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
        p.kill()
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
    rc, out, _ = await run("docker", "ps", "-a", "--filter", f"name=^clab-{lab}-",
                           "--format", "{{.Names}}|{{.State}}")
    states = {}
    prefix = f"clab-{lab}-"
    for line in out.splitlines():
        name, _, state = line.partition("|")
        if name.startswith(prefix):
            states[name[len(prefix):]] = state
    return states


async def container_pid(lab, node) -> int | None:
    rc, out, _ = await run("docker", "inspect", "-f", "{{.State.Pid}}", cname(lab, node))
    try:
        pid = int(out.strip())
        return pid or None
    except ValueError:
        return None
