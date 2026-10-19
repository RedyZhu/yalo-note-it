"""Create a local, verifiable Codex thread archive. Standard library only."""
import argparse
import json
from pathlib import Path
import sys

from archive_errors import ArchiveError, UnsupportedAppServerError
from archive_store import (
    commit_archive,
    configure,
    load_config,
)
from app_server_client import AppServerClient
from runtime_context import codex_home, current_identity
from session_source import (
    locate_source,
    prepare_package,
    probe,
)
from thread_source import ThreadArchiveSource


def _package_result(package, read_only):
    return {
        'thread_id': package.thread_id,
        'session_id': package.session_id,
        'source': package.source,
        'records': package.record_count,
        'turns': package.turn_count,
        'assets': len(package.assets),
        'cutoff': package.cutoff_id,
        'read_only': read_only,
    }


def _app_server_operation(args, identity, home):
    with AppServerClient(codex_home=home) as client:
        source = ThreadArchiveSource(client, identity, home)
        if args.command == 'probe':
            return source.probe(args.message_id)
        package = source.prepare(
            args.message_id,
            args.sha256,
            args.confirmed,
        )
        result = _package_result(package, args.command == 'check')
        if args.command == 'archive':
            root = load_config()
            if root is None:
                raise ArchiveError(
                    'Archive not configured; ask user to confirm destination first'
                )
            result['archive'] = str(commit_archive(root, package))
            result.pop('read_only')
        return result


def _rollout_operation(args, identity, home):
    source = locate_source(identity, home)
    if args.command == 'probe':
        return {
            'thread_id': identity.thread_id,
            'session_id': identity.session_id,
            'source': str(source),
            'source_kind': 'rollout-jsonl-fallback',
            **probe(source, args.message_id),
        }
    package = prepare_package(
        source,
        identity,
        args.message_id,
        args.sha256,
        home,
        args.confirmed,
    )
    result = _package_result(package, args.command == 'check')
    if args.command == 'archive':
        root = load_config()
        if root is None:
            raise ArchiveError(
                'Archive not configured; ask user to confirm destination first'
            )
        result['archive'] = str(commit_archive(root, package))
        result.pop('read_only')
    return result


def _conversation_operation(args, identity, home):
    try:
        return _app_server_operation(args, identity, home)
    except UnsupportedAppServerError:
        return _rollout_operation(args, identity, home)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    config = sub.add_parser('configure')
    config.add_argument('--root', type=Path, required=True)
    config.add_argument('--user-confirmed', action='store_true', required=True)
    migration = config.add_mutually_exclusive_group()
    migration.add_argument('--migrate-existing', action='store_true')
    migration.add_argument('--keep-existing', action='store_true')
    inspect = sub.add_parser('probe')
    inspect.add_argument('--message-id')
    for verb in ('check', 'archive'):
        command = sub.add_parser(verb)
        command.add_argument('--message-id', required=True)
        command.add_argument('--sha256', required=True)
        command.add_argument(
            '--confirmed',
            action='store_true',
            help='Only after explicit user archive confirmation',
        )
    args = parser.parse_args()
    try:
        if args.command == 'status':
            root = load_config()
            result = {'configured': root is not None, 'archive_root': str(root) if root else None}
        elif args.command == 'configure':
            choice = True if args.migrate_existing else False if args.keep_existing else None
            result = {'archive_root': str(configure(args.root, choice))}
        else:
            identity = current_identity()
            home = codex_home()
            result = _conversation_operation(args, identity, home)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ArchiveError, OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
