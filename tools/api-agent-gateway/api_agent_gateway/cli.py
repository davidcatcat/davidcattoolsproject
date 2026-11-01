"""``gw-agent`` command line: run, resume, sessions, end."""
from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import Callable, Mapping, Optional

from . import __version__
from .adapters import make_adapter
from .agent import AgentLoop, add_resume_round, new_history
from .audit import AuditError, AuditLog, is_int, sha256_hex
from .confine import ConfinementError, Workspace
from .contract import (EXIT_CODES, EXIT_INTERNAL, EXIT_USAGE, MODEL_ERROR, POLICY_REFUSED, SCHEMA_VERSION,
                       Profile, Refusal, Task, outside, parse_profile, parse_task, read_json_file,
                       write_result)
from .screen import Screener
from .sessions import END_REASONS, Store, valid_id
from .tools import ToolExecutor

AUTH_MAX_AGE_SECONDS = 900
AUTH_MAX_SKEW_SECONDS = 60
_ISO = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')


def _default_state_dir(env: Mapping[str, str]) -> str:
    if env.get('GW_AGENT_STATE_DIR'):
        return env['GW_AGENT_STATE_DIR']
    return os.path.join(env.get('HOME') or os.path.expanduser('~'), '.local', 'state', 'api-agent-gateway')


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='gw-agent', description='API Agent Gateway (phase 1)')
    parser.add_argument('--version', action='version', version=__version__)
    sub = parser.add_subparsers(dest='command', required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument('--state-dir')
        p.add_argument('--profiles-file')

    for name in ('run', 'resume'):
        p = sub.add_parser(name)
        if name == 'resume':
            p.add_argument('session_id')
        p.add_argument('--authorization-file', required=True)
        p.add_argument('--workdir', required=True)
        p.add_argument('--profile', required=True)
        p.add_argument('--task-file', required=True)
        p.add_argument('--result-file', required=True)
        p.add_argument('--audit-file', required=True)
        common(p)
    p = sub.add_parser('sessions')
    p.add_argument('--json', action='store_true', required=True)
    common(p)
    p = sub.add_parser('end')
    p.add_argument('session_id')
    p.add_argument('--reason', choices=('ended', 'task_terminal'), default='ended')
    common(p)
    return parser


# -- git (read-only, fixed arguments) -----------------------------------------
def _git(root: str, *args: str) -> subprocess.CompletedProcess:
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0', 'HOME': os.devnull, 'LC_ALL': 'C'}
    cmd = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=' + os.devnull, '-C', root, *args]
    try:
        return subprocess.run(cmd, env=env, capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refusal(POLICY_REFUSED, 'GIT_UNAVAILABLE') from exc


def _check_head(root: str, task: Task) -> None:
    top = _git(root, 'rev-parse', '--show-toplevel')
    if top.returncode or os.path.realpath(top.stdout.decode().strip()) != os.path.realpath(root):
        raise Refusal(POLICY_REFUSED, 'WORKDIR_NOT_REPO_ROOT')
    head = _git(root, 'rev-parse', '--verify', 'HEAD^{commit}')
    if head.returncode or head.stdout.decode().strip() != task.source_sha:
        raise Refusal(POLICY_REFUSED, 'SOURCE_SHA_MISMATCH')


def _is_ancestor(root: str, old: str, new: str) -> bool:
    if old == new:
        return True
    return _git(root, 'merge-base', '--is-ancestor', old, new).returncode == 0


def _parse_iso(value: object) -> Optional[float]:
    if not isinstance(value, str) or not _ISO.fullmatch(value):
        return None
    try:
        return float(calendar.timegm(time.strptime(value, '%Y-%m-%dT%H:%M:%SZ')))
    except ValueError:
        return None


def _check_authorization(path: str, sid: Optional[str], meta: Optional[dict], task: Task, profile: Profile,
                         now: float) -> str:
    """Validate llmtool's fresh authorization record before any provider call.

    ``run`` (sid/meta None) and ``resume`` use the same record format; the
    record must bind this task's computed scope_digest, task_id, egress_basis,
    data_class and profile, and (for resume) the session id.
    """
    bad = Refusal(POLICY_REFUSED, 'SESSION_AUTHORIZATION_INVALID' if sid else 'RUN_AUTHORIZATION_INVALID')
    try:
        obj, raw = read_json_file(path, 'AUTHORIZATION')
    except Refusal as exc:
        raise bad from exc
    if not isinstance(obj, dict) or not is_int(obj.get('schema_version'), SCHEMA_VERSION):
        raise bad
    ref = obj.get('authorization_ref')
    checked = _parse_iso(obj.get('checked_at'))
    if not isinstance(ref, str) or not ref.strip() or len(ref) > 512 or checked is None:
        raise bad
    if not (-AUTH_MAX_SKEW_SECONDS <= now - checked <= AUTH_MAX_AGE_SECONDS):
        raise bad
    if obj.get('scope_digest') != task.scope_digest() or obj.get('task_id') != task.task_id:
        raise bad
    if meta is not None and obj.get('scope_digest') != meta.get('scope_digest'):
        raise bad
    if obj.get('session_id') != sid:  # must be null/absent for run
        raise bad
    if (obj.get('egress_basis') != task.egress_basis or obj.get('data_class') != task.data_class
            or obj.get('profile') != profile.name):
        raise bad
    return sha256_hex(raw)


def _check_private_file(path: str) -> None:
    """The profiles file chooses base_url: it must be ours and not writable by others."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise Refusal(POLICY_REFUSED, 'PROFILES_UNREADABLE') from exc
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o022:
        raise Refusal(POLICY_REFUSED, 'PROFILES_UNSAFE')


class Invocation:
    """One run or resume: everything before the first provider call is a check."""

    def __init__(self, args: argparse.Namespace, env: Mapping[str, str], clock: Callable[[], float],
                 hook=None):
        self.args = args
        self.env = env
        self.clock = clock
        self.hook = hook
        self.run_id = secrets.token_hex(12)
        self.audit: Optional[AuditLog] = None
        self.header: dict = {'run_id': self.run_id, 'command': args.command, 'task_id': None,
                             'source_sha': None, 'workdir': None, 'profile': None, 'profile_sha256': None,
                             'budgets': None, 'task_sha256': None, 'scope_digest': None,
                             'session_id': getattr(args, 'session_id', None), 'authorization_sha256': None,
                             'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(clock()))}
        self._header_written = False
        self.session_id: Optional[str] = None
        self.task: Optional[Task] = None

    def _record(self, record_type: str, **fields) -> None:
        if self.audit is None:
            return
        if not self._header_written:
            self.audit.append('header', **self.header)
            self._header_written = True
        self.audit.append(record_type, **fields)

    def _result(self, status: str, code: Optional[str], **extra) -> int:
        result = {
            'schema_version': SCHEMA_VERSION, 'status': status, 'code': code, 'run_id': self.run_id,
            'session_id': self.session_id, 'task_id': self.task.task_id if self.task else None,
            'changed_paths': [], 'summary': '', 'served_model': 'UNKNOWN',
            'usage': {'status': 'UNKNOWN', 'calls': 0, 'calls_without_usage': 0,
                      'input_tokens': None, 'output_tokens': None},
            'turns': 0, 'budget_used': None, 'audit_sha256': None,
        }
        result.update(extra)
        if self.audit is not None:
            self._record('end', status=status, code=code, session_id=self.session_id,
                         turns=result['turns'], changed_paths=len(result['changed_paths']))
            result['audit_sha256'] = self.audit.last_sha256
            self.audit.close()
        try:
            write_result(self.args.result_file, result)
        except OSError:
            print('gw-agent: cannot write result file', file=sys.stderr)
            return EXIT_INTERNAL
        return EXIT_CODES[status]

    def execute(self) -> int:
        args = self.args
        if not outside(args.result_file, args.workdir) or not outside(args.audit_file, args.workdir):
            # Do not create files inside the workdir; report on stderr only.
            print('gw-agent: result and audit files must be outside the workdir', file=sys.stderr)
            return EXIT_USAGE
        if os.path.abspath(args.result_file) == os.path.abspath(args.audit_file):
            print('gw-agent: result and audit files must differ', file=sys.stderr)
            return EXIT_USAGE
        try:
            self.audit = AuditLog(args.audit_file)
        except AuditError:
            return self._result(POLICY_REFUSED, 'AUDIT_FILE_UNAVAILABLE')
        try:
            try:
                return self._execute()
            except Refusal as exc:
                self._record('refusal', code=exc.code)
                return self._result(exc.status, exc.code)
            except Exception as exc:  # contract: always a result and an end record
                self._record('refusal', code='INTERNAL_ERROR', error=type(exc).__name__)
                return self._result(POLICY_REFUSED, 'INTERNAL_ERROR')
        finally:
            if self._lock is not None:
                self._lock.release()

    _lock = None
    _workspace = None

    def _execute(self) -> int:
        args = self.args
        state_dir = args.state_dir or _default_state_dir(self.env)
        store = Store(state_dir, clock=self.clock)
        store.sweep()
        meta = None
        if args.command == 'resume':
            sid = args.session_id
            if not valid_id(sid):
                raise Refusal(POLICY_REFUSED, 'SESSION_NOT_FOUND')
            store.load_meta(sid)
            self._lock = store.lock(sid)
            meta = store.load_meta(sid)
            self.session_id = sid
            if store.expired(meta):
                # sweep() skips sessions whose lock was busy; re-check under our lock.
                store.delete(sid, 'expired', meta)
                raise Refusal(POLICY_REFUSED, 'SESSION_ENDED')
        task_obj, raw = read_json_file(args.task_file, 'TASK')
        self.header['task_sha256'] = sha256_hex(raw)
        task = parse_task(task_obj, raw)
        self.task = task
        self.header.update(task_id=task.task_id, source_sha=task.source_sha, budgets=task.budgets,
                           scope_digest=task.scope_digest())
        profiles_path = args.profiles_file or self.env.get('GW_AGENT_PROFILES') or os.path.join(state_dir, 'profiles.json')
        _check_private_file(profiles_path)
        profiles, _ = read_json_file(profiles_path, 'PROFILES')
        profile = parse_profile(profiles, args.profile)
        self.header.update(profile=profile.name, profile_sha256=profile.sha256)
        if task.data_class not in profile.allowed_data_classes:
            raise Refusal(POLICY_REFUSED, 'DATA_CLASS_NOT_AUTHORIZED')
        try:
            ws = Workspace(args.workdir, task.scope, hook=self.hook)
        except ConfinementError as exc:
            raise Refusal(POLICY_REFUSED, exc.code) from exc
        try:
            return self._with_workspace(store, task, profile, ws, meta)
        finally:
            ws.close()

    def _with_workspace(self, store: Store, task: Task, profile: Profile, ws: Workspace,
                        meta: Optional[dict]) -> int:
        args = self.args
        workdir = {'dev': ws.identity[0], 'ino': ws.identity[1]}
        self.header['workdir'] = workdir
        if meta is not None:
            sid = self.session_id
            if meta.get('workdir') != workdir:
                raise Refusal(POLICY_REFUSED, 'SESSION_WORKDIR_MISMATCH')
            if meta.get('profile') != profile.name or meta.get('profile_sha256') != profile.sha256:
                raise Refusal(POLICY_REFUSED, 'SESSION_PROFILE_MISMATCH')
            if meta.get('task_id') != task.task_id:
                raise Refusal(POLICY_REFUSED, 'SESSION_TASK_MISMATCH')
            if meta.get('scope_digest') != task.scope_digest():
                raise Refusal(POLICY_REFUSED, 'SESSION_SCOPE_CHANGED')
            self.header['authorization_sha256'] = _check_authorization(
                args.authorization_file, sid, meta, task, profile, self.clock())
        else:
            self.header['authorization_sha256'] = _check_authorization(
                args.authorization_file, None, None, task, profile, self.clock())
        _check_head(args.workdir, task)
        if meta is not None and not _is_ancestor(args.workdir, meta.get('source_sha', ''), task.source_sha):
            raise Refusal(POLICY_REFUSED, 'SESSION_HEAD_NOT_DESCENDANT')
        api_key = None
        if profile.auth_env:
            api_key = self.env.get(profile.auth_env)
            if not api_key:
                raise Refusal(MODEL_ERROR, 'AUTH_ENV_MISSING')
        adapter = make_adapter(profile, api_key)
        screener = Screener([api_key] if api_key else [])
        if meta is None:
            meta = {'schema_version': SCHEMA_VERSION, 'workdir': workdir, 'source_sha': task.source_sha,
                    'profile': profile.name, 'profile_sha256': profile.sha256, 'task_id': task.task_id,
                    'scope_digest': task.scope_digest(), 'expiry_hours': profile.session_expiry_hours,
                    'turns': 0, 'runs': 0, 'last_status': None}
            sid, self._lock = store.create(meta)
            meta = store.load_meta(sid)
            self.session_id = sid
            history = new_history(task)
        else:
            sid = self.session_id
            history = store.load_history(sid)
            add_resume_round(history, task)
        persist = lambda hist: store.save_history(sid, hist)  # noqa: E731
        persist(history)
        loop = AgentLoop(task=task, profile=profile, executor=ToolExecutor(ws, task.budgets['max_tool_result_bytes']),
                         adapter=adapter, audit=_Recorder(self), screener=screener, history=history,
                         clock=self.clock, persist=persist)
        outcome = loop.run()
        meta.update(last_turn_at=self.clock(), source_sha=task.source_sha, runs=int(meta.get('runs', 0)) + 1,
                    turns=int(meta.get('turns', 0)) + outcome.turns, last_status=outcome.status,
                    expiry_hours=min(int(meta['expiry_hours']), profile.session_expiry_hours))
        store.save_meta(sid, meta)
        return self._result(outcome.status, outcome.code, changed_paths=outcome.changed_paths,
                            summary=outcome.summary, served_model=outcome.served_model, usage=outcome.usage,
                            turns=outcome.turns, budget_used=outcome.budget_used)


class _Recorder:
    """Adapter so the loop writes through the invocation (header first)."""

    def __init__(self, inv: Invocation):
        self._inv = inv

    def append(self, record_type: str, **fields) -> None:
        self._inv._record(record_type, **fields)


def _simple(args: argparse.Namespace, env: Mapping[str, str], clock) -> int:
    try:
        store = Store(args.state_dir or _default_state_dir(env), clock=clock)
        store.sweep()
        if args.command == 'sessions':
            print(json.dumps({'schema_version': SCHEMA_VERSION, 'sessions': store.listing()}, sort_keys=True))
            return 0
        tomb = store.end(args.session_id, args.reason)
        print(json.dumps({'schema_version': SCHEMA_VERSION, 'status': 'ENDED', 'session_id': args.session_id,
                          'end_reason': tomb['end_reason']}, sort_keys=True))
        return 0
    except Refusal as exc:
        print(json.dumps({'schema_version': SCHEMA_VERSION, 'status': exc.status, 'code': exc.code}, sort_keys=True))
        return EXIT_CODES[exc.status]


def main(argv: Optional[list[str]] = None, *, env: Optional[Mapping[str, str]] = None,
         clock: Callable[[], float] = time.time, workspace_hook=None) -> int:
    env = os.environ if env is None else env
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else 0
    if args.command in ('sessions', 'end'):
        return _simple(args, env, clock)
    return Invocation(args, env, clock, hook=workspace_hook).execute()


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())
