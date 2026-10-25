"""Browser terminal: WebSocket <-> pty running `docker exec -it`."""
import asyncio, codecs, fcntl, json, os, pty, struct, subprocess, termios
from fastapi import WebSocket, WebSocketDisconnect
from .util import cname, check_name
from .devices import write_pc_rc


def _set_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _ctty():
    # runs in the child after stdio is dup'd onto the pty slave and setsid() has run
    # (stdlib order). uvloop's subprocess runs preexec_fn *before* the dup2, so the
    # ioctl failed there and every console closed at once - spawn with Popen instead.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


async def console(ws: WebSocket, lab: str, node: str, mode: str):
    await ws.accept()
    try:
        check_name(lab); check_name(node)
    except ValueError as e:
        await ws.send_text(f"\r\n{e}\r\n"); await ws.close(); return
    ctr = cname(lab, node)
    if mode == "vtysh":
        cmd = ["docker", "exec", "-it", ctr, "vtysh"]
    elif mode == "pc":
        await write_pc_rc(lab, node)
        cmd = ["docker", "exec", "-it", "-e", "ENV=/etc/netlab.rc", ctr, "sh", "-i"]
    else:
        cmd = ["docker", "exec", "-it", ctr, "sh"]
    master, slave = pty.openpty()
    _set_size(master, 30, 100)
    try:
        proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave, close_fds=True,
                                start_new_session=True, preexec_fn=_ctty)
    except Exception as e:
        os.close(slave); os.close(master)
        await ws.send_text(f"\r\nconsole failed to start: {e}\r\n"); await ws.close(); return
    os.close(slave)
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    dec = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def on_read():
        try:
            data = os.read(master, 4096)
        except OSError:
            data = b""
        if not data:
            loop.remove_reader(master)
        q.put_nowait(data)

    loop.add_reader(master, on_read)

    async def out():
        while True:
            d = await q.get()
            if not d:
                return
            await ws.send_text(dec.decode(d))

    async def inp():
        while True:
            m = json.loads(await ws.receive_text())
            if m.get("t") == "i":
                os.write(master, m["d"].encode())
            elif m.get("t") == "r":
                _set_size(master, int(m["rows"]), int(m["cols"]))

    tasks = [asyncio.create_task(out()), asyncio.create_task(inp())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        try:
            loop.remove_reader(master)
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(proc.wait), 3)
            except asyncio.TimeoutError:
                proc.kill()
        os.close(master)
        try:
            await ws.send_text("\r\n[session closed]\r\n")
            await ws.close()
        except Exception:
            pass
