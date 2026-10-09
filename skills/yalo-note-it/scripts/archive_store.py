"""Archive configuration, migration, and durable session storage."""
import json
import os
from pathlib import Path
import shutil
import tempfile

from archive_errors import ArchiveError
from runtime_context import codex_home
from session_assets import copy_assets


ARCHIVE_FOLDER_NAME = 'YaloNote'
ARCHIVE_LOCATION_FILE = '.yalo-note-location.json'
ARCHIVE_LOCATION_KIND = 'yalo-note-archive-root'
ARCHIVE_LOCATION_SCHEMA_VERSION = 1


def config_path():
    return Path.home() / '.ai-native-brand' / 'archive-config.json'


def load_config(require_available=True):
    path = config_path()
    if not path.exists():
        return None
    try:
        cfg = json.loads(path.read_text(encoding='utf-8'))
        root = Path(cfg['archive_root'])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ArchiveError('Invalid archive configuration; no default fallback') from exc
    if not root.is_absolute():
        raise ArchiveError('Invalid archive configuration; no default fallback')
    if require_available and not root.is_dir():
        raise ArchiveError('Configured archive directory unavailable; no default fallback')
    return root.resolve()


def atomic_bytes(path, data):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != data:
            raise ArchiveError('Temporary write verification failed')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_archive_location(root):
    root = root.resolve()
    payload = {
        'schemaVersion': ARCHIVE_LOCATION_SCHEMA_VERSION,
        'kind': ARCHIVE_LOCATION_KIND,
        'archive_root': str(root),
    }
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    atomic_bytes(root / ARCHIVE_LOCATION_FILE, data)


def _activate_archive_root(root):
    root = root.resolve()
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps({'archive_root': str(root)}, ensure_ascii=False, indent=2)
            + '\n').encode('utf-8')
    atomic_bytes(path, data)


def check_writable(root):
    with tempfile.TemporaryFile(dir=root) as stream:
        stream.write(b'archive write check')
        stream.flush()
        os.fsync(stream.fileno())


def copy_archive_tree(source, destination):
    if source == destination or source in destination.parents or destination in source.parents:
        raise ArchiveError('Old and new archive directories must not contain each other')
    for path in source.rglob('*'):
        if path.is_symlink():
            raise ArchiveError(f'Archive migration does not follow symbolic links: {path}')
        relative = path.relative_to(source)
        if relative == Path(ARCHIVE_LOCATION_FILE):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists():
            if not target.is_file() or target.read_bytes() != path.read_bytes():
                raise ArchiveError(f'Archive migration destination conflict: {target}')
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if target.read_bytes() != path.read_bytes():
            raise ArchiveError(f'Archive migration verification failed: {target}')


def configure(base, migrate_existing=None):
    if not base.is_absolute():
        raise ArchiveError('Archive path must be absolute')
    base = base.resolve()
    root = (base / ARCHIVE_FOLDER_NAME).resolve()
    source_root = codex_home()
    if (root == source_root or source_root in root.parents
            or root in source_root.parents):
        raise ArchiveError('Archive destination must be outside Codex source storage')
    existing = load_config(require_available=False)
    if existing is not None and existing != root and migrate_existing is None:
        raise ArchiveError('Archive path change requires an explicit migrate-or-keep choice')
    if existing is not None and existing != root and (existing in root.parents or root in existing.parents):
        raise ArchiveError('Old and new archive directories must not contain each other')
    if existing is not None and existing != root and migrate_existing and not existing.is_dir():
        raise ArchiveError('Existing archive directory unavailable; migration aborted')
    root.mkdir(parents=True, exist_ok=True)
    check_writable(root)
    if existing is not None and existing != root and migrate_existing:
        copy_archive_tree(existing, root)
        write_archive_location(root)
        _activate_archive_root(root)
        try:
            shutil.rmtree(existing)
        except OSError as exc:
            raise ArchiveError(
                f'Archive migrated and new path activated, but old directory cleanup failed: {existing}'
            ) from exc
        return root
    write_archive_location(root)
    _activate_archive_root(root)
    return root


def commit_archive(root, session_id, started, selected, asset_plan):
    check_writable(root)
    write_archive_location(root)
    target = root / 'sessions' / 'codex' / f'{started.year:04}-{started.month:02}' / session_id
    target.mkdir(parents=True, exist_ok=True)
    lock = target / '.archive.lock'
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ArchiveError('Archive already locked; investigate an interrupted/concurrent write') from exc
    created = []
    try:
        os.close(lock_fd)
        copy_assets(target, asset_plan, created)
        data = b''.join(raw for raw, _ in selected)
        for line in data.splitlines():
            json.loads(line.decode('utf-8'))
        output = target / 'session.jsonl'
        atomic_bytes(output, data)
        return output
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    finally:
        assets = target / 'assets'
        if assets.is_dir() and not any(assets.iterdir()):
            assets.rmdir()
        lock.unlink(missing_ok=True)
