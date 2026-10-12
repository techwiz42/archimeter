"""Pure-Python OCI/Docker registry client (no daemon, no runtime). Pull only; layers are cached by digest."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

import httpx

from ..errors import ArchimeterError, AuthError, UnsafeArchiveError
from ..safety import Redactor
from .sources import ImageInfo, _open_layer

CACHE = Path(os.environ.get("ARCHIMETER_CACHE_DIR", "~/.cache/archimeter")).expanduser() / "images"
MANIFEST_ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"])
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json")
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


@dataclass
class Ref:
    registry: str
    repo: str
    reference: str  # tag or sha256:...
    original: str

    @property
    def is_digest(self) -> bool:
        return self.reference.startswith("sha256:")


def parse_ref(raw: str) -> Ref:
    s = raw.removeprefix("docker://")
    digest = None
    if "@" in s:
        s, digest = s.split("@", 1)
    tag = None
    last = s.rsplit("/", 1)[-1]
    if ":" in last:
        s, tag = s.rsplit(":", 1)
    parts = s.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry, repo = parts[0], "/".join(parts[1:])
    else:
        registry, repo = "docker.io", s
    if registry in ("docker.io", "index.docker.io"):
        registry = "docker.io"
        if "/" not in repo:
            repo = "library/" + repo
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", repo):
        raise ArchimeterError(f"invalid image repository name: {repo!r}")
    return Ref(registry, repo, digest or tag or "latest", raw)


def api_host(registry: str) -> str:
    return "registry-1.docker.io" if registry == "docker.io" else registry


# ---------------------------------------------------------------- credentials
def docker_config_creds(registry: str) -> tuple[str, str] | None:
    """Reuse Docker's config.json and credential helpers. Helpers are user-installed tools, never target code."""
    cfg_dir = Path(os.environ.get("DOCKER_CONFIG", "~/.docker")).expanduser()
    try:
        cfg = json.loads((cfg_dir / "config.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    keys = [registry] + (["https://index.docker.io/v1/", "index.docker.io"] if registry == "docker.io" else []) + [f"https://{registry}"]
    helper = (cfg.get("credHelpers") or {}).get(registry) or cfg.get("credsStore")
    for k in keys:
        entry = (cfg.get("auths") or {}).get(k)
        if entry:
            if entry.get("auth"):
                u, _, p = base64.b64decode(entry["auth"]).decode().partition(":")
                Redactor.register(p)
                return u, p
            if entry.get("identitytoken"):
                Redactor.register(entry["identitytoken"])
                return "<token>", entry["identitytoken"]
    if helper and re.fullmatch(r"[\w.-]+", helper):
        for k in keys[:2]:
            try:
                r = subprocess.run([f"docker-credential-{helper}", "get"], input=k, capture_output=True, text=True, timeout=20)
            except (OSError, subprocess.SubprocessError):
                return None
            if r.returncode == 0:
                try:
                    d = json.loads(r.stdout)
                except json.JSONDecodeError:
                    continue
                if d.get("Secret"):
                    Redactor.register(d["Secret"])
                    return d.get("Username") or "<token>", d["Secret"]
    return None


def ghcr_creds() -> tuple[str, str] | None:
    from ..auth import github as gh_api, sources
    cred = sources.find_credential()
    if not cred:
        return None
    ident = gh_api.whoami(cred.token)
    if ident.scopes is not None and "read:packages" not in ident.scopes and "repo" not in ident.scopes:
        raise AuthError(
            f"your token (scopes: {', '.join(ident.scopes) or 'none'}) cannot read GitHub Container Registry packages. "
            "Add the 'read:packages' scope (classic token) to pull from ghcr.io. archimeter requests it only for ghcr.io targets.")
    if ident.scopes is None:
        raise AuthError("fine-grained personal access tokens cannot read ghcr.io packages: use a classic token with 'read:packages', "
                        "or `docker login ghcr.io` credentials")
    if "read:packages" not in ident.scopes:
        raise AuthError("your token lacks the 'read:packages' scope required for ghcr.io: add it to pull container images")
    return ident.login, cred.token


# ---------------------------------------------------------------- client
class RegistryImage:
    def __init__(self, ref: Ref, *, platform: str = "linux/amd64", client: httpx.Client | None = None,
                 creds: tuple[str, str] | None | bool = True):
        self.ref = ref
        self.platform = platform
        self.http = client or httpx.Client(timeout=httpx.Timeout(60, connect=20), follow_redirects=True)
        self.base = f"https://{api_host(ref.registry)}/v2/{ref.repo}"
        self._token: str | None = None
        self._creds_explicit = creds
        self._creds: tuple[str, str] | None = None
        self._creds_loaded = creds is not True
        if creds is not True:
            self._creds = creds or None
        CACHE.mkdir(parents=True, exist_ok=True)

    # -- auth --
    def _load_creds(self) -> tuple[str, str] | None:
        if not self._creds_loaded:
            self._creds = ghcr_creds() if self.ref.registry == "ghcr.io" else docker_config_creds(self.ref.registry)
            self._creds_loaded = True
        return self._creds

    def _get(self, url: str, *, accept: str | None = None, stream: bool = False) -> httpx.Response:
        for attempt in range(2):
            headers = {"User-Agent": "archimeter"}
            if accept:
                headers["Accept"] = accept
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            req = self.http.build_request("GET", url, headers=headers)
            try:
                r = self.http.send(req, stream=stream)
            except httpx.HTTPError as e:
                raise ArchimeterError(Redactor.redact(f"registry request failed: {e}")) from None
            if r.status_code == 401 and attempt == 0:
                challenge = r.headers.get("www-authenticate", "")
                r.close()
                self._authenticate(challenge)
                continue
            return r
        raise AuthError(f"registry {self.ref.registry} rejected our credentials for {self.ref.repo}")

    def _authenticate(self, challenge: str) -> None:
        m = re.match(r"(?i)bearer\s+(.*)", challenge)
        if not m:
            creds = self._load_creds()
            if creds and challenge.lower().startswith("basic"):
                self._token = None
                self.http.auth = httpx.BasicAuth(*creds)
                return
            raise AuthError(f"registry {self.ref.registry} requires authentication ({challenge or 'no challenge'}); "
                            "run `docker login` or provide credentials in Docker's config.json")
        params = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        realm = params.pop("realm", None)
        if not realm:
            raise AuthError("registry sent an invalid Bearer challenge")
        params.setdefault("scope", f"repository:{self.ref.repo}:pull")
        creds = self._load_creds()
        auth = httpx.BasicAuth(*creds) if creds else None
        try:
            r = self.http.get(realm, params=params, auth=auth, headers={"User-Agent": "archimeter"})
        except httpx.HTTPError as e:
            raise ArchimeterError(Redactor.redact(f"token request failed: {e}")) from None
        if r.status_code in (401, 403):
            raise AuthError(f"access to {self.ref.registry}/{self.ref.repo} was denied"
                            + ("" if creds else ": the image is private; run `docker login` or use a token") )
        if r.status_code != 200:
            raise ArchimeterError(f"registry token endpoint returned HTTP {r.status_code}")
        body = r.json()
        self._token = body.get("token") or body.get("access_token")
        if not self._token:
            raise AuthError("registry token response contained no token")
        Redactor.register(self._token)

    # -- content --
    def _blob_path(self, digest: str) -> Path:
        algo, _, hexd = digest.partition(":")
        if algo != "sha256" or not re.fullmatch(r"[0-9a-f]{64}", hexd):
            raise UnsafeArchiveError(f"unsupported or malformed digest: {digest!r}")
        return CACHE / "sha256" / hexd

    def _fetch_blob(self, digest: str) -> Path:
        p = self._blob_path(digest)
        if p.exists():
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if "sha256:" + h.hexdigest() == digest:
                return p
            p.unlink()  # corrupt cache entry: discard and re-pull
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        r = self._get(f"{self.base}/blobs/{digest}", stream=True)
        if r.status_code != 200:
            r.close()
            raise ArchimeterError(f"blob {digest} not available (HTTP {r.status_code})")
        h = hashlib.sha256()
        with open(tmp, "wb") as out:
            for chunk in r.iter_bytes(1 << 20):
                h.update(chunk)
                out.write(chunk)
        r.close()
        if "sha256:" + h.hexdigest() != digest:
            tmp.unlink(missing_ok=True)
            raise UnsafeArchiveError(f"blob {digest} failed digest verification after download")
        os.replace(tmp, p)
        return p

    def _manifest(self, reference: str) -> tuple[str, dict]:
        r = self._get(f"{self.base}/manifests/{reference}", accept=MANIFEST_ACCEPT)
        if r.status_code == 404:
            raise ArchimeterError(f"image {self.ref.registry}/{self.ref.repo}:{reference} not found")
        if r.status_code in (401, 403):
            raise AuthError(f"access to {self.ref.registry}/{self.ref.repo} denied: the image is private or does not exist")
        if r.status_code != 200:
            raise ArchimeterError(f"manifest request failed: HTTP {r.status_code}")
        if len(r.content) > MAX_MANIFEST_BYTES:
            raise UnsafeArchiveError("manifest is unreasonably large")
        digest = "sha256:" + hashlib.sha256(r.content).hexdigest()
        if reference.startswith("sha256:") and digest != reference:
            raise UnsafeArchiveError("manifest failed digest verification")
        return digest, json.loads(r.content)

    def resolve(self) -> ImageInfo:
        digest, m = self._manifest(self.ref.reference)
        if m.get("mediaType") in INDEX_TYPES or "manifests" in m:
            want_os, _, want_arch = self.platform.partition("/")
            pick = next((d for d in m["manifests"] if (d.get("platform") or {}).get("os") == want_os
                         and (d.get("platform") or {}).get("architecture") == want_arch), None)
            if pick is None:
                have = sorted({f"{(d.get('platform') or {}).get('os')}/{(d.get('platform') or {}).get('architecture')}" for d in m["manifests"]})
                raise ArchimeterError(f"no {self.platform} variant in image index (available: {', '.join(have)}); set [image] platform")
            digest, m = self._manifest(pick["digest"])
        cfg_path = self._fetch_blob(m["config"]["digest"])
        cfg = json.loads(cfg_path.read_bytes())
        info = ImageInfo(ref=f"{self.ref.registry}/{self.ref.repo}:{self.ref.reference}", digest=digest, config=cfg,
                         layer_digests=[l["digest"] for l in m["layers"]], repo_tags=[self.ref.original])
        self._info = info
        return info

    def layers(self, info: ImageInfo) -> Iterator[BinaryIO]:
        for d in info.layer_digests:
            p = self._fetch_blob(d)
            yield _open_layer(open(p, "rb"))

    def close(self) -> None:
        self.http.close()
