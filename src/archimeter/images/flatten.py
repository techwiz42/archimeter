"""Flatten an image into a temp tree and separate application code from base-image and third-party code."""
from __future__ import annotations

import json
import re
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..safety import LANG_BY_EXT
from .sources import ImageInfo
from .unpack import Layout, apply_layer


@dataclass
class Flat:
    root: Path
    app_root: Path  # the directory to analyze (a subdirectory of root, or root itself)
    summary: dict[str, Any]
    separation: dict[str, Any]
    inventory: list[dict[str, Any]]
    layout: Layout


def _has_sources(p: Path) -> int:
    n = 0
    for f in p.rglob("*"):
        if f.is_file() and f.suffix.lower() in LANG_BY_EXT and not f.is_symlink():
            n += 1
    return n


def _inventory(root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for md in list(root.rglob("*.dist-info/METADATA")) + list(root.rglob("*.egg-info/PKG-INFO")):
        name = version = None
        try:
            for line in md.read_text(encoding="utf-8", errors="replace").splitlines()[:40]:
                if line.startswith("Name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                if name and version:
                    break
        except OSError:
            continue
        if name:
            out.append({"ecosystem": "pypi", "name": name, "version": version, "evidence": md.parent.name})
    for pj in root.rglob("node_modules/*/package.json"):
        out += _npm(pj)
    for pj in root.rglob("node_modules/@*/*/package.json"):
        out += _npm(pj)
    for req in root.rglob("requirements*.txt"):
        try:
            for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:==\s*([\w.\-+]+))?", line)
                if m and not line.lstrip().startswith(("#", "-")):
                    out.append({"ecosystem": "pypi", "name": m.group(1), "version": m.group(2), "evidence": req.name})
        except OSError:
            continue
    seen, uniq = set(), []
    for d in out:
        k = (d["ecosystem"], d["name"].lower(), d["version"])
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    return sorted(uniq, key=lambda d: (d["ecosystem"], d["name"].lower()))


def _npm(pj: Path) -> list[dict[str, Any]]:
    try:
        d = json.loads(pj.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [{"ecosystem": "npm", "name": d["name"], "version": d.get("version"), "evidence": "package.json"}] if d.get("name") else []


def _entry_tokens(rt: dict) -> list[str]:
    toks: list[str] = []
    for key in ("Entrypoint", "Cmd"):
        v = rt.get(key) or []
        if isinstance(v, str):
            v = v.split()
        for part in v:
            toks += str(part).split()
    return toks


def flatten(source, info: ImageInfo, dest: Path, cfg: Config) -> Flat:
    dest.mkdir(parents=True, exist_ok=True)
    layout = Layout()
    for stream in source.layers(info):
        try:
            apply_layer(stream, dest, layout, max_total=cfg.analysis.max_total_extract_bytes,
                        max_file=cfg.analysis.max_file_bytes * 50, extra_deny=cfg.security.secrets_denylist)
        finally:
            stream.close()
    summary = info.summary()
    rt = info.runtime
    reasons: list[str] = []
    roots: list[str] = []
    strategy = "heuristic"
    uncertain = False

    configured = [p.strip("/") for p in cfg.image.app_paths]
    if configured:
        strategy = "config"
        roots = [p for p in configured if (dest / p).is_dir()]
        for p in configured:
            if p not in roots:
                reasons.append(f"configured app path '/{p}' does not exist in the image")
    else:
        wd = (rt.get("WorkingDir") or "/").strip("/")
        if wd and (dest / wd).is_dir() and _has_sources(dest / wd):
            strategy = "workdir"
            roots = [wd]
        else:
            toks = _entry_tokens(rt)
            cand: list[str] = []
            for t in toks:
                if t.startswith("/") and (dest / t.strip("/")).exists():
                    cand.append(str(Path(t.strip("/")).parent) if (dest / t.strip("/")).is_file() else t.strip("/"))
                elif re.fullmatch(r"[\w.]+:[\w]+", t):  # uvicorn/gunicorn module:attr
                    top = t.split(":")[0].split(".")[0]
                    for base in ([wd] if wd else []) + [""]:
                        p = f"{base}/{top}".strip("/")
                        if (dest / p).is_dir():
                            cand.append(p)
                            break
            cand = [c for c in dict.fromkeys(cand) if c not in ("", ".") and _has_sources(dest / c)]
            if cand:
                strategy = "entrypoint"
                roots = cand
    if not roots:
        tops = sorted(p.name for p in dest.iterdir() if p.is_dir() and _has_sources(p))
        # prefer "app-like" locations inside common containers
        picked = []
        for t in tops:
            if t in ("opt", "srv", "home", "workspace", "code", "app", "usr"):
                for sub in sorted((dest / t).iterdir()):
                    if sub.is_dir() and _has_sources(sub):
                        picked.append(f"{t}/{sub.name}")
            else:
                picked.append(t)
        roots = picked
        uncertain = True
        reasons.append("neither WorkingDir, entrypoint nor configuration identified the application code; "
                       "all non-OS directories containing source files were treated as application code")
    # candidate trees outside the chosen roots are excluded from analysis but reported
    excluded: list[dict[str, Any]] = []
    for top in sorted(p for p in dest.iterdir() if p.is_dir()):
        n = _has_sources(top)
        if not n:
            continue
        inside = any(r == top.name or r.startswith(top.name + "/") or top.name.startswith(r + "/") for r in roots)
        if not inside:
            excluded.append({"path": "/" + top.name, "source_files": n})
            shutil.rmtree(top, ignore_errors=True)
    if excluded and strategy != "heuristic":
        uncertain = True
        reasons.append("source files exist outside the identified application root(s); they were excluded (see excluded_candidates)")
    if len(roots) == 1:
        app_root = dest / roots[0]
    else:
        app_root = dest
        for top in sorted(p for p in dest.iterdir() if p.is_dir()):
            if not any(r == top.name or r.startswith(top.name + "/") for r in roots) and not top.name.startswith("."):
                shutil.rmtree(top, ignore_errors=True)
    sep = {
        "strategy": strategy, "app_roots": ["/" + r for r in roots], "uncertain": uncertain, "reasons": reasons,
        "excluded_candidates": excluded,
        "not_analyzed": {"os_files": layout.skipped_os_files, "third_party_files": layout.skipped_third_party_files,
                         "secret_files_never_read": layout.skipped_secret_files, "oversized_files": layout.skipped_large_files,
                         "special_files": layout.skipped_special},
    }
    return Flat(dest, app_root, summary, sep, _inventory(dest), layout)
