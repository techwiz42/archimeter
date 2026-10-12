import gzip
import hashlib
import json
import logging

import httpx
import pytest
import respx

from archimeter.errors import ArchimeterError, AuthError, UnsafeArchiveError
from archimeter.images import registry as reg
from archimeter.images.flatten import flatten
from archimeter.config import Config
from imagefix import layer

PW = "registry-password-xyz"


def sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "CACHE", tmp_path / "imgcache")
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "nodocker"))
    return tmp_path / "imgcache"


def build_image(app_files):
    l = gzip.compress(layer(app_files))
    cfg = json.dumps({"architecture": "amd64", "os": "linux", "config": {"WorkingDir": "/app", "Env": ["TOKEN=do-not-leak"], "Entrypoint": ["python", "main.py"]}}).encode()
    manifest = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                           "config": {"digest": sha(cfg), "size": len(cfg), "mediaType": "application/vnd.oci.image.config.v1+json"},
                           "layers": [{"digest": sha(l), "size": len(l), "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip"}]}).encode()
    return {"layer": l, "cfg": cfg, "manifest": manifest}


def mock_registry(img, *, require_auth=False, index=True, tamper=False):
    base = "https://registry-1.docker.io/v2/acme/app"
    seen = {"tokens": 0}

    def guard(req):
        if require_auth and req.headers.get("authorization") != "Bearer REGTOKEN":
            return httpx.Response(401, headers={"www-authenticate": 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:acme/app:pull"'})
        return None

    def manifest_handler(req):
        g = guard(req)
        if g:
            return g
        ref = req.url.path.rsplit("/", 1)[-1]
        if ref == "latest" and index:
            idx = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [
                {"digest": "sha256:" + "0" * 64, "platform": {"os": "linux", "architecture": "arm64"}, "mediaType": "x"},
                {"digest": sha(img["manifest"]), "platform": {"os": "linux", "architecture": "amd64"}, "mediaType": "x"}]}).encode()
            return httpx.Response(200, content=idx, headers={"content-type": "application/vnd.oci.image.index.v1+json"})
        return httpx.Response(200, content=img["manifest"])

    def blob_handler(req):
        g = guard(req)
        if g:
            return g
        d = req.url.path.rsplit("/", 1)[-1]
        if tamper:
            return httpx.Response(200, content=b"tampered")
        for b in (img["layer"], img["cfg"]):
            if sha(b) == d:
                return httpx.Response(200, content=b)
        return httpx.Response(404)

    def token_handler(req):
        seen["tokens"] += 1
        auth = req.headers.get("authorization")
        return httpx.Response(200, json={"token": "REGTOKEN"}) if (auth or not require_auth) else httpx.Response(401)

    respx.route(method="GET", url__regex=rf"{base}/manifests/.*").mock(side_effect=manifest_handler)
    respx.route(method="GET", url__regex=rf"{base}/blobs/.*").mock(side_effect=blob_handler)
    respx.get("https://auth.docker.io/token").mock(side_effect=token_handler)
    return seen


@respx.mock
def test_pull_public_image_selects_platform_and_flattens(cache, tmp_path):
    img = build_image([("file", "app/main.py", b"print(1)\n"), ("file", "app/.env", b"X=1")])
    mock_registry(img)
    src = reg.RegistryImage(reg.parse_ref("docker://acme/app:latest"))
    info = src.resolve()
    assert info.digest == sha(img["manifest"]) and info.env_names() == ["TOKEN"]
    flat = flatten(src, info, tmp_path / "flat", Config())
    assert (flat.app_root / "main.py").exists() and not list(flat.root.rglob(".env"))
    assert flat.separation["strategy"] == "workdir"
    assert "do-not-leak" not in json.dumps(flat.summary)


@respx.mock
def test_second_pull_uses_digest_cache_and_corrupt_entry_is_repulled(cache, tmp_path):
    img = build_image([("file", "app/main.py", b"print(1)\n")])
    mock_registry(img)
    src = reg.RegistryImage(reg.parse_ref("docker://acme/app:latest"))
    info = src.resolve()
    list(src.layers(info))
    blob_calls = lambda: sum(1 for c in respx.calls if "/blobs/" in str(c.request.url))
    n = blob_calls()
    list(src.layers(info))
    assert blob_calls() == n  # served from ~/.cache/archimeter/images by digest
    p = cache / "sha256" / info.layer_digests[0].split(":")[1]
    p.write_bytes(b"corrupted")
    list(src.layers(info))
    assert hashlib.sha256(p.read_bytes()).hexdigest() == info.layer_digests[0].split(":")[1]


@respx.mock
def test_blob_with_wrong_digest_is_rejected(cache):
    img = build_image([("file", "app/main.py", b"1")])
    mock_registry(img, tamper=True)
    src = reg.RegistryImage(reg.parse_ref("docker://acme/app:latest"))
    with pytest.raises(UnsafeArchiveError, match="digest"):
        src.resolve()


@respx.mock
def test_private_registry_uses_docker_config_credentials_and_never_logs_them(cache, tmp_path, monkeypatch, caplog):
    import base64
    d = tmp_path / "dockercfg"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"auths": {"docker.io": {"auth": base64.b64encode(f"me:{PW}".encode()).decode()}}}))
    monkeypatch.setenv("DOCKER_CONFIG", str(d))
    img = build_image([("file", "app/main.py", b"1")])
    mock_registry(img, require_auth=True)
    from archimeter.safety import install_log_redaction
    install_log_redaction()
    with caplog.at_level(logging.DEBUG):
        info = reg.RegistryImage(reg.parse_ref("docker://acme/app:latest")).resolve()
    assert info.layer_digests
    tok_req = [c.request for c in respx.calls if c.request.url.host == "auth.docker.io"][0]
    assert tok_req.headers["authorization"].startswith("Basic ")
    assert PW not in caplog.text


@respx.mock
def test_private_image_without_credentials_gives_actionable_error(cache):
    img = build_image([("file", "app/main.py", b"1")])
    respx.get("https://registry-1.docker.io/v2/acme/app/manifests/latest").mock(return_value=httpx.Response(
        401, headers={"www-authenticate": 'Bearer realm="https://auth.docker.io/token",service="s"'}))
    respx.get("https://auth.docker.io/token").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError, match="private.*docker login"):
        reg.RegistryImage(reg.parse_ref("docker://acme/app:latest")).resolve()


@pytest.mark.parametrize("scopes,msg", [(["gist"], "read:packages"), (None, "fine-grained")])
def test_ghcr_requires_read_packages_only_when_used(monkeypatch, scopes, msg):
    from archimeter.auth import github as gh_api, sources
    monkeypatch.setattr(sources, "find_credential", lambda: sources.Credential("tok-12345678", "environment:GITHUB_TOKEN"))
    monkeypatch.setattr(gh_api, "whoami", lambda t, c=None: gh_api.Identity("me", scopes))
    with pytest.raises(AuthError, match=msg):
        reg.ghcr_creds()
    monkeypatch.setattr(gh_api, "whoami", lambda t, c=None: gh_api.Identity("me", ["read:packages"]))
    assert reg.ghcr_creds() == ("me", "tok-12345678")


def test_parse_ref_forms():
    r = reg.parse_ref("docker://nginx:1.25")
    assert (r.registry, r.repo, r.reference) == ("docker.io", "library/nginx", "1.25")
    r = reg.parse_ref("docker://ghcr.io/owner/app@sha256:" + "a" * 64)
    assert (r.registry, r.repo, r.is_digest) == ("ghcr.io", "owner/app", True)
    with pytest.raises(ArchimeterError):
        reg.parse_ref("docker://UPPER/../x")
