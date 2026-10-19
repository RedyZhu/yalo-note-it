"""Map Codex App Server thread history into the neutral archive model."""
import copy
import datetime as dt
import hashlib
import json
import time

from archive_errors import ArchiveError
from archive_model import (
    ArchivePackage,
    ArchiveRecord,
    ReferenceContent,
    ReferenceSource,
    THREAD_SCHEMA,
)
from archive_trigger import is_trigger
from runtime_context import RuntimeIdentity
from session_assets import plan_assets


INTERNAL_ITEM_TYPES = {'hookPrompt', 'reasoning', 'contextCompaction'}
VISIBLE_ITEM_TYPES = {
    'userMessage',
    'agentMessage',
    'plan',
    'commandExecution',
    'fileChange',
    'mcpToolCall',
    'dynamicToolCall',
    'collabAgentToolCall',
    'subAgentActivity',
    'webSearch',
    'imageView',
    'sleep',
    'imageGeneration',
    'enteredReviewMode',
    'exitedReviewMode',
}
USER_CONTENT_TYPES = {
    'text', 'image', 'localImage', 'audio', 'localAudio', 'skill', 'mention'
}


def canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')


def item_digest(turn_id, item):
    return hashlib.sha256(canonical_bytes({'turnId': turn_id, 'item': item})).hexdigest()


def _required_string(value, context):
    if not isinstance(value, str) or not value:
        raise ArchiveError(f'Invalid {context}')
    return value


def _user_content(item):
    content = item.get('content')
    if not isinstance(content, list):
        raise ArchiveError('Invalid App Server userMessage content')
    for block in content:
        if not isinstance(block, dict) or block.get('type') not in USER_CONTENT_TYPES:
            raise ArchiveError('Unknown App Server userMessage content type')
        kind = block['type']
        field = 'text' if kind == 'text' else 'url' if kind in {'image', 'audio'} else 'path'
        if kind in {'skill', 'mention'}:
            field = 'path'
        _required_string(block.get(field), f'userMessage {kind} value')
        if kind in {'skill', 'mention'}:
            _required_string(block.get('name'), f'userMessage {kind} name')
    return content


def user_text(item):
    if item.get('type') != 'userMessage':
        return None
    texts = [block['text'] for block in _user_content(item) if block['type'] == 'text']
    return '\n'.join(texts) if texts else None


def visible_item(item):
    if not isinstance(item, dict):
        raise ArchiveError('Invalid App Server thread item')
    _required_string(item.get('id'), 'App Server thread item ID')
    kind = item.get('type')
    if kind == 'userMessage':
        _user_content(item)
    if kind in INTERNAL_ITEM_TYPES:
        return False
    if kind not in VISIBLE_ITEM_TYPES:
        raise ArchiveError(f'Unknown App Server thread item type: {kind!r}')
    return True


def reference_source(item):
    """Return explicit attachment-bearing values from one validated API item."""
    record_id = item['id']
    kind = item['type']
    content = []
    if kind == 'userMessage':
        for block in _user_content(item):
            block_type = block['type']
            if block_type == 'text':
                content.append(ReferenceContent(block['text']))
            elif block_type in {'image', 'localImage'}:
                content.append(ReferenceContent(
                    block.get('url', block.get('path')), 'image'))
            elif block_type in {'audio', 'localAudio'}:
                content.append(ReferenceContent(
                    block.get('url', block.get('path')), 'audio'))
    elif kind in {'agentMessage', 'plan'} and isinstance(item.get('text'), str):
        content.append(ReferenceContent(item['text']))
    elif kind == 'commandExecution' and isinstance(item.get('aggregatedOutput'), str):
        content.append(ReferenceContent(item['aggregatedOutput']))
    elif kind == 'mcpToolCall' and item.get('result') is not None:
        content.append(ReferenceContent(json.dumps(
            item['result'], ensure_ascii=False, separators=(',', ':'))))
    elif kind == 'dynamicToolCall' and isinstance(item.get('contentItems'), list):
        for block in item['contentItems']:
            if not isinstance(block, dict):
                raise ArchiveError('Invalid dynamicToolCall content item')
            block_type = block.get('type')
            if block_type == 'inputText' and isinstance(block.get('text'), str):
                content.append(ReferenceContent(block['text']))
            elif block_type == 'inputImage' and isinstance(block.get('imageUrl'), str):
                content.append(ReferenceContent(block['imageUrl'], 'image'))
            elif block_type == 'inputAudio' and isinstance(block.get('audioUrl'), str):
                content.append(ReferenceContent(block['audioUrl'], 'audio'))
            else:
                raise ArchiveError('Unknown dynamicToolCall content item type')
    elif kind == 'imageView':
        content.append(ReferenceContent(
            _required_string(item.get('path'), 'imageView path'), 'image'))
    elif kind == 'imageGeneration' and item.get('savedPath') is not None:
        content.append(ReferenceContent(
            _required_string(item.get('savedPath'), 'imageGeneration savedPath'),
            'image',
        ))
    return ReferenceSource(record_id, tuple(content)) if content else None


class ThreadArchiveSource:
    """Read, validate, authorize, and package one current Codex thread."""

    def __init__(self, client, identity: RuntimeIdentity, codex_home,
                 retry_attempts=3, retry_delay=0.15):
        self.client = client
        self.identity = identity
        self.codex_home = codex_home
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay

    def probe(self, message_id=None):
        snapshot = self._snapshot_with_retry(message_id)
        cutoff = snapshot['cutoff']
        return {
            'thread_id': snapshot['thread']['id'],
            'session_id': snapshot['thread']['sessionId'],
            'source': 'codex-app-server',
            'message_id': cutoff['item']['id'],
            'sha256': item_digest(cutoff['turnId'], cutoff['item']),
            'timestamp': snapshot['timestamp'],
            'text': cutoff['text'],
            'is_trigger': is_trigger(cutoff['text']),
        }

    def prepare(self, message_id, expected_hash, confirmed=False):
        snapshot = self._snapshot_with_retry(message_id)
        cutoff = snapshot['cutoff']
        actual_hash = item_digest(cutoff['turnId'], cutoff['item'])
        if actual_hash != expected_hash:
            raise ArchiveError('Cutoff message fingerprint changed')
        if not confirmed and not is_trigger(cutoff['text']):
            raise ArchiveError('Cutoff is not an authorized archive trigger')

        reference_sources = []
        for turn in snapshot['turns']:
            for item in turn['items']:
                candidate = reference_source(item)
                if candidate is not None:
                    reference_sources.append(candidate)
        assets = plan_assets(reference_sources, self.codex_home)
        thread = snapshot['thread']
        started = dt.datetime.fromtimestamp(thread['createdAt'], dt.timezone.utc)
        document = {
            'schema': THREAD_SCHEMA,
            'source': 'codex-app-server',
            'thread': snapshot['metadata'],
            'turns': snapshot['turns'],
        }
        records = tuple(
            ArchiveRecord(item['id'], item['type'], item, turn['id'])
            for turn in snapshot['turns']
            for item in turn['items']
        )
        return ArchivePackage(
            thread_id=thread['id'],
            session_id=thread['sessionId'],
            started_at=started,
            cutoff_id=message_id,
            cutoff_sha256=actual_hash,
            document=document,
            source='codex-app-server',
            source_info={
                'contract': 'codex-app-server/v2',
                'cliVersion': thread['cliVersion'],
                'userAgent': self.client.server_info.get('userAgent'),
                'runtimeSessionId': self.identity.session_id,
            },
            turn_count=len(snapshot['turns']),
            records=records,
            assets=tuple(assets),
        )

    def _snapshot_with_retry(self, cutoff_id):
        last_error = None
        prior_cutoff = None
        for attempt in range(self.retry_attempts):
            try:
                snapshot = self._snapshot(cutoff_id)
                if cutoff_id is None and self._active_turn_missing_user(snapshot):
                    raise ArchiveError(
                        'Active turn is not yet fully visible from App Server history'
                    )
                if cutoff_id is None:
                    current_cutoff = (
                        snapshot['cutoff']['item']['id'],
                        item_digest(
                            snapshot['cutoff']['turnId'],
                            snapshot['cutoff']['item'],
                        ),
                    )
                    if prior_cutoff != current_cutoff:
                        prior_cutoff = current_cutoff
                        if attempt + 1 >= self.retry_attempts:
                            raise ArchiveError(
                                'Latest user item did not stabilize in App Server history'
                            )
                        time.sleep(self.retry_delay)
                        continue
                return snapshot
            except ArchiveError as exc:
                last_error = exc
                retryable = (
                    'cutoff message is not yet visible' in str(exc).lower()
                    or 'active turn is not yet fully visible' in str(exc).lower()
                )
                if not retryable or attempt + 1 >= self.retry_attempts:
                    raise
                time.sleep(self.retry_delay)
        raise last_error

    def _snapshot(self, cutoff_id):
        thread = self.client.read_thread(self.identity.thread_id)
        self._validate_thread(thread)
        turns = self._complete_turns(thread['id'], self.client.list_turns(thread['id']))
        selected, cutoff = self._select(turns, cutoff_id)
        metadata = copy.deepcopy(thread)
        metadata.pop('turns', None)
        cutoff_time = cutoff.get('startedAt')
        timestamp = dt.datetime.fromtimestamp(
            cutoff_time if type(cutoff_time) is int else thread['createdAt'],
            dt.timezone.utc,
        ).isoformat().replace('+00:00', 'Z')
        return {
            'thread': thread,
            'metadata': metadata,
            'turns': selected,
            'cutoff': cutoff,
            'timestamp': timestamp,
            'all_turns': turns,
        }

    def _validate_thread(self, thread):
        if thread.get('id') != self.identity.thread_id:
            raise ArchiveError('thread/read identity does not match current Thread ID')
        _required_string(thread.get('sessionId'), 'thread sessionId')
        if self.identity.session_id is not None:
            related = {
                thread.get('sessionId'),
                thread.get('parentThreadId'),
                thread.get('forkedFromId'),
            }
            if self.identity.session_id not in related:
                raise ArchiveError(
                    'thread/read identity is unrelated to current Session ID'
                )
        if type(thread.get('createdAt')) is not int or thread['createdAt'] < 0:
            raise ArchiveError('Invalid thread/read createdAt')
        _required_string(thread.get('cliVersion'), 'thread cliVersion')

    def _complete_turns(self, thread_id, turns):
        if not isinstance(turns, list):
            raise ArchiveError('Invalid thread/turns/list history')
        completed = []
        seen_turns = set()
        prior_started = None
        for turn in turns:
            if not isinstance(turn, dict):
                raise ArchiveError('Invalid App Server turn')
            turn_id = _required_string(turn.get('id'), 'App Server turn ID')
            if turn_id in seen_turns:
                raise ArchiveError('thread/turns/list returned a duplicate turn')
            seen_turns.add(turn_id)
            started = turn.get('startedAt')
            if started is not None:
                if type(started) is not int or started < 0:
                    raise ArchiveError('Invalid App Server turn startedAt')
                if prior_started is not None and started < prior_started:
                    raise ArchiveError('thread/turns/list is not in ascending order')
                prior_started = started
            hydrated = copy.deepcopy(turn)
            if turn.get('itemsView') != 'full' or not isinstance(turn.get('items'), list):
                entries = self.client.list_items(thread_id, turn_id)
                items = []
                seen_items = set()
                for entry in entries:
                    if (not isinstance(entry, dict) or entry.get('turnId') != turn_id
                            or not isinstance(entry.get('item'), dict)):
                        raise ArchiveError('thread/items/list returned inconsistent turn data')
                    item_id = _required_string(
                        entry['item'].get('id'), 'App Server thread item ID')
                    if item_id in seen_items:
                        raise ArchiveError('thread/items/list returned a duplicate item')
                    seen_items.add(item_id)
                    items.append(copy.deepcopy(entry['item']))
                hydrated['items'] = items
                hydrated['itemsView'] = 'full'
            completed.append(hydrated)
        return completed

    def _select(self, turns, cutoff_id):
        selected = []
        cutoff = None
        seen_items = set()
        latest_user = None
        for turn in turns:
            output_items = []
            for item in turn['items']:
                item_id = _required_string(item.get('id'), 'App Server thread item ID')
                if item_id in seen_items:
                    raise ArchiveError('App Server history contains a duplicate item ID')
                seen_items.add(item_id)
                if not visible_item(item):
                    continue
                copied = copy.deepcopy(item)
                output_items.append(copied)
                text = user_text(item)
                if text is not None:
                    latest_user = {
                        'turnId': turn['id'], 'item': copied, 'text': text,
                        'startedAt': turn.get('startedAt'),
                    }
                if cutoff_id is not None and item_id == cutoff_id:
                    if text is None:
                        raise ArchiveError('Cutoff is not a visible text user message')
                    cutoff = latest_user
                    break
            if output_items:
                output_turn = copy.deepcopy(turn)
                output_turn['items'] = output_items
                output_turn['itemsView'] = 'full'
                selected.append(output_turn)
            if cutoff is not None:
                break
        if cutoff_id is not None and cutoff is None:
            raise ArchiveError('Requested cutoff message is not yet visible in App Server history')
        if cutoff_id is None:
            cutoff = latest_user
        if cutoff is None:
            raise ArchiveError('No visible user message available')
        if not selected:
            raise ArchiveError('No visible App Server history available')
        return selected, cutoff

    @staticmethod
    def _active_turn_missing_user(snapshot):
        all_turns = snapshot['all_turns']
        if not all_turns or all_turns[-1].get('status') != 'inProgress':
            return False
        return not any(
            isinstance(item, dict) and item.get('type') == 'userMessage'
            for item in all_turns[-1].get('items', [])
        )
