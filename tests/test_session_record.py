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
import record_session as archive
from codex_format import ArchiveError, is_trigger, keep_record
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


def raw(row):
    return (json.dumps(row, ensure_ascii=False, separators=(', ', ': ')) + '\r\n').encode('utf-8')


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
        return archive.prepare(self.source, 'request', self.cutoff_hash)

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
        self.assertEqual(archive.locate_source(SID, self.home), self.source)

    def test_identity_mismatch_and_ambiguity_fail(self):
        self.rows[0]['payload']['session_id'] = 'wrong'
        self.source.write_bytes(b''.join(map(raw, self.rows)))
        with self.assertRaises(ArchiveError):
            archive.locate_source(SID, self.home)

    def test_offline_replayed_user_prefix_selects_unique_complete_rollout(self):
        stale = self.source.with_name('offline-' + SID + '_' + CHILD_SID + '.jsonl')
        stale_rows = [
            record('session_meta', {'session_id': SID, 'id': SID}),
            message('hello\n', 'offline-first'),
        ]
        stale.write_bytes(b''.join(map(raw, stale_rows)))

        source = archive.locate_source(SID, self.home)

        self.assertEqual(source, self.source)

    def test_offline_prefix_fallback_rejects_conflicting_or_complete_candidate(self):
        stale = self.source.with_name('offline-' + SID + '_' + CHILD_SID + '.jsonl')
        stale.write_bytes(b''.join(map(raw, [
            record('session_meta', {'session_id': SID, 'id': SID}),
            message('different request', 'offline-first'),
        ])))
        with self.assertRaisesRegex(ArchiveError, 'active source branch'):
            archive.locate_source(SID, self.home)

        stale.write_bytes(self.source.read_bytes())
        with self.assertRaisesRegex(ArchiveError, 'active source branch'):
            archive.locate_source(SID, self.home)

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

        source = archive.locate_source(SID, self.home)
        result = archive.probe(source)
        selected, _ = archive.prepare(source, 'branch-request', archive.digest(raw(child_rows[-1])))

        self.assertIsInstance(source, archive.SessionSource)
        self.assertEqual([segment.path for segment in source.segments], [self.source, child])
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

        source = archive.locate_source(SID, self.home)
        selected, _ = archive.prepare(
            source,
            'branch-request',
            archive.digest(raw(grandchild_rows[-1])),
        )

        self.assertEqual(
            [segment.path for segment in source.segments],
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
            archive.locate_source(SID, self.home)
        self.source.with_name('duplicate-' + SID + '.jsonl').write_bytes(self.source.read_bytes())
        with self.assertRaises(ArchiveError):
            archive.locate_source(SID, self.home)

    def test_missing_or_conflicting_environment_fails(self):
        for env in [{}, {'CODEX_SESSION_ID': SID, 'CODEX_THREAD_ID': 'different'}]:
            with patch.dict(os.environ, env, clear=True), self.assertRaises(ArchiveError):
                archive.current_id()
        with patch.dict(os.environ, {'CODEX_SESSION_ID': SID}, clear=True):
            self.assertEqual(archive.current_id(), SID)

    def test_exact_bytes_order_and_cutoff(self):
        selected, _ = self.prepare()
        self.assertEqual(b''.join(r for r, _ in selected), b''.join(raw(self.rows[i]) for i in [2, 3, 5, 6, 8, 9]))

    def test_probe_reads_committed_request(self):
        result = archive.probe(self.source)
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

    def test_analysis_and_metadata_excluded(self):
        self.assertFalse(keep_record(self.rows[0]))
        self.assertFalse(keep_record(message('hidden', role='assistant', phase='analysis')))
        self.assertFalse(keep_record(record('compacted', {
            'message': 'internal summary',
            'replacement_history': [],
        })))

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

    def test_hash_mismatch_or_absent_cutoff_fails(self):
        for identifier, hash_value in [('request', 'bad'), ('absent', self.cutoff_hash)]:
            with self.assertRaises(ArchiveError):
                archive.prepare(self.source, identifier, hash_value)

    def test_confirmation_requires_explicit_mode(self):
        row = message('是的，归档吧', 'confirmed')
        self.source.write_bytes(b''.join(map(raw, self.rows[:9] + [row])))
        h = archive.digest(raw(row))
        with self.assertRaises(ArchiveError):
            archive.prepare(self.source, 'confirmed', h)
        selected, _ = archive.prepare(self.source, 'confirmed', h, confirmed=True)
        self.assertEqual(selected[-1][1]['payload']['id'], 'confirmed')

    def test_configuration_reply_does_not_move_cutoff(self):
        self.source.write_bytes(self.source.read_bytes() + raw(message(r'D:\Records', 'path-reply')))
        selected, _ = self.prepare()
        self.assertEqual(selected[-1][1]['payload']['id'], 'request')

    def test_repeat_archive_is_one_file_and_source_unchanged(self):
        before = self.source.read_bytes()
        selected, started = self.prepare()
        output = archive.commit_archive(self.destination, SID, started, selected, [])
        archive.commit_archive(self.destination, SID, started, selected, [])
        self.assertEqual(output, self.destination / 'sessions' / 'codex' / '2026-09' / SID / 'session.jsonl')
        self.assertEqual(output.read_bytes(), b''.join(r for r, _ in selected))
        self.assertEqual(list(output.parent.iterdir()), [output])
        self.assertEqual(self.source.read_bytes(), before)

    def test_replace_failure_preserves_previous_archive(self):
        selected, started = self.prepare()
        output = archive.commit_archive(self.destination, SID, started, selected, [])
        previous = output.read_bytes()
        with patch.object(archive.os, 'replace', side_effect=OSError('denied')):
            with self.assertRaises(OSError):
                archive.commit_archive(self.destination, SID, started, selected[:1], [])
        self.assertEqual(output.read_bytes(), previous)
        self.assertEqual(list(output.parent.iterdir()), [output])

    def test_lock_refuses_concurrent_write(self):
        selected, started = self.prepare()
        output = archive.commit_archive(self.destination, SID, started, selected, [])
        (output.parent / '.archive.lock').write_text('')
        with self.assertRaises(ArchiveError):
            archive.commit_archive(self.destination, SID, started, selected, [])

    def test_status_does_not_create_config(self):
        config = self.root / 'settings' / 'archive-config.json'
        with patch.object(archive, 'config_path', return_value=config):
            self.assertIsNone(archive.load_config())
        self.assertFalse(config.parent.exists())

    def test_configuration_is_independent_and_invalid_path_has_no_fallback(self):
        config = self.root / 'settings' / 'archive-config.json'
        with patch.object(archive, 'config_path', return_value=config):
            archive.configure(self.destination)
            expected = self.destination / archive.ARCHIVE_FOLDER_NAME
            self.assertEqual(archive.load_config(), expected)
            config.write_text(json.dumps({'archive_root': str(self.root / 'absent')}))
            with self.assertRaises(ArchiveError):
                archive.load_config()

    def test_path_change_requires_choice_and_can_keep_old_archive(self):
        config = self.root / 'settings' / 'archive-config.json'
        first_base = self.root / 'first'
        second_base = self.root / 'second'
        with patch.object(archive, 'config_path', return_value=config):
            first = archive.configure(first_base)
            (first / 'old.txt').write_text('old', encoding='utf-8')
            with self.assertRaisesRegex(ArchiveError, 'migrate-or-keep'):
                archive.configure(second_base)
            second = archive.configure(second_base, migrate_existing=False)
            self.assertEqual(second, second_base / archive.ARCHIVE_FOLDER_NAME)
            self.assertTrue((first / 'old.txt').is_file())
            self.assertEqual(archive.load_config(), second)

    def test_path_change_can_migrate_and_remove_old_archive(self):
        config = self.root / 'settings' / 'archive-config.json'
        first_base = self.root / 'first'
        second_base = self.root / 'second'
        with patch.object(archive, 'config_path', return_value=config):
            first = archive.configure(first_base)
            nested = first / 'sessions' / 'codex'
            nested.mkdir(parents=True)
            (nested / 'old.txt').write_text('old', encoding='utf-8')
            second = archive.configure(second_base, migrate_existing=True)
            self.assertEqual((second / 'sessions/codex/old.txt').read_text(encoding='utf-8'), 'old')
            self.assertFalse(first.exists())
            self.assertEqual(archive.load_config(), second)

    def test_path_change_rejects_new_archive_inside_old_archive(self):
        config = self.root / 'settings' / 'archive-config.json'
        first_base = self.root / 'first'
        with patch.object(archive, 'config_path', return_value=config):
            first = archive.configure(first_base)
            nested_base = first / 'nested'
            with self.assertRaisesRegex(ArchiveError, 'must not contain each other'):
                archive.configure(nested_base, migrate_existing=False)
            self.assertFalse((nested_base / archive.ARCHIVE_FOLDER_NAME).exists())
            self.assertEqual(archive.load_config(), first)

    def test_path_change_rejects_old_archive_inside_new_archive(self):
        config = self.root / 'settings' / 'archive-config.json'
        first_base = self.root / 'outer' / archive.ARCHIVE_FOLDER_NAME / 'inner'
        with patch.object(archive, 'config_path', return_value=config):
            first = archive.configure(first_base)
            outer_base = self.root / 'outer'
            with self.assertRaisesRegex(ArchiveError, 'must not contain each other'):
                archive.configure(outer_base, migrate_existing=True)
            self.assertEqual(archive.load_config(), first)

    def test_cleanup_failure_keeps_new_complete_archive_active(self):
        config = self.root / 'settings' / 'archive-config.json'
        first_base = self.root / 'first'
        second_base = self.root / 'second'
        with patch.object(archive, 'config_path', return_value=config):
            first = archive.configure(first_base)
            (first / 'old.txt').write_text('old', encoding='utf-8')
            with patch.object(archive.shutil, 'rmtree', side_effect=OSError('denied')):
                with self.assertRaisesRegex(ArchiveError, 'new path activated'):
                    archive.configure(second_base, migrate_existing=True)
            second = second_base / archive.ARCHIVE_FOLDER_NAME
            self.assertEqual(archive.load_config(), second)
            self.assertEqual((second / 'old.txt').read_text(encoding='utf-8'), 'old')

    def test_configured_archive_is_created_under_user_selected_base(self):
        config = self.root / 'settings' / 'archive-config.json'
        base = self.root / 'selected'
        with patch.object(archive, 'config_path', return_value=config):
            root = archive.configure(base)
        self.assertEqual(root, base / 'yalo note')
        self.assertTrue(root.is_dir())

    def test_temporary_attachment_and_name_collision(self):
        paths = [self.root / 'one' / 'picture.png', self.root / 'two' / 'picture.png']
        rows = []
        for i, p in enumerate(paths):
            p.parent.mkdir()
            p.write_bytes(bytes([i]))
            row = message(f'![image](<{p}>)', f'asset-{i}')
            rows.append((raw(row), row))
        assets = plan_assets(rows, self.home)
        output = archive.commit_archive(self.destination, SID, STAMP, rows, assets)
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

        assets = plan_assets([(raw(row), row)], self.home)

        self.assertEqual([asset.source for asset in assets], [attachment.resolve()])

    def test_embedded_image_and_ordinary_file_need_no_copy(self):
        row = message('text')
        row['payload']['content'].append({'type': 'input_image', 'image_url': 'data:image/png;base64,AA=='})
        self.assertEqual(plan_assets([(raw(row), row)], self.home), [])
        row = message('![ordinary](/ordinary/project/image.png)')
        with patch('session_assets.temporary_roots', return_value={self.root / 'temporary'}):
            self.assertEqual(plan_assets([(raw(row), row)], self.home), [])

    def test_missing_temporary_and_remote_image_fail(self):
        row = message(f'![image]({self.root / "missing.png"})')
        with self.assertRaises(ArchiveError):
            plan_assets([(raw(row), row)], self.home)

    def test_diagnostic_paths_are_not_attachments(self):
        row = message(f'AssertionError: WindowsPath(\'{self.root / "deleted" / "source.jsonl"}\') != expected')
        self.assertEqual(plan_assets([(raw(row), row)], self.home), [])
        row['payload']['content'] = [{'type': 'input_image', 'image_url': 'https://example.com/temporary.png'}]
        with self.assertRaises(ArchiveError):
            plan_assets([(raw(row), row)], self.home)

    def test_asset_changed_after_planning_preserves_archive(self):
        selected, started = self.prepare()
        output = archive.commit_archive(self.destination, SID, started, selected, [])
        before = output.read_bytes()
        p = self.root / 'file.png'
        p.write_bytes(b'one')
        row = message(f'![image]({p})')
        assets = plan_assets([(raw(row), row)], self.home)
        p.write_bytes(b'two')
        with self.assertRaises(ArchiveError):
            archive.commit_archive(self.destination, SID, started, selected, assets)
        self.assertEqual(output.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
