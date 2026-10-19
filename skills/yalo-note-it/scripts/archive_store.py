"""Archive configuration, migration, and durable session storage."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from archive_errors import ArchiveError
from archive_model import MANIFEST_SCHEMA
from runtime_context import codex_home
from session_assets import copy_assets


ARCHIVE_FOLDER_NAME = 'YaloNote'
LOCATOR_NAME = '.yalo-note-location.json'


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
    path.parent.mkdir(parents=True, exist_ok=True)
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


def check_writable(root):
    with tempfile.TemporaryFile(dir=root) as stream:
        stream.write(b'archive write check')
        stream.flush()
        os.fsync(stream.fileno())


def locator_bytes(root):
    return (json.dumps({
        'schemaVersion': 1,
        'kind': 'yalo-note-archive-root',
        'archive_root': str(root.resolve()),
    }, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def write_locator(root):
    atomic_bytes(root / LOCATOR_NAME, locator_bytes(root))


def copy_archive_tree(source, destination):
    if source == destination or source in destination.parents or destination in source.parents:
        raise ArchiveError('Old and new archive directories must not contain each other')
    for path in source.rglob('*'):
        if path.is_symlink():
            raise ArchiveError(f'Archive migration does not follow symbolic links: {path}')
        relative = path.relative_to(source)
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
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if existing is not None and existing != root and migrate_existing:
        copy_archive_tree(existing, root)
        write_locator(root)
        atomic_bytes(path, (json.dumps({'archive_root': str(root)}, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        try:
            shutil.rmtree(existing)
        except OSError as exc:
            raise ArchiveError(
                f'Archive migrated and new path activated, but old directory cleanup failed: {existing}'
            ) from exc
        return root
    write_locator(root)
    atomic_bytes(path, (json.dumps({'archive_root': str(root)}, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return root


def _json_bytes(value):
    return (json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2,
    ) + '\n').encode('utf-8')


def _sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _manifest(package, stage):
    files = {}
    for path in sorted(stage.rglob('*')):
        if path.is_file():
            files[path.relative_to(stage).as_posix()] = {
                'sha256': _sha256(path),
                'bytes': path.stat().st_size,
            }
    return {
        'schema': MANIFEST_SCHEMA,
        'source': package.source,
        'thread': {
            'id': package.thread_id,
            'sessionId': package.session_id,
            'startedAt': package.started_at.isoformat().replace('+00:00', 'Z'),
        },
        'sourceInfo': package.source_info,
        'cutoff': {
            'itemId': package.cutoff_id,
            'sha256': package.cutoff_sha256,
        },
        'history': {
            'itemsView': package.items_view,
            'complete': package.complete,
            'turns': package.turn_count,
            'records': package.record_count,
        },
        'files': files,
    }


def commit_archive(root, package):
    check_writable(root)
    write_locator(root)
    parent = root / 'sessions' / 'codex' / (
        f'{package.started_at.year:04}-{package.started_at.month:02}'
    )
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / package.thread_id
    lock = parent / f'.{package.thread_id}.archive.lock'
    legacy_lock = target / '.archive.lock'
    if legacy_lock.exists():
        raise ArchiveError('Archive already locked; investigate an interrupted/concurrent write')
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ArchiveError('Archive already locked; investigate an interrupted/concurrent write') from exc
    stage = None
    backup = None
    try:
        os.close(lock_fd)
        stage = Path(tempfile.mkdtemp(
            prefix=f'.{package.thread_id}-', dir=parent))
        created = []
        copy_assets(stage, package.assets, created)
        atomic_bytes(stage / 'thread.json', _json_bytes(package.document))
        if package.raw_evidence is not None:
            for line in package.raw_evidence.splitlines():
                json.loads(line.decode('utf-8'))
            atomic_bytes(stage / 'raw' / 'session.jsonl', package.raw_evidence)
        atomic_bytes(stage / 'manifest.json', _json_bytes(_manifest(package, stage)))

        if target.exists():
            backup = Path(tempfile.mkdtemp(
                prefix=f'.{package.thread_id}-backup-', dir=parent))
            backup.rmdir()
            os.replace(target, backup)
        try:
            os.replace(stage, target)
        except Exception:
            if backup is not None and backup.exists() and not target.exists():
                os.replace(backup, target)
                backup = None
            raise
        if backup is not None:
            shutil.rmtree(backup)
            backup = None
        return target / 'thread.json'
    except Exception:
        if stage is not None and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    finally:
        lock.unlink(missing_ok=True)
