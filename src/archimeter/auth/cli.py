from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from ..config import load_config
from ..errors import AuthError
from ..safety import Redactor, install_log_redaction
from . import device, github, sources, store


def login(*, with_token: bool, client_id: str | None, config: Path | None) -> None:
    install_log_redaction()
    if with_token:
        token = sys.stdin.read().strip()
        if not token:
            raise AuthError("no token on stdin: pipe a fine-grained personal access token, e.g. `pbpaste | archimeter auth login --with-token`")
    else:
        cid = client_id or os.environ.get("ARCHIMETER_OAUTH_CLIENT_ID") or load_config(config).github.oauth_client_id
        token = device.device_login(cid, device.SCOPES_PRIVATE, typer.echo)
    Redactor.register(token)
    ident = github.whoami(token)  # validate before storing anything
    store.save_token(token)
    typer.echo(f"Logged in to github.com as {ident.login}; token stored in the OS keychain.")
    if ident.scopes is not None and "repo" in ident.scopes:
        typer.echo("Note: classic OAuth 'repo' scope grants read AND write to your repositories. archimeter only reads, "
                   "but a fine-grained token (Contents: read, Metadata: read) is the least-privilege option.")


def status() -> None:
    cred = sources.find_credential()
    if not cred:
        typer.echo("Not authenticated. Public repositories work; for private ones run `archimeter auth login`.")
        raise typer.Exit(1)
    ident = github.whoami(cred.token)
    scopes = ", ".join(ident.scopes) if ident.scopes is not None else "fine-grained (no scopes reported)"
    typer.echo(f"Authenticated as {ident.login}\nSource: {cred.source}\nScopes: {scopes}")


def logout() -> None:
    if store.delete_token():
        typer.echo("Stored token removed from the OS keychain.")
    else:
        typer.echo("No stored token found.")
    if sources._from_env():
        typer.echo("Note: a token is still provided by an environment variable; unset it to stop using it.")
