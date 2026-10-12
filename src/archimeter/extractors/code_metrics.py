"""Per-function and per-file code metrics: cyclomatic (radon/lizard), cognitive (complexipy), MI, logical LOC, abstractness."""
from __future__ import annotations

import ast
import os
from concurrent.futures import ProcessPoolExecutor

from .base import Context, register


def _py_abstract_counts(tree: ast.AST) -> tuple[int, int]:
    classes = abstract = 0
    for n in ast.walk(tree):
        if not isinstance(n, ast.ClassDef):
            continue
        classes += 1
        bases = {getattr(b, "id", getattr(b, "attr", "")) for b in n.bases}
        for b in n.bases:  # Protocol[T], Generic[T]
            if isinstance(b, ast.Subscript):
                bases.add(getattr(b.value, "id", getattr(b.value, "attr", "")))
        meta = {getattr(k.value, "id", getattr(k.value, "attr", "")) for k in n.keywords if k.arg == "metaclass"}
        has_abs = any(
            isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(getattr(d, "id", getattr(d, "attr", "")) in ("abstractmethod", "abstractproperty") for d in m.decorator_list)
            for m in n.body
        )
        if bases & {"ABC", "Protocol"} or "ABCMeta" in meta or has_abs:
            abstract += 1
    return classes, abstract


class _Budget(BaseException):
    pass


def _with_budget(seconds: float, fn):
    """Run fn() with a wall-clock budget (pure-Python work only). Returns (value, timed_out)."""
    import signal
    import threading
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        return fn(), False

    def _raise(signum, frame):
        raise _Budget()

    old = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn(), False
    except _Budget:
        return None, True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


MI_BUDGET_SECONDS = 2.0


def _py_logical_loc(tree: ast.AST) -> int:
    """Logical lines of code: AST statements, excluding docstrings (blank lines and comments never count).

    Used instead of radon.raw, which is superlinear on files with huge literal expressions.
    """
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            n += 1
    return n


def _analyze_python(rel: str, code: str) -> dict:
    from radon.complexity import cc_visit
    from radon.metrics import mi_visit

    out: dict = {"rel": rel, "functions": [], "lloc": 0, "mi": None, "classes": 0, "abstract": 0}
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return out | {"error": "unparseable"}
    out["classes"], out["abstract"] = _py_abstract_counts(tree)
    try:
        out["lloc"] = _py_logical_loc(tree)
        out["mi"], timed_out = _with_budget(MI_BUDGET_SECONDS, lambda: mi_visit(code, multi=True))
        if timed_out:
            out["mi_skipped"] = f"maintainability index exceeded {MI_BUDGET_SECONDS:.0f}s budget (data-like or deeply nested file)"
    except Exception as e:  # radon failure must not hide the file
        out["error"] = f"radon: {type(e).__name__}"
    cog: dict[tuple[str, int], int] = {}
    try:
        from complexipy import code_complexity
        for fc in code_complexity(code).functions:
            cog[(fc.name.split("::")[-1], fc.line_start)] = fc.complexity
    except Exception as e:
        out["cognitive_error"] = type(e).__name__

    def visit(block, prefix=""):
        from radon.visitors import Class
        if isinstance(block, Class):
            for m in block.methods:
                visit(m, prefix + block.name + ".")
            return
        qn = prefix + block.name
        out["functions"].append({
            "name": qn, "line": block.lineno, "cyclomatic": block.complexity,
            "cognitive": cog.get((block.name, block.lineno)),
            "lloc": max(1, (block.endline or block.lineno) - block.lineno + 1),
        })
        for c in getattr(block, "closures", []):
            visit(c, qn + ".")

    for b in cc_visit(code):
        visit(b)
    return out


def _analyze_lizard(rel: str, code: str) -> dict:
    import lizard
    a = lizard.analyze_file.analyze_source_code(rel, code)
    out = {"rel": rel, "lloc": a.nloc, "mi": None, "classes": 0, "abstract": 0,
           "functions": [{"name": f.name, "line": f.start_line, "cyclomatic": f.cyclomatic_complexity,
                          "cognitive": None, "lloc": f.nloc} for f in a.function_list]}
    if rel.rsplit(".", 1)[-1] in ("ts", "tsx", "mts", "cts", "js", "jsx", "mjs", "cjs"):
        from .jsparse import parse, walk
        tree, _ = parse(rel, code)
        for n in walk(tree.root_node):
            if n.type in ("class_declaration", "class"):
                out["classes"] += 1
            elif n.type == "abstract_class_declaration":
                out["classes"] += 1; out["abstract"] += 1
            elif n.type == "interface_declaration":
                out["classes"] += 1; out["abstract"] += 1
    return out


def analyze_one(args: tuple[str, str, str]) -> dict:
    rel, lang, code = args
    try:
        return _analyze_python(rel, code) if lang == "python" else _analyze_lizard(rel, code)
    except Exception as e:
        return {"rel": rel, "functions": [], "lloc": 0, "mi": None, "classes": 0, "abstract": 0,
                "error": f"{type(e).__name__}: {e}"}


@register
class CodeMetrics:
    name = "code-metrics"
    requires = ("modules",)

    def applies(self, ctx: Context) -> bool:
        return bool(ctx.files)

    def extract(self, ctx: Context) -> None:
        jobs = [(f.rel, f.lang, ctx.read(f)) for f in ctx.files]
        if (os.cpu_count() or 1) > 1:
            # Always analyse in worker processes so the parent never initialises native threads before forking.
            import multiprocessing as mp
            import sys
            os.environ.setdefault("RAYON_NUM_THREADS", "1")  # one native thread per worker; avoids oversubscription
            from concurrent.futures.process import BrokenProcessPool
            method = "fork" if sys.platform.startswith("linux") else "spawn"
            try:
                with ProcessPoolExecutor(max_workers=min(os.cpu_count() or 2, 8), mp_context=mp.get_context(method)) as pool:
                    results = list(pool.map(analyze_one, jobs, chunksize=16))
            except BrokenProcessPool as e:
                from ..errors import ArchimeterError
                raise ArchimeterError("worker process pool failed; when calling archimeter from a script on this platform, "
                                      "guard the entry point with `if __name__ == '__main__':`") from e
        else:
            results = [analyze_one(j) for j in jobs]
        g = ctx.graph
        for r in results:
            fid = f"file:{r['rel']}"
            g.add_node(fid, "file", lloc=r["lloc"], mi=r["mi"], classes=r["classes"], abstract_classes=r["abstract"])
            if r.get("mi_skipped"):
                ctx.notes.append(f"{r['rel']}: {r['mi_skipped']}")
            if r.get("error"):
                ctx.parse_errors.append({"path": r["rel"], "error": r["error"]})
            for fn in r["functions"]:
                g.add_node(f"function:{r['rel']}::{fn['name']}@{fn['line']}", "function", fid,
                           name=fn["name"], line=fn["line"], cyclomatic=fn["cyclomatic"],
                           cognitive=fn["cognitive"], lloc=fn["lloc"])
