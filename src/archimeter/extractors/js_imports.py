"""JavaScript / TypeScript import edges via tree-sitter, with tsconfig path aliases and workspace packages."""
from __future__ import annotations

import json
import posixpath
import re
from collections import defaultdict
from pathlib import Path

from .base import Context, register
from .jsparse import parse, string_value, walk

EXTS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"]


def _load_jsonc(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    # strip // and /* */ comments outside strings, and trailing commas
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True; out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class TsConfig:
    def __init__(self, dir_rel: str, base_url: str | None, paths: dict[str, list[str]]):
        self.dir_rel, self.base_url, self.paths = dir_rel, base_url, paths


def _read_tsconfig(root: Path, dir_rel: str, depth: int = 0) -> TsConfig | None:
    for name in ("tsconfig.json", "jsconfig.json"):
        p = root / dir_rel / name if dir_rel else root / name
        if p.is_file():
            data = _load_jsonc(p)
            opts = dict(data.get("compilerOptions") or {})
            ext = data.get("extends")
            if isinstance(ext, str) and ext.startswith(".") and depth < 3:
                ep = posixpath.normpath(posixpath.join(dir_rel, ext))
                ep = ep if ep.endswith(".json") else ep + ".json"
                parent = _load_jsonc(root / ep)
                popts = dict(parent.get("compilerOptions") or {})
                popts.update(opts)
                opts = popts
            return TsConfig(dir_rel, opts.get("baseUrl"), opts.get("paths") or {})
    return None


@register
class JsImports:
    name = "js-imports"
    requires = ("modules",)

    def applies(self, ctx: Context) -> bool:
        return any(f.lang in ("javascript", "typescript") for f in ctx.files)

    def extract(self, ctx: Context) -> None:
        files = [f for f in ctx.files if f.lang in ("javascript", "typescript")]
        known = {f.rel for f in files}
        workspace = self._workspace_packages(ctx, known)
        tscache: dict[str, TsConfig | None] = {}
        external: dict[str, int] = defaultdict(int)
        g = ctx.graph

        def tsconfig_for(rel: str) -> TsConfig | None:
            parts = rel.split("/")[:-1]
            for i in range(len(parts), -1, -1):
                d = "/".join(parts[:i])
                if d not in tscache:
                    tscache[d] = _read_tsconfig(ctx.root, d)
                if tscache[d] is not None:
                    return tscache[d]
            return None

        def try_file(base: str) -> str | None:
            base = posixpath.normpath(base)
            if base in known:
                return base
            stem = re.sub(r"\.(m|c)?jsx?$", "", base)
            for e in EXTS:
                if base + e in known:
                    return base + e
                if stem != base and stem + e in known:
                    return stem + e
            for e in EXTS:
                if f"{base}/index{e}" in known:
                    return f"{base}/index{e}"
            return None

        def resolve(spec: str, from_rel: str) -> str | None:
            if spec.startswith("."):
                return try_file(posixpath.join(posixpath.dirname(from_rel), spec))
            ts = tsconfig_for(from_rel)
            if ts:
                base_dir = posixpath.normpath(posixpath.join(ts.dir_rel, ts.base_url)) if ts.base_url else ts.dir_rel
                for pat, targets in ts.paths.items():
                    if "*" in pat:
                        pre, post = pat.split("*", 1)
                        if spec.startswith(pre) and spec.endswith(post) and len(spec) >= len(pre) + len(post):
                            mid = spec[len(pre):len(spec) - len(post) if post else None]
                            for t in targets:
                                r = try_file(posixpath.join(base_dir, t.replace("*", mid)))
                                if r:
                                    return r
                    elif spec == pat:
                        for t in targets:
                            r = try_file(posixpath.join(base_dir, t))
                            if r:
                                return r
                if ts.base_url is not None:
                    r = try_file(posixpath.join(base_dir, spec))
                    if r:
                        return r
            for pkg, entry in workspace.items():
                if spec == pkg:
                    return entry
                if spec.startswith(pkg + "/"):
                    r = try_file(posixpath.join(workspace_dirs[pkg], spec[len(pkg) + 1:]))
                    if r:
                        return r
            return None

        workspace_dirs = {k: posixpath.dirname(v) if v else "" for k, v in workspace.items()}
        workspace_dirs.update(ctx.shared.get("workspace_dirs", {}))

        for f in files:
            tree, data = parse(f.rel, ctx.read(f))
            if tree.root_node.has_error:
                ctx.parse_errors.append({"path": f.rel, "error": "tree-sitter reported syntax errors (partial parse)"})
            src = f"file:{f.rel}"
            for n in walk(tree.root_node):
                spec = None
                if n.type in ("import_statement", "export_statement"):
                    s = next((c for c in n.children if c.type == "string"), None)
                    spec = string_value(s, data) if s is not None else None
                elif n.type == "call_expression":
                    fn = n.child_by_field_name("function")
                    if fn is not None and (fn.type == "import" or (fn.type == "identifier" and data[fn.start_byte:fn.end_byte] == b"require")):
                        args = n.child_by_field_name("arguments")
                        first = next((c for c in args.children if c.type in ("string", "template_string")), None) if args else None
                        spec = string_value(first, data) if first is not None else None
                if not spec:
                    continue
                t = resolve(spec, f.rel)
                if t and t != f.rel:
                    g.add_edge(src, f"file:{t}", "import", 1.0, f"{f.rel}:{n.start_point[0] + 1}")
                elif not t and not spec.startswith("."):
                    pkg = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
                    external[pkg] += 1
        ctx.shared["external_js"] = dict(sorted(external.items(), key=lambda kv: -kv[1]))

    @staticmethod
    def _workspace_packages(ctx: Context, known: set[str]) -> dict[str, str]:
        """package name -> entry file, for package.json files found in analyzed directories."""
        out: dict[str, str] = {}
        dirs = {posixpath.dirname(r) for r in known}
        for d in sorted(dirs):
            parts = d.split("/") if d else []
            for i in range(len(parts) + 1):
                dd = "/".join(parts[:i])
                pj = ctx.root / dd / "package.json" if dd else ctx.root / "package.json"
                if pj.is_file() and dd not in ctx.shared.setdefault("_pj_seen", set()):
                    ctx.shared["_pj_seen"].add(dd)
                    data = _load_jsonc(pj)
                    name = data.get("name")
                    if isinstance(name, str) and dd:
                        entry = ""
                        for key in ("source", "module", "main", "types"):
                            v = data.get(key)
                            if isinstance(v, str):
                                cand = posixpath.normpath(posixpath.join(dd, v))
                                for e in [""] + EXTS:
                                    if cand + e in known:
                                        entry = cand + e
                                        break
                            if entry:
                                break
                        if not entry:
                            for e in EXTS:
                                for sub in ("src/index", "index"):
                                    if f"{dd}/{sub}{e}" in known:
                                        entry = f"{dd}/{sub}{e}"
                                        break
                                if entry:
                                    break
                        out[name] = entry
                        ctx.shared.setdefault("workspace_dirs", {})[name] = dd
        return out
