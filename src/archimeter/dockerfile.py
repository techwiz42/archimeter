"""Minimal static Dockerfile parser (never builds)."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field


@dataclass
class Stage:
    base: str
    name: str | None = None
    copies: list[tuple[str, str, str | None]] = field(default_factory=list)  # (src, dest, from_stage)
    workdir: str = "/"
    env_names: list[str] = field(default_factory=list)
    expose: list[str] = field(default_factory=list)
    entrypoint: list[str] | None = None
    cmd: list[str] | None = None
    user: str | None = None


def _logical_lines(text: str) -> list[str]:
    out, cur = [], ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not cur and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.lstrip().startswith("#"):
            continue
        if line.endswith("\\"):
            cur += line[:-1] + " "
            continue
        out.append((cur + line).strip())
        cur = ""
    if cur:
        out.append(cur.strip())
    return out


def _exec_form(arg: str) -> list[str]:
    arg = arg.strip()
    if arg.startswith("["):
        import json
        try:
            v = json.loads(arg)
            if isinstance(v, list):
                return [str(x) for x in v]
        except json.JSONDecodeError:
            pass
    try:
        return shlex.split(arg)
    except ValueError:
        return arg.split()


def parse_dockerfile(text: str) -> list[Stage]:
    stages: list[Stage] = []
    for line in _logical_lines(text):
        m = re.match(r"(\w+)\s*(.*)$", line)
        if not m:
            continue
        ins, arg = m.group(1).upper(), m.group(2)
        if ins == "FROM":
            parts = arg.split()
            parts = [p for p in parts if not p.startswith("--")]
            name = parts[2] if len(parts) >= 3 and parts[1].lower() == "as" else None
            stages.append(Stage(base=parts[0] if parts else "", name=name))
        elif not stages:
            continue
        else:
            st = stages[-1]
            if ins in ("COPY", "ADD"):
                flags = dict(f[2:].split("=", 1) for f in arg.split() if f.startswith("--") and "=" in f)
                rest = [t for t in _exec_form(arg) if not t.startswith("--")]
                if len(rest) >= 2:
                    for src in rest[:-1]:
                        st.copies.append((src, rest[-1], flags.get("from")))
            elif ins == "WORKDIR":
                st.workdir = arg.strip() if arg.strip().startswith("/") else st.workdir.rstrip("/") + "/" + arg.strip()
            elif ins in ("ENV", "ARG"):
                for tok in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=|^([A-Za-z_][A-Za-z0-9_]*)\s", arg + " "):
                    n = tok[0] or tok[1]
                    if n and n not in st.env_names:
                        st.env_names.append(n)
            elif ins == "EXPOSE":
                st.expose += [p.split("/")[0] for p in arg.split()]
            elif ins == "ENTRYPOINT":
                st.entrypoint = _exec_form(arg)
            elif ins == "CMD":
                st.cmd = _exec_form(arg)
            elif ins == "USER":
                st.user = arg.strip()
    return stages
