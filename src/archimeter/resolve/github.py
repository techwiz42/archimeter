"""GitHub targets: blobless, hook-free, token-never-persisted clones into ~/.cache/archimeter/<owner>/<repo>."""
from __future__ import annotations

import base64
import os
import re
import subprocess
from pathlib import Path

from ..auth import github as gh_api
from ..auth import sources
from ..errors import ArchimeterError, RepoAccessError
from ..safety import Redactor, install_log_redaction

CACHE = Path(os.environ.get("ARCHIMETER_CACHE_DIR", "~/.cache/archimeter")).expanduser()

_SHORT = re.compile(r"^(?P<owner>[A-Za-z0-9][A-Za-z0-9-]*)/(?P<repo>[\w.-]+?)(?:\.git)?(?:@(?P<ref>\S+))?$")
_URL = re.compile(r"^(?:https://github\.com/|git@github\.com:)(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?(?:/)?(?:@(?P<ref>\S+))?$")


def parse_github(target: str) -> tuple[str, str, str | None] | None:
    m = _URL.match(target) or (None if target.startswith((".", "/", "~")) else _SHORT.match(target))
    if not m:
        return None
    return m["owner"], m["repo"], m["ref"]


def git_env(token: str | None, *, submodules: bool = False, allow_local: bool = False) -> dict[str, str]:
    """Environment for git: no user/system config, hooks off, token only via env-scoped config (never argv, never disk)."""
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0"})
    cfg = [("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"), ("protocol.file.allow", "always" if allow_local else "never"),
           ("protocol.ext.allow", "never"), ("submodule.recurse", "true" if submodules else "false"),
           ("credential.helper", "")]
    if token:
        b = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cfg.append(("http.https://github.com/.extraheader", f"Authorization: Basic {b}"))
        Redactor.register(token)
        Redactor.register(b)
    env["GIT_CONFIG_COUNT"] = str(len(cfg))
    for i, (k, v) in enumerate(cfg):
        env[f"GIT_CONFIG_KEY_{i}"] = k
        env[f"GIT_CONFIG_VALUE_{i}"] = v
    return env


def _git(args: list[str], cwd: Path | None, env: dict[str, str], timeout: int = 1800) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise ArchimeterError(Redactor.redact(f"git {args[0]} failed to run: {e}")) from None
    if r.returncode != 0:
        raise ArchimeterError(Redactor.redact(f"git {args[0]} failed: {r.stderr.strip()[-600:]}"))
    return r.stdout


def clone_or_update(owner: str, repo: str, ref: str | None, *, submodules: bool = False) -> tuple[Path, str, str]:
    """-> (checked-out path, commit sha, resolved ref name)."""
    install_log_redaction()
    cred = sources.find_credential()
    token = cred.token if cred else None
    meta = gh_api.check_repo_access(owner, repo, token)  # raises RepoAccessError with an actionable message
    env = git_env(token, submodules=submodules)
    dest = CACHE / owner / repo
    url = f"https://github.com/{owner}/{repo}.git"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        _git(["clone", "--filter=blob:none", "--no-checkout", "--no-recurse-submodules", url, str(dest)], None, env)
    else:
        _git(["remote", "set-url", "origin", url], dest, env)  # keep the stored URL credential-free
        _git(["fetch", "--filter=blob:none", "--prune", "--tags", "origin", "+refs/heads/*:refs/remotes/origin/*"], dest, env)
    want = ref or meta.get("default_branch") or "HEAD"
    sha = None
    for cand in (f"refs/remotes/origin/{want}", f"refs/tags/{want}", want):
        try:
            sha = _git(["rev-parse", "--verify", "--quiet", cand + "^{commit}"], dest, env).strip()
            break
        except ArchimeterError:
            continue
    if sha is None and re.fullmatch(r"[0-9a-fA-F]{7,40}", want):
        try:
            _git(["fetch", "--filter=blob:none", "origin", want], dest, env)
            sha = _git(["rev-parse", "--verify", want + "^{commit}"], dest, env).strip()
        except ArchimeterError:
            pass
    if sha is None:
        raise ArchimeterError(f"ref '{want}' not found in {owner}/{repo} (not a branch, tag or commit)")
    _git(["checkout", "--detach", "--force", sha], dest, env)  # lazily fetches only the blobs of this ref
    if submodules:
        _git(["submodule", "update", "--init", "--recursive", "--depth", "1"], dest, env)
    return dest, sha, want
