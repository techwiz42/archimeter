class ArchimeterError(Exception):
    """User-facing failure with an actionable message."""


class UnsafeArchiveError(ArchimeterError):
    """An archive member or layer would escape the extraction root or exceed caps."""


class AuthError(ArchimeterError):
    pass


class RepoAccessError(AuthError):
    pass
