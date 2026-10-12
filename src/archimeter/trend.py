"""`archimeter trend`: metrics at historical commits, computed in temporary worktrees of a private clone."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, __version__
from .config import find_config, load_config
from .errors import ArchimeterError
from .gitlog import GitHistory
from .pipeline import analyze_context, build_context

CACHE = Path(os.environ.get("ARCHIMETER_CACHE_DIR", "~/.cache/archimeter")).expanduser()


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    e = env or _plain_env()
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=e, timeout=1800)
    if r.returncode != 0:
        from .safety import Redactor
        raise ArchimeterError(Redactor.redact(f"git {args[0]} failed: {r.stderr.strip()[-400:]}"))
    return r.stdout


def _plain_env() -> dict[str, str]:
    from .resolve.github import git_env
    return git_env(None)


def sample_points(since: date, step: str, until: date) -> list[date]:
    if step not in ("weekly", "monthly"):
        raise ArchimeterError("--step must be 'weekly' or 'monthly'")
    pts, d = [], since
    while d <= until:
        pts.append(d)
        if step == "weekly":
            d += timedelta(days=7)
        else:
            d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    if not pts or pts[-1] != until:
        pts.append(until)
    return pts


def _snapshot_metrics(r: dict[str, Any]) -> dict[str, Any]:
    m = r["metrics"]
    mod = m["levels"]["module"]
    return {
        "propagation_cost": mod["propagation_cost"].get("combined"),
        "propagation_cost_import": mod["propagation_cost"].get("import"),
        "cycles": len(mod["cycles"].get("combined", [])),
        "hidden_coupling": len(r.get("hidden_coupling") or []),
        "mean_complexity": m["totals"]["mean_complexity"],
        "loc": m["totals"]["loc"], "files": m["totals"]["files"], "modules": len(mod["nodes"]),
        "events": (r.get("events") or {}).get("summary", {}).get("events"),
    }


def run(target: str, since: str, step: str, config: Path | None, out: Path) -> None:
    import typer
    from .resolve import resolve_target
    try:
        since_d = date.fromisoformat(since)
    except ValueError:
        raise ArchimeterError("--since must be a date like 2026-01-31") from None
    with contextlib.ExitStack() as stack:
        rt = stack.enter_context(resolve_target(target))
        if rt.kind not in ("path", "github"):
            raise ArchimeterError("trend needs a git repository (local path or GitHub repo)")
        if not GitHistory.available(rt.root):
            raise ArchimeterError(f"{rt.root} is not a git repository: trend needs commit history")
        key = hashlib.sha256(str(rt.identity.get("repo") or rt.root.resolve()).encode()).hexdigest()[:16]
        # Never touch the user's working tree or even their .git: work in a private clone under the cache.
        priv = CACHE / "trend" / key / "repo"
        if rt.kind == "github":
            priv = rt.root  # the cache clone already is private to archimeter
        elif not (priv / ".git").exists():
            priv.parent.mkdir(parents=True, exist_ok=True)
            from .resolve.github import git_env
            r = subprocess.run(["git", "clone", "--no-checkout", "--local", str(rt.root.resolve()), str(priv)], capture_output=True, text=True,
                               env=git_env(None, allow_local=True))
            if r.returncode != 0:
                raise ArchimeterError(f"could not make a private clone of {rt.root}: {r.stderr.strip()[-300:]}")
        else:
            from .resolve.github import git_env
            _git(priv, "fetch", "--force", "origin", "+refs/heads/*:refs/remotes/origin/*", "+HEAD:refs/remotes/origin/HEAD", env=git_env(None, allow_local=True))
        head_ref = rt.identity.get("commit") or _git(priv, "rev-parse", "origin/HEAD" if rt.kind == "path" else "HEAD").strip()
        head_date = datetime.fromisoformat(_git(priv, "show", "-s", "--format=%cI", head_ref).strip()).date()
        pts = sample_points(since_d, step, head_date)
        shas: list[tuple[date, str]] = []
        for p in pts:
            sha = _git(priv, "rev-list", "-1", f"--before={(p + timedelta(days=1)).isoformat()}", head_ref).strip()
            if sha and sha not in [s for _, s in shas]:
                shas.append((p, sha))
        if not shas:
            raise ArchimeterError(f"no commits on or before the requested period in {target}")
        cfg_path = config
        snaps: list[dict[str, Any]] = []
        cdir = CACHE / "snapshots" / key
        cdir.mkdir(parents=True, exist_ok=True)
        for p, sha in shas:
            cfg_probe = load_config(cfg_path) if cfg_path else None
            cfg_hash = hashlib.sha256(json.dumps((cfg_probe or load_config(None)).to_dict(), sort_keys=True).encode()).hexdigest()[:8]
            cf = cdir / f"{sha}-{SCHEMA_VERSION}-{__version__}-{cfg_hash}.json"
            if cf.exists():
                snap = json.loads(cf.read_text())
                typer.echo(f"{p} {sha[:10]} (cached)")
            else:
                snap = _analyze_snapshot(priv, sha, cfg_path)
                snap["date"] = p.isoformat()
                cf.write_text(json.dumps(snap))
                typer.echo(f"{p} {sha[:10]} pc={snap['metrics']['propagation_cost']}")
            snap["date"] = p.isoformat()
            snaps.append(snap)
        out.mkdir(parents=True, exist_ok=True)
        doc = {"schema_version": SCHEMA_VERSION, "tool": {"name": "archimeter", "version": __version__},
               "target": rt.identity, "since": since, "step": step, "snapshots": snaps}
        from .report.write import write_trend
        write_trend(doc, out)
        typer.echo(f"wrote {out / 'trend.json'} and {out / 'trend.html'}")


def _analyze_snapshot(repo: Path, sha: str, cfg_path: Path | None) -> dict[str, Any]:
    wt_root = Path(tempfile.mkdtemp(prefix="archimeter-wt-"))
    wt = wt_root / "tree"
    from .resolve.github import git_env
    from .auth import sources
    cred = sources.find_credential()
    env = git_env(cred.token if cred else None)
    try:
        _git(repo, "worktree", "add", "--detach", "--force", str(wt), sha, env=env)
        cfg = load_config(cfg_path or find_config(wt))
        ctx = build_context(wt, cfg, target={"name": repo.name, "commit": sha, "kind": "snapshot"})
        ctx.git = GitHistory(wt, ref=sha, include_author_names=cfg.git.include_author_names)
        r = analyze_context(ctx)
        return {"commit": sha, "metrics": _snapshot_metrics(r)}
    finally:
        with contextlib.suppress(Exception):
            _git(repo, "worktree", "remove", "--force", str(wt), env=env)
        shutil.rmtree(wt_root, ignore_errors=True)
        with contextlib.suppress(Exception):
            _git(repo, "worktree", "prune", env=env)
