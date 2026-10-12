"""target -> extractors -> graph -> metrics -> result dict."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, __version__
from .config import Config
from .extractors import Context, run_all
from .graph import Graph
from .metrics import compute_metrics
from .safety import walk_sources


def build_context(root: Path, cfg: Config, *, target: dict[str, Any], kind: str = "source") -> Context:
    walk = walk_sources(
        root, max_bytes=cfg.analysis.max_file_bytes, skip_vendored=cfg.analysis.skip_vendored,
        extra_deny=cfg.security.secrets_denylist, exclude=cfg.modules.exclude,
        skip_generated=cfg.analysis.skip_generated and kind != "generated",
    )
    ctx = Context(root=root, files=walk.files, config=cfg, graph=Graph(), target=target, kind=kind)
    ctx.shared["skipped"] = walk.skipped
    return ctx


def analyze_context(ctx: Context) -> dict[str, Any]:
    ran = run_all(ctx)
    m = compute_metrics(ctx.graph, threshold=ctx.config.analysis.complexity_threshold)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "archimeter", "version": __version__},
        "target": ctx.target,
        "config": ctx.config.to_dict(),
        "extractors": ran,
        "metrics": m,
        "skipped": ctx.shared.get("skipped", []),
        "parse_errors": ctx.parse_errors,
        "notes": ctx.notes,
    }
    for key in ("events", "hotspots", "hidden_coupling", "change_coupling", "ownership", "provenance",
                "service_graph", "findings", "churn", "images", "dependency_inventory", "uncertainty"):
        if key in ctx.shared:
            result[key] = ctx.shared[key]
    ext = {k: ctx.shared[k] for k in ("external_python", "external_js") if k in ctx.shared}
    if ext:
        result["external_dependencies"] = ext
    return result
