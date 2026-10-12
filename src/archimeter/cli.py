from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from .errors import ArchimeterError

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Measure structural complexity (imports, events, change coupling) of source projects and generated applications.")
auth_app = typer.Typer(no_args_is_help=True, help="Manage GitHub credentials.")
app.add_typer(auth_app, name="auth")


def _fail(e: Exception) -> None:
    from .safety import Redactor
    typer.echo(f"error: {Redactor.redact(str(e))}", err=True)
    raise typer.Exit(2)


@app.command()
def analyze(
    target: str = typer.Argument(..., help="path, GitHub repo, docker:// image, oci: tarball or build-output directory"),
    config: Optional[Path] = typer.Option(None, "--config", help="archimeter.toml (default: found in target root)"),
    out: Path = typer.Option(Path("./report"), "--out", help="output directory"),
    framework: Optional[str] = typer.Option(None, "--framework", help="framework source target for provenance matching"),
    submodules: bool = typer.Option(False, "--submodules", help="also fetch git submodules"),
) -> None:
    """Analyze one target and write metrics.json and report.html."""
    from .run import run_analyze
    try:
        run_analyze(target, config, out, framework=framework, submodules=submodules)
    except ArchimeterError as e:
        _fail(e)


@app.command()
def compare(
    targets: list[str] = typer.Argument(..., help="two or more generated applications"),
    framework: Optional[str] = typer.Option(None, "--framework"),
    config: Optional[Path] = typer.Option(None, "--config"),
    out: Path = typer.Option(Path("./report"), "--out"),
) -> None:
    """Compare generated applications side by side."""
    from .run import run_compare
    try:
        run_compare(targets, config, out, framework=framework)
    except ArchimeterError as e:
        _fail(e)


@app.command()
def trend(
    target: str = typer.Argument(...),
    since: str = typer.Option(..., "--since", help="start date, YYYY-MM-DD"),
    step: str = typer.Option("monthly", "--step", help="weekly or monthly"),
    config: Optional[Path] = typer.Option(None, "--config"),
    out: Path = typer.Option(Path("./report"), "--out"),
) -> None:
    """Compute metrics at historical commits and chart the trend."""
    from .run import run_trend
    try:
        run_trend(target, since, step, config, out)
    except ArchimeterError as e:
        _fail(e)


@app.command()
def init(
    target: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", help="overwrite an existing archimeter.toml"),
) -> None:
    """Write a starter archimeter.toml from what is detected in the target."""
    from .run import run_init
    try:
        run_init(target, force)
    except ArchimeterError as e:
        _fail(e)


@auth_app.command("login")
def auth_login(with_token: bool = typer.Option(False, "--with-token", help="read a personal access token from stdin"),
               client_id: Optional[str] = typer.Option(None, "--client-id", help="OAuth App client ID (or config / ARCHIMETER_OAUTH_CLIENT_ID)"),
               config: Optional[Path] = typer.Option(None, "--config")) -> None:
    from .auth import cli as auth_cli
    try:
        auth_cli.login(with_token=with_token, client_id=client_id, config=config)
    except ArchimeterError as e:
        _fail(e)


@auth_app.command("status")
def auth_status() -> None:
    from .auth import cli as auth_cli
    try:
        auth_cli.status()
    except ArchimeterError as e:
        _fail(e)


@auth_app.command("logout")
def auth_logout() -> None:
    from .auth import cli as auth_cli
    try:
        auth_cli.logout()
    except ArchimeterError as e:
        _fail(e)


if __name__ == "__main__":
    app()
