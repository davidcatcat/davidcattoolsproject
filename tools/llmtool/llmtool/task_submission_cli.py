"""Direct user submission to one explicitly selected PM provider profile."""

import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone

from .chat import reply_usage
from .config import api_key, resolve_profile_config, selected_provider
from .errors import LLMToolError
from .providers import create_client
from .task_store import TaskStore, new_id
from .task_verification import (AcceptanceCriterion, CriterionKind, TaskContract)
from .test_output_excerpts import _emitted_sensitive

REQUEST_LIMIT = 64 * 1024
RESPONSE_LIMIT = 64 * 1024
_PROFILE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
_SYSTEM = (
    'You are the user-selected llmtool Integrator/PM AI. Respond directly to the '
    'submitted development request with a concise understanding, proposed next '
    'planning step, and any important unknowns. Do not claim that work, tests, '
    'repository access, approvals, or execution occurred. This intake does not '
    'dispatch a Developer or authorize repository changes. Return a JSON object '
    'with schema_version=1, summary (string), and developer_assignment (null or an '
    'object containing route (company-gw/codex/opencode/none), risk (LOW/OTHER), '
    'goal (string), and acceptance (array of strings). A route is only a PM '
    'recommendation; it does not authorize or prove dispatch. Return exactly one raw '
    'JSON object with no Markdown fences and no prose before or after it.'
)


def _timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _screen_text(value, limit):
    if type(value) is not str or not value.strip():
        raise LLMToolError('request or response must be non-empty UTF-8 text')
    try:
        raw = value.encode('utf-8')
    except UnicodeError:
        raise LLMToolError('request or response must be valid UTF-8 text') from None
    if len(raw) > limit:
        raise LLMToolError('request or response exceeds the supported size')
    if any((ord(c) < 32 and c not in '\t\n\r') or 127 <= ord(c) <= 159 or
            unicodedata.category(c) == 'Cf' for c in value):
        raise LLMToolError('request or response contains unsupported control characters')
    if _emitted_sensitive(value):
        raise LLMToolError('request or response contains recognizable credential material; redact it first')
    return value


def _provider_error_code(exc):
    diagnostic = getattr(exc, 'diagnostic', None)
    code = getattr(getattr(diagnostic, 'code', None), 'value', None)
    if type(code) is str and re.fullmatch(r'[A-Z0-9_]{1,64}', code):
        return code
    return 'PM_PROVIDER_UNAVAILABLE'


def _pm_assignment(response):
    """Parse an optional structured recommendation; never infer it from prose."""
    try:
        value = json.loads(response)
        if (type(value) is not dict or
                set(value) != {'schema_version', 'summary', 'developer_assignment'} or
                type(value['schema_version']) is not int or value['schema_version'] != 1):
            return None
        _screen_text(value['summary'], 2048)
        assignment = value['developer_assignment']
        if assignment is None:
            return None
        if (type(assignment) is not dict or
                set(assignment) != {'route', 'risk', 'goal', 'acceptance'} or
                assignment['route'] not in ('company-gw', 'codex', 'opencode', 'none') or
                assignment['risk'] not in ('LOW', 'OTHER') or
                type(assignment['goal']) is not str or
                type(assignment['acceptance']) is not list or
                not 1 <= len(assignment['acceptance']) <= 16):
            return None
        result = {'schema_version': 1, **assignment}
        _screen_text(result['goal'], 512)
        result['acceptance'] = [_screen_text(item, 512) for item in result['acceptance']]
        return result
    except (ValueError, TypeError, LLMToolError, UnicodeError):
        return None


def _contract(task_id, request_sha256=None):
    goal = ('PM intake rejected; original request was not stored.'
            if request_sha256 is None else
            'PM intake for development request sha256:' + request_sha256)
    return TaskContract(
        schema_version=1,
        task_id=task_id,
        contract_version=1,
        previous_digest=None,
        goal=goal,
        authorized_scope=('pm-planning',),
        forbidden_scope=(),
        criteria=(AcceptanceCriterion(
            criterion_id='pm_response',
            kind=CriterionKind.ARTIFACT,
            description='Record the selected PM response or a truthful unavailable outcome.',
        ),),
    )


def _record(task_id, run_id, session_id, profile, prompt, *, state='PM_PENDING',
            provider_type=None, model=None, backend='NOT_STARTED', request_sha=None,
            contract=None, error_code=None):
    now = _timestamp()
    contract = _contract(task_id, request_sha) if contract is None else contract
    return {
        'schema_version': 2,
        'task_id': task_id,
        'run_id': run_id,
        'pm_session_id': session_id,
        'pm_provider_profile': profile,
        'pm_provider_type': provider_type,
        'pm_model': model,
        'backend_session_semantics': backend,
        'native_session_id': None,
        'state': state,
        'created_at': now,
        'updated_at': now,
        'task_contract': contract.to_dict(),
        'task_binding': contract.binding(run_id).to_dict(),
        'request': prompt,
        'request_sha256': request_sha,
        'response': None,
        'usage': None,
        'error_code': error_code,
        'developer': None,
        'pm_assignment': None,
        'record_sha256': None,
    }


def _print(value):
    rendered = json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
    sys.stdout.write(rendered + '\n')
    sys.stdout.flush()


def _task_view(record):
    fields = (
        'schema_version', 'task_id', 'run_id', 'pm_session_id',
        'pm_provider_profile', 'pm_provider_type', 'pm_model',
        'backend_session_semantics', 'native_session_id', 'state',
        'created_at', 'updated_at', 'task_contract', 'task_binding',
        'request', 'request_sha256', 'response',
        'usage', 'error_code', 'record_sha256',
    )
    view = {key: record[key] for key in fields}
    if record['schema_version'] == 2:
        view['pm_assignment'] = record['pm_assignment']
        view['developer'] = (None if record['developer'] is None else {
            **record['developer'],
            'usage_status': ('UNKNOWN' if record['developer']['usage'] is None else 'REPORTED'),
        })
    view['usage_status'] = 'UNKNOWN' if record['usage'] is None else 'REPORTED'
    return view


def add_task_submission_parsers(actions):
    submit = actions.add_parser('submit', help='Submit a development request to an explicit PM provider profile')
    submit.add_argument('--provider', required=True, help='configured PM provider profile; no implicit default')
    submit.add_argument('request', nargs='+', help='development request text')
    show = actions.add_parser('show', help='Retrieve one durable task and its PM outcome')
    show.add_argument('task_id', help='32-character task ID returned by task submit')
    delegate = actions.add_parser('delegate', help='Request a bounded company-gw code patch for an existing task')
    delegate.add_argument('task_id', help='existing durable Task ID')
    delegate.add_argument('--provider', required=True, help='must be the explicitly configured company-gw profile')
    delegate.add_argument('--selection-source', required=True, choices=('PM', 'USER'))
    delegate.add_argument('--source-sha', required=True, help='40-character source revision for supplied context')
    delegate.add_argument('--read-path', action='append', required=True, help='authorized relative context path; repeat as needed')
    delegate.add_argument('--write-path', action='append', required=True, help='authorized relative target path; repeat as needed')
    delegate.add_argument('--acceptance', action='append', required=True, help='bounded acceptance condition; repeat as needed')
    delegate.add_argument('--egress-basis', required=True, help='task-specific non-sensitive data authorization basis')
    delegate.add_argument('--context-stdin', action='store_true', required=True,
                          help='read bounded, sensitive-screened code context from stdin')


_HEX40 = re.compile(r'[0-9a-f]{40}')
_DIFF_HEADER = re.compile(r'^diff --git a/([^\r\n]+) b/([^\r\n]+)$')
_HUNK_HEADER = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$')


def _assignment_paths(read_paths, write_paths):
    def valid(path):
        return (type(path) is str and 0 < len(path) <= 512 and
                not path.startswith('/') and '\\' not in path and
                all(part not in ('', '.', '..') for part in path.split('/')) and
                all(0x20 <= ord(char) < 0x7f for char in path))
    if (type(read_paths) is not list or type(write_paths) is not list or
            not read_paths or not write_paths or len(read_paths) > 32 or len(write_paths) > 32 or
            any(not valid(path) for path in read_paths + write_paths) or
            len(set(read_paths)) != len(read_paths) or len(set(write_paths)) != len(write_paths) or
            not set(write_paths).issubset(read_paths)):
        raise LLMToolError('developer assignment scope is invalid')
    return sorted(read_paths), sorted(write_paths)


def _patch_paths(patch):
    if type(patch) is not str or not patch or not patch.startswith('diff --git '):
        raise ValueError
    lines = patch.splitlines()
    paths = set()
    index = 0
    while index < len(lines):
        match = _DIFF_HEADER.fullmatch(lines[index])
        if match is None:
            raise ValueError
        left, right = match.groups()
        if left.startswith('"') or right.startswith('"') or left != right:
            raise ValueError
        path = left
        if (not path or path.startswith('/') or '\\' in path or
                any(part in ('', '.', '..') for part in path.split('/'))):
            raise ValueError
        if path in paths:
            raise ValueError
        paths.add(path)
        index += 1

        # Accept only ordinary text-file metadata generated by git diff.
        while index < len(lines) and lines[index].startswith(('index ', 'old mode ', 'new mode ')):
            index += 1
        if index + 2 >= len(lines):
            raise ValueError
        if lines[index] != '--- a/' + path or lines[index + 1] != '+++ b/' + path:
            raise ValueError
        index += 2
        hunks = 0
        changed = False
        while index < len(lines) and not lines[index].startswith('diff --git '):
            hunk = _HUNK_HEADER.fullmatch(lines[index])
            if hunk is None:
                raise ValueError
            old_remaining = int(hunk.group(2) or '1')
            new_remaining = int(hunk.group(4) or '1')
            index += 1
            old_seen = new_seen = 0
            while (index < len(lines) and
                   (old_seen < old_remaining or new_seen < new_remaining)):
                line = lines[index]
                if line.startswith('\\ No newline at end of file'):
                    index += 1
                    continue
                if line.startswith(' '):
                    old_seen += 1
                    new_seen += 1
                elif line.startswith('-'):
                    old_seen += 1
                    changed = True
                elif line.startswith('+'):
                    new_seen += 1
                    changed = True
                else:
                    raise ValueError
                if old_seen > old_remaining or new_seen > new_remaining:
                    raise ValueError
                index += 1
            if old_seen != old_remaining or new_seen != new_remaining:
                raise ValueError
            hunks += 1
            while index < len(lines) and lines[index].startswith('\\ No newline at end of file'):
                index += 1
        if not hunks or not changed:
            raise ValueError
    if not paths:
        raise ValueError
    return paths


def _developer_output_rejection(value, write_paths):
    """Classify rejected output using local checks only; never return provider text."""
    if type(value) is not str or not value.strip():
        return 'DEVELOPER_OUTPUT_INVALID_ENVELOPE'
    try:
        raw = value.encode('utf-8')
    except UnicodeError:
        return 'DEVELOPER_OUTPUT_INVALID_TEXT'
    if len(raw) > RESPONSE_LIMIT:
        return 'DEVELOPER_OUTPUT_TOO_LARGE'
    if any((ord(char) < 32 and char not in '\t\n\r') or
           127 <= ord(char) <= 159 or unicodedata.category(char) == 'Cf'
           for char in value):
        return 'DEVELOPER_OUTPUT_INVALID_TEXT'
    if _emitted_sensitive(value):
        return 'DEVELOPER_OUTPUT_SENSITIVE'
    if not value.startswith('diff --git '):
        return 'DEVELOPER_OUTPUT_INVALID_ENVELOPE'
    try:
        paths = _patch_paths(value)
    except ValueError:
        return 'DEVELOPER_OUTPUT_INVALID_DIFF'
    if not paths.issubset(set(write_paths)):
        return 'DEVELOPER_OUTPUT_OUT_OF_SCOPE'
    return None


def _developer_prompt(task, *, source_sha, read_paths, write_paths, acceptance, context, pm_goal):
    packet = {
        'task_id': task['task_id'], 'run_id': task['run_id'],
        'pm_session_id': task['pm_session_id'],
        'source_sha': source_sha, 'read_paths': read_paths,
        'write_paths': write_paths, 'acceptance': acceptance, 'context': context,
        'pm_selected_goal': pm_goal,
    }
    system = (
        'You are a company-gw code Developer. Return only a plain-text unified diff '
        'that changes files within write_paths. Do not use Markdown fences and do not '
        'include prose before or after the diff. Exact minimal format example:\n'
        'diff --git a/example.py b/example.py\n'
        '--- a/example.py\n'
        '+++ b/example.py\n'
        '@@ -1 +1 @@\n'
        '-old\n'
        '+new\n'
        'Use actual paths from write_paths and real changed lines. Do not claim to run '
        'tests, execute tools, commit, dispatch another agent, approve work, or modify '
        'files. Do not include credentials, secrets, private data, or prose outside the diff.'
    )
    return [{'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(packet, ensure_ascii=True,
                                                  sort_keys=True, separators=(',', ':'))}]


def run_task_delegate(args, *, store=None):
    store = TaskStore() if store is None else store
    task = store.get(args.task_id)
    if task['state'] != 'PM_RESPONDED' or not task['response']:
        raise LLMToolError('task has no successful PM response')
    if task['schema_version'] != 2:
        raise LLMToolError('legacy task record must be reopened before delegation')
    if task['developer'] is not None:
        # A pending or ambiguous provider call is never replayed.
        raise LLMToolError('developer assignment already exists; duplicate dispatch refused')
    if args.provider != 'company-gw':
        raise LLMToolError('this task route only supports the explicitly selected company-gw profile')
    pm_assignment = task['pm_assignment']
    if args.selection_source == 'PM':
        if (type(pm_assignment) is not dict or pm_assignment.get('route') != 'company-gw' or
                pm_assignment.get('risk') != 'LOW'):
            raise LLMToolError('no validated low-risk company-gw recommendation from the selected PM')
        if args.acceptance != pm_assignment['acceptance']:
            raise LLMToolError('acceptance differs from the selected PM assignment')
    source_sha = args.source_sha
    if type(source_sha) is not str or not _HEX40.fullmatch(source_sha):
        raise LLMToolError('source SHA is invalid')
    read_paths, write_paths = _assignment_paths(args.read_path, args.write_path)
    if type(args.acceptance) is not list or not 1 <= len(args.acceptance) <= 16:
        raise LLMToolError('developer acceptance scope is invalid')
    acceptance = [_screen_text(item, 512) for item in args.acceptance]
    context = _screen_text(args.context if hasattr(args, 'context') else
                           sys.stdin.read(48 * 1024 + 1), 48 * 1024)
    egress_basis = _screen_text(args.egress_basis, 512)
    _screen_text(task['request'], REQUEST_LIMIT)
    _screen_text(task['response'], RESPONSE_LIMIT)
    messages = _developer_prompt(
        task, source_sha=source_sha, read_paths=read_paths, write_paths=write_paths,
        acceptance=acceptance, context=context,
        pm_goal=(pm_assignment['goal'] if args.selection_source == 'PM' else task['request']))
    input_bytes = len(json.dumps(messages, ensure_ascii=True, sort_keys=True,
                                 separators=(',', ':')).encode('utf-8'))
    if input_bytes > REQUEST_LIMIT:
        raise LLMToolError('developer request exceeds the supported size')
    developer = {
        'assignment_id': new_id(), 'selection_source': args.selection_source,
        'state': 'DEVELOPER_PENDING', 'provider_profile': 'company-gw',
        'provider_type': None, 'model': None,
        'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
        'session_id': 'dev-' + new_id(), 'source_sha': source_sha,
        'source_sha_binding': 'UNVERIFIED_CALLER_INPUT',
        'read_paths': read_paths, 'write_paths': write_paths,
        'acceptance': acceptance,
        'context_sha256': hashlib.sha256(context.encode('utf-8')).hexdigest(),
        'egress_basis': egress_basis, 'input_bytes': input_bytes,
        'max_tokens': None,
        'artifact_format': 'unified_diff', 'artifact': None,
        'artifact_sha256': None, 'usage': None, 'error_code': None,
        'created_at': _timestamp(),
    }
    # Persist the one-shot claim before network I/O. A crash or ambiguous result
    # remains pending; this task cannot silently trigger a duplicate provider call.
    store.compare_and_update(
        task['task_id'],
        expected={'schema_version': 2, 'state': 'PM_RESPONDED', 'developer': None},
        developer=developer,
    )
    try:
        config = resolve_profile_config('company-gw', require_configured=True)
        provider = selected_provider(config)
        if provider.get('type') != 'openai-compatible':
            raise LLMToolError('company-gw profile is not an openai-compatible route')
        credential = api_key(config)
        client = create_client(config, credential)
        max_tokens = config['generation']['max_tokens']
        if type(max_tokens) is not int or not 1 <= max_tokens <= 1_000_000:
            raise LLMToolError('company-gw output budget is invalid')
    except Exception as exc:
        code = _provider_error_code(exc).replace('PM_', 'DEVELOPER_', 1)
        developer.update(state='DEVELOPER_UNAVAILABLE', error_code=code)
        saved = store.update(task['task_id'], developer=developer)
        _print(_task_view(saved))
        return 1
    developer['state'] = 'DEVELOPER_DISPATCHING'
    developer['provider_type'] = 'openai-compatible'
    developer['model'] = provider.get('model')
    developer['max_tokens'] = max_tokens
    store.update(task['task_id'], developer=developer)
    try:
        result = client.complete(messages)
    except Exception as exc:
        code = _provider_error_code(exc).replace('PM_', 'DEVELOPER_', 1)
        developer.update(state='DEVELOPER_OUTCOME_UNKNOWN', error_code=code)
        saved = store.update(task['task_id'], developer=developer)
        _print(_task_view(saved))
        return 1
    usage = reply_usage(result.metadata)
    rejection_code = _developer_output_rejection(result.content, write_paths)
    if rejection_code is not None:
        developer.update(state='DEVELOPER_OUTPUT_REJECTED',
                         error_code=rejection_code,
                         usage=None if usage is None else dict(usage))
        saved = store.update(task['task_id'], developer=developer)
        _print(_task_view(saved))
        return 1
    artifact = result.content
    developer.update(state='DEVELOPER_RESPONDED', artifact=artifact,
                      artifact_sha256=hashlib.sha256(artifact.encode('utf-8')).hexdigest(),
                      usage=None if usage is None else dict(usage), error_code=None)
    try:
        saved = store.update(task['task_id'], developer=developer)
    except Exception:
        raise LLMToolError('developer outcome could not be persisted; details omitted') from None
    _print(_task_view(saved))
    return 0


def run_task_submit(args, *, store=None):
    store = TaskStore() if store is None else store
    profile = args.provider
    if type(profile) is not str or _PROFILE.fullmatch(profile) is None:
        raise LLMToolError('select one valid configured PM provider profile')
    prompt = ' '.join(args.request)
    task_id, run_id = new_id(), new_id()
    session_id = 'pm-' + new_id()
    try:
        _screen_text(prompt, REQUEST_LIMIT)
    except LLMToolError:
        contract = _contract(task_id)
        record = _record(task_id, run_id, session_id, profile, None,
                         state='INPUT_REJECTED', request_sha=None, contract=contract,
                         error_code='PM_INPUT_REJECTED')
        store.create(record)
        _print(_task_view(store.get(task_id)))
        return 1

    digest = hashlib.sha256(prompt.encode('utf-8', 'strict')).hexdigest()
    contract = _contract(task_id, digest)
    record = _record(task_id, run_id, session_id, profile, prompt,
                     request_sha=digest, contract=contract)
    store.create(record)  # Durable IDs and request precede any provider call.

    try:
        config = resolve_profile_config(profile, require_configured=True)
    except Exception as exc:
        code = _provider_error_code(exc)
        if code == 'PM_PROVIDER_UNAVAILABLE':
            code = 'PM_PROVIDER_NOT_CONFIGURED'
        failed = store.update(task_id, state='PM_UNAVAILABLE', response=None,
                              usage=None, error_code=code)
        _print(_task_view(failed))
        return 1

    try:
        provider = selected_provider(config)
        backend = ('EPHEMERAL_SINGLE_CALL' if provider['type'] == 'codex-cli'
                   else 'NO_NATIVE_SESSION_ID_EXPOSED')
        store.update(task_id, pm_provider_type=provider['type'], pm_model=provider.get('model'),
                     backend_session_semantics=backend)
        result = create_client(config, api_key(config)).complete([
            {'role': 'system', 'content': _SYSTEM},
            {'role': 'user', 'content': prompt},
        ])
    except LLMToolError as exc:
        failed = store.update(task_id, state='PM_UNAVAILABLE', response=None,
                              usage=None, error_code=_provider_error_code(exc))
        _print(_task_view(failed))
        return 1
    except Exception:
        failed = store.update(task_id, state='PM_UNAVAILABLE', response=None,
                              usage=None, error_code='PM_PROVIDER_UNAVAILABLE')
        _print(_task_view(failed))
        return 1

    try:
        response = _screen_text(result.content, RESPONSE_LIMIT)
    except LLMToolError:
        rejected = store.update(task_id, state='PM_RESPONSE_REJECTED', response=None,
                                usage=None, error_code='PM_RESPONSE_REJECTED')
        _print(_task_view(rejected))
        return 1
    usage = reply_usage(result.metadata)
    stored_usage = None if usage is None else dict(usage)
    try:
        completed = store.update(task_id, state='PM_RESPONDED', response=response,
                                 usage=stored_usage, pm_assignment=_pm_assignment(response),
                                 error_code=None)
    except Exception:
        # Do not print an unpersisted provider response as a completed Task.
        raise LLMToolError('task outcome could not be saved; details omitted') from None
    _print(_task_view(completed))
    return 0


def run_task_show(args, *, store=None):
    store = TaskStore() if store is None else store
    _print(_task_view(store.get(args.task_id)))
    return 0
