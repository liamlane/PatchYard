"""Lab management: snapshots (with diff + restore), clone, zip export/import.

A snapshot is one JSON file: the lab definition plus every text file under the lab's
configs/ folder (router frr.conf, switch scripts, server DNS/web files). Running router
configs are saved first, so a snapshot captures what the routers are actually running.
"""
import difflib, io, json, re, shutil, time, zipfile
from pathlib import Path
from . import labs
from .labs import lab_dir, workdir, check_name

MAX_FILE = 2 * 1024 * 1024
MAX_ZIP = 50 * 1024 * 1024


def _snapdir(name) -> Path:
    return lab_dir(name) / "snapshots"


def _collect(lab: dict) -> dict:
    base = workdir(lab) / "configs"
    files = {}
    if base.is_dir():
        for f in sorted(base.rglob("*")):
            if f.is_file() and f.stat().st_size <= MAX_FILE and not f.name.endswith(".sav"):
                try:
                    files[str(f.relative_to(workdir(lab)))] = f.read_text()
                except UnicodeDecodeError:
                    pass
    return files


def _lab_view(lab: dict) -> str:
    """Stable, human-diffable text of the lab definition (positions left out)."""
    v = {"nodes": [{k: n[k] for k in ("id", "type", "props") if k in n} for n in lab.get("nodes", [])],
         "links": [{k: l.get(k) for k in ("a", "a_if", "b", "b_if", "media", "impair", "admin")} for l in lab.get("links", [])]}
    return json.dumps(v, indent=1, sort_keys=True)


def snapshot(lab: dict, label: str, auto: bool = False) -> dict:
    sid = time.strftime("%Y%m%d-%H%M%S")
    d = _snapdir(lab["name"]); d.mkdir(parents=True, exist_ok=True)
    while (d / f"{sid}.json").exists():
        sid += "x"
    label = re.sub(r"[^\w .:/()-]", "", label or "")[:60] or ("auto" if auto else "snapshot")
    snap = {"id": sid, "label": label, "ts": time.time(), "auto": auto, "lab": lab, "files": _collect(lab)}
    (d / f"{sid}.json").write_text(json.dumps(snap))
    return _meta(snap)


def _meta(s):
    return {"id": s["id"], "label": s["label"], "ts": s["ts"], "auto": s.get("auto", False),
            "files": len(s["files"]), "nodes": len(s["lab"].get("nodes", [])), "links": len(s["lab"].get("links", []))}


def _load(name, sid) -> dict:
    if not re.fullmatch(r"[0-9x-]{15,24}", sid):
        raise ValueError("bad snapshot id")
    f = _snapdir(name) / f"{sid}.json"
    if not f.exists():
        raise FileNotFoundError(f"snapshot {sid} not found")
    return json.loads(f.read_text())


def list_snapshots(name):
    d = _snapdir(name)
    out = []
    for f in sorted(d.glob("*.json"), reverse=True) if d.is_dir() else []:
        try:
            out.append(_meta(json.loads(f.read_text())))
        except (ValueError, KeyError):
            pass
    return out


def delete_snapshot(name, sid):
    _load(name, sid)
    (_snapdir(name) / f"{sid}.json").unlink()


def diff(lab: dict, sid: str, against: str | None = None) -> dict:
    """Diff snapshot sid against the current lab (or against snapshot `against`)."""
    a = _load(lab["name"], sid)
    if against:
        b = _load(lab["name"], against); b_lab, b_files, b_name = b["lab"], b["files"], b["label"]
    else:
        b_lab, b_files, b_name = lab, _collect(lab), "current"
    out = []
    pairs = [("lab definition", _lab_view(a["lab"]), _lab_view(b_lab))]
    for p in sorted(set(a["files"]) | set(b_files)):
        pairs.append((p, a["files"].get(p), b_files.get(p)))
    for path, x, y in pairs:
        if x == y:
            continue
        status = "added" if x is None else "removed" if y is None else "changed"
        d = "".join(difflib.unified_diff((x or "").splitlines(True), (y or "").splitlines(True),
                                         f"{a['label']}/{path}", f"{b_name}/{path}", n=3))
        out.append({"path": path, "status": status, "diff": d[:200_000]})
    return {"from": _meta(a), "to": b_name, "changes": out}


def restore(lab: dict, sid: str) -> dict:
    s = _load(lab["name"], sid)
    wd = workdir(lab)
    base = (wd / "configs").resolve()
    for rel, txt in s["files"].items():
        p = (wd / rel).resolve()
        if not str(p).startswith(str(base)):
            continue
        labs.write_inplace(p, txt)
    restored = dict(s["lab"])
    for k in ("name", "workdir", "imported_from", "mgmt_network", "mgmt_subnet"):
        if k in lab:
            restored[k] = lab[k]
    labs.save(restored)
    return {"restored": _meta(s), "files": len(s["files"])}


def clone(lab: dict, new: str) -> dict:
    check_name(new)
    if (lab_dir(new) / "lab.json").exists():
        raise FileExistsError(f"lab '{new}' already exists")
    src = workdir(lab) / "configs"
    dst = lab_dir(new) / "configs"
    if src.is_dir():
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.sav"))
    c = json.loads(json.dumps(lab))
    c["name"] = new
    c.pop("workdir", None)
    c["cloned_from"] = lab["name"]
    labs.save(c)
    warn = [n["id"] for n in c["nodes"] if (n.get("raw") or {}).get("kind") == "bridge"]
    return {"lab": c, "warning": f"host bridges {', '.join(warn)} are shared with '{lab['name']}'; don't run both at once" if warn else ""}


def export_zip(lab: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        lj = dict(lab); lj.pop("workdir", None)
        z.writestr("lab.json", json.dumps(lj, indent=2))
        for rel, txt in _collect(lab).items():
            z.writestr(rel, txt)
        z.writestr("README.txt", f"Patchyard lab export: {lab['name']}\nExported {time.strftime('%Y-%m-%d %H:%M')}\n"
                                 "Import it from the Patchyard Lab manager (Import zip).\n")
    return buf.getvalue()


def import_zip(name: str, data: bytes) -> dict:
    check_name(name)
    if len(data) > MAX_ZIP:
        raise ValueError("zip larger than 50 MB")
    if (lab_dir(name) / "lab.json").exists():
        raise FileExistsError(f"lab '{name}' already exists")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("not a zip file")
    if "lab.json" not in z.namelist():
        raise ValueError("zip has no lab.json (export it from Patchyard)")
    lab = json.loads(z.read("lab.json"))
    root = lab_dir(name).resolve()
    total = 0
    for info in z.infolist():
        n = info.filename
        if info.is_dir() or n in ("lab.json", "README.txt"):
            continue
        if not n.startswith("configs/") or ".." in Path(n).parts or n.startswith("/"):
            continue
        total += info.file_size
        if info.file_size > MAX_FILE or total > MAX_ZIP:
            raise ValueError("zip content too large")
        p = (root / n).resolve()
        if not str(p).startswith(str(root)):
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(z.read(info))
    lab["name"] = name
    lab.pop("workdir", None)
    labs.save(lab)
    return lab


def prune_auto(name, keep: int):
    d = _snapdir(name)
    autos = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            if json.loads(f.read_text()).get("auto"):
                autos.append(f)
        except ValueError:
            pass
    for f in autos[:-keep] if keep and len(autos) > keep else []:
        f.unlink()
