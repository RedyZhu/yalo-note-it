"""Shared error contract for the Yalo archive application."""


class ArchiveError(Exception):
    """A supported, user-reportable archive validation failure."""


class UnsupportedAppServerError(ArchiveError):
    """The installed Codex explicitly lacks the required App Server contract."""
