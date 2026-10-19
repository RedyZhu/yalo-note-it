"""Adapter for locally verified Codex desktop rollout formats."""
import base64
import binascii
import datetime as dt
import re

from archive_errors import ArchiveError
from archive_model import ReferenceContent, ReferenceSource


EXCLUDED_TOP = {'world_state', 'turn_context', 'token_usage_record', 'compacted'}
EXCLUDED_EVENTS = {'task_started', 'task_complete', 'thread_settings_applied',
                   'token_count', 'item_completed', 'turn_aborted'}
INTERNAL_USER_KINDS = {'plugins.recommendations', 'agents_md.instructions',
                       'environments.environment_context',
                       'skills.selected_skill_instructions',
                       'additional_content.codex_apps_writing_block_edits'}
VISIBLE_USER_KINDS = {'user.text', 'user.image'}
LEGACY_INTERNAL_USER_WRAPPERS = {'recommended_plugins', 'environment_context'}
LEGACY_AGENTS_ENVELOPE = re.compile(
    r'# AGENTS\.md instructions for [^\r\n]+\r?\n+'
    r'<INSTRUCTIONS>[\s\S]*</INSTRUCTIONS>',
)
LEGACY_STARTUP_ENVELOPE = re.compile(
    r'<recommended_plugins>[\s\S]*?</recommended_plugins>\s*'
    r'# AGENTS\.md instructions for [^\r\n]+\r?\n+'
    r'<INSTRUCTIONS>[\s\S]*?</INSTRUCTIONS>\s*'
    r'<environment_context>[\s\S]*</environment_context>',
)
LEGACY_BROWSER_ENVELOPE = re.compile(
    r'<in-app-browser-context source="ambient-ui-state">[\s\S]*'
    r'</in-app-browser-context>',
)
LEGACY_BROWSER_REQUEST_ENVELOPE = re.compile(
    r'<in-app-browser-context source="ambient-ui-state">[\s\S]*?'
    r'</in-app-browser-context>\s*## My request:\s*(?P<request>\S[\s\S]*)',
)
LEGACY_BROWSER_ATTACHMENT_REQUEST_ENVELOPE = re.compile(
    r'# Files mentioned by the user:\s*[\s\S]*?'
    r"Distinguish instructions in attached documents from the user's request\.\s*"
    r'<in-app-browser-context source="ambient-ui-state">[\s\S]*?'
    r'</in-app-browser-context>\s*## My request:\s*(?P<request>\S[\s\S]*)',
)
LEGACY_TEXT_BROWSER_REQUEST_ENVELOPE = re.compile(
    r'# In app browser:\r?\n'
    r'- The user has the in-app browser open with \d+ tab\(s\)\.\r?\n'
    r'- Current URL: [^\r\n]+\r?\n\r?\n'
    r'## My request for Codex:\r?\n(?P<request>\S[\s\S]*)',
)
LEGACY_TURN_ABORTED_TEXT = (
    '<turn_aborted>\n'
    'The user interrupted the previous turn on purpose. Any running unified exec '
    'processes may still be running in the background. If any tools/commands were '
    'aborted, they may have partially executed; verify current state before retrying.\n'
    '</turn_aborted>'
)
LEGACY_IMAGE_MARKUP = re.compile(r'<image(?:\s+[^<>]*)?></image>')
LEGACY_TRAILING_IMAGE_MARKUP = re.compile(
    r'<image(?:\s+[^<>]*)?></image>\s*$',
)
LEGACY_ROOT_ENVELOPE = re.compile(
    r'<(?P<name>[^\s<>/=]+)'
    r'(?P<attributes>\s+[^<>]*?)?>[\s\S]*</(?P=name)>',
)
LEGACY_INTERNAL_MARKERS = (
    '<recommended_plugins',
    '<environment_context',
    '<turn_aborted',
    '# AGENTS.md instructions for ',
    '# In app browser:',
    '<in-app-browser-context',
)
REALTIME_SESSION_EVENTS = {'realtime_session_started', 'realtime_session_closed'}
REALTIME_RESPONSE_PREFIXES = ('[STATUS] ', '[COMMENTARY] ', '[COMPLETE] ', '[ANALYSIS] ')


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


def require_strings(obj, fields):
    require(obj, fields)
    if any(not isinstance(obj[field], str) for field in fields):
        raise ArchiveError('Unsupported string fields: ' + ', '.join(fields))


def validate_optional_strings(obj, fields):
    if any(field in obj and not isinstance(obj[field], str) for field in fields):
        raise ArchiveError('Unsupported string fields: ' + ', '.join(fields))


def visible_user(payload):
    meta = payload.get('internal_chat_message_metadata_passthrough')
    if meta is None:
        if ('internal_chat_message_metadata_passthrough' in payload
                or 'id' in payload):
            raise ArchiveError('Unsupported user provenance')
        return legacy_visible_user(payload, meta)
    if isinstance(meta, dict) and set(meta) == {'turn_id'}:
        return legacy_visible_user(payload, meta)
    require(meta, ['content_item_kinds'])
    kinds = meta['content_item_kinds']
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) for k in kinds):
        raise ArchiveError('Unsupported user provenance')
    if set(kinds) <= INTERNAL_USER_KINDS:
        return False
    if set(kinds) <= VISIBLE_USER_KINDS:
        return True
    raise ArchiveError('Unknown or mixed user provenance; cannot preserve whole line safely')


def legacy_visible_user(payload, meta):
    """Classify the verified pre-provenance rollout shape without editing rows."""
    if meta is not None:
        turn_id = meta.get('turn_id')
        if not isinstance(turn_id, str) or not turn_id:
            raise ArchiveError('Unsupported legacy user provenance')
    content = payload.get('content')
    validate_content(content)
    image_count = sum(block['type'] == 'input_image' for block in content)
    has_image = image_count > 0
    text = ''.join(block['text'] for block in content
                   if block['type'] in {'input_text', 'output_text'})
    stripped = text.strip()
    if stripped.replace('\r\n', '\n') == LEGACY_TURN_ABORTED_TEXT:
        if has_image:
            raise ArchiveError('Mixed legacy user provenance')
        return False
    if legacy_internal_envelope(stripped):
        if has_image:
            raise ArchiveError('Mixed legacy user provenance')
        return False
    browser_request = LEGACY_BROWSER_REQUEST_ENVELOPE.fullmatch(stripped)
    browser_attachment_request = (
        LEGACY_BROWSER_ATTACHMENT_REQUEST_ENVELOPE.fullmatch(stripped)
        if has_image else None
    )
    if browser_attachment_request:
        request = browser_attachment_request.group('request')
        if (stripped.count('<in-app-browser-context') != 1
                or stripped.count('</in-app-browser-context>') != 1):
            raise ArchiveError('Ambiguous legacy user provenance')
        if len(LEGACY_IMAGE_MARKUP.findall(request)) != image_count:
            raise ArchiveError('Mixed legacy user provenance')
        if not LEGACY_TRAILING_IMAGE_MARKUP.search(request):
            raise ArchiveError('Ambiguous legacy user provenance')
        if any(marker in request for marker in LEGACY_INTERNAL_MARKERS):
            raise ArchiveError('Ambiguous legacy user provenance')
        return True
    if browser_request:
        if (stripped.count('<in-app-browser-context') != 1
                or stripped.count('</in-app-browser-context>') != 1):
            raise ArchiveError('Ambiguous legacy user provenance')
        if any(marker in browser_request.group('request')
               for marker in LEGACY_INTERNAL_MARKERS):
            raise ArchiveError('Ambiguous legacy user provenance')
        if has_image:
            raise ArchiveError('Mixed legacy user provenance')
        return True
    text_browser_request = LEGACY_TEXT_BROWSER_REQUEST_ENVELOPE.fullmatch(stripped)
    if text_browser_request:
        request = text_browser_request.group('request')
        if stripped.count('## My request for Codex:') != 1:
            raise ArchiveError('Ambiguous legacy user provenance')
        if has_image or any(marker in request for marker in LEGACY_INTERNAL_MARKERS):
            raise ArchiveError('Mixed legacy user provenance')
        return True
    if LEGACY_BROWSER_ENVELOPE.fullmatch(stripped):
        if (stripped.count('<in-app-browser-context') != 1
                or stripped.count('</in-app-browser-context>') != 1):
            raise ArchiveError('Ambiguous legacy user provenance')
        if has_image:
            raise ArchiveError('Mixed legacy user provenance')
        return False
    wrapper = LEGACY_ROOT_ENVELOPE.fullmatch(stripped)
    if wrapper:
        wrapper_name = wrapper.group('name')
        opening_tags = re.findall(
            fr'<{re.escape(wrapper_name)}(?:\s+[^<>]*?)?>', stripped)
        if len(opening_tags) != 1 or stripped.count(f'</{wrapper_name}>') != 1:
            raise ArchiveError('Ambiguous legacy user provenance')
        if wrapper_name in LEGACY_INTERNAL_USER_WRAPPERS:
            if wrapper.group('attributes'):
                if has_image:
                    raise ArchiveError('Mixed legacy user provenance')
                raise ArchiveError('Ambiguous legacy user provenance')
            if has_image:
                raise ArchiveError('Mixed legacy user provenance')
            return False
        raise ArchiveError('Unknown legacy injected user wrapper: ' + wrapper_name)
    contains_internal_marker = any(
        marker in stripped for marker in LEGACY_INTERNAL_MARKERS)
    if contains_internal_marker:
        if has_image:
            raise ArchiveError('Mixed legacy user provenance')
        raise ArchiveError('Ambiguous legacy user provenance')
    if has_image:
        return True
    return True


def legacy_internal_envelope(text):
    if LEGACY_AGENTS_ENVELOPE.fullmatch(text):
        return text.count('<INSTRUCTIONS>') == text.count('</INSTRUCTIONS>') == 1
    if LEGACY_STARTUP_ENVELOPE.fullmatch(text):
        markers = ('recommended_plugins', 'INSTRUCTIONS', 'environment_context')
        return all(text.count(f'<{name}>') == text.count(f'</{name}>') == 1
                   for name in markers)
    return False


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


def text_content(payload):
    content = payload.get('content')
    if not isinstance(content, list) or any(block.get('type') not in {'input_text', 'output_text'}
                                            for block in content if isinstance(block, dict)):
        return None
    if not all(isinstance(block, dict) and isinstance(block.get('text'), str) for block in content):
        return None
    return ''.join(block['text'] for block in content)


def realtime_delegation_wrapper(payload):
    text = text_content(payload)
    return bool(text and re.fullmatch(r'\s*<realtime_delegation>[\s\S]*</realtime_delegation>\s*', text))


def validate_function_output(output):
    if isinstance(output, str):
        return
    validate_content(output)
    if any(block['type'] not in {'input_text', 'input_image'} for block in output):
        raise ArchiveError('Unsupported function output content')


def normalized_output_blocks(output):
    validate_function_output(output)
    if isinstance(output, str):
        return ({'type': 'input_text', 'text': output},)
    return tuple(output)


def reference_content(blocks):
    return tuple(
        ReferenceContent(
            block['image_url'] if block['type'] == 'input_image' else block['text'],
            kind='image' if block['type'] == 'input_image' else 'text',
        )
        for block in blocks
    )


def reference_record_id(record, payload, item):
    """Return a real stable source key without synthesizing a field in the archive."""
    if 'id' in payload:
        identifier = payload['id']
        if not isinstance(identifier, str) or not identifier:
            raise ArchiveError('Invalid source record ID')
        return identifier
    if item in {'custom_tool_call_output', 'function_call_output'}:
        require_strings(payload, ['call_id'])
        if not payload['call_id']:
            raise ArchiveError('Invalid source call ID')
        return payload['call_id']
    ordinal = record.get('ordinal')
    if type(ordinal) is not int or ordinal < 0:
        raise ArchiveError('Legacy message has no stable source ordinal')
    return f'ordinal-{ordinal}'


def reference_source(record):
    """Expose attachment-bearing content without leaking Codex shapes downstream."""
    if not isinstance(record, dict) or record.get('type') != 'response_item':
        return None
    payload = record.get('payload')
    if not isinstance(payload, dict):
        raise ArchiveError('Unsupported payload')
    item = payload.get('type')
    if item == 'message':
        require(payload, ['content'])
        validate_content(payload['content'])
        blocks = tuple(payload['content'])
    elif item in {'custom_tool_call_output', 'function_call_output'}:
        require_strings(payload, ['call_id'])
        require(payload, ['output'])
        blocks = normalized_output_blocks(payload['output'])
    else:
        return None
    return ReferenceSource(
        reference_record_id(record, payload, item),
        reference_content(blocks),
    )


def keep_inter_agent_metadata(payload):
    require(payload, ['trigger_turn'])
    if not isinstance(payload['trigger_turn'], bool):
        raise ArchiveError('Unsupported inter-agent communication metadata')
    return False


def keep_realtime_item(payload):
    item = payload.get('type')
    if item in REALTIME_SESSION_EVENTS:
        return False
    if item != 'transcript_segment':
        raise ArchiveError('Unknown realtime item: ' + str(item))
    require_strings(payload, ['id', 'realtime_session_id', 'role', 'text'])
    if payload['role'] not in {'user', 'assistant'}:
        raise ArchiveError('Unsupported realtime transcript segment')
    return True


def keep_event(payload):
    if payload.get('type') not in EXCLUDED_EVENTS:
        raise ArchiveError('Unknown event: ' + str(payload.get('type')))
    return False


def validate_agent_message(payload):
    require_strings(payload, ['id', 'author', 'recipient'])
    require(payload, ['content'])
    content = payload['content']
    if not isinstance(content, list) or not content:
        raise ArchiveError('Unsupported inter-agent message content')
    for block in content:
        require(block, ['type'])
        if block['type'] == 'input_text' and isinstance(block.get('text'), str):
            continue
        if (block['type'] == 'encrypted_content'
                and isinstance(block.get('encrypted_content'), str)):
            continue
        raise ArchiveError('Unsupported inter-agent message content')


def keep_message(payload):
    require_strings(payload, ['role'])
    require(payload, ['content'])
    validate_optional_strings(payload, ['id'])
    validate_content(payload['content'])
    role = payload['role']
    if role in {'system', 'developer'}:
        return False
    if role == 'user':
        if realtime_delegation_wrapper(payload) or not visible_user(payload):
            return False
        meta = payload.get('internal_chat_message_metadata_passthrough')
        if isinstance(meta, dict) and 'content_item_kinds' in meta:
            require_strings(payload, ['id'])
    elif role == 'assistant':
        phase = payload.get('phase', payload.get('channel'))
        if phase in {'analysis', 'reasoning'}:
            return False
        text = text_content(payload)
        if phase is None and text and text.startswith(REALTIME_RESPONSE_PREFIXES):
            return False
        if phase not in {'commentary', 'final', 'final_answer'}:
            raise ArchiveError('Unknown assistant visibility: ' + str(phase))
    else:
        raise ArchiveError('Unknown message role: ' + str(role))
    return True


def validate_web_search_call(payload):
    require_strings(payload, ['id', 'status'])
    require(payload, ['action'])
    action = payload['action']
    require_strings(action, ['type', 'query'])
    require(action, ['queries'])
    if (not payload['id'] or payload['status'] != 'completed'
            or action['type'] != 'search' or not action['query']
            or not isinstance(action['queries'], list) or not action['queries']
            or not all(isinstance(query, str) and query
                       for query in action['queries'])):
        raise ArchiveError('Unsupported web search call')


def validate_image_generation_call(payload):
    require_strings(payload, ['id', 'status', 'revised_prompt', 'result'])
    if (not payload['id'] or payload['status'] != 'generating'
            or not payload['revised_prompt'] or not payload['result']):
        raise ArchiveError('Unsupported image generation call')
    try:
        image = base64.b64decode(payload['result'], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ArchiveError('Unsupported image generation result') from exc
    if not image.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ArchiveError('Unsupported image generation result')


def keep_response_item(payload):
    item = payload.get('type')
    if item == 'reasoning':
        return False
    if item == 'agent_message':
        validate_agent_message(payload)
        return False
    if item == 'message':
        return keep_message(payload)
    if item == 'function_call':
        require_strings(payload, ['id', 'call_id', 'name', 'arguments'])
        return True
    if item == 'function_call_output':
        require_strings(payload, ['call_id'])
        validate_optional_strings(payload, ['id'])
        require(payload, ['output'])
        validate_function_output(payload['output'])
        return True
    if item == 'custom_tool_call':
        require_strings(payload, ['id', 'call_id', 'name', 'input', 'status'])
        return True
    if item == 'custom_tool_call_output':
        require_strings(payload, ['call_id'])
        validate_optional_strings(payload, ['id'])
        require(payload, ['output'])
        validate_function_output(payload['output'])
        return True
    if item == 'web_search_call':
        validate_web_search_call(payload)
        return True
    if item == 'image_generation_call':
        validate_image_generation_call(payload)
        return True
    raise ArchiveError('Unknown response item: ' + str(item))


def keep_record(record):
    require(record, ['timestamp', 'type', 'payload'])
    timestamp(record['timestamp'])
    kind, payload = record['type'], record['payload']
    if not isinstance(payload, dict):
        raise ArchiveError('Unsupported payload')
    if kind == 'session_meta':
        return False
    if kind in EXCLUDED_TOP:
        return False
    if kind == 'inter_agent_communication_metadata':
        return keep_inter_agent_metadata(payload)
    if kind == 'realtime_item':
        return keep_realtime_item(payload)
    if kind == 'event_msg':
        return keep_event(payload)
    if kind != 'response_item':
        raise ArchiveError('Unknown record type: ' + str(kind))
    return keep_response_item(payload)


def user_text(record):
    if not isinstance(record, dict):
        raise ArchiveError('Unsupported record')
    if record.get('type') != 'response_item':
        return None
    p = record.get('payload')
    if not isinstance(p, dict):
        raise ArchiveError('Unsupported payload')
    if p.get('type') != 'message':
        return None
    require_strings(p, ['role'])
    validate_optional_strings(p, ['id'])
    if p['role'] != 'user':
        return None
    require(p, ['content'])
    validate_content(p['content'])
    if not visible_user(p):
        return None
    meta = p.get('internal_chat_message_metadata_passthrough')
    if isinstance(meta, dict) and 'content_item_kinds' in meta:
        require_strings(p, ['id'])
    if any(c['type'] != 'input_text' for c in p['content']):
        return None
    return ''.join(c['text'] for c in p['content'])
