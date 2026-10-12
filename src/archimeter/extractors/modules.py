"""Module boundary detection: builds service -> package -> module -> file nodes."""
from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from .base import Context, register

CONTAINER_DIRS = {"modules", "packages", "apps", "services", "plugins", "libs", "components", "features"}
STRIP_PREFIX = {"src", "lib"}
ROOT_MODULE = "(root)"


def assign_module(rel: str, ctx: Context, pkg_dirs: set[str], _stripped: bool = False) -> tuple[str, str]:
    """Return (module_name, package_name) for a file path."""
    cfg = ctx.config.modules
    prefixes = ctx.shared.get("prefix_roots") or set()
    first = rel.split("/")[0]
    if not _stripped and first in prefixes and "/" in rel:
        inner_mod, inner_pkg = assign_module(rel.split("/", 1)[1], ctx, pkg_dirs, _stripped=True)
        return f"{first}/{inner_mod}" if inner_mod != ROOT_MODULE else f"{first}/{ROOT_MODULE}", f"{first}/{inner_pkg}"
    for name, globs in cfg.group.items():
        if any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g.rstrip("/") + "/**") for g in globs):
            return name, "grouped"
    parts = rel.split("/")
    dirs = parts[:-1]
    for r in cfg.roots:
        rp = r.strip("/").split("/")
        if dirs[:len(rp)] == rp and len(dirs) > len(rp):
            return "/".join(rp + [dirs[len(rp)]]), "/".join(rp)
    # nearest workspace package.json below the root
    for i in range(len(dirs), 0, -1):
        d = "/".join(dirs[:i])
        if d in pkg_dirs:
            return d, "/".join(dirs[:i - 1]) or "."
    if not dirs:
        return ROOT_MODULE, "."
    base = list(dirs)
    prefix: list[str] = []
    if base[0] in STRIP_PREFIX and len(base) > 1:
        prefix = [base[0]]
        base = base[1:]
    if base[0] in CONTAINER_DIRS and len(base) > 1:
        mod = "/".join(prefix + base[:2])
        return mod, "/".join(prefix + base[:1])
    mod = "/".join(prefix + base[:1])
    return mod, "/".join(prefix) or "."


@register
class ModuleDetector:
    name = "modules"
    requires: tuple[str, ...] = ()

    def applies(self, ctx: Context) -> bool:
        return True

    def extract(self, ctx: Context) -> None:
        g = ctx.graph
        service = ctx.target.get("service") or ctx.target.get("name") or ctx.root.name
        sid = f"service:{service}"
        g.add_node(sid, "service")
        pkg_dirs = _workspace_package_dirs(ctx)
        module_of: dict[str, str] = {}
        for f in ctx.files:
            mod, pkg = assign_module(f.rel, ctx, pkg_dirs)
            pid = f"package:{pkg}"
            mid = f"module:{mod}"
            g.add_node(pid, "package", sid)
            g.add_node(mid, "module", pid, path=mod)
            g.add_node(f"file:{f.rel}", "file", mid, lang=f.lang, bytes=f.size)
            module_of[f.rel] = mid
        ctx.shared["module_of"] = module_of
        ctx.shared["service_id"] = sid


def _workspace_package_dirs(ctx: Context) -> set[str]:
    """Directories (other than the root) containing a package.json that own at least one analyzed file."""
    seen: set[str] = set()
    out: set[str] = set()
    for f in ctx.files:
        if f.lang not in ("javascript", "typescript"):
            continue
        parts = f.rel.split("/")[:-1]
        for i in range(len(parts), 0, -1):
            d = "/".join(parts[:i])
            if d in seen:
                if d in out:
                    break
                continue
            seen.add(d)
            if (ctx.root / d / "package.json").is_file():
                out.add(d)
                break
    return out
