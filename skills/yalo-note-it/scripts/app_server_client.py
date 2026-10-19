"""Small JSON-RPC client for the local Codex App Server read APIs."""
from collections import deque
import json
import os
from pathlib import Path
import queue
import subprocess
import threading

from archive_errors import ArchiveError, UnsupportedAppServerError


UNSUPPORTED_CODES = {-32601}
UNSUPPORTED_TEXT = (
    'method not found',
    'unknown method',
    'unsupported method',
    'experimental api is not enabled',
    'unrecognized subcommand',
    'unknown command',
)


class AppServerClient:
    """Own one App Server process and expose validated paginated reads."""

    def __init__(self, command=None, timeout=15, page_size=100, process_factory=None,
                 codex_home=None):
        executable = os.environ.get('CODEX_APP_SERVER_COMMAND', 'codex')
        self.command = command or (executable, 'app-server', '--stdio')
        self.timeout = timeout
        self.page_size = page_size
        self.process_factory = process_factory or subprocess.Popen
        self.codex_home = Path(
            codex_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex'
        ).resolve()
        self.process = None
        self._stdout = queue.Queue()
        self._stderr = deque(maxlen=80)
        self._next_id = 1
        self.server_info = {}

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.close()

    def start(self):
        if self.process is not None:
            return
        try:
            environment = os.environ.copy()
            environment['CODEX_HOME'] = str(self.codex_home)
            self.process = self.process_factory(
                list(self.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                env=environment,
            )
        except FileNotFoundError as exc:
            raise UnsupportedAppServerError(
                'Codex App Server command is unavailable'
            ) from exc
        threading.Thread(
            target=self._read_stdout, args=(self.process.stdout,), daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr, args=(self.process.stderr,), daemon=True,
        ).start()
        try:
            result = self.request('initialize', {
                'clientInfo': {
                    'name': 'yalo-note-it',
                    'title': 'Yalo note it',
                    'version': '0.14.0',
                },
                'capabilities': {'experimentalApi': True},
            })
            if not isinstance(result, dict):
                raise ArchiveError('Invalid App Server initialize response')
            user_agent = result.get('userAgent')
            server_home = result.get('codexHome')
            if not isinstance(user_agent, str) or not user_agent:
                raise ArchiveError('App Server initialize response has no userAgent')
            if not isinstance(server_home, str) or not Path(server_home).is_absolute():
                raise ArchiveError('App Server initialize response has invalid codexHome')
            if os.path.normcase(str(Path(server_home).resolve())) != os.path.normcase(
                    str(self.codex_home)):
                raise ArchiveError('App Server initialized with an unexpected codexHome')
            self.server_info = dict(result)
            self.notify('initialized')
        except Exception:
            self.close()
            raise

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def _read_stdout(self, stream):
        try:
            for line in stream:
                self._stdout.put(line)
        finally:
            self._stdout.put(None)

    def _read_stderr(self, stream):
        for line in stream:
            self._stderr.append(line.rstrip())

    def _write(self, message):
        if self.process is None or self.process.stdin is None:
            raise ArchiveError('App Server is not running')
        try:
            self.process.stdin.write(
                json.dumps(message, ensure_ascii=False, separators=(',', ':')) + '\n'
            )
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise self._process_failure('App Server input closed') from exc

    def notify(self, method, params=None):
        message = {'method': method}
        if params is not None:
            message['params'] = params
        self._write(message)

    def request(self, method, params):
        request_id = self._next_id
        self._next_id += 1
        self._write({'id': request_id, 'method': method, 'params': params})
        while True:
            try:
                line = self._stdout.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise ArchiveError(f'App Server timed out during {method}') from exc
            if line is None:
                raise self._process_failure(f'App Server exited during {method}')
            try:
                message = json.loads(line)
            except ValueError as exc:
                raise ArchiveError('App Server emitted invalid JSON') from exc
            if not isinstance(message, dict):
                raise ArchiveError('App Server emitted a non-object message')
            if message.get('id') != request_id:
                if 'method' in message and 'id' in message:
                    self._write({
                        'id': message['id'],
                        'error': {'code': -32601, 'message': 'Unsupported client method'},
                    })
                continue
            if 'error' in message:
                raise self._rpc_failure(method, message['error'])
            if 'result' not in message:
                raise ArchiveError(f'App Server response missing result for {method}')
            return message['result']

    def _rpc_failure(self, method, error):
        if isinstance(error, dict):
            code = error.get('code')
            message = error.get('message')
        else:
            code, message = None, str(error)
        detail = message if isinstance(message, str) and message else repr(error)
        lowered = detail.lower()
        if code in UNSUPPORTED_CODES or any(text in lowered for text in UNSUPPORTED_TEXT):
            return UnsupportedAppServerError(
                f'Codex App Server does not support {method}: {detail}'
            )
        return ArchiveError(f'Codex App Server {method} failed: {detail}')

    def _process_failure(self, context):
        detail = '\n'.join(self._stderr).strip()
        message = f'{context}: {detail}' if detail else context
        lowered = message.lower()
        if any(text in lowered for text in UNSUPPORTED_TEXT):
            return UnsupportedAppServerError(message)
        return ArchiveError(message)

    def read_thread(self, thread_id):
        result = self.request('thread/read', {
            'threadId': thread_id,
            'includeTurns': False,
        })
        if not isinstance(result, dict) or not isinstance(result.get('thread'), dict):
            raise ArchiveError('Invalid thread/read response')
        return result['thread']

    def list_turns(self, thread_id):
        turns = []
        seen_cursors = set()
        cursor = None
        while True:
            params = {
                'threadId': thread_id,
                'limit': self.page_size,
                'sortDirection': 'asc',
                'itemsView': 'full',
            }
            if cursor is not None:
                params['cursor'] = cursor
            result = self.request('thread/turns/list', params)
            page, cursor = self._page(result, 'turns')
            turns.extend(page)
            if cursor is None:
                return turns
            if cursor in seen_cursors:
                raise ArchiveError('thread/turns/list returned a repeated cursor')
            seen_cursors.add(cursor)

    def list_items(self, thread_id, turn_id=None):
        entries = []
        seen_cursors = set()
        cursor = None
        while True:
            params = {
                'threadId': thread_id,
                'limit': self.page_size,
                'sortDirection': 'asc',
            }
            if turn_id is not None:
                params['turnId'] = turn_id
            if cursor is not None:
                params['cursor'] = cursor
            result = self.request('thread/items/list', params)
            page, cursor = self._page(result, 'items')
            entries.extend(page)
            if cursor is None:
                return entries
            if cursor in seen_cursors:
                raise ArchiveError('thread/items/list returned a repeated cursor')
            seen_cursors.add(cursor)

    @staticmethod
    def _page(result, label):
        if not isinstance(result, dict) or not isinstance(result.get('data'), list):
            raise ArchiveError(f'Invalid {label} page from App Server')
        cursor = result.get('nextCursor')
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            raise ArchiveError(f'Invalid {label} pagination cursor from App Server')
        return result['data'], cursor
