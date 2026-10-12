"""Python import edges via stdlib `ast`. Never imports or executes target code."""
from __future__ import annotations

import ast
import sys
from collections import defaultdict

from .base import Context, register

STDLIB = set(sys.stdlib_module_names)


def _module_name(rel: str) -> str:
    p = rel[:-3]
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


@register
class PythonImports:
    name = "python-imports"
    requires = ("modules",)

    def applies(self, ctx: Context) -> bool:
        return any(f.lang == "python" for f in ctx.files)

    def extract(self, ctx: Context) -> None:
        pyfiles = [f for f in ctx.files if f.lang == "python"]
        by_dotted: dict[str, str] = {}
        suffix_index: dict[str, set[str]] = defaultdict(set)
        for f in pyfiles:
            dotted = _module_name(f.rel)
            by_dotted[dotted] = f.rel
            parts = dotted.split(".")
            for i in range(1, len(parts)):
                suffix_index[".".join(parts[i:])].add(f.rel)
        roots = self._roots(ctx, pyfiles)
        g = ctx.graph
        external: dict[str, int] = defaultdict(int)
        ambiguous = 0

        prefixes: set[str] = ctx.shared.get("prefix_roots") or set()
        cur: dict[str, object] = {"roots": roots, "own": None}

        def resolve(dotted: str) -> str | None:
            nonlocal ambiguous
            for r in cur["roots"]:  # type: ignore[union-attr]
                key = f"{r}.{dotted}" if r else dotted
                if key in by_dotted:
                    return by_dotted[key]
            hits = suffix_index.get(dotted)
            if hits and cur["own"]:  # image-backed services never import each other's files
                hits = {h for h in hits if h.startswith(cur["own"] + "/")}  # type: ignore[operator]
            if hits:
                if len(hits) == 1:
                    return next(iter(hits))
                ambiguous += 1
            return None

        def resolve_longest(dotted: str) -> str | None:
            parts = dotted.split(".")
            for n in range(len(parts), 0, -1):
                r = resolve(".".join(parts[:n]))
                if r:
                    return r
            return None

        for f in pyfiles:
            own = f.rel.split("/")[0] if f.rel.split("/")[0] in prefixes else None
            cur["own"] = own
            cur["roots"] = ([own] + [r for r in roots if r != own]) if own else roots
            try:
                tree = ast.parse(ctx.read(f), filename=f.rel)
            except (SyntaxError, ValueError, RecursionError) as e:
                ctx.parse_errors.append({"path": f.rel, "error": f"{type(e).__name__}: {e}"})
                continue
            src = f"file:{f.rel}"
            pkg_parts = _module_name(f.rel).split(".")
            if not f.rel.endswith("__init__.py"):
                pkg_parts = pkg_parts[:-1]
            for node in ast.walk(tree):
                targets: list[str | None] = []
                if isinstance(node, ast.Import):
                    for a in node.names:
                        t = resolve_longest(a.name)
                        if t is None and a.name.split(".")[0] not in STDLIB:
                            external[a.name.split(".")[0]] += 1
                        targets.append(t)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        base = pkg_parts[: len(pkg_parts) - (node.level - 1)] if node.level > 1 else pkg_parts
                        base_dotted = ".".join(base + ([node.module] if node.module else []))
                        cands = [base_dotted] + [f"{base_dotted}.{a.name}" if base_dotted else a.name for a in node.names]
                        for a, c in zip([None] + list(node.names), cands):
                            t = by_dotted.get(c)
                            if t:
                                targets.append(t)
                    else:
                        modname = node.module or ""
                        t = resolve_longest(modname) if modname else None
                        if t is None and modname.split(".")[0] not in STDLIB:
                            external[modname.split(".")[0]] += 1
                        targets.append(t)
                        for a in node.names:  # `from pkg import submodule`
                            sub = resolve(f"{modname}.{a.name}")
                            if sub:
                                targets.append(sub)
                for t in targets:
                    if t and t != f.rel and f"file:{t}" in g.nodes:
                        g.add_edge(src, f"file:{t}", "import", 1.0, f"{f.rel}:{getattr(node, 'lineno', 0)}")
        ctx.shared["external_python"] = dict(sorted(external.items(), key=lambda kv: -kv[1]))
        ctx.shared["python_ambiguous_imports"] = ambiguous

    @staticmethod
    def _roots(ctx: Context, pyfiles) -> list[str]:
        """Dotted prefixes that act as import roots: '', 'src', and top-level dirs holding packages."""
        roots = [""]
        tops = {f.rel.split("/")[0] for f in pyfiles if "/" in f.rel}
        if "src" in tops:
            roots.append("src")
        for t in sorted(tops - {"src"}):
            if not (ctx.root / t / "__init__.py").exists():
                roots.append(t)
        for r in ctx.config.modules.roots:
            dotted = r.strip("/").replace("/", ".")
            if dotted not in roots:
                roots.append(dotted)
        return roots
