"""Local, byte-preserving Codex session archive. Python standard library only."""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

from codex_format import ArchiveError, is_trigger, keep_record, timestamp, user_text


@dataclass(frozen=True)
class SourceSegment:
    path: Path
    end_byte_offset: int | None = None


@dataclass(frozen=True)
class SessionSource:
    segments: tuple[SourceSegment, ...]

    def __str__(self):
        return ' -> '.join(str(segment.path) for segment in self.segments)


def codex_home():
    return Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).resolve()


def current_id():
    ids = {os.environ[k] for k in ('CODEX_SESSION_ID', 'CODEX_THREAD_ID') if os.environ.get(k)}
    if len(ids) != 1:
        raise ArchiveError('Current Session ID missing or environment IDs disagree')
    value = ids.pop()
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except ValueError as exc:
        raise ArchiveError('Invalid current Session ID') from exc
    return value


def read_metadata(path, session_id):
    with path.open('rb') as handle:
        try:
            meta = json.loads(handle.readline().decode('utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise ArchiveError('Invalid source metadata') from exc
    payload = meta.get('payload', {})
    if (meta.get('type') != 'session_meta' or payload.get('session_id') != session_id
            or payload.get('id', session_id) != session_id):
        raise ArchiveError('Source session_meta does not match current Session ID')
    timestamp(meta.get('timestamp'))
    return meta


def validate_history_boundary(path, end_ordinal, end_offset):
    if not isinstance(end_ordinal, int) or end_ordinal <= 0:
        return False
    if not isinstance(end_offset, int) or end_offset <= 0:
        return False
    size = path.stat().st_size
    if end_offset > size:
        return False
    with path.open('rb') as handle:
        prefix = handle.read(end_offset)
    if len(prefix) != end_offset or not prefix.endswith(b'\n'):
        return False
    lines = prefix.splitlines()
    if len(lines) != end_ordinal:
        return False
    try:
        last = json.loads(lines[-1].decode('utf-8'))
    except (ValueError, UnicodeError):
        return False
    return last.get('ordinal') == end_ordinal - 1


def locate_source(session_id, home):
    candidates = []
    for folder in ('sessions', 'archived_sessions'):
        root = home / folder
        if root.exists():
            candidates.extend(root.rglob('*' + session_id + '*.jsonl'))
    if not candidates:
        raise ArchiveError('No source for current Session')
    sources = [path.resolve() for path in candidates]
    metadata = {path: read_metadata(path, session_id) for path in sources}
    if len(sources) == 1:
        return sources[0]

    parents = {}
    for child, meta in metadata.items():
        base = meta['payload'].get('history_base')
        if base is None:
            continue
        if not isinstance(base, dict) or base.get('thread_id') != session_id:
            raise ArchiveError('Invalid history_base metadata')
        end_ordinal = base.get('end_ordinal_exclusive')
        end_offset = base.get('end_byte_offset')
        matches = [path for path in sources if path != child
                   and validate_history_boundary(path, end_ordinal, end_offset)]
        if len(matches) != 1:
            raise ArchiveError('History base does not resolve to exactly one source')
        parents[child] = (matches[0], end_offset)

    leaves = [path for path in sources if path not in {parent for parent, _ in parents.values()}]
    if len(leaves) != 1:
        raise ArchiveError(f'Expected one active source branch; found {len(leaves)}')

    chain = [SourceSegment(leaves[0])]
    current = leaves[0]
    seen = set()
    while current in parents:
        if current in seen:
            raise ArchiveError('Cycle in source history')
        seen.add(current)
        parent, offset = parents[current]
        chain.append(SourceSegment(parent, offset))
        current = parent
    chain.reverse()
    if {segment.path for segment in chain} != set(sources):
        raise ArchiveError('Unrelated source candidates remain after history reconstruction')
    return SessionSource(tuple(chain))


def read_records(source, stop_id=None):
    records = []
    segments = source.segments if isinstance(source, SessionSource) else (SourceSegment(source),)
    for segment in segments:
        with segment.path.open('rb') as handle:
            data = handle.read() if segment.end_byte_offset is None else handle.read(segment.end_byte_offset)
        for number, raw in enumerate(data.splitlines(keepends=True), 1):
            if not raw.endswith(b'\n'):
                # An active rollout can have an unfinished final write. Never archive it.
                break
            try:
                row = json.loads(raw.decode('utf-8'))
                if not isinstance(row, dict):
                    raise ValueError()
            except (ValueError, UnicodeError) as exc:
                raise ArchiveError(f'Invalid source JSON/UTF-8 at line {number}') from exc
            records.append((raw, row))
            if stop_id and row.get('type') == 'response_item' and row.get('payload', {}).get('id') == stop_id:
                return records
    if stop_id:
        raise ArchiveError('Requested cutoff message has not been durably written to source')
    return records


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def probe(source, message_id=None):
    rows = read_records(source, message_id)
    users = [(raw, row) for raw, row in rows if user_text(row) is not None]
    if not users:
        raise ArchiveError('No visible user message available')
    raw, row = users[-1]
    if message_id and row['payload']['id'] != message_id:
        raise ArchiveError('Cutoff is not a visible text user message')
    text = user_text(row)
    return {'message_id': row['payload']['id'], 'sha256': digest(raw),
            'timestamp': row['timestamp'], 'text': text, 'is_trigger': is_trigger(text)}


def prepare(source, message_id, expected_hash, confirmed=False):
    rows = read_records(source, message_id)
    raw, last = rows[-1]
    if digest(raw) != expected_hash:
        raise ArchiveError('Cutoff message fingerprint changed')
    text = user_text(last)
    if text is None or (not confirmed and not is_trigger(text)):
        raise ArchiveError('Cutoff is not an authorized archive trigger')
    selected = []
    for number, (raw, row) in enumerate(rows, 1):
        try:
            keep = keep_record(row)
        except ArchiveError as exc:
            raise ArchiveError(f'Line {number}: {exc}') from exc
        if keep:
            selected.append((raw, row))
    if not selected or selected[-1][1]['payload'].get('id') != message_id:
        raise ArchiveError('Cutoff was not included')
    return selected, rows[0][1]['timestamp']


def config_path():
    return Path.home() / '.ai-native-brand' / 'archive-config.json'


def load_config():
    path = config_path()
    if not path.exists():
        return None
    try:
        cfg = json.loads(path.read_text(encoding='utf-8'))
        root = Path(cfg['archive_root'])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ArchiveError('Invalid archive configuration; no default fallback') from exc
    if not root.is_absolute() or not root.is_dir():
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


def check_writable(root):
    with tempfile.TemporaryFile(dir=root) as stream:
        stream.write(b'archive write check')
        stream.flush()
        os.fsync(stream.fileno())


def configure(root):
    if not root.is_absolute():
        raise ArchiveError('Archive path must be absolute')
    root = root.resolve()
    if root == codex_home() or codex_home() in root.parents:
        raise ArchiveError('Archive destination must be outside Codex source storage')
    root.mkdir(parents=True, exist_ok=True)
    check_writable(root)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_bytes(path, (json.dumps({'archive_root': str(root)}, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return root


def commit_archive(root, session_id, started, selected, asset_plan):
    check_writable(root)
    date = timestamp(started)
    target = root / 'sessions' / 'codex' / f'{date.year:04}-{date.month:02}' / session_id
    target.mkdir(parents=True, exist_ok=True)
    lock = target / '.archive.lock'
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ArchiveError('Archive already locked; investigate an interrupted/concurrent write') from exc
    created = []
    try:
        os.close(lock_fd)
        from session_assets import copy_assets
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    config = sub.add_parser('configure')
    config.add_argument('--root', type=Path, required=True)
    config.add_argument('--user-confirmed', action='store_true', required=True)
    inspect = sub.add_parser('probe')
    inspect.add_argument('--message-id')
    for verb in ('check', 'archive'):
        p = sub.add_parser(verb)
        p.add_argument('--message-id', required=True)
        p.add_argument('--sha256', required=True)
        p.add_argument('--confirmed', action='store_true', help='Only after explicit user archive confirmation')
    args = parser.parse_args()
    try:
        if args.command == 'status':
            root = load_config()
            result = {'configured': root is not None, 'archive_root': str(root) if root else None}
        elif args.command == 'configure':
            result = {'archive_root': str(configure(args.root))}
        else:
            sid = current_id()
            source = locate_source(sid, codex_home())
            if args.command == 'probe':
                result = {'session_id': sid, 'source': str(source), **probe(source, args.message_id)}
            else:
                selected, started = prepare(source, args.message_id, args.sha256, args.confirmed)
                from session_assets import plan_assets
                assets = plan_assets(selected, codex_home())
                result = {'session_id': sid, 'records': len(selected), 'assets': len(assets), 'cutoff': args.message_id}
                if args.command == 'archive':
                    root = load_config()
                    if root is None:
                        raise ArchiveError('Archive not configured; ask user to confirm destination first')
                    result['archive'] = str(commit_archive(root, sid, started, selected, assets))
                else:
                    result['read_only'] = True
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ArchiveError, OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
