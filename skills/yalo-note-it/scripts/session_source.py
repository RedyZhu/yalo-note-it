"""Locate, validate, and prepare Codex session source records."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import uuid

from archive_errors import ArchiveError
from archive_model import ArchivePackage, ArchiveRecord, THREAD_SCHEMA
from archive_trigger import is_trigger
from codex_format import keep_record, reference_source, timestamp, user_text
from runtime_context import RuntimeIdentity
from session_assets import plan_assets


@dataclass(frozen=True)
class SourceSegment:
    path: Path
    end_byte_offset: int | None = None


@dataclass(frozen=True)
class SessionSource:
    segments: tuple[SourceSegment, ...]

    def __str__(self):
        return ' -> '.join(str(segment.path) for segment in self.segments)


def decode_record(raw, path, line_number):
    try:
        row = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise ArchiveError(f'Invalid source JSON/UTF-8 at {path}:{line_number}') from exc
    if not isinstance(row, dict):
        raise ArchiveError(f'Invalid source record at {path}:{line_number}; expected JSON object')
    if not isinstance(row.get('payload'), dict):
        raise ArchiveError(f'Invalid source payload at {path}:{line_number}; expected JSON object')
    return row


def _identity(value):
    return value if isinstance(value, RuntimeIdentity) else RuntimeIdentity(value, value)


def read_metadata(path, identity):
    identity = _identity(identity)
    with path.open('rb') as handle:
        meta = decode_record(handle.readline(), path, 1)
    payload = meta['payload']
    source_id = payload.get('id')
    if (meta.get('type') != 'session_meta'
            or not isinstance(source_id, str) or not source_id
            or (identity.session_id is not None
                and payload.get('session_id') != identity.session_id)
            or (identity.session_id is None and source_id != identity.thread_id)):
        raise ArchiveError(f'Source session_meta does not match current runtime identity: {path}')
    try:
        timestamp(meta.get('timestamp'))
    except ArchiveError as exc:
        raise ArchiveError(f'Invalid source metadata at {path}: {exc}') from exc
    return meta


def resolve_history_boundary(path, end_ordinal, end_offset, allow_ordinal_fallback=False):
    if type(end_ordinal) is not int or end_ordinal <= 0:
        return None
    if type(end_offset) is not int or end_offset <= 0:
        return None
    expected = None
    actual_offset = None
    with path.open('rb') as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.endswith(b'\n'):
                return None
            row = decode_record(raw, path, line_number)
            ordinal = row.get('ordinal')
            if type(ordinal) is not int or ordinal < 0:
                return None
            if expected is None:
                expected = ordinal
            if ordinal != expected or ordinal >= end_ordinal:
                return None
            expected += 1
            if expected == end_ordinal:
                actual_offset = handle.tell()
                break
    if actual_offset is None:
        return None
    if actual_offset == end_offset:
        return actual_offset
    return actual_offset if allow_ordinal_fallback else None


def source_thread_id(path):
    identifiers = re.findall(
        r'(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])',
        path.stem,
        re.IGNORECASE,
    )
    if not identifiers:
        raise ArchiveError(f'Source filename has no thread ID: {path.name}')
    return identifiers[-1].lower()


def checked_user_text(row, context):
    try:
        return user_text(row)
    except ArchiveError as exc:
        raise ArchiveError(f'{context}: {exc}') from exc
    except (AttributeError, KeyError, TypeError) as exc:
        raise ArchiveError(f'{context}: invalid user message structure') from exc


def normalized_replayed_user_text(row, context='Replayed source record'):
    text = checked_user_text(row, context)
    if text is None:
        return None
    return text.replace('\r\n', '\n').rstrip('\n')


def read_records(source, stop_id=None):
    records = []
    for segment in source.segments:
        with segment.path.open('rb') as handle:
            data = handle.read() if segment.end_byte_offset is None else handle.read(segment.end_byte_offset)
        for line_number, raw in enumerate(data.splitlines(keepends=True), 1):
            if not raw.endswith(b'\n'):
                # An active rollout can have an unfinished final write. Never archive it.
                break
            row = decode_record(raw, segment.path, line_number)
            records.append((raw, row))
            if (stop_id and row.get('type') == 'response_item'
                    and row['payload'].get('id') == stop_id):
                return records
    if stop_id:
        raise ArchiveError('Requested cutoff message has not been durably written to source')
    return records


def resolve_replayed_prefix(paths):
    """Resolve a stale offline-submit file only when one rollout strictly contains it."""
    retained = {}
    for path in paths:
        source = SessionSource((SourceSegment(path),))
        rows = []
        for line_number, (_, row) in enumerate(read_records(source), 1):
            try:
                keep = keep_record(row)
            except ArchiveError as exc:
                raise ArchiveError(f'{path}:{line_number}: {exc}') from exc
            if keep:
                rows.append(row)
        retained[path] = rows

    supersets = []
    for candidate, candidate_rows in retained.items():
        if not candidate_rows:
            continue
        supersedes_all = True
        for other, other_rows in retained.items():
            if other == candidate:
                continue
            if not other_rows or len(candidate_rows) <= len(other_rows):
                supersedes_all = False
                break
            replayed = [normalized_replayed_user_text(
                row, f'{other}: retained record {number}'
            ) for number, row in enumerate(other_rows, 1)]
            if any(text is None for text in replayed):
                supersedes_all = False
                break
            candidate_prefix = [normalized_replayed_user_text(
                row, f'{candidate}: retained record {number}'
            ) for number, row in enumerate(candidate_rows[:len(other_rows)], 1)]
            if candidate_prefix != replayed:
                supersedes_all = False
                break
        if supersedes_all:
            supersets.append(candidate)
    return supersets[0] if len(supersets) == 1 else None


def locate_source(identity, home):
    identity = _identity(identity)
    candidates = set()
    identifiers = {identity.thread_id}
    if identity.session_id is not None:
        identifiers.add(identity.session_id)
    for folder in ('sessions', 'archived_sessions'):
        root = home / folder
        if root.exists():
            for identifier in identifiers:
                candidates.update(root.rglob('*' + identifier + '*.jsonl'))
    if not candidates:
        raise ArchiveError('No rollout fallback source for current Thread')
    sources = sorted(path.resolve() for path in candidates)
    metadata = {path: read_metadata(path, identity) for path in sources}
    direct = [
        path for path in sources
        if metadata[path]['payload'].get('id') == identity.thread_id
    ]
    if (identity.session_id is not None
            and identity.session_id != identity.thread_id
            and len(direct) == 1
            and metadata[direct[0]]['payload'].get('history_base') is None):
        return SessionSource((SourceSegment(direct[0]),))
    if len(sources) == 1:
        if (metadata[sources[0]]['payload'].get('id') != identity.thread_id
                and source_thread_id(sources[0]) != identity.thread_id):
            raise ArchiveError('Rollout source does not match current Thread ID')
        return SessionSource((SourceSegment(sources[0]),))

    source_ids = {path: source_thread_id(path) for path in sources}

    parents = {}
    for child, meta in metadata.items():
        base = meta['payload'].get('history_base')
        if base is None:
            continue
        if not isinstance(base, dict):
            raise ArchiveError(f'Invalid history_base metadata: {child}')
        parent_thread_id = base.get('thread_id')
        try:
            parent_thread_id = str(uuid.UUID(parent_thread_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ArchiveError(f'Invalid history_base metadata: {child}') from exc
        end_ordinal = base.get('end_ordinal_exclusive')
        end_offset = base.get('end_byte_offset')
        allow_ordinal_fallback = meta['payload'].get('history_mode') == 'paginated'
        matches = []
        for path in sources:
            if path == child or source_ids[path] != parent_thread_id:
                continue
            resolved_offset = resolve_history_boundary(
                path,
                end_ordinal,
                end_offset,
                allow_ordinal_fallback=allow_ordinal_fallback,
            )
            if resolved_offset is not None:
                matches.append((path, resolved_offset))
        if len(matches) != 1:
            raise ArchiveError(f'History base does not resolve to exactly one source: {child}')
        parents[child] = matches[0]

    if not parents:
        replayed_source = resolve_replayed_prefix(sources)
        if replayed_source is not None:
            return SessionSource((SourceSegment(replayed_source),))

    leaves = [path for path in sources if path not in {parent for parent, _ in parents.values()}]
    if len(leaves) != 1:
        raise ArchiveError(f'Expected one active source branch; found {len(leaves)}')
    leaf_meta = metadata[leaves[0]]['payload']
    if (leaf_meta.get('id') != identity.thread_id
            and source_thread_id(leaves[0]) != identity.thread_id):
        raise ArchiveError('Active rollout branch does not match current Thread ID')

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


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def stable_cutoff_id(row):
    identifier = row['payload'].get('id')
    if not isinstance(identifier, str) or not identifier:
        raise ArchiveError('Latest visible user message has no stable cutoff ID')
    return identifier


def probe(source, message_id=None):
    rows = read_records(source, message_id)
    users = []
    for number, (raw, row) in enumerate(rows, 1):
        text = checked_user_text(row, f'Source record {number}')
        if text is not None:
            users.append((raw, row, text))
    if not users:
        raise ArchiveError('No visible user message available')
    raw, row, text = users[-1]
    identifier = stable_cutoff_id(row)
    if message_id and identifier != message_id:
        raise ArchiveError('Cutoff is not a visible text user message')
    return {'message_id': identifier, 'sha256': digest(raw),
            'timestamp': row['timestamp'], 'text': text, 'is_trigger': is_trigger(text)}


def prepare(source, message_id, expected_hash, confirmed=False):
    rows = read_records(source, message_id)
    raw, last = rows[-1]
    if digest(raw) != expected_hash:
        raise ArchiveError('Cutoff message fingerprint changed')
    text = checked_user_text(last, 'Cutoff record')
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
    return selected, timestamp(rows[0][1]['timestamp'])


def prepare_package(source, identity, message_id, expected_hash, home,
                    confirmed=False):
    """Convert the strict rollout adapter result into the neutral archive model."""
    identity = _identity(identity)
    selected, started = prepare(source, message_id, expected_hash, confirmed)
    all_rows = read_records(source, message_id)
    meta = all_rows[0][1]
    payload = meta['payload']
    references = []
    for _, row in selected:
        candidate = reference_source(row)
        if candidate is not None:
            references.append(candidate)
    assets = plan_assets(references, home)
    cli_version = payload.get('cli_version')
    if cli_version is not None and not isinstance(cli_version, str):
        raise ArchiveError('Invalid rollout cli_version metadata')
    session_id = identity.session_id or payload.get('session_id') or identity.thread_id
    document = {
        'schema': THREAD_SCHEMA,
        'source': 'rollout-jsonl-fallback',
        'thread': {
            'id': identity.thread_id,
            'sessionId': session_id,
            'createdAt': int(started.timestamp()),
            'cliVersion': cli_version,
        },
        'turns': [],
        'fallback': {
            'rawEvidence': 'raw/session.jsonl',
            'recordCount': len(selected),
            'sourceSegments': [str(segment.path) for segment in source.segments],
        },
    }
    records = tuple(
        ArchiveRecord(
            row['payload'].get('id') or f'ordinal-{index}',
            row['type'],
            row,
        )
        for index, (_, row) in enumerate(selected)
    )
    return ArchivePackage(
        thread_id=identity.thread_id,
        session_id=session_id,
        started_at=started,
        cutoff_id=message_id,
        cutoff_sha256=expected_hash,
        document=document,
        source='rollout-jsonl-fallback',
        source_info={
            'contract': 'codex-rollout-jsonl',
            'cliVersion': cli_version,
        },
        turn_count=0,
        records=records,
        assets=tuple(assets),
        raw_evidence=b''.join(raw for raw, _ in selected),
    )
