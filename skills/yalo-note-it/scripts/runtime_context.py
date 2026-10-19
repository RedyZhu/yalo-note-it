"""Resolve the Codex runtime identity and storage location."""
import os
from dataclasses import dataclass
from pathlib import Path
import uuid

from archive_errors import ArchiveError


def codex_home():
    return Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).resolve()


def _uuid(value, label):
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (AttributeError, TypeError, ValueError) as exc:
        raise ArchiveError(f'Invalid current {label}') from exc
    return value


@dataclass(frozen=True)
class RuntimeIdentity:
    """Identifiers exposed by Codex for a thread and its session tree."""

    thread_id: str
    session_id: str | None = None


def current_identity():
    thread_id = os.environ.get('CODEX_THREAD_ID')
    if not thread_id:
        raise ArchiveError('Current Thread ID missing')
    session_id = os.environ.get('CODEX_SESSION_ID')
    return RuntimeIdentity(
        thread_id=_uuid(thread_id, 'Thread ID'),
        session_id=_uuid(session_id, 'Session ID') if session_id else None,
    )


def current_id():
    """Backward-compatible thread identifier accessor."""
    return current_identity().thread_id
