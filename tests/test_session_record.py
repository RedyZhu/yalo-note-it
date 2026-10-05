import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/yalo-note-it/scripts'
sys.path.insert(0, str(SCRIPTS))
import archive_store as store
import runtime_context as runtime
import session_source as source
from archive_errors import ArchiveError
from archive_trigger import is_trigger
from codex_format import keep_record, reference_source, timestamp, user_text
from session_assets import plan_assets

SID = '11111111-1111-4111-8111-111111111111'
CHILD_SID = '22222222-2222-4222-8222-222222222222'
GRANDCHILD_SID = '33333333-3333-4333-8333-333333333333'
STAMP = '2026-09-10T10:00:00.123Z'


def record(kind, payload):
    return {'timestamp': STAMP, 'type': kind, 'payload': payload}


def message(text, identifier='request', role='user', kinds=None, phase=None):
    payload = {'type': 'message', 'id': identifier, 'role': role,
               'content': [{'type': 'input_text' if role == 'user' else 'output_text', 'text': text}]}
    if role == 'user':
        payload['internal_chat_message_metadata_passthrough'] = {
            'content_item_kinds': ['user.text'] if kinds is None else kinds}
    if phase:
        payload['phase'] = phase
    return record('response_item', payload)


def legacy_message(text, image_count=0):
    row = message(text)
    row['payload']['content'].extend({
        'type': 'input_image',
        'image_url': f'data:image/png;base64,AA{index}=',
    } for index in range(image_count))
    row['payload']['internal_chat_message_metadata_passthrough'] = {
        'turn_id': 'legacy-turn'}
    return row


def pre_id_message(text, role='user', phase=None, turn_id=False, image_count=0):
    payload = {
        'type': 'message',
        'role': role,
        'content': [{
            'type': 'input_text' if role == 'user' else 'output_text',
            'text': text,
        }],
    }
    payload['content'].extend({
        'type': 'input_image',
        'image_url': f'data:image/png;base64,AA{index}=',
    } for index in range(image_count))
    if phase:
        payload['phase'] = phase
    if turn_id:
        payload['internal_chat_message_metadata_passthrough'] = {
            'turn_id': 'legacy-turn',
        }
    return record('response_item', payload)


def raw(row):
    return (json.dumps(row, ensure_ascii=False, separators=(', ', ': ')) + '\r\n').encode('utf-8')


def asset_sources(rows):
    return [candidate for _, row in rows
            if (candidate := reference_source(row)) is not None]


def single_source(path):
    return source.SessionSource((source.SourceSegment(path),))


def sample():
    return [record('session_meta', {'session_id': SID, 'id': SID, 'base_instructions': {'text': 'private'}}),
            message('injected', 'context', kinds=['agents_md.instructions']),
            message('hello', 'first'),
            message('working', 'comment', 'assistant', phase='commentary'),
            record('response_item', {'type': 'reasoning', 'encrypted_content': 'not-archived'}),
            record('response_item', {'type': 'custom_tool_call', 'id': 'tool', 'call_id': 'call',
                                      'name': 'example', 'input': 'x', 'status': 'completed'}),
            record('response_item', {'type': 'custom_tool_call_output', 'id': 'result', 'call_id': 'call',
                                      'output': [{'type': 'input_text', 'text': 'truncated result'}]}),
            record('event_msg', {'type': 'token_count'}),
            message('done', 'answer', 'assistant', phase='final_answer'),
            message('亚楼记一下'),
            message('archive execution', 'after', 'assistant', phase='commentary')]


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'codex'
        self.source = self.home / 'sessions' / ('rollout-' + SID + '.jsonl')
        self.source.parent.mkdir(parents=True)
        self.rows = sample()
        self.source.write_bytes(b''.join(map(raw, self.rows)))
        self.cutoff_hash = hashlib.sha256(raw(self.rows[-2])).hexdigest()
        self.destination = self.root / 'archive'
        self.destination.mkdir()

    def prepare(self):
        return source.prepare(single_source(self.source), 'request', self.cutoff_hash)

    def test_trigger_variants_and_negative_contexts(self):
        for text in ['亚楼记一下', ' 亚楼，你记一下。\n', '亚楼，记一下', '亚楼 记一下!', '亚楼你记一下',
                     '刘亚楼，你记一下', '刘亚楼你记一下', '请亚楼把结论记一下谢谢',
                     '亚楼\n请记一下', '亚楼记一下？', '“亚楼记一下”', '不要亚楼记一下',
                     '解释亚楼记一下', '```\n亚楼记一下\n```', 'yalo note it', 'Yalo, note it',
                     'YALO — NOTE IT', 'please yalo, could you note it', 'yalo\nnote it']:
            with self.subTest(text=text):
                self.assertTrue(is_trigger(text))
        for text in ['记一下亚楼', '亚楼', '记一下', 'note it yalo', 'yalo', 'note it',
                     'yalonote it', 'yalo notebook', '这个议题到此为止', '这个问题到此为止', '$yalo-note-it']:
            with self.subTest(text=text):
                self.assertFalse(is_trigger(text))

    def test_identity_is_not_recency(self):
        other = self.source.parent / 'newer-unrelated.jsonl'
        other.write_bytes(b'{}\n')
        located = source.locate_source(SID, self.home)
        self.assertEqual(located.segments, (source.SourceSegment(self.source),))

    def test_identity_mismatch_and_ambiguity_fail(self):
        self.rows[0]['payload']['session_id'] = 'wrong'
        self.source.write_bytes(b''.join(map(raw, self.rows)))
        with self.assertRaises(ArchiveError):
            source.locate_source(SID, self.home)

    def test_non_object_source_records_use_archive_error_contract(self):
        for data, error in [
                (b'[]\n', 'expected JSON object'),
                (raw({'timestamp': STAMP, 'type': 'session_meta', 'payload': []}),
                 'payload.*expected JSON object')]:
            with self.subTest(data=data):
                self.source.write_bytes(data)
                with self.assertRaisesRegex(ArchiveError, error):
                    source.locate_source(SID, self.home)

    def test_user_message_requires_stable_identity(self):
        row = message('visible but malformed')
        del row['payload']['id']

        with self.assertRaisesRegex(ArchiveError, 'required fields: id'):
            user_text(row)

    def test_verified_pre_id_message_visibility(self):
        developer = pre_id_message('private developer instructions', role='developer')
        assistant = pre_id_message('visible answer', role='assistant', phase='final_answer')
        ordinary = pre_id_message('legacy visible user')
        turn_id = pre_id_message('legacy turn user', turn_id=True)
        environment = pre_id_message(
            '<environment_context>private</environment_context>')
        interrupted = pre_id_message(
            '<turn_aborted>\n'
            'The user interrupted the previous turn on purpose. Any running unified '
            'exec processes may still be running in the background. If any '
            'tools/commands were aborted, they may have partially executed; verify '
            'current state before retrying.\n'
            '</turn_aborted>')
        browser = pre_id_message(
            '# In app browser:\n'
            '- The user has the in-app browser open with 2 tab(s).\n'
            '- Current URL: https://example.com/\n\n'
            '## My request for Codex:\ninspect the visible page')

        self.assertFalse(keep_record(developer))
        self.assertIsNone(user_text(developer))
        self.assertTrue(keep_record(assistant))
        self.assertTrue(keep_record(ordinary))
        self.assertEqual(user_text(ordinary), 'legacy visible user')
        self.assertTrue(keep_record(turn_id))
        self.assertEqual(user_text(turn_id), 'legacy turn user')
        self.assertFalse(keep_record(environment))
        self.assertFalse(keep_record(interrupted))
        self.assertTrue(keep_record(browser))

        malformed_current = message('missing current provenance')
        del malformed_current['payload']['internal_chat_message_metadata_passthrough']
        with self.assertRaisesRegex(ArchiveError, 'Unsupported user provenance'):
            keep_record(malformed_current)
        explicit_null_metadata = pre_id_message('unverified legacy shape')
        explicit_null_metadata['payload'][
            'internal_chat_message_metadata_passthrough'] = None
        with self.assertRaisesRegex(ArchiveError, 'Unsupported user provenance'):
            keep_record(explicit_null_metadata)
        with self.assertRaisesRegex(
                ArchiveError, 'Unknown legacy injected user wrapper'):
            keep_record(pre_id_message('<turn_aborted>different</turn_aborted>'))

    def test_offline_replayed_user_prefix_selects_unique_complete_rollout(self):
        stale = self.source.with_name('offline-' + SID + '_' + CHILD_SID + '.jsonl')
        stale_rows = [
            record('session_meta', {'session_id': SID, 'id': SID}),
            message('hello\n', 'offline-first'),
        ]
        stale.write_bytes(b''.join(map(raw, stale_rows)))

        located = source.locate_source(SID, self.home)

        self.assertEqual(located.segments, (source.SourceSegment(self.source),))

    def test_offline_prefix_fallback_rejects_conflicting_or_complete_candidate(self):
        stale = self.source.with_name('offline-' + SID + '_' + CHILD_SID + '.jsonl')
        stale.write_bytes(b''.join(map(raw, [
            record('session_meta', {'session_id': SID, 'id': SID}),
            message('different request', 'offline-first'),
        ])))
        with self.assertRaisesRegex(ArchiveError, 'active source branch'):
            source.locate_source(SID, self.home)

        stale.write_bytes(self.source.read_bytes())
        with self.assertRaisesRegex(ArchiveError, 'active source branch'):
            source.locate_source(SID, self.home)

    def test_history_branch_is_reconstructed_from_verified_boundary(self):
        parent_rows = [
            {**record('session_meta', {'session_id': SID, 'id': SID,
                                       'base_instructions': {'text': 'private'}}), 'ordinal': 0},
            {**message('hello', 'first'), 'ordinal': 1},
        ]
        parent_data = b''.join(map(raw, parent_rows))
        self.source.write_bytes(parent_data + raw({**message('abandoned', 'old'), 'ordinal': 2}))
        child = self.source.with_name('branch-' + SID + '_' + CHILD_SID + '.jsonl')
        child_rows = [
            {**record('session_meta', {
                'session_id': SID,
                'id': SID,
                'base_instructions': {'text': 'private'},
                'history_base': {
                    'thread_id': SID,
                    'end_ordinal_exclusive': 2,
                    'end_byte_offset': len(parent_data),
                },
            }), 'ordinal': 2},
            {**message('yalo note it', 'branch-request'), 'ordinal': 3},
        ]
        child.write_bytes(b''.join(map(raw, child_rows)))

        located = source.locate_source(SID, self.home)
        result = source.probe(located)
        selected, _ = source.prepare(
            located, 'branch-request', source.digest(raw(child_rows[-1])))

        self.assertIsInstance(located, source.SessionSource)
        self.assertEqual([segment.path for segment in located.segments], [self.source, child])
        self.assertEqual(result['message_id'], 'branch-request')
        self.assertEqual([row['payload'].get('id') for _, row in selected], ['first', 'branch-request'])

    def test_multi_hop_history_uses_parent_rollout_thread_id(self):
        parent_rows = [
            {**record('session_meta', {'session_id': SID, 'id': SID}), 'ordinal': 0},
            {**message('hello', 'first'), 'ordinal': 1},
        ]
        parent_data = b''.join(map(raw, parent_rows))
        self.source.write_bytes(parent_data)
        child = self.source.with_name('branch-' + SID + '_' + CHILD_SID + '.jsonl')
        child_rows = [
            {**record('session_meta', {
                'session_id': SID,
                'id': SID,
                'history_base': {
                    'thread_id': SID,
                    'end_ordinal_exclusive': 2,
                    'end_byte_offset': len(parent_data),
                },
            }), 'ordinal': 2},
            {**message('middle', 'middle'), 'ordinal': 3},
        ]
        child_data = b''.join(map(raw, child_rows))
        child.write_bytes(child_data)
        grandchild = self.source.with_name('branch-' + SID + '_' + GRANDCHILD_SID + '.jsonl')
        grandchild_rows = [
            {**record('session_meta', {
                'session_id': SID,
                'id': SID,
                'history_base': {
                    'thread_id': CHILD_SID,
                    'end_ordinal_exclusive': 4,
                    'end_byte_offset': len(child_data),
                },
            }), 'ordinal': 4},
            {**message('yalo note it', 'branch-request'), 'ordinal': 5},
        ]
        grandchild.write_bytes(b''.join(map(raw, grandchild_rows)))

        located = source.locate_source(SID, self.home)
        selected, _ = source.prepare(
            located,
            'branch-request',
            source.digest(raw(grandchild_rows[-1])),
        )

        self.assertEqual(
            [segment.path for segment in located.segments],
            [self.source, child, grandchild],
        )
        self.assertEqual(
            [row['payload'].get('id') for _, row in selected],
            ['first', 'middle', 'branch-request'],
        )

    def test_history_branch_rejects_unverified_boundary(self):
        parent_rows = [
            {**record('session_meta', {'session_id': SID, 'id': SID,
                                       'base_instructions': {'text': 'private'}}), 'ordinal': 0},
            {**message('hello', 'first'), 'ordinal': 1},
        ]
        self.source.write_bytes(b''.join(map(raw, parent_rows)))
        child = self.source.with_name('branch-' + SID + '-child.jsonl')
        child.write_bytes(raw({**record('session_meta', {
            'session_id': SID,
            'id': SID,
            'history_base': {
                'thread_id': SID,
                'end_ordinal_exclusive': 2,
                'end_byte_offset': self.source.stat().st_size - 1,
            },
        }), 'ordinal': 2}))

        with self.assertRaises(ArchiveError):
            source.locate_source(SID, self.home)

        self.assertIsNone(source.resolve_history_boundary(
            self.source, True, True, allow_ordinal_fallback=True))
        for invalid_ordinal in (True, 1.0):
            invalid_ordinal_rows = [
                {**record('session_meta', {
                    'session_id': SID,
                    'id': SID,
                }), 'ordinal': 0},
                {**message('hello', 'first'), 'ordinal': invalid_ordinal},
            ]
            invalid_ordinal_data = b''.join(map(raw, invalid_ordinal_rows))
            self.source.write_bytes(invalid_ordinal_data)
            with self.subTest(invalid_ordinal=invalid_ordinal):
                self.assertIsNone(source.resolve_history_boundary(
                    self.source,
                    2,
                    len(invalid_ordinal_data),
                    allow_ordinal_fallback=True,
                ))

    def test_paginated_history_can_recover_boundary_from_contiguous_ordinals(self):
        parent_rows = [
            {**record('session_meta', {'session_id': SID, 'id': SID}), 'ordinal': 0},
            {**message('hello', 'first'), 'ordinal': 1},
        ]
        parent_data = b''.join(map(raw, parent_rows))
        self.source.write_bytes(parent_data + raw({**message('abandoned', 'old'), 'ordinal': 2}))
        child = self.source.with_name('branch-' + SID + '_' + CHILD_SID + '.jsonl')
        child_rows = [
            {**record('session_meta', {
                'session_id': SID,
                'id': SID,
                'history_mode': 'paginated',
                'history_base': {
                    'thread_id': SID,
                    'end_ordinal_exclusive': 2,
                    'end_byte_offset': len(parent_data) + 1,
                },
            }), 'ordinal': 2},
            {**message('yalo note it', 'branch-request'), 'ordinal': 3},
        ]
        child.write_bytes(b''.join(map(raw, child_rows)))

        located = source.locate_source(SID, self.home)
        selected, _ = source.prepare(
            located, 'branch-request', source.digest(raw(child_rows[-1])))

        self.assertEqual(located.segments[0].end_byte_offset, len(parent_data))
        self.assertEqual(
            [row['payload'].get('id') for _, row in selected],
            ['first', 'branch-request'],
        )

    def test_paginated_history_rejects_non_contiguous_ordinals(self):
        parent_rows = [
            {**record('session_meta', {'session_id': SID, 'id': SID}), 'ordinal': 0},
            {**message('hello', 'first'), 'ordinal': 2},
        ]
        parent_data = b''.join(map(raw, parent_rows))
        self.source.write_bytes(parent_data)
        child = self.source.with_name('branch-' + SID + '_' + CHILD_SID + '.jsonl')
        child.write_bytes(raw({**record('session_meta', {
            'session_id': SID,
            'id': SID,
            'history_mode': 'paginated',
            'history_base': {
                'thread_id': SID,
                'end_ordinal_exclusive': 3,
                'end_byte_offset': len(parent_data) + 1,
            },
        }), 'ordinal': 3}))

        with self.assertRaisesRegex(ArchiveError, 'does not resolve'):
            source.locate_source(SID, self.home)

    def test_paginated_history_rejects_ambiguous_parent(self):
        parent_rows = [
            {**record('session_meta', {'session_id': SID, 'id': SID}), 'ordinal': 0},
            {**message('hello', 'first'), 'ordinal': 1},
        ]
        parent_data = b''.join(map(raw, parent_rows))
        self.source.write_bytes(parent_data)
        self.source.with_name('duplicate-' + SID + '.jsonl').write_bytes(parent_data)
        child = self.source.with_name('branch-' + SID + '_' + CHILD_SID + '.jsonl')
        child.write_bytes(raw({**record('session_meta', {
            'session_id': SID,
            'id': SID,
            'history_mode': 'paginated',
            'history_base': {
                'thread_id': SID,
                'end_ordinal_exclusive': 2,
                'end_byte_offset': len(parent_data) + 1,
            },
        }), 'ordinal': 2}))

        with self.assertRaisesRegex(ArchiveError, 'does not resolve'):
            source.locate_source(SID, self.home)

    def test_missing_or_conflicting_environment_fails(self):
        for env in [{}, {'CODEX_SESSION_ID': SID, 'CODEX_THREAD_ID': 'different'}]:
            with patch.dict(os.environ, env, clear=True), self.assertRaises(ArchiveError):
                runtime.current_id()
        with patch.dict(os.environ, {'CODEX_SESSION_ID': SID}, clear=True):
            self.assertEqual(runtime.current_id(), SID)

    def test_exact_bytes_order_and_cutoff(self):
        selected, _ = self.prepare()
        self.assertEqual(b''.join(r for r, _ in selected), b''.join(raw(self.rows[i]) for i in [2, 3, 5, 6, 8, 9]))

    def test_pre_id_rollout_prefix_prepares_without_rewriting(self):
        rows = [
            {**record('session_meta', {'session_id': SID, 'id': SID}), 'ordinal': 0},
            {**pre_id_message('private', role='developer'), 'ordinal': 1},
            {**pre_id_message(
                '<environment_context>private</environment_context>'), 'ordinal': 2},
            {**pre_id_message('legacy ordinary user'), 'ordinal': 3},
            {**pre_id_message('legacy turn user', turn_id=True), 'ordinal': 4},
            {**pre_id_message(
                'legacy answer', role='assistant', phase='final_answer'), 'ordinal': 5},
            {**record('response_item', {
                'type': 'function_call',
                'id': 'legacy-function',
                'call_id': 'legacy-call',
                'name': 'shell_command',
                'arguments': '{}',
            }), 'ordinal': 6},
            {**record('response_item', {
                'type': 'function_call_output',
                'call_id': 'legacy-call',
                'output': 'legacy result',
            }), 'ordinal': 7},
            {**record('response_item', {
                'type': 'web_search_call',
                'id': 'legacy-web',
                'status': 'completed',
                'action': {
                    'type': 'search',
                    'query': 'example query',
                    'queries': ['example query', 'alternate query'],
                },
            }), 'ordinal': 8},
            {**record('response_item', {
                'type': 'image_generation_call',
                'id': 'legacy-image',
                'status': 'generating',
                'revised_prompt': 'example prompt',
                'result': 'iVBORw0KGgo=',
            }), 'ordinal': 9},
            {**message('亚楼记一下', 'modern-trigger'), 'ordinal': 10},
        ]
        self.source.write_bytes(b''.join(map(raw, rows)))
        trigger_hash = source.digest(raw(rows[-1]))

        result = source.probe(single_source(self.source))
        selected, _ = source.prepare(
            single_source(self.source), 'modern-trigger', trigger_hash)

        self.assertEqual(result['message_id'], 'modern-trigger')
        self.assertEqual(result['sha256'], trigger_hash)
        self.assertEqual(len(selected), 8)
        self.assertEqual(
            b''.join(item for item, _ in selected),
            b''.join(raw(rows[index]) for index in range(3, 11)),
        )

    def test_probe_rejects_idless_latest_user_as_cutoff(self):
        rows = [
            record('session_meta', {'session_id': SID, 'id': SID}),
            pre_id_message('亚楼记一下'),
        ]
        self.source.write_bytes(b''.join(map(raw, rows)))

        with self.assertRaisesRegex(ArchiveError, 'no stable cutoff ID'):
            source.probe(single_source(self.source))

    def test_probe_reads_committed_request(self):
        result = source.probe(single_source(self.source))
        self.assertEqual(result['message_id'], 'request')
        self.assertTrue(result['is_trigger'])
        self.assertEqual(result['sha256'], self.cutoff_hash)

    def test_partial_cutoff_fails_but_partial_later_write_is_irrelevant(self):
        self.source.write_bytes(b''.join(map(raw, self.rows[:-2])) + raw(self.rows[-2])[:-1])
        with self.assertRaises(ArchiveError):
            self.prepare()
        self.source.write_bytes(b''.join(map(raw, self.rows[:-1])) + b'{unfinished')
        self.prepare()

    def test_unknown_event_and_item_fail(self):
        for row in [record('new_top', {}), record('event_msg', {'type': 'new_event'}),
                    record('response_item', {'type': 'new_call'})]:
            with self.subTest(row=row), self.assertRaises(ArchiveError):
                keep_record(row)

    def test_unknown_mixed_user_and_unknown_phase_fail(self):
        for row in [message('x', kinds=['unknown']),
                    message('x', kinds=['user.text', 'agents_md.instructions']),
                    message('x', role='assistant')]:
            with self.subTest(row=row), self.assertRaises(ArchiveError):
                keep_record(row)

    def test_tool_call_string_fields_are_validated(self):
        rows = [
            record('response_item', {
                'type': 'function_call',
                'id': 'function',
                'call_id': 7,
                'name': 'tool',
                'arguments': '{}',
            }),
            record('response_item', {
                'type': 'function_call_output',
                'id': 'output',
                'call_id': None,
                'output': 'result',
            }),
            record('response_item', {
                'type': 'custom_tool_call',
                'id': 'custom',
                'call_id': 'call',
                'name': 'tool',
                'input': {},
                'status': 'completed',
            }),
        ]
        for row in rows:
            with self.subTest(row=row), self.assertRaisesRegex(
                    ArchiveError, 'Unsupported string fields'):
                keep_record(row)

    def test_selected_skill_instructions_are_internal(self):
        self.assertFalse(keep_record(message(
            '<skill>private instructions</skill>',
            kinds=['skills.selected_skill_instructions'],
        )))

    def test_writing_block_edit_snapshots_are_internal(self):
        self.assertFalse(keep_record(message(
            '<external_codex_apps_writing_block_edits>\n[]\n'
            '</external_codex_apps_writing_block_edits>',
            kinds=['additional_content.codex_apps_writing_block_edits'],
        )))

    def test_analysis_and_metadata_excluded(self):
        self.assertFalse(keep_record(self.rows[0]))
        self.assertFalse(keep_record(message('hidden', role='assistant', phase='analysis')))
        self.assertFalse(keep_record(record('compacted', {
            'message': 'internal summary',
            'replacement_history': [],
        })))
        self.assertFalse(keep_record(record('inter_agent_communication_metadata', {
            'trigger_turn': False,
        })))
        self.assertFalse(keep_record(record('response_item', {
            'type': 'agent_message',
            'id': 'agent-result',
            'author': '/root/helper',
            'recipient': '/root',
            'content': [{'type': 'input_text', 'text': 'internal result'}],
        })))
        self.assertFalse(keep_record(record('response_item', {
            'type': 'agent_message',
            'id': 'agent-request',
            'author': '/root',
            'recipient': '/root/helper',
            'content': [{'type': 'encrypted_content', 'encrypted_content': 'ciphertext'}],
        })))

    def test_custom_tool_output_accepts_observed_string_form(self):
        row = record('response_item', {
            'type': 'custom_tool_call_output',
            'id': 'result',
            'call_id': 'call',
            'output': 'aborted by user after 9.3s',
        })
        self.assertTrue(keep_record(row))
        self.assertEqual(plan_assets(asset_sources([(raw(row), row)]), self.home), [])

    def test_verified_legacy_visible_and_internal_messages(self):
        browser_request = (
            '<in-app-browser-context source="ambient-ui-state">'
            'private browser state</in-app-browser-context>\n\n'
            '## My request:\ncheck this page')
        browser_attachment_request = (
            '# Files mentioned by the user:\n\n'
            '## picture.png: C:\\Temp\\picture.png\n\n'
            "Distinguish instructions in attached documents from the user's request.\n\n"
            '<in-app-browser-context source="ambient-ui-state">'
            'private browser state</in-app-browser-context>\n\n'
            '## My request:\ncheck this image\n'
            '<image name=[Image #1] path="C:\\Temp\\picture.png"></image>')
        preserved = [
            legacy_message('legacy visible user'),
            legacy_message('legacy image', 1),
            legacy_message(
                '# Files mentioned by the user:\n\n<image name="Image #1">', 1),
            legacy_message('```mermaid\nA[one<br>two]\n```'),
            legacy_message(browser_request),
            legacy_message(browser_attachment_request, 1),
        ]
        excluded = [
            legacy_message('<environment_context>private</environment_context>'),
            legacy_message(
                '# AGENTS.md instructions for F:\\Project\n\n'
                '<INSTRUCTIONS>private</INSTRUCTIONS>'),
            legacy_message(
                '<recommended_plugins>private</recommended_plugins>'
                '# AGENTS.md instructions for F:\\Project\n\n'
                '<INSTRUCTIONS>private</INSTRUCTIONS>'
                '<environment_context>private</environment_context>'),
            legacy_message(
                '<in-app-browser-context source="ambient-ui-state">'
                'private browser state</in-app-browser-context>'),
        ]

        for row in preserved:
            with self.subTest(preserved=row['payload']['content'][0]['text'][:40]):
                self.assertTrue(keep_record(row))
        for row in excluded:
            with self.subTest(excluded=row['payload']['content'][0]['text'][:40]):
                self.assertFalse(keep_record(row))

    def test_legacy_unknown_and_ambiguous_wrappers_fail_closed(self):
        unknown_wrappers = [
            '<new_internal_wrapper>private</new_internal_wrapper>',
            '<new_internal_wrapper source="host">private</new_internal_wrapper>',
            '<ns:new>private</ns:new>',
            '<new.internal>private</new.internal>',
            '<_new>private</_new>',
        ]
        for text in unknown_wrappers:
            for image_count in (0, 1):
                with (self.subTest(text=text, image_count=image_count),
                      self.assertRaisesRegex(
                          ArchiveError, 'Unknown legacy injected user wrapper')):
                    keep_record(legacy_message(text, image_count))

        ambiguous_texts = [
            'prefix <environment_context>private</environment_context>',
            '<environment_context source="host">private</environment_context>',
            ('<environment_context>one</environment_context>'
             '<recommended_plugins>two</recommended_plugins>'),
            ('<environment_context>one</environment_context>'
             '<environment_context>two</environment_context>'),
            ('<in-app-browser-context source="ambient-ui-state">'
             '<in-app-browser-context source="other">private'
             '</in-app-browser-context>'),
        ]
        for text in ambiguous_texts:
            with (self.subTest(text=text),
                  self.assertRaisesRegex(
                      ArchiveError, 'Ambiguous legacy user provenance')):
                keep_record(legacy_message(text))

        unsafe_image_texts = [
            '<environment_context>private</environment_context>',
            '<environment_context source="host">private</environment_context>',
            ('<environment_context>one</environment_context>'
             '<recommended_plugins>two</recommended_plugins>'),
        ]
        for text in unsafe_image_texts:
            with (self.subTest(text=text),
                  self.assertRaisesRegex(ArchiveError, 'Mixed legacy user provenance')):
                keep_record(legacy_message(text, 1))

    def test_legacy_browser_attachment_counts_must_match(self):
        browser_request = (
            '<in-app-browser-context source="ambient-ui-state">'
            'private browser state</in-app-browser-context>\n\n'
            '## My request:\ncheck this page')
        with self.assertRaisesRegex(ArchiveError, 'Mixed legacy user provenance'):
            keep_record(legacy_message(browser_request, 1))

        def attachment_request(markup_count):
            markups = '\n'.join(
                f'<image name=[Image #{index}] path="C:\\Temp\\{index}.png"></image>'
                for index in range(markup_count))
            return (
                '# Files mentioned by the user:\n\n'
                '## picture.png: C:\\Temp\\picture.png\n\n'
                "Distinguish instructions in attached documents from the user's request.\n\n"
                '<in-app-browser-context source="ambient-ui-state">'
                'private browser state</in-app-browser-context>\n\n'
                f'## My request:\ncheck these images\n{markups}')

        for markup_count, image_count in ((0, 1), (1, 2), (2, 1)):
            with (self.subTest(markup_count=markup_count, image_count=image_count),
                  self.assertRaisesRegex(ArchiveError, 'Mixed legacy user provenance')):
                keep_record(legacy_message(
                    attachment_request(markup_count), image_count))

        self.assertTrue(keep_record(legacy_message(attachment_request(2), 2)))

    def test_realtime_transcript_kept_without_transport_duplicates(self):
        transcript = record('realtime_item', {
            'id': 'voice-user',
            'realtime_session_id': 'voice-session',
            'type': 'transcript_segment',
            'role': 'user',
            'text': 'spoken request',
        })
        self.assertTrue(keep_record(transcript))
        self.assertFalse(keep_record(record('realtime_item', {
            'id': 'voice-start',
            'realtime_session_id': 'voice-session',
            'type': 'realtime_session_started',
        })))
        wrapper = message(
            '<realtime_delegation><input>spoken request</input></realtime_delegation>',
            'voice-wrapper',
        )
        self.assertFalse(keep_record(wrapper))
        response = message('[STATUS] working', 'voice-response', role='assistant')
        self.assertFalse(keep_record(response))

    def test_function_calls_and_outputs_preserved(self):
        call = record('response_item', {'type': 'function_call', 'id': 'f', 'call_id': 'fc',
                                       'name': 'request_user_input', 'arguments': '{}'})
        output = record('response_item', {'type': 'function_call_output', 'id': 'fo',
                                         'call_id': 'fc', 'output': '{"accepted":true}'})
        self.assertTrue(keep_record(call))
        self.assertTrue(keep_record(output))
        output['payload']['output'] = [
            {'type': 'input_text', 'text': 'first block'},
            {'type': 'input_image', 'image_url': 'data:image/png;base64,AA=='},
        ]
        self.assertTrue(keep_record(output))
        output['payload']['output'] = [{'type': 'output_text', 'text': 'unverified shape'}]
        with self.assertRaises(ArchiveError):
            keep_record(output)
        output['payload']['output'] = None
        with self.assertRaises(ArchiveError):
            keep_record(output)

    def test_pre_id_tool_outputs_use_existing_stable_keys(self):
        output = {
            **record('response_item', {
                'type': 'function_call_output',
                'call_id': 'legacy-function-call',
                'output': 'result',
            }),
            'ordinal': 17,
        }
        custom_output = {
            **record('response_item', {
                'type': 'custom_tool_call_output',
                'call_id': 'legacy-custom-call',
                'output': 'result',
            }),
            'ordinal': 18,
        }
        legacy_user = {**pre_id_message('legacy'), 'ordinal': 19}

        self.assertTrue(keep_record(output))
        self.assertTrue(keep_record(custom_output))
        self.assertEqual(reference_source(output).record_id, 'legacy-function-call')
        self.assertEqual(
            reference_source(custom_output).record_id, 'legacy-custom-call')
        self.assertEqual(reference_source(legacy_user).record_id, 'ordinal-19')

        output['payload']['id'] = 7
        with self.assertRaisesRegex(ArchiveError, 'Unsupported string fields'):
            keep_record(output)
        del legacy_user['ordinal']
        with self.assertRaisesRegex(ArchiveError, 'no stable source ordinal'):
            reference_source(legacy_user)
        legacy_user['ordinal'] = True
        with self.assertRaisesRegex(ArchiveError, 'no stable source ordinal'):
            reference_source(legacy_user)

    def test_verified_web_and_image_tool_records_are_strict(self):
        web = record('response_item', {
            'type': 'web_search_call',
            'id': 'web',
            'status': 'completed',
            'action': {
                'type': 'search',
                'query': 'example query',
                'queries': ['example query', 'alternate query'],
            },
        })
        image = record('response_item', {
            'type': 'image_generation_call',
            'id': 'image',
            'status': 'generating',
            'revised_prompt': 'example prompt',
            'result': 'iVBORw0KGgo=',
        })
        self.assertTrue(keep_record(web))
        self.assertTrue(keep_record(image))

        invalid_web = json.loads(json.dumps(web))
        invalid_web['payload']['action']['type'] = 'unknown'
        with self.assertRaisesRegex(ArchiveError, 'Unsupported web search call'):
            keep_record(invalid_web)
        invalid_web = json.loads(json.dumps(web))
        invalid_web['payload']['action']['queries'] = 'not-an-array'
        with self.assertRaisesRegex(ArchiveError, 'Unsupported web search call'):
            keep_record(invalid_web)
        for field, value in [('id', ''), ('action.query', ''), ('action.queries', [''])]:
            invalid_web = json.loads(json.dumps(web))
            target, name = (invalid_web['payload']['action'], field.split('.', 1)[1]) \
                if field.startswith('action.') else (invalid_web['payload'], field)
            target[name] = value
            with (self.subTest(field=field),
                  self.assertRaisesRegex(ArchiveError, 'Unsupported web search call')):
                keep_record(invalid_web)
        invalid_image = json.loads(json.dumps(image))
        invalid_image['payload']['status'] = 'completed'
        with self.assertRaisesRegex(ArchiveError, 'Unsupported image generation call'):
            keep_record(invalid_image)
        invalid_image = json.loads(json.dumps(image))
        invalid_image['payload']['id'] = ''
        with self.assertRaisesRegex(ArchiveError, 'Unsupported image generation call'):
            keep_record(invalid_image)
        for result in ('https://example.com/result.png', 'C:\\Temp\\result.png',
                       'cGxhaW4gdGV4dA=='):
            invalid_image = json.loads(json.dumps(image))
            invalid_image['payload']['result'] = result
            with (self.subTest(result=result),
                  self.assertRaisesRegex(
                      ArchiveError, 'Unsupported image generation result')):
                keep_record(invalid_image)
        del image['payload']['result']
        with self.assertRaisesRegex(ArchiveError, 'required fields: .*result'):
            keep_record(image)

    def test_hash_mismatch_or_absent_cutoff_fails(self):
        for identifier, hash_value in [('request', 'bad'), ('absent', self.cutoff_hash)]:
            with self.assertRaises(ArchiveError):
                source.prepare(single_source(self.source), identifier, hash_value)

    def test_confirmation_requires_explicit_mode(self):
        row = message('是的，归档吧', 'confirmed')
        self.source.write_bytes(b''.join(map(raw, self.rows[:9] + [row])))
        h = source.digest(raw(row))
        with self.assertRaises(ArchiveError):
            source.prepare(single_source(self.source), 'confirmed', h)
        selected, _ = source.prepare(
            single_source(self.source), 'confirmed', h, confirmed=True)
        self.assertEqual(selected[-1][1]['payload']['id'], 'confirmed')

    def test_configuration_reply_does_not_move_cutoff(self):
        self.source.write_bytes(self.source.read_bytes() + raw(message(r'D:\Records', 'path-reply')))
        selected, _ = self.prepare()
        self.assertEqual(selected[-1][1]['payload']['id'], 'request')

    def test_repeat_archive_is_one_file_and_source_unchanged(self):
        before = self.source.read_bytes()
        selected, started = self.prepare()
        output = store.commit_archive(self.destination, SID, started, selected, [])
        store.commit_archive(self.destination, SID, started, selected, [])
        self.assertEqual(output, self.destination / 'sessions' / 'codex' / '2026-09' / SID / 'session.jsonl')
        self.assertEqual(output.read_bytes(), b''.join(r for r, _ in selected))
        self.assertEqual(list(output.parent.iterdir()), [output])
        self.assertEqual(self.source.read_bytes(), before)

    def test_replace_failure_preserves_previous_archive(self):
        selected, started = self.prepare()
        output = store.commit_archive(self.destination, SID, started, selected, [])
        previous = output.read_bytes()
        with patch.object(store.os, 'replace', side_effect=OSError('denied')):
            with self.assertRaises(OSError):
                store.commit_archive(self.destination, SID, started, selected[:1], [])
        self.assertEqual(output.read_bytes(), previous)
        self.assertEqual(list(output.parent.iterdir()), [output])

    def test_lock_refuses_concurrent_write(self):
        selected, started = self.prepare()
        output = store.commit_archive(self.destination, SID, started, selected, [])
        (output.parent / '.archive.lock').write_text('')
        with self.assertRaises(ArchiveError):
            store.commit_archive(self.destination, SID, started, selected, [])

    def test_status_does_not_create_config(self):
        config = self.root / 'settings' / 'archive-config.json'
        with patch.object(store, 'config_path', return_value=config):
            self.assertIsNone(store.load_config())
        self.assertFalse(config.parent.exists())

    def test_configuration_is_independent_and_invalid_path_has_no_fallback(self):
        config = self.root / 'settings' / 'archive-config.json'
        with patch.object(store, 'config_path', return_value=config):
            store.configure(self.destination)
            expected = self.destination / store.ARCHIVE_FOLDER_NAME
            self.assertEqual(store.load_config(), expected)
            config.write_text(json.dumps({'archive_root': str(self.root / 'absent')}))
            with self.assertRaises(ArchiveError):
                store.load_config()

    def test_existing_legacy_archive_name_remains_configured(self):
        config = self.root / 'settings' / 'archive-config.json'
        legacy = self.root / 'yalo-note'
        legacy.mkdir()
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'archive_root': str(legacy)}), encoding='utf-8')

        with patch.object(store, 'config_path', return_value=config):
            self.assertEqual(store.load_config(), legacy)

    def test_configuration_can_recover_from_unavailable_old_directory(self):
        config = self.root / 'settings' / 'archive-config.json'
        missing = self.root / 'missing-old-root'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'archive_root': str(missing)}), encoding='utf-8')
        new_base = self.root / 'new-base'

        with patch.object(store, 'config_path', return_value=config):
            with self.assertRaisesRegex(ArchiveError, 'unavailable'):
                store.load_config()
            root = store.configure(new_base, migrate_existing=False)
            self.assertEqual(root, new_base / store.ARCHIVE_FOLDER_NAME)
            self.assertEqual(store.load_config(), root)

    def test_missing_old_archive_aborts_migration_without_switching_config(self):
        config = self.root / 'settings' / 'archive-config.json'
        missing = self.root / 'missing-old-root'
        config.parent.mkdir(parents=True)
        original = json.dumps({'archive_root': str(missing)})
        config.write_text(original, encoding='utf-8')
        new_base = self.root / 'new-base'

        with patch.object(store, 'config_path', return_value=config):
            with self.assertRaisesRegex(ArchiveError, 'migration aborted'):
                store.configure(new_base, migrate_existing=True)

        self.assertEqual(config.read_text(encoding='utf-8'), original)
        self.assertFalse((new_base / store.ARCHIVE_FOLDER_NAME).exists())

    def test_path_change_requires_choice_and_can_keep_old_archive(self):
        config = self.root / 'settings' / 'archive-config.json'
        first_base = self.root / 'first'
        second_base = self.root / 'second'
        with patch.object(store, 'config_path', return_value=config):
            first = store.configure(first_base)
            (first / 'old.txt').write_text('old', encoding='utf-8')
            with self.assertRaisesRegex(ArchiveError, 'migrate-or-keep'):
                store.configure(second_base)
            second = store.configure(second_base, migrate_existing=False)
            self.assertEqual(second, second_base / store.ARCHIVE_FOLDER_NAME)
            self.assertTrue((first / 'old.txt').is_file())
            self.assertEqual(store.load_config(), second)

    def test_path_change_can_migrate_and_remove_old_archive(self):
        config = self.root / 'settings' / 'archive-config.json'
        first_base = self.root / 'first'
        second_base = self.root / 'second'
        with patch.object(store, 'config_path', return_value=config):
            first = store.configure(first_base)
            nested = first / 'sessions' / 'codex'
            nested.mkdir(parents=True)
            (nested / 'old.txt').write_text('old', encoding='utf-8')
            second = store.configure(second_base, migrate_existing=True)
            self.assertEqual((second / 'sessions/codex/old.txt').read_text(encoding='utf-8'), 'old')
            self.assertFalse(first.exists())
            self.assertEqual(store.load_config(), second)

    def test_path_change_rejects_new_archive_inside_old_archive(self):
        config = self.root / 'settings' / 'archive-config.json'
        first_base = self.root / 'first'
        with patch.object(store, 'config_path', return_value=config):
            first = store.configure(first_base)
            nested_base = first / 'nested'
            with self.assertRaisesRegex(ArchiveError, 'must not contain each other'):
                store.configure(nested_base, migrate_existing=False)
            self.assertFalse((nested_base / store.ARCHIVE_FOLDER_NAME).exists())
            self.assertEqual(store.load_config(), first)

    def test_path_change_rejects_old_archive_inside_new_archive(self):
        config = self.root / 'settings' / 'archive-config.json'
        first_base = self.root / 'outer' / store.ARCHIVE_FOLDER_NAME / 'inner'
        with patch.object(store, 'config_path', return_value=config):
            first = store.configure(first_base)
            outer_base = self.root / 'outer'
            with self.assertRaisesRegex(ArchiveError, 'must not contain each other'):
                store.configure(outer_base, migrate_existing=True)
            self.assertEqual(store.load_config(), first)

    def test_cleanup_failure_keeps_new_complete_archive_active(self):
        config = self.root / 'settings' / 'archive-config.json'
        first_base = self.root / 'first'
        second_base = self.root / 'second'
        with patch.object(store, 'config_path', return_value=config):
            first = store.configure(first_base)
            (first / 'old.txt').write_text('old', encoding='utf-8')
            with patch.object(store.shutil, 'rmtree', side_effect=OSError('denied')):
                with self.assertRaisesRegex(ArchiveError, 'new path activated'):
                    store.configure(second_base, migrate_existing=True)
            second = second_base / store.ARCHIVE_FOLDER_NAME
            self.assertEqual(store.load_config(), second)
            self.assertEqual((second / 'old.txt').read_text(encoding='utf-8'), 'old')

    def test_configured_archive_is_created_under_user_selected_base(self):
        config = self.root / 'settings' / 'archive-config.json'
        base = self.root / 'selected'
        with patch.object(store, 'config_path', return_value=config):
            root = store.configure(base)
        self.assertEqual(root, base / 'YaloNote')
        self.assertTrue(root.is_dir())

    def test_archive_root_cannot_contain_codex_source_storage(self):
        config = self.root / 'settings' / 'archive-config.json'
        base = self.root / 'selected'
        archive_root = base / store.ARCHIVE_FOLDER_NAME
        nested_source = archive_root / 'codex-source'

        with (patch.object(store, 'config_path', return_value=config),
              patch.object(store, 'codex_home', return_value=nested_source),
              self.assertRaisesRegex(ArchiveError, 'outside Codex source storage')):
            store.configure(base)

        self.assertFalse(archive_root.exists())

    def test_temporary_attachment_and_name_collision(self):
        paths = [self.root / 'one' / 'picture.png', self.root / 'two' / 'picture.png']
        rows = []
        for i, p in enumerate(paths):
            p.parent.mkdir()
            p.write_bytes(bytes([i]))
            row = message(f'![image](<{p}>)', f'asset-{i}')
            rows.append((raw(row), row))
        assets = plan_assets(asset_sources(rows), self.home)
        output = store.commit_archive(
            self.destination, SID, timestamp(STAMP), rows, assets)
        self.assertEqual((output.parent / 'assets/picture.png').read_bytes(), b'\x00')
        self.assertEqual((output.parent / 'assets/picture-asset-1.png').read_bytes(), b'\x01')
        self.assertEqual(output.read_bytes(), b''.join(r for r, _ in rows))

    def test_function_output_content_blocks_are_scanned_for_assets(self):
        attachment = self.root / 'function-output.png'
        attachment.write_bytes(b'image')
        row = record('response_item', {
            'type': 'function_call_output',
            'id': 'function-result',
            'call_id': 'function-call',
            'output': [{'type': 'input_text', 'text': f'![image](<{attachment}>)'}],
        })

        assets = plan_assets(asset_sources([(raw(row), row)]), self.home)

        self.assertEqual([asset.source for asset in assets], [attachment.resolve()])

    def test_embedded_image_and_ordinary_file_need_no_copy(self):
        row = message('text')
        row['payload']['content'].append({'type': 'input_image', 'image_url': 'data:image/png;base64,AA=='})
        self.assertEqual(plan_assets(asset_sources([(raw(row), row)]), self.home), [])
        row = message('![ordinary](/ordinary/project/image.png)')
        with patch('session_assets.temporary_roots', return_value={self.root / 'temporary'}):
            self.assertEqual(plan_assets(asset_sources([(raw(row), row)]), self.home), [])

    def test_missing_temporary_and_remote_image_fail(self):
        row = message(f'![image]({self.root / "missing.png"})')
        with self.assertRaises(ArchiveError):
            plan_assets(asset_sources([(raw(row), row)]), self.home)

    def test_diagnostic_paths_are_not_attachments(self):
        row = message(f'AssertionError: WindowsPath(\'{self.root / "deleted" / "source.jsonl"}\') != expected')
        self.assertEqual(plan_assets(asset_sources([(raw(row), row)]), self.home), [])
        row['payload']['content'] = [{'type': 'input_image', 'image_url': 'https://example.com/temporary.png'}]
        with self.assertRaises(ArchiveError):
            plan_assets(asset_sources([(raw(row), row)]), self.home)

    def test_asset_changed_after_planning_preserves_archive(self):
        selected, started = self.prepare()
        output = store.commit_archive(self.destination, SID, started, selected, [])
        before = output.read_bytes()
        p = self.root / 'file.png'
        p.write_bytes(b'one')
        row = message(f'![image]({p})')
        assets = plan_assets(asset_sources([(raw(row), row)]), self.home)
        p.write_bytes(b'two')
        with self.assertRaises(ArchiveError):
            store.commit_archive(self.destination, SID, started, selected, assets)
        self.assertEqual(output.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
