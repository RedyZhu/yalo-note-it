"""Resolve the Codex runtime identity and storage location."""
import os
from pathlib import Path
import uuid

from archive_errors import ArchiveError


def codex_home():
    return Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).resolve()


def current_id():
    ids = {os.environ[key] for key in ('CODEX_SESSION_ID', 'CODEX_THREAD_ID')
           if os.environ.get(key)}
    if len(ids) != 1:
        raise ArchiveError('Current Session ID missing or environment IDs disagree')
    value = ids.pop()
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except ValueError as exc:
        raise ArchiveError('Invalid current Session ID') from exc
    return value
