"""Token discovery: env -> stored (keyring) -> `gh auth token` -> (caller starts interactive login)."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from ..errors import AuthError
from ..safety import Redactor, install_log_redaction

ENV_VARS = ("ARCHIMETER_GITHUB_TOKEN", "GITHUB_TOKEN")


@dataclass
class Credential:
    token: str
    source: str  # environment:NAME | keyring | gh-cli

    def __repr__(self) -> str:  # never leak the token via repr/logging
        return f"Credential(source={self.source!r}, token='***')"


def _from_env() -> Credential | None:
    for name in ENV_VARS:
        v = os.environ.get(name, "").strip()
        if v:
            return Credential(v, f"environment:{name}")
    return None


def _from_keyring() -> Credential | None:
    from . import store
    try:
        t = store.load_token()
    except AuthError:
        return None  # no keychain here: fall through to the next source
    return Credential(t, "keyring") if t else None


def _from_gh() -> Credential | None:
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        r = subprocess.run([gh, "auth", "token", "--hostname", "github.com"], capture_output=True, text=True, timeout=20,
                           env={k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "XDG_CONFIG_HOME", "GH_CONFIG_DIR")})
    except (OSError, subprocess.SubprocessError):
        return None
    t = r.stdout.strip()
    return Credential(t, "gh-cli") if r.returncode == 0 and t else None


def find_credential() -> Credential | None:
    install_log_redaction()
    for fn in (_from_env, _from_keyring, _from_gh):
        c = fn()
        if c:
            Redactor.register(c.token)
            return c
    return None
