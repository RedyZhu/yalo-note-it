"""Adapter for the locally verified Codex desktop rollout format (0.153.4)."""
import datetime as dt
import re


class ArchiveError(Exception):
    pass


EXCLUDED_TOP = {'world_state', 'turn_context', 'token_usage_record'}
EXCLUDED_EVENTS = {'task_started', 'task_complete', 'thread_settings_applied',
                   'token_count', 'item_completed'}
INTERNAL_USER_KINDS = {'plugins.recommendations', 'agents_md.instructions',
                       'environments.environment_context'}
VISIBLE_USER_KINDS = {'user.text', 'user.image'}
TRIGGERS = (
    re.compile(r'亚楼.*?记一下', re.DOTALL),
    re.compile(r'\byalo\b.*?\bnote\s+it\b', re.DOTALL | re.IGNORECASE),
)


def is_trigger(text):
    return isinstance(text, str) and any(pattern.search(text) for pattern in TRIGGERS)


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', value):
        raise ArchiveError('Missing or unsupported UTC timestamp')
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ArchiveError('Invalid timestamp') from exc


def require(obj, fields):
    if not isinstance(obj, dict) or any(k not in obj for k in fields):
        raise ArchiveError('Unsupported structure; required fields: ' + ', '.join(fields))


def visible_user(payload):
    meta = payload.get('internal_chat_message_metadata_passthrough')
    require(meta, ['content_item_kinds'])
    kinds = meta['content_item_kinds']
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) for k in kinds):
        raise ArchiveError('Unsupported user provenance')
    if set(kinds) <= INTERNAL_USER_KINDS:
        return False
    if set(kinds) <= VISIBLE_USER_KINDS:
        return True
    raise ArchiveError('Unknown or mixed user provenance; cannot preserve whole line safely')


def validate_content(content):
    if not isinstance(content, list) or not content:
        raise ArchiveError('Missing message content')
    for block in content:
        require(block, ['type'])
        kind = block['type']
        if kind in {'input_text', 'output_text'}:
            if not isinstance(block.get('text'), str):
                raise ArchiveError('Invalid text block')
        elif kind == 'input_image':
            if not isinstance(block.get('image_url'), str):
                raise ArchiveError('Invalid image reference')
        else:
            raise ArchiveError('Unsupported content type: ' + str(kind))


def validate_function_output(output):
    if isinstance(output, str):
        return
    if not isinstance(output, list) or not output:
        raise ArchiveError('Unsupported function output')
    for block in output:
        require(block, ['type', 'text'])
        if block['type'] != 'input_text' or not isinstance(block['text'], str):
            raise ArchiveError('Unsupported function output content')


def keep_record(record, keep_meta=False):
    require(record, ['timestamp', 'type', 'payload'])
    timestamp(record['timestamp'])
    kind, payload = record['type'], record['payload']
    if not isinstance(payload, dict):
        raise ArchiveError('Unsupported payload')
    if kind == 'session_meta':
        return keep_meta
    if kind in EXCLUDED_TOP:
        return False
    if kind == 'event_msg':
        if payload.get('type') not in EXCLUDED_EVENTS:
            raise ArchiveError('Unknown event: ' + str(payload.get('type')))
        return False
    if kind != 'response_item':
        raise ArchiveError('Unknown record type: ' + str(kind))
    item = payload.get('type')
    if item == 'reasoning':
        return False
    if item == 'message':
        require(payload, ['id', 'role', 'content'])
        role = payload['role']
        if role in {'system', 'developer'}:
            return False
        if role == 'user':
            if not visible_user(payload):
                return False
        elif role == 'assistant':
            phase = payload.get('phase', payload.get('channel'))
            if phase in {'analysis', 'reasoning'}:
                return False
            if phase not in {'commentary', 'final', 'final_answer'}:
                raise ArchiveError('Unknown assistant visibility: ' + str(phase))
        else:
            raise ArchiveError('Unknown message role: ' + str(role))
        validate_content(payload['content'])
        return True
    if item == 'function_call':
        require(payload, ['id', 'call_id', 'name', 'arguments'])
        if not isinstance(payload['arguments'], str):
            raise ArchiveError('Unsupported function arguments')
        return True
    if item == 'function_call_output':
        require(payload, ['id', 'call_id', 'output'])
        validate_function_output(payload['output'])
        return True
    if item == 'custom_tool_call':
        require(payload, ['id', 'call_id', 'name', 'input', 'status'])
        return True
    if item == 'custom_tool_call_output':
        require(payload, ['id', 'call_id', 'output'])
        validate_content(payload['output'])
        return True
    raise ArchiveError('Unknown response item: ' + str(item))


def user_text(record):
    p = record['payload']
    if record['type'] != 'response_item' or p.get('role') != 'user':
        return None
    if not visible_user(p):
        return None
    validate_content(p['content'])
    if any(c['type'] != 'input_text' for c in p['content']):
        return None
    return ''.join(c['text'] for c in p['content'])
