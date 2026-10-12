"""Deterministic structural similarity between two source files (no execution, parser-only)."""
from __future__ import annotations

import ast
import hashlib


def normalize(text: str) -> str:
    return "\n".join(l.rstrip() for l in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip() + "\n"


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8", "replace")).hexdigest()


def _h(*parts: str) -> int:
    return hash(parts) & 0xFFFFFFFF if False else int.from_bytes(hashlib.blake2b("\x1f".join(parts).encode(), digest_size=6).digest(), "big")


def _py_features(text: str) -> set[int] | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    seq: list[str] = []
    toks: set[int] = set()
    stack = [tree]
    while stack:
        n = stack.pop()
        t = type(n).__name__
        seq.append(t)
        if isinstance(n, ast.Name):
            toks.add(_h("id", n.id))
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            toks.add(_h("def", n.name))
        elif isinstance(n, ast.Attribute):
            toks.add(_h("attr", n.attr))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            toks.add(_h("str", n.value[:80]))
        stack.extend(reversed(list(ast.iter_child_nodes(n))))
    grams = {_h(*seq[i:i + 3]) for i in range(len(seq) - 2)}
    return grams | toks


def _js_features(rel: str, text: str) -> set[int] | None:
    from .extractors.jsparse import parse
    tree, data = parse(rel, text)
    seq: list[str] = []
    toks: set[int] = set()
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        seq.append(n.type)
        if n.type in ("identifier", "property_identifier", "type_identifier"):
            toks.add(_h("id", data[n.start_byte:n.end_byte].decode("utf-8", "replace")))
        elif n.type == "string_fragment":
            toks.add(_h("str", data[n.start_byte:n.end_byte].decode("utf-8", "replace")[:80]))
        stack.extend(reversed(n.children))
    return {_h(*seq[i:i + 3]) for i in range(len(seq) - 2)} | toks


def features(rel: str, lang: str, text: str) -> set[int] | None:
    return _py_features(text) if lang == "python" else _js_features(rel, text)


def jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
