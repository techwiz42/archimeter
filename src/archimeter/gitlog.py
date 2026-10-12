"""Read-only git history access. Needs commit/tree objects only (works on blobless clones)."""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .errors import ArchimeterError


@dataclass
class Commit:
    sha: str
    when: datetime
    author: str  # name <email> hash or name, per config
    files: list[str]  # relative to the analyzed root


def _safe_env() -> dict[str, str]:
    """No user/system git config, hooks and fsmonitor off: a hostile repo's own config cannot run anything."""
    from .resolve.github import git_env
    return git_env(None, allow_local=True)


def _git(repo: Path, *args: str, timeout: int = 600) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout, env=_safe_env())
    except (OSError, subprocess.SubprocessError) as e:
        raise ArchimeterError(f"git failed: {e}") from None
    if r.returncode != 0:
        raise ArchimeterError(f"git {args[0]} failed: {r.stderr.strip()[:300]}")
    return r.stdout


class GitHistory:
    def __init__(self, root: Path, *, ref: str = "HEAD", include_author_names: bool = False):
        self.root = root
        self.ref = ref
        self.include_author_names = include_author_names
        top = _git(root, "rev-parse", "--show-toplevel").strip()
        self.top = Path(top)
        rel = root.resolve().relative_to(self.top.resolve())
        self.prefix = "" if str(rel) == "." else str(rel).replace("\\", "/") + "/"
        self.head = _git(root, "rev-parse", ref).strip()

    @staticmethod
    def available(root: Path) -> bool:
        try:
            r = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-dir"], capture_output=True, text=True, timeout=30, env=_safe_env())
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def head_date(self) -> datetime:
        return datetime.fromisoformat(_git(self.root, "show", "-s", "--format=%cI", self.head).strip())

    def commits(self, window_days: int | None) -> list[Commit]:
        end = self.head_date()
        args = ["log", self.head, "--no-merges", "--no-renames", "--name-only", "--format=%x1e%H%x1f%cI%x1f%an%x1f%ae"]
        if window_days is not None:
            args.append(f"--since={(end - timedelta(days=window_days)).isoformat()}")
        out = _git(self.root, *args)
        res: list[Commit] = []
        for rec in out.split("\x1e"):
            if not rec.strip():
                continue
            head, _, files = rec.partition("\n")
            sha, when, name, email = head.split("\x1f")
            author = name if self.include_author_names else hashlib.sha256(email.strip().lower().encode()).hexdigest()[:10]
            fl = []
            for f in files.splitlines():
                f = f.strip()
                if not f:
                    continue
                if self.prefix:
                    if not f.startswith(self.prefix):
                        continue
                    f = f[len(self.prefix):]
                fl.append(f)
            res.append(Commit(sha, datetime.fromisoformat(when), author, fl))
        return res
