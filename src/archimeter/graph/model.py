"""Hierarchical typed weighted graph: function -> file -> module -> package -> service."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

LEVELS = ["function", "file", "module", "package", "service"]
KINDS = ["import", "event", "change", "service"]
_RANK = {l: i for i, l in enumerate(LEVELS)}


@dataclass
class Node:
    id: str
    level: str
    parent: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] | None = None


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    weight: float = 1.0
    evidence: list[str] = field(default_factory=list)


def nid(level: str, name: str) -> str:
    return f"{level}:{name}"


class Graph:
    MAX_EVIDENCE = 5

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, str], Edge] = {}

    def add_node(self, id: str, level: str, parent: str | None = None, **attrs: Any) -> Node:
        n = self.nodes.get(id)
        if n is None:
            n = Node(id, level, parent, dict(attrs))
            self.nodes[id] = n
        else:
            if parent is not None:
                n.parent = parent
            n.attrs.update(attrs)
        return n

    def add_edge(self, src: str, dst: str, kind: str, weight: float = 1.0, evidence: str | None = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown edge kind {kind}")
        if src not in self.nodes or dst not in self.nodes:
            raise KeyError(f"edge endpoints must exist: {src} -> {dst}")
        key = (src, dst, kind)
        e = self.edges.get(key)
        if e is None:
            e = self.edges[key] = Edge(src, dst, kind, 0.0)
        e.weight += weight
        if evidence and len(e.evidence) < self.MAX_EVIDENCE and evidence not in e.evidence:
            e.evidence.append(evidence)

    def ancestor(self, id: str, level: str) -> str | None:
        """The ancestor of `id` at `level` (id itself if already there); None if the chain skips it."""
        n = self.nodes[id]
        target = _RANK[level]
        while n is not None:
            if n.level == level:
                return n.id
            if _RANK[n.level] > target:
                return None
            n = self.nodes.get(n.parent) if n.parent else None
        return None

    def nodes_at(self, level: str) -> list[str]:
        return sorted(i for i, n in self.nodes.items() if n.level == level)

    def children(self, id: str) -> list[str]:
        return [i for i, n in self.nodes.items() if n.parent == id]

    def rollup(self, level: str, kinds: Iterable[str] | None = None) -> dict[tuple[str, str, str], float]:
        """Aggregate edges to `level`. Self edges are dropped; edges whose endpoint has no ancestor there are dropped."""
        ks = set(kinds) if kinds else None
        out: dict[tuple[str, str, str], float] = defaultdict(float)
        cache: dict[str, str | None] = {}
        for (s, d, k), e in self.edges.items():
            if ks and k not in ks:
                continue
            a = cache.get(s) if s in cache else cache.setdefault(s, self.ancestor(s, level))
            b = cache.get(d) if d in cache else cache.setdefault(d, self.ancestor(d, level))
            if a is None or b is None or a == b:
                continue
            out[(a, b, k)] += e.weight
        return dict(out)
