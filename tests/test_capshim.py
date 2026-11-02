"""capshim: FRR starts in a sandboxed container without CAP_SYS_ADMIN."""
import os, shutil, subprocess
from pathlib import Path
import pytest

SRC = Path(__file__).resolve().parents[1] / "app" / "capshim" / "capshim.c"
TEST = r'''
#include <stdio.h>
#include <sys/capability.h>
int main(void) {   /* what FRR's privs_init does: ask for NET_ADMIN, NET_RAW and SYS_ADMIN */
    cap_t c = cap_get_proc();
    cap_value_t want[] = {CAP_NET_ADMIN, CAP_NET_RAW, CAP_SYS_ADMIN};
    cap_clear(c);
    cap_set_flag(c, CAP_PERMITTED, 3, want, CAP_SET);
    cap_set_flag(c, CAP_EFFECTIVE, 3, want, CAP_SET);
    if (cap_set_proc(c)) { perror("cap_set_proc"); return 1; }
    printf("%s\n", cap_to_text(cap_get_proc(), NULL));
    return 0;
}
'''


@pytest.mark.skipif(not (shutil.which("gcc") and shutil.which("capsh") and os.geteuid() == 0
                         and Path("/usr/include/sys/capability.h").exists()), reason="needs root, gcc, capsh and libcap-dev")
def test_shim_drops_what_the_container_cant_have(tmp_path):
    (tmp_path / "t.c").write_text(TEST)
    subprocess.run(["gcc", "-o", tmp_path / "t", tmp_path / "t.c", "-lcap"], check=True)
    subprocess.run(["gcc", "-shared", "-fPIC", "-o", tmp_path / "shim.so", SRC, "-ldl"], check=True)
    sandboxed = ["capsh", "--drop=cap_sys_admin", "--"]
    plain = subprocess.run(sandboxed + ["-c", str(tmp_path / "t")], capture_output=True, text=True)
    assert plain.returncode == 1                                     # FRR's "initial cap_set_proc failed"
    shim = subprocess.run(sandboxed + ["-c", f"LD_PRELOAD={tmp_path / 'shim.so'} {tmp_path / 't'}"], capture_output=True, text=True)
    assert shim.returncode == 0 and "cap_net_admin,cap_net_raw=ep" in shim.stdout
    full = subprocess.run([tmp_path / "t"], env={**os.environ, "LD_PRELOAD": str(tmp_path / "shim.so")}, capture_output=True, text=True)
    assert "cap_sys_admin" in full.stdout                            # nothing taken away where it's allowed


def test_sandbox_preloads_shim_into_frr(tmp_path, monkeypatch):
    from app import sandbox, labs
    shim = tmp_path / "capshim.so"; shim.write_bytes(b"\x7fELF fake")
    monkeypatch.setattr(sandbox, "SHIM", shim)
    monkeypatch.setattr(labs, "workdir", lambda lab: tmp_path / "lab")
    lab = {"name": "t-shim", "sandbox_subnet": "172.30.0.0/26", "nodes": [], "links": []}
    r, _ = sandbox.harden(lab, "r1", {"kind": "linux", "image": "quay.io/frrouting/frr:10.0.0", "binds": ["configs/r1:/etc/frr"]}, "admin")
    assert r["env"]["LD_PRELOAD"] == sandbox.SHIM_PATH and f"{sandbox.SHIM_BIND}:{sandbox.SHIM_PATH}:ro" in r["binds"]
    assert (tmp_path / "lab" / sandbox.SHIM_BIND).read_bytes() == b"\x7fELF fake"
    assert "SYS_ADMIN" not in r["cap-add"] and not r["privileged"]
    pc, _ = sandbox.harden(lab, "pc1", {"kind": "linux", "image": "ghcr.io/hellt/network-multitool"}, "admin")
    assert "LD_PRELOAD" not in pc["env"] and not pc.get("binds")
    monkeypatch.setattr(sandbox, "SHIM", tmp_path / "missing.so")       # an image without the shim: unchanged
    r2, _ = sandbox.harden(lab, "r2", {"kind": "linux", "image": "quay.io/frrouting/frr:10.0.0"}, "admin")
    assert "LD_PRELOAD" not in r2["env"]
