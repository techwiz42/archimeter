"""GitHub API helpers: identity, scopes, repository access classification."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..errors import AuthError, RepoAccessError
from ..safety import Redactor

API = "https://api.github.com"
UA = "archimeter"


def _headers(token: str | None) -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "User-Agent": UA, "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


@dataclass
class Identity:
    login: str
    scopes: list[str] | None  # None for fine-grained tokens, which do not report scopes


def whoami(token: str, client: httpx.Client | None = None) -> Identity:
    c = client or httpx.Client(timeout=20)
    try:
        r = c.get(f"{API}/user", headers=_headers(token))
    except httpx.HTTPError as e:
        raise AuthError(Redactor.redact(f"could not reach api.github.com: {e}")) from None
    if r.status_code == 401:
        raise AuthError("GitHub rejected the token (401 Bad credentials): it is expired, revoked, or mistyped")
    if r.status_code != 200:
        raise AuthError(f"GitHub /user returned HTTP {r.status_code}")
    scopes = r.headers.get("x-oauth-scopes")
    return Identity(r.json()["login"], [s.strip() for s in scopes.split(",") if s.strip()] if scopes is not None else None)


def check_repo_access(owner: str, repo: str, token: str | None, client: httpx.Client | None = None) -> dict:
    """Return repo metadata or raise RepoAccessError with an actionable, distinguishable message."""
    c = client or httpx.Client(timeout=20)
    try:
        r = c.get(f"{API}/repos/{owner}/{repo}", headers=_headers(token))
    except httpx.HTTPError as e:
        raise RepoAccessError(Redactor.redact(f"could not reach api.github.com: {e}")) from None
    if r.status_code == 200:
        return r.json()
    sso = r.headers.get("x-github-sso")
    if r.status_code == 403 and sso and "required" in sso:
        url = sso.split("url=", 1)[1] if "url=" in sso else "https://github.com/settings/tokens"
        raise RepoAccessError(
            f"organization '{owner}' requires SSO authorization for this token. Authorize it at: {url}")
    if r.status_code in (401,):
        raise RepoAccessError("GitHub rejected the token (401): run `archimeter auth login` again or fix GITHUB_TOKEN")
    if r.status_code == 403:
        msg = (r.json().get("message") if r.headers.get("content-type", "").startswith("application/json") else "") or ""
        if "rate limit" in msg.lower():
            raise RepoAccessError("GitHub API rate limit exceeded; authenticate (archimeter auth login) for a higher limit")
        raise RepoAccessError(f"token lacks access to {owner}/{repo} (HTTP 403: {msg or 'forbidden'})")
    if r.status_code == 404:
        # GitHub returns 404 for private repos the caller cannot see, so existence has to be probed separately.
        owner_exists = False
        try:
            o = c.get(f"{API}/users/{owner}", headers=_headers(token))
            owner_exists = o.status_code == 200
        except httpx.HTTPError:
            pass
        if not owner_exists:
            raise RepoAccessError(f"repository {owner}/{repo} does not exist: GitHub has no user or organization named '{owner}'")
        if token is None:
            raise RepoAccessError(
                f"repository {owner}/{repo} was not found. It does not exist, or it is private: "
                "run `archimeter auth login` (or set GITHUB_TOKEN) to access private repositories")
        ident = None
        try:
            ident = whoami(token, c)
        except AuthError:
            pass
        scopes = ident.scopes if ident else None
        if scopes is not None and not ({"repo", "public_repo"} & set(scopes)):
            raise RepoAccessError(
                f"token lacks access to {owner}/{repo}: its OAuth scopes ({', '.join(scopes) or 'none'}) do not include 'repo'. "
                "Create a token with read access to repository contents.")
        who = f" (authenticated as {ident.login})" if ident else ""
        raise RepoAccessError(
            f"repository {owner}/{repo} was not found{who}. GitHub reports a repository the token cannot see the same as one that "
            f"does not exist: check the spelling, and that the token can read it "
            "(fine-grained token: add the repository under 'Repository access' with Contents: read and Metadata: read)")
    raise RepoAccessError(f"GitHub returned HTTP {r.status_code} for {owner}/{repo}")
