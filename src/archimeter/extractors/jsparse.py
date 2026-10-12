"""tree-sitter parser access for JS/TS (shared by import and event extractors)."""
from __future__ import annotations

from functools import lru_cache

import tree_sitter_javascript as _js
import tree_sitter_typescript as _ts
from tree_sitter import Language, Parser


@lru_cache(maxsize=4)
def _parser(kind: str) -> Parser:
    lang = {"js": _js.language, "ts": _ts.language_typescript, "tsx": _ts.language_tsx}[kind]()
    return Parser(Language(lang))


def parse(rel: str, source: str):
    ext = rel.rsplit(".", 1)[-1].lower()
    kind = "tsx" if ext == "tsx" else "ts" if ext in ("ts", "mts", "cts") else "js"
    data = source.encode("utf-8", errors="replace")
    return _parser(kind).parse(data), data


def string_value(node, data: bytes) -> str | None:
    """Literal string value of a `string` node (no template substitutions), else None."""
    if node is None:
        return None
    if node.type == "string":
        return b"".join(data[c.start_byte:c.end_byte] for c in node.children if c.type == "string_fragment").decode("utf-8", "replace") \
            if any(c.type == "string_fragment" for c in node.children) else ""
    if node.type == "template_string" and not any(c.type == "template_substitution" for c in node.children):
        return data[node.start_byte + 1:node.end_byte - 1].decode("utf-8", "replace")
    return None


def walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))
