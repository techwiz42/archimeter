import json
import logging

import httpx
import pytest
import respx

from archimeter.auth import device, github, sources, store
from archimeter.errors import AuthError, RepoAccessError
from archimeter.safety import Redactor, install_log_redaction

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    Redactor.clear()
    for v in ("GITHUB_TOKEN", "ARCHIMETER_GITHUB_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    yield
    Redactor.clear()


# ---------------------------------------------------------------- credential precedence
def test_env_beats_keyring_and_gh(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token-value")
    monkeypatch.setattr(sources, "_from_keyring", lambda: sources.Credential("kr-token", "keyring"))
    monkeypatch.setattr(sources, "_from_gh", lambda: sources.Credential("gh-token", "gh-cli"))
    c = sources.find_credential()
    assert c.token == "env-token-value" and c.source == "environment:GITHUB_TOKEN"


def test_archimeter_env_name_preferred(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "a-token-1")
    monkeypatch.setenv("ARCHIMETER_GITHUB_TOKEN", "b-token-2")
    assert sources.find_credential().token == "b-token-2"


def test_keyring_beats_gh(monkeypatch):
    monkeypatch.setattr(sources, "_from_keyring", lambda: sources.Credential("kr-token", "keyring"))
    monkeypatch.setattr(sources, "_from_gh", lambda: sources.Credential("gh-token", "gh-cli"))
    assert sources.find_credential().source == "keyring"


def test_gh_used_when_nothing_else(monkeypatch):
    monkeypatch.setattr(sources, "_from_keyring", lambda: None)
    monkeypatch.setattr(sources, "_from_gh", lambda: sources.Credential("gh-token", "gh-cli"))
    assert sources.find_credential().source == "gh-cli"


def test_no_credential_returns_none(monkeypatch):
    monkeypatch.setattr(sources, "_from_keyring", lambda: None)
    monkeypatch.setattr(sources, "_from_gh", lambda: None)
    assert sources.find_credential() is None


def test_credential_repr_never_shows_token():
    assert "supersecret" not in repr(sources.Credential("supersecret-token", "keyring"))


# ---------------------------------------------------------------- keyring storage
class MemKeyring:
    pass


@pytest.fixture
def memkeyring():
    import keyring
    from keyring.backend import KeyringBackend

    class Mem(KeyringBackend):
        priority = 1
        def __init__(self):
            self.d = {}
        def set_password(self, s, u, p): self.d[(s, u)] = p
        def get_password(self, s, u): return self.d.get((s, u))
        def delete_password(self, s, u):
            from keyring.errors import PasswordDeleteError
            if (s, u) not in self.d:
                raise PasswordDeleteError("missing")
            del self.d[(s, u)]

    old = keyring.get_keyring()
    mem = Mem()
    keyring.set_keyring(mem)
    yield mem
    keyring.set_keyring(old)


def test_store_roundtrip_and_logout(memkeyring):
    store.save_token(TOKEN)
    assert store.load_token() == TOKEN
    assert store.delete_token() is True and store.load_token() is None
    assert store.delete_token() is False


def test_no_keychain_fails_loudly_without_plaintext_fallback(monkeypatch, tmp_path):
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring
    old = keyring.get_keyring()
    keyring.set_keyring(FailKeyring())
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        with pytest.raises(AuthError, match="never stores tokens in plain files"):
            store.save_token(TOKEN)
    finally:
        keyring.set_keyring(old)
    assert list(tmp_path.rglob("*")) == []  # nothing written anywhere


# ---------------------------------------------------------------- device flow
@respx.mock
def test_device_flow_pending_slow_down_then_success():
    respx.post(device.DEVICE_URL).mock(return_value=httpx.Response(200, json={
        "device_code": "DC", "user_code": "ABCD-1234", "verification_uri": "https://github.com/login/device", "expires_in": 900, "interval": 5}))
    respx.post(device.TOKEN_URL).mock(side_effect=[
        httpx.Response(200, json={"error": "authorization_pending"}),
        httpx.Response(200, json={"error": "slow_down", "interval": 10}),
        httpx.Response(200, json={"access_token": TOKEN, "token_type": "bearer", "scope": "repo"})])
    said, slept = [], []
    t = device.device_login("client-id", ["repo"], said.append, sleep=slept.append, now=lambda: 0.0)
    assert t == TOKEN
    assert "ABCD-1234" in said[0] and "https://github.com/login/device" in said[0]
    assert slept == [5, 5, 10]  # slow_down raises the polling interval
    body = respx.calls[0].request.content.decode()
    assert "scope=repo" in body and "client_id=client-id" in body


@respx.mock
def test_device_flow_expired_and_denied_store_nothing():
    respx.post(device.DEVICE_URL).mock(return_value=httpx.Response(200, json={
        "device_code": "DC", "user_code": "X", "verification_uri": "u", "expires_in": 900, "interval": 1}))
    respx.post(device.TOKEN_URL).mock(return_value=httpx.Response(200, json={"error": "expired_token"}))
    with pytest.raises(AuthError, match="expired"):
        device.device_login("cid", ["repo"], lambda m: None, sleep=lambda s: None)
    respx.post(device.TOKEN_URL).mock(return_value=httpx.Response(200, json={"error": "access_denied"}))
    with pytest.raises(AuthError, match="denied"):
        device.device_login("cid", ["repo"], lambda m: None, sleep=lambda s: None)


def test_device_flow_needs_a_client_id():
    with pytest.raises(AuthError, match="client ID"):
        device.device_login("", ["repo"], lambda m: None)


def test_only_read_scopes_requested_for_private_repos():
    assert device.SCOPES_PRIVATE == ["repo"]  # the narrowest classic scope that reads private repositories
    assert "write" not in " ".join(device.SCOPES_PRIVATE)


# ---------------------------------------------------------------- access errors
@respx.mock
def test_error_repo_does_not_exist():
    respx.get("https://api.github.com/repos/ghost/none").mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    respx.get("https://api.github.com/users/ghost").mock(return_value=httpx.Response(404))
    with pytest.raises(RepoAccessError, match="does not exist.*no user or organization named 'ghost'"):
        github.check_repo_access("ghost", "none", TOKEN)


@respx.mock
def test_error_token_lacks_access_scopes_listed():
    respx.get("https://api.github.com/repos/acme/priv").mock(return_value=httpx.Response(404))
    respx.get("https://api.github.com/users/acme").mock(return_value=httpx.Response(200, json={"login": "acme"}))
    respx.get("https://api.github.com/user").mock(return_value=httpx.Response(200, json={"login": "me"}, headers={"x-oauth-scopes": "gist"}))
    with pytest.raises(RepoAccessError, match="token lacks access.*scopes \\(gist\\)"):
        github.check_repo_access("acme", "priv", TOKEN)


@respx.mock
def test_error_private_without_token_says_to_log_in():
    respx.get("https://api.github.com/repos/acme/priv").mock(return_value=httpx.Response(404))
    respx.get("https://api.github.com/users/acme").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(RepoAccessError, match="auth login"):
        github.check_repo_access("acme", "priv", None)


@respx.mock
def test_error_org_requires_sso():
    respx.get("https://api.github.com/repos/corp/secret").mock(return_value=httpx.Response(
        403, json={"message": "Resource protected by organization SAML enforcement."},
        headers={"x-github-sso": "required; url=https://github.com/orgs/corp/sso?authorization_request=XYZ"}))
    with pytest.raises(RepoAccessError, match="requires SSO authorization.*orgs/corp/sso"):
        github.check_repo_access("corp", "secret", TOKEN)


@respx.mock
def test_three_failure_modes_have_distinct_messages():
    msgs = []
    respx.get("https://api.github.com/repos/a/b").mock(return_value=httpx.Response(404))
    respx.get("https://api.github.com/users/a").mock(return_value=httpx.Response(404))
    for _ in range(1):
        with pytest.raises(RepoAccessError) as e1:
            github.check_repo_access("a", "b", TOKEN)
        msgs.append(str(e1.value))
    respx.get("https://api.github.com/users/a").mock(return_value=httpx.Response(200, json={}))
    respx.get("https://api.github.com/user").mock(return_value=httpx.Response(200, json={"login": "me"}, headers={"x-oauth-scopes": "gist"}))
    with pytest.raises(RepoAccessError) as e2:
        github.check_repo_access("a", "b", TOKEN)
    msgs.append(str(e2.value))
    respx.get("https://api.github.com/repos/a/b").mock(return_value=httpx.Response(403, headers={"x-github-sso": "required; url=https://x/sso"}))
    with pytest.raises(RepoAccessError) as e3:
        github.check_repo_access("a", "b", TOKEN)
    msgs.append(str(e3.value))
    assert len(set(msgs)) == 3


# ---------------------------------------------------------------- redaction
def test_redactor_scrubs_registered_and_pattern_tokens():
    Redactor.register("custom-secret-value")
    assert "custom-secret-value" not in Redactor.redact("failed with custom-secret-value here")
    assert TOKEN not in Redactor.redact(f"token {TOKEN} bad")
    assert "abc123" not in Redactor.redact("Authorization: Bearer abc123xyz")
    assert "hunter2" not in Redactor.redact("clone https://user:hunter2@github.com/o/r")


@respx.mock
def test_transport_errors_do_not_leak_token():
    Redactor.register(TOKEN)
    respx.get("https://api.github.com/user").mock(side_effect=httpx.ConnectError(f"boom while using {TOKEN}"))
    with pytest.raises(AuthError) as e:
        github.whoami(TOKEN)
    assert TOKEN not in str(e.value)


def test_logging_is_redacted(caplog):
    install_log_redaction()
    Redactor.register(TOKEN)
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("t").error("request failed for %s", TOKEN)
    assert TOKEN not in caplog.text and "***" in caplog.text


def test_git_env_keeps_token_off_argv_and_disk(tmp_path):
    from archimeter.resolve.github import git_env
    env = git_env(TOKEN)
    values = " ".join(env.values())
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    keys = [v for k, v in env.items() if k.startswith("GIT_CONFIG_KEY_")]
    assert "core.hooksPath" in keys and "http.https://github.com/.extraheader" in keys  # scoped to github.com only
    assert TOKEN not in values  # carried base64-encoded inside the header, never raw
    import subprocess, base64
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    cfg = (repo / ".git" / "config").read_text()
    assert "extraheader" not in cfg.lower() and base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode() not in cfg


@respx.mock
def test_whoami_and_status_do_not_reveal_token(monkeypatch, capsys):
    from archimeter.auth import cli
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    respx.get("https://api.github.com/user").mock(return_value=httpx.Response(200, json={"login": "octo"}, headers={"x-oauth-scopes": "repo"}))
    cli.status()
    out = capsys.readouterr().out
    assert "octo" in out and "environment:GITHUB_TOKEN" in out and TOKEN not in out


@respx.mock
def test_login_with_token_validates_before_storing(monkeypatch, memkeyring, capsys):
    import io, sys
    from archimeter.auth import cli
    monkeypatch.setattr(sys, "stdin", io.StringIO(TOKEN + "\n"))
    respx.get("https://api.github.com/user").mock(return_value=httpx.Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(AuthError, match="rejected"):
        cli.login(with_token=True, client_id=None, config=None)
    assert store.load_token() is None  # nothing stored for a bad token
    monkeypatch.setattr(sys, "stdin", io.StringIO(TOKEN + "\n"))
    respx.get("https://api.github.com/user").mock(return_value=httpx.Response(200, json={"login": "octo"}))
    cli.login(with_token=True, client_id=None, config=None)
    assert store.load_token() == TOKEN and TOKEN not in capsys.readouterr().out
