"""Local, byte-preserving Codex session archive. Python standard library only."""
import argparse
import json
from pathlib import Path
import sys

from archive_errors import ArchiveError
from archive_store import (
    commit_archive,
    configure,
    load_config,
)
from codex_format import reference_source
from runtime_context import codex_home, current_id
from session_assets import plan_assets
from session_source import (
    locate_source,
    prepare,
    probe,
)


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
            session_id = current_id()
            home = codex_home()
            source = locate_source(session_id, home)
            if args.command == 'probe':
                result = {
                    'session_id': session_id,
                    'source': str(source),
                    **probe(source, args.message_id),
                }
            else:
                selected, started = prepare(
                    source,
                    args.message_id,
                    args.sha256,
                    args.confirmed,
                )
                reference_sources = []
                for _, row in selected:
                    candidate = reference_source(row)
                    if candidate is not None:
                        reference_sources.append(candidate)
                assets = plan_assets(reference_sources, home)
                result = {
                    'session_id': session_id,
                    'records': len(selected),
                    'assets': len(assets),
                    'cutoff': args.message_id,
                }
                if args.command == 'archive':
                    root = load_config()
                    if root is None:
                        raise ArchiveError(
                            'Archive not configured; ask user to confirm destination first'
                        )
                    result['archive'] = str(commit_archive(
                        root,
                        session_id,
                        started,
                        selected,
                        assets,
                    ))
                else:
                    result['read_only'] = True
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ArchiveError, OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
