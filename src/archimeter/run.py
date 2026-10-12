"""Command implementations."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from .config import find_config, load_config
from .errors import ArchimeterError
from .pipeline import analyze_context, build_context


def analyze_target(target: str, config: Path | None, *, framework: str | None = None, submodules: bool = False) -> dict[str, Any]:
    from .resolve import resolve_target
    with contextlib.ExitStack() as stack:
        rt = stack.enter_context(resolve_target(target, submodules=submodules))
        cfg = load_config(config or (find_config(rt.root) if rt.kind != "image" else None))
        if framework:
            fw = stack.enter_context(resolve_target(framework, submodules=submodules))
            cfg.provenance.framework = str(fw.analysis_root)
        ctx = build_context(rt.analysis_root, cfg, target=dict(rt.identity), kind=rt.mode)
        if rt.mode == "source":
            from .gitlog import GitHistory
            if GitHistory.available(rt.analysis_root):
                ctx.git = GitHistory(rt.analysis_root, include_author_names=cfg.git.include_author_names)
        rt.prepare(ctx)
        if cfg.provenance.framework:
            fw_id = locals().get("fw")
            ctx.target["framework"] = fw_id.identity if fw_id else {"path": Path(cfg.provenance.framework).name}
        return analyze_context(ctx)


def run_analyze(target: str, config: Path | None, out: Path, *, framework: str | None = None, submodules: bool = False) -> dict:
    result = analyze_target(target, config, framework=framework, submodules=submodules)
    out.mkdir(parents=True, exist_ok=True)
    from .report.write import write_outputs
    write_outputs(result, out)
    _summarize(result, out)
    return result


def _summarize(r: dict, out: Path) -> None:
    import typer
    m = r["metrics"]
    t = m["totals"]
    mod = m["levels"]["module"]
    typer.echo(f"{r['target'].get('name', 'target')}: {t['files']} files, {t['loc']} LOC, {t['functions']} functions, {len(mod['nodes'])} modules")
    pcs = ", ".join(f"{k} {v:.1%}" for k, v in mod["propagation_cost"].items() if v is not None)
    typer.echo(f"propagation cost (modules): {pcs or 'n/a (fewer than 2 modules)'}")
    hc = r.get("hidden_coupling")
    if hc is None:
        hc = ((r.get("provenance") or {}).get("framework_change_metrics") or {}).get("hidden_coupling")
    typer.echo(f"cycles: {len(mod['cycles'].get('combined', []))}   hidden couplings: {len(hc or [])}"
               f"   events: {r.get('events', {}).get('summary', {}).get('events', 0)}")
    typer.echo(f"wrote {out / 'metrics.json'}" + (f" and {out / 'report.html'}" if (out / 'report.html').exists() else ""))


def run_compare(targets: list[str], config: Path | None, out: Path, *, framework: str | None = None) -> None:
    if len(targets) < 2:
        raise ArchimeterError("compare needs two or more targets")
    from .compare import compare_results
    from .report.write import write_compare
    results = [analyze_target(t, config, framework=framework) for t in targets]
    comp = compare_results(results)
    out.mkdir(parents=True, exist_ok=True)
    write_compare(comp, results, out)
    import typer
    typer.echo(f"most complex: {comp['most_complex']}   least complex: {comp['least_complex']}")
    typer.echo(f"wrote {out / 'compare.json'} and {out / 'compare.html'}")


def run_trend(target: str, since: str, step: str, config: Path | None, out: Path) -> None:
    from .trend import run as trend_run
    trend_run(target, since, step, config, out)


def run_init(target: str, force: bool) -> None:
    from .init import write_starter
    write_starter(target, force)
