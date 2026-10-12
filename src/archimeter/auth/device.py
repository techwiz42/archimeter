"""GitHub OAuth device flow (RFC 8628): works over SSH and without a browser on the machine."""
from __future__ import annotations

import time
from typing import Callable

import httpx

from ..errors import AuthError
from ..safety import Redactor

DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
# OAuth Apps cannot request read-only repository access: 'repo' is the narrowest scope that reads private
# repositories. A fine-grained personal access token (Contents: read, Metadata: read) is the least-privilege
# alternative: `archimeter auth login --with-token`.
SCOPES_PRIVATE = ["repo"]
SCOPE_PACKAGES = "read:packages"


def device_login(client_id: str, scopes: list[str], say: Callable[[str], None], *, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.monotonic) -> str:
    if not client_id:
        raise AuthError(
            "no OAuth App client ID configured. Register an OAuth App at https://github.com/settings/developers "
            "(enable 'Device Flow'), then set [github] oauth_client_id in archimeter.toml, the ARCHIMETER_OAUTH_CLIENT_ID "
            "environment variable, or pass --client-id. Alternatively use `archimeter auth login --with-token`.")
    c = client or httpx.Client(timeout=30)
    hdr = {"Accept": "application/json", "User-Agent": "archimeter"}
    try:
        r = c.post(DEVICE_URL, data={"client_id": client_id, "scope": " ".join(scopes)}, headers=hdr)
    except httpx.HTTPError as e:
        raise AuthError(Redactor.redact(f"could not start device flow: {e}")) from None
    data = r.json()
    if r.status_code != 200 or "device_code" not in data:
        raise AuthError(f"GitHub refused the device-flow request: {data.get('error_description') or data.get('error') or r.status_code}")
    say(f"Open {data['verification_uri']} and enter the code: {data['user_code']}")
    interval = int(data.get("interval", 5))
    deadline = now() + int(data.get("expires_in", 900))
    while True:
        if now() >= deadline:
            raise AuthError("the device code expired before it was approved; run `archimeter auth login` again")
        sleep(interval)
        try:
            t = c.post(TOKEN_URL, headers=hdr, data={
                "client_id": client_id, "device_code": data["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}).json()
        except httpx.HTTPError as e:
            raise AuthError(Redactor.redact(f"polling failed: {e}")) from None
        err = t.get("error")
        if not err and t.get("access_token"):
            Redactor.register(t["access_token"])
            return t["access_token"]
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval = int(t.get("interval", interval + 5))
            continue
        if err == "expired_token":
            raise AuthError("the device code expired before it was approved; run `archimeter auth login` again")
        if err == "access_denied":
            raise AuthError("authorization was denied in the browser; nothing was stored")
        raise AuthError(f"device flow failed: {t.get('error_description') or err}")
