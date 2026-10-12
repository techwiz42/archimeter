"""OS-keychain token storage. Tokens are never written to files."""
from __future__ import annotations

from ..errors import AuthError

SERVICE = "archimeter"
ACCOUNT = "github.com"


def _kr():
    try:
        import keyring
        from keyring.errors import KeyringError  # noqa: F401
        return keyring
    except ImportError as e:  # pragma: no cover
        raise AuthError(f"keyring is not installed: {e}") from None


def _guard(fn):
    def wrapper(*a, **k):
        kr = _kr()
        from keyring.errors import KeyringError, NoKeyringError
        try:
            backend = kr.get_keyring()
            if backend.__class__.__module__.startswith("keyring.backends.fail"):
                raise NoKeyringError()
            return fn(kr, *a, **k)
        except (NoKeyringError, KeyringError) as e:
            raise AuthError(
                "no usable OS keychain backend is available (e.g. a headless server without a Secret Service). "
                "archimeter never stores tokens in plain files. Use the GITHUB_TOKEN or ARCHIMETER_GITHUB_TOKEN "
                "environment variable, or an authenticated `gh` CLI instead.") from e
    return wrapper


@_guard
def save_token(kr, token: str) -> None:
    kr.set_password(SERVICE, ACCOUNT, token)


@_guard
def load_token(kr) -> str | None:
    return kr.get_password(SERVICE, ACCOUNT)


@_guard
def delete_token(kr) -> bool:
    try:
        kr.delete_password(SERVICE, ACCOUNT)
        return True
    except Exception as e:
        from keyring.errors import PasswordDeleteError
        if isinstance(e, PasswordDeleteError):
            return False
        raise
