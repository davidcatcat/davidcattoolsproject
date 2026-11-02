"""Shared fixtures: a local fake OpenAI-compatible server and a temp repo.

Everything is offline: the server binds 127.0.0.1 only, and fake secrets are
assembled from fragments so no literal credential appears in the source.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from api_agent_gateway import cli  # noqa: E402
from api_agent_gateway.audit import verify  # noqa: E402

# Fake credentials, built from fragments (never literal in source).
FAKE_AWS = 'AK' + 'IA' + 'ABCDEFGHIJKLMNOP'
FAKE_GH = 'gh' + 'p_' + 'Zx9' * 10
FAKE_API_KEY = 'unit-' + 'test-' + 'provider-' + 'key-' + '7f3a9c'
PEM_HEAD = '-----BEGIN ' + 'RSA PRIVATE ' + 'KEY-----'
PEM_TAIL = '-----END ' + 'RSA PRIVATE ' + 'KEY-----'
PEM_BODY = 'MIIEpAIBAAKCAQEAx' + 'Q7bR2s9kLmN4pW8vT1yZ3cF6hJ0aE5uI2oK7nB4mD9gH1jL6qS3wX8eR5tY2uV'

GIT_ENV = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'LC_ALL': 'C'}


def git(repo: str, *args: str) -> str:
    out = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
                          '-c', 'init.defaultBranch=main', '-c', 'commit.gpgsign=false', '-C', repo, *args],
                         env=GIT_ENV, capture_output=True, check=True, stdin=subprocess.DEVNULL)
    return out.stdout.decode().strip()


def tool_call(name: str, call_id: str = None, **args) -> dict:
    return {'id': call_id or f'call_{name}_{time.monotonic_ns()}', 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)}}


def reply(*calls: dict, content=None, usage=(10, 5), model='fake-served-model',
          finish_reason=None, reasoning_content=None) -> dict:
    msg = {'role': 'assistant', 'content': content}
    if reasoning_content is not None:
        msg['reasoning_content'] = reasoning_content
    if calls:
        msg['tool_calls'] = list(calls)
    body = {'id': 'x', 'object': 'chat.completion', 'model': model,
            'choices': [{'index': 0, 'message': msg,
                         'finish_reason': finish_reason or ('tool_calls' if calls else 'stop')}]}
    if usage is not None:
        body['usage'] = {'prompt_tokens': usage[0], 'completion_tokens': usage[1]}
    return body


class FakeServer:
    """Scripted chat/completions endpoint on loopback; records every request."""

    def __init__(self):
        self.script: list = []
        self.requests: list[dict] = []
        self.raw: list[bytes] = []
        self.headers: list[dict] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                data = self.rfile.read(length)
                server.raw.append(data)
                server.headers.append(dict(self.headers))
                body = json.loads(data)
                server.requests.append(body)
                item = server.script.pop(0) if server.script else reply(tool_call('finish', summary='auto'))
                if callable(item):
                    item = item(body)
                if isinstance(item, tuple) and item[0] == 'drip':
                    _, delay, count = item
                    self.send_response(200)
                    self.send_header('Content-Length', str(count))
                    self.end_headers()
                    try:
                        for _ in range(count):
                            self.wfile.write(b' ')
                            self.wfile.flush()
                            time.sleep(delay)
                    except OSError:
                        pass
                    return
                if isinstance(item, int):
                    self.send_response(item)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                out = json.dumps(item).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def all_text(self) -> str:
        return b'\n'.join(self.raw).decode('utf-8')


class GatewayCase(unittest.TestCase):
    """Temp repo + outside dir + state dir + profiles + fake server."""

    files = {
        'src/app.py': 'def greet():\n    return "hello"\n',
        'src/util.py': 'X = 1\n',
        'tests/test_app.py': 'import app\n',
        'docs/private.md': 'OUT OF SCOPE CONTENT 4471\n',
        'README.md': 'readme\n',
    }

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='gwagent-test-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, 'repo')
        os.mkdir(self.repo)
        git(self.repo, 'init', '-q')
        for rel, text in self.files.items():
            path = os.path.join(self.repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as fh:
                fh.write(text)
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'init')
        self.sha = git(self.repo, 'rev-parse', 'HEAD')
        self.outside = os.path.join(self.tmp, 'outside')
        os.mkdir(self.outside)
        with open(os.path.join(self.outside, 'secret.txt'), 'w') as fh:
            fh.write('OUTSIDE SECRET 9912\n')
        self.state = os.path.join(self.tmp, 'state')
        self.out = os.path.join(self.tmp, 'out')
        os.mkdir(self.out)
        self.server = FakeServer()
        self.addCleanup(self.server.close)
        self.profile = {
            'adapter': 'openai-chat-tools', 'base_url': f'http://127.0.0.1:{self.server.port}/v1',
            'model': 'fake-model', 'auth_env': 'GW_TEST_KEY', 'context_cap_tokens': 200000,
            'max_output_tokens': 4096, 'allowed_data_classes': ['synthetic', 'internal-code'],
            'session_expiry_hours': 72, 'timeout_seconds': 10,
        }
        self.write_profiles()
        self.counter = 0

    def write_profiles(self, **overrides):
        profile = dict(self.profile, **overrides)
        path = os.path.join(self.tmp, 'profiles.json')
        if os.path.exists(path):
            os.unlink(path)
        with open(path, 'w') as fh:
            json.dump({'schema_version': 1, 'profiles': {'fake': profile}}, fh)
        self.profiles_path = path

    def task(self, **overrides) -> dict:
        task = {
            'schema_version': 1, 'task_id': 'task-001', 'source_sha': self.sha,
            'goal': 'Make greet return a friendlier greeting GOALMARK', 'acceptance': ['greet returns hi'],
            'read_paths': ['src/', 'tests/test_app.py'], 'write_paths': ['src/app.py', 'tests/'],
            'egress_basis': 'owner-grant-ref-123', 'data_class': 'synthetic',
            'budgets': {'max_turns': 10, 'max_input_tokens': 1_000_000, 'max_output_tokens': 100_000,
                        'max_wall_seconds': 600, 'max_tool_result_bytes': 65536, 'max_sent_bytes': 10_000_000},
        }
        for k, v in overrides.items():
            if v is None:
                task.pop(k, None)
            else:
                task[k] = v
        return task

    def _file(self, prefix: str, obj=None) -> str:
        self.counter += 1
        path = os.path.join(self.out, f'{prefix}-{self.counter}.json')
        if obj is not None:
            with open(path, 'w') as fh:
                json.dump(obj, fh)
        return path

    def invoke(self, command: str, task: dict, *, session_id=None, auth=None, workdir=None,
               clock=time.time, hook=None, env_extra=None) -> tuple[int, dict, str]:
        task_file = self._file('task', task)
        result_file = self._file('result')
        audit_file = os.path.join(self.out, f'audit-{self.counter}.jsonl')
        argv = [command]
        if session_id:
            argv.append(session_id)
        if command == 'run' and auth is None:
            auth = self.auth_for(None, task)
        if auth is not False:
            argv += ['--authorization-file', auth if isinstance(auth, str) else self._file('auth', auth)]
        argv += ['--workdir', workdir or self.repo, '--profile', 'fake', '--task-file', task_file,
                 '--result-file', result_file, '--audit-file', audit_file, '--state-dir', self.state,
                 '--profiles-file', self.profiles_path]
        env = {'GW_TEST_KEY': FAKE_API_KEY, 'HOME': self.tmp}
        env.update(env_extra or {})
        code = cli.main(argv, env=env, clock=clock, workspace_hook=hook)
        with open(result_file) as fh:
            result = json.load(fh)
        return code, result, audit_file

    def run_task(self, task=None, **kw):
        return self.invoke('run', task or self.task(), **kw)

    def auth_for(self, sid: str, task: dict, *, checked_at=None, **overrides) -> dict:
        from api_agent_gateway.contract import Refusal, parse_task
        raw = json.dumps(task).encode()
        try:
            digest = parse_task(json.loads(raw), raw).scope_digest()
        except Refusal:
            digest = '0' * 64  # task itself is invalid; refused before the record matters
        auth = {'schema_version': 1, 'authorization_ref': 'llmtool-recheck-1',
                'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(checked_at or time.time())),
                'scope_digest': digest, 'task_id': task.get('task_id'), 'session_id': sid,
                'egress_basis': task.get('egress_basis'), 'data_class': task.get('data_class'),
                'profile': 'fake'}
        auth.update(overrides)
        return auth

    def resume(self, session_id: str, task: dict, auth=None, **kw):
        if auth is None:
            auth = self.auth_for(session_id, task)
        return self.invoke('resume', task, session_id=session_id, auth=auth, **kw)

    def sessions(self, *extra) -> tuple[int, dict]:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(['sessions', '--json', '--state-dir', self.state, *extra], env={'HOME': self.tmp})
        return code, json.loads(buf.getvalue())

    def end(self, session_id: str, reason='ended') -> tuple[int, dict]:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(['end', session_id, '--reason', reason, '--state-dir', self.state],
                            env={'HOME': self.tmp})
        return code, json.loads(buf.getvalue())

    def read(self, rel: str) -> str:
        with open(os.path.join(self.repo, rel)) as fh:
            return fh.read()

    def assert_audit_ok(self, audit_file: str, result: dict):
        self.assertEqual(verify(audit_file), result['audit_sha256'])
        self.assertEqual(os.stat(audit_file).st_mode & 0o777, 0o600)

    def state_bytes(self) -> bytes:
        blob = b''
        for dirpath, _, names in os.walk(self.state):
            for name in names:
                with open(os.path.join(dirpath, name), 'rb') as fh:
                    blob += fh.read()
        return blob
