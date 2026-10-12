"""Extractor plugin interface and registry."""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol

from ..config import Config
from ..graph import Graph
from ..safety import SourceFile


@dataclass
class Context:
    root: Path
    files: list[SourceFile]
    config: Config
    graph: Graph
    target: dict[str, Any] = field(default_factory=dict)  # kind/commit/digest, etc.
    git: Any = None  # GitHistory | None
    shared: dict[str, Any] = field(default_factory=dict)  # cross-extractor products (modules, events, ...)
    parse_errors: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    kind: str = "source"  # source | generated

    _text: dict[str, str] = field(default_factory=dict)

    def file_node(self, rel: str) -> str:
        return f"file:{rel}"

    def read(self, f: SourceFile) -> str:
        t = self._text.get(f.rel)
        if t is None:
            t = f.abs.read_text(encoding="utf-8", errors="replace")
            self._text[f.rel] = t
        return t


class Extractor(Protocol):
    name: ClassVar[str]
    requires: ClassVar[tuple[str, ...]]

    def applies(self, ctx: Context) -> bool: ...
    def extract(self, ctx: Context) -> None: ...


_REGISTRY: dict[str, type] = {}


def register(cls):
    _REGISTRY[cls.name] = cls
    return cls


def discover() -> dict[str, type]:
    """Import every module in this package (built-ins) and any `archimeter.extractors` entry points."""
    from . import __path__ as pkg_path
    for m in pkgutil.iter_modules(pkg_path):
        if m.name != "base":
            importlib.import_module(f"{__package__}.{m.name}")
    from importlib.metadata import entry_points
    for ep in entry_points(group="archimeter.extractors"):
        ep.load()
    return dict(_REGISTRY)


def ordered(classes: dict[str, type]) -> list[type]:
    done: list[str] = []
    pending = dict(classes)
    while pending:
        progressed = False
        for name, cls in sorted(pending.items()):
            if all(r in done or r not in classes for r in cls.requires):
                done.append(name)
                del pending[name]
                progressed = True
                break
        if not progressed:
            raise RuntimeError(f"extractor dependency cycle among {sorted(pending)}")
    return [classes[n] for n in done]


def run_all(ctx: Context) -> list[str]:
    ran = []
    for cls in ordered(discover()):
        ex = cls()
        if ex.applies(ctx):
            ex.extract(ctx)
            ran.append(cls.name)
    return ran
