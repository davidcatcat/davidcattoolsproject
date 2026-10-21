"""Direct user submission to one explicitly selected PM provider profile."""

import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .chat import reply_usage
from .config import api_key, resolve_profile_config, selected_provider
from .errors import LLMToolError
from .providers import create_client
from .providers.cli_lifecycle import CLIHostTermination
from .task_store import TaskStore, new_id
from . import task_completion
from . import opencode_direct_developer
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
_COMPLETION_SYSTEM = (
    'You are the original llmtool PM for this Task. Review only the supplied bounded '
    'evidence summary. Choose exactly one decision: ACCEPT, RETURN_TO_DEVELOPER, '
    'REASSIGN_DEVELOPER, WAIT_FOR_AUTHORIZATION, or STOP. State what evidence supports '
    'your recommendation and any missing concern. This is a recommendation only: it '
    'does not authorize execution, review, Git, PR, merge, spending, or provider changes. '
    'Return one raw JSON object with schema_version=1, decision, and summary; no prose '
    'outside the object.'
)
_EXECUTOR_RESULT_SYSTEM = (
    'You are the original llmtool PM for this Task. Review only the supplied bounded '
    'Executor result and local test evidence. Formal independent review, PR and CI '
    'have not yet been completed. Choose exactly one decision: SEND_TO_REVIEW, '
    'RETURN_TO_DEVELOPER, REASSIGN_DEVELOPER, WAIT_FOR_AUTHORIZATION, or STOP. '
    'Do not treat this Executor result as accepted product work. This is a '
    'recommendation only and grants no execution, review, Git, PR, merge, spending, '
    'or provider authority. Return one raw JSON object with schema_version=1, '
    'decision, and summary; no prose outside the object.'
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


def _provider_route_binding(provider):
    """Keep only normalized, non-secret PM route identity in the Task record."""
    provider_type = provider.get('type')
    model = provider.get('model')
    base_url = provider.get('base_url')
    if (type(provider_type) is not str or
            (model is not None and type(model) is not str) or
            (base_url is not None and type(base_url) is not str)):
        raise LLMToolError('PM provider route identity is invalid')
    provider_type = provider_type.lower()
    if base_url is None:
        normalized_url = None
    else:
        try:
            if any(c.isspace() or ord(c) < 32 for c in base_url):
                raise ValueError
            parsed = urlsplit(base_url)
            hostname = parsed.hostname
            if (parsed.scheme.lower() not in ('http', 'https') or not hostname or
                    parsed.username is not None or parsed.password is not None or
                    parsed.path not in ('', '/') or parsed.query or parsed.fragment):
                raise ValueError
            hostname = hostname.rstrip('.').encode('idna').decode('ascii').lower()
            port = parsed.port
            if port is not None and not 1 <= port <= 65535:
                raise ValueError
            authority_host = '[' + hostname + ']' if ':' in hostname else hostname
            if port is not None and not (parsed.scheme.lower() == 'http' and port == 80 or
                                         parsed.scheme.lower() == 'https' and port == 443):
                authority_host += ':' + str(port)
            normalized_url = parsed.scheme.lower() + '://' + authority_host
        except (UnicodeError, ValueError):
            raise LLMToolError('PM provider route identity is invalid') from None
    return {'type': provider_type, 'model': model, 'base_url': normalized_url}


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


def _strict_json_object(raw):
    """Parse bounded JSON while rejecting duplicate keys and non-standard constants."""
    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON key')
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=unique_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(value) is not dict:
        raise ValueError('JSON object required')
    return value


def _read_pm_record_input(stream=None):
    stream = sys.stdin if stream is None else stream
    source = getattr(stream, 'buffer', stream)
    raw = source.read(REQUEST_LIMIT + RESPONSE_LIMIT + 1024 + 1)
    if type(raw) is str:
        try:
            raw = raw.encode('utf-8')
        except UnicodeError:
            raise LLMToolError('PM record input must be valid UTF-8 JSON') from None
    if type(raw) is not bytes or len(raw) > REQUEST_LIMIT + RESPONSE_LIMIT + 1024:
        raise LLMToolError('PM record input exceeds the supported size')
    try:
        envelope = _strict_json_object(raw.decode('utf-8', 'strict'))
    except (UnicodeError, ValueError, TypeError):
        raise LLMToolError('PM record input must be one strict JSON object') from None
    if set(envelope) != {'request', 'response'}:
        raise LLMToolError('PM record input must contain only request and response')
    request = _screen_text(envelope['request'], REQUEST_LIMIT)
    response = _screen_text(envelope['response'], RESPONSE_LIMIT)
    try:
        _strict_json_object(response)
    except (ValueError, TypeError):
        raise LLMToolError('PM response must be one strict JSON object') from None
    assignment = _pm_assignment(response)
    if (assignment is None or assignment['route'] != 'opencode' or
            assignment['risk'] != 'LOW'):
        raise LLMToolError('PM response must recommend one low-risk OpenCode assignment')
    return request, response, assignment


def _pm_recommendation(response, *, executor_result=False):
    try:
        value = json.loads(response)
        if (type(value) is not dict or set(value) != {'schema_version', 'decision', 'summary'} or
                type(value['schema_version']) is not int or value['schema_version'] != 1 or
                value['decision'] not in (('SEND_TO_REVIEW', 'RETURN_TO_DEVELOPER',
                    'REASSIGN_DEVELOPER', 'WAIT_FOR_AUTHORIZATION', 'STOP') if executor_result else
                    ('ACCEPT', 'RETURN_TO_DEVELOPER', 'REASSIGN_DEVELOPER',
                     'WAIT_FOR_AUTHORIZATION', 'STOP'))):
            return None
        return {'decision': value['decision'],
                'summary': _screen_text(value['summary'], 2048)}
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
        'pm_route_binding': None,
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
        'completion': None,
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
    if 'pm_route_binding' in record:
        view['pm_route_binding'] = record['pm_route_binding']
    if 'pm_provenance' in record:
        view['pm_provenance'] = record['pm_provenance']
    if record['schema_version'] == 2:
        if 'completion' in record:
            view['completion'] = record['completion']
        view['pm_assignment'] = record['pm_assignment']
        if record['developer'] is None:
            view['developer'] = None
        else:
            view['developer'] = {
                **record['developer'],
                'usage_status': ('UNKNOWN' if record['developer']['usage'] is None else 'REPORTED'),
                'execution_result': {
                    'status': 'UNVERIFIED',
                    'code': 'EXECUTOR_EVIDENCE_NOT_ATTACHED',
                },
            }
    view['usage_status'] = 'UNKNOWN' if record['usage'] is None else 'REPORTED'
    return view


def add_task_submission_parsers(actions):
    submit = actions.add_parser('submit', help='Submit a development request to an explicit PM provider profile')
    submit.add_argument('--provider', required=True, help='configured PM provider profile; no implicit default')
    submit.add_argument('request', nargs='+', help='development request text')
    record_pm = actions.add_parser(
        'record-pm', help='Record an active PM runtime decision without invoking a provider')
    record_pm.add_argument('--stdin-json', action='store_true', required=True,
                           help='read one bounded JSON object with request and response from stdin')
    show = actions.add_parser('show', help='Retrieve one durable task and its PM outcome')
    show.add_argument('task_id', help='32-character task ID returned by task submit')
    delegate = actions.add_parser('delegate', help='Dispatch an explicitly selected Developer for an existing task')
    delegate.add_argument('task_id', help='existing durable Task ID')
    delegate.add_argument('--provider', required=True, choices=('company-gw', 'opencode'),
                          help='explicit Developer route; OpenCode uses the configured local CLI auth')
    delegate.add_argument('--selection-source', required=True, choices=('PM', 'USER'))
    delegate.add_argument('--executor-task', required=True,
                          help='registered D6 Executor task id; root/source/identity are read from its record')
    delegate.add_argument('--read-path', action='append', required=True, help='authorized relative context path; repeat as needed')
    delegate.add_argument('--write-path', action='append', required=True, help='authorized relative target path; repeat as needed')
    delegate.add_argument('--acceptance', action='append', required=True, help='bounded acceptance condition; repeat as needed')
    delegate.add_argument('--egress-basis', required=True, help='task-specific non-sensitive data authorization basis')
    delegate.add_argument('--context-stdin', action='store_true',
                          help='company-gw only: read bounded, sensitive-screened context from stdin')
    complete = actions.add_parser('complete', help='Collect verified Executor evidence and return it to the original PM')
    complete.add_argument('task_id', help='existing durable Task ID')
    complete.add_argument('--executor-task', required=True, help='registered D6 Executor task id bound at delegation')
    complete.add_argument('--reviewer-task', help='registered D6 independent Reviewer task id (omit for the initial Executor-result callback)')
    complete.add_argument('--pr', type=int, help='public GitHub pull request number (omit for the initial Executor-result callback)')


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


def _opencode_developer_prompt(task, *, source_sha, read_paths, write_paths,
                               acceptance, pm_goal):
    packet = {
        'task_id': task['task_id'], 'run_id': task['run_id'],
        'task_contract_digest': task['task_binding']['contract_digest'],
        'source_sha': source_sha, 'goal': pm_goal,
        'acceptance': acceptance, 'read_paths': read_paths,
        'write_paths': write_paths,
    }
    return (
        'You are the OpenCode Developer for this bounded llmtool Task. Make the '
        'smallest code change that satisfies the goal and acceptance criteria. '
        'Read only the listed paths and edit only write_paths. Do not use shell, '
        'Git, network, MCP, integrations, subagents, or other tools. Do not include '
        'secrets or private data. Run no tests unless a permitted tool is explicitly '
        'available. At the end, briefly summarize the change and any uncertainty; '
        'do not claim tests, review, commits, or PRs that did not occur.\n'
        'TASK_PACKET_JSON=' + json.dumps(packet, ensure_ascii=True, sort_keys=True,
                                         separators=(',', ':'))
    )


def _direct_execution(result, *, status='SUCCEEDED'):
    return {
        'status': status,
        'native_session_id': result.session_id,
        'process_exit_code': result.exit_code,
        'stdout_sha256': result.stdout_sha256,
        'elapsed_ms': result.elapsed_ms,
        'cleanup_status': result.cleanup_status,
        'usage_source': result.usage_source,
        'source_bytes': result.source_bytes,
        'source_file_count': result.source_file_count,
        'source_manifest_sha256': result.source_manifest_sha256,
    }


def _interrupted_execution(exc):
    """Return only adapter-observed process evidence after an interrupt."""
    observed = getattr(exc, '_llmtool_direct_opencode_execution', None)
    fields = ('native_session_id', 'process_exit_code', 'stdout_sha256',
              'elapsed_ms', 'cleanup_status', 'source_bytes',
              'source_file_count', 'source_manifest_sha256')
    value = {name: None for name in fields}
    value.update(status='UNKNOWN', cleanup_status='UNKNOWN', usage_source='UNKNOWN')
    if type(observed) is dict:
        for name in fields:
            if name in observed:
                value[name] = observed[name]
        # A pre-interruption CLI event stream has not been fully validated.
        value['native_session_id'] = None
        value['cleanup_status'] = (observed.get('cleanup_status')
                                   if observed.get('cleanup_status') in ('CONFIRMED', 'UNKNOWN')
                                   else 'UNKNOWN')
        value['usage_source'] = 'UNKNOWN'
    return value


def _persist_opencode_interruption(store, task_id, developer, exc, *, result=None):
    """Persist an ambiguous outcome without replaying the provider request."""
    developer.update(
        state='DEVELOPER_OUTCOME_UNKNOWN',
        provider_type='opencode-cli', model=opencode_direct_developer.MODEL,
        max_tokens=opencode_direct_developer.MAX_OUTPUT_TOKENS,
        artifact=None, artifact_sha256=None, usage=None,
        error_code=('OPENCODE_INTERRUPTED' if isinstance(exc, KeyboardInterrupt)
                    else 'OPENCODE_HOST_TERMINATED'),
        execution=(_direct_execution(result, status='UNKNOWN') if result is not None
                   else _interrupted_execution(exc)),
    )
    try:
        return store.update(task_id, developer=developer)
    except BaseException:
        # Keep the original interrupt/termination even if the recovery write
        # is itself interrupted or otherwise unavailable.
        raise exc


def _print_without_masking_interrupt(saved):
    try:
        _print(_task_view(saved))
    except BaseException:
        # Output is best-effort on an already-interrupted command path.
        pass


def run_task_opencode_delegate(args, *, store):
    """Claim one PM-bound OpenCode run and capture its real scoped workspace diff."""
    task = store.get(args.task_id)
    if (task['state'] != 'PM_RESPONDED' or not task['response'] or
            task['schema_version'] != 2):
        raise LLMToolError('task has no successful PM response')
    if task['developer'] is not None:
        raise LLMToolError('developer assignment already exists; duplicate dispatch refused')
    if getattr(args, 'context_stdin', False):
        raise LLMToolError('OpenCode reads only its registered task-local file scope')
    pm_assignment = task['pm_assignment']
    if args.selection_source == 'PM':
        if (type(pm_assignment) is not dict or pm_assignment.get('route') != 'opencode' or
                pm_assignment.get('risk') != 'LOW'):
            raise LLMToolError('no validated low-risk OpenCode recommendation from the selected PM')
        if args.acceptance != pm_assignment['acceptance']:
            raise LLMToolError('acceptance differs from the selected PM assignment')
    snapshot = task_completion.collect_git_snapshot(os.getcwd())
    registration, snapshot = task_completion.bind_executor_task(
        args.executor_task, expected_common_dir=snapshot.common_dir, root=snapshot.root)
    read_paths, write_paths = _assignment_paths(args.read_path, args.write_path)
    if type(args.acceptance) is not list or not 1 <= len(args.acceptance) <= 16:
        raise LLMToolError('developer acceptance scope is invalid')
    acceptance = [_screen_text(item, 512) for item in args.acceptance]
    egress_basis = _screen_text(args.egress_basis, 512)
    _screen_text(task['request'], REQUEST_LIMIT)
    _screen_text(task['response'], RESPONSE_LIMIT)
    pm_goal = (pm_assignment['goal'] if args.selection_source == 'PM'
               else task['request'])
    prompt = _opencode_developer_prompt(
        task, source_sha=snapshot.head, read_paths=read_paths,
        write_paths=write_paths, acceptance=acceptance, pm_goal=pm_goal)
    prompt_size = len(prompt.encode('utf-8'))
    if prompt_size > REQUEST_LIMIT:
        raise LLMToolError('OpenCode task prompt exceeds the supported size')
    developer = {
        'assignment_id': new_id(), 'selection_source': args.selection_source,
        'state': 'DEVELOPER_PENDING', 'provider_profile': 'opencode',
        'provider_type': None, 'model': None,
        'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
        'session_id': 'dev-' + new_id(), 'source_sha': snapshot.head,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': read_paths, 'write_paths': write_paths,
        'acceptance': acceptance,
        'context_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
        'egress_basis': egress_basis, 'input_bytes': prompt_size,
        'max_tokens': None,
        'artifact_format': 'unified_diff', 'artifact': None,
        'artifact_sha256': None, 'usage': None, 'error_code': None,
        'execution': None, 'created_at': _timestamp(),
    }
    completion = {
        'state': 'WAITING_EXECUTOR', 'executor_task_id': registration.task_id,
        'reviewer_task_id': None,
        'evidence': {
            'task_id': task['task_id'], 'run_id': task['run_id'],
            'contract_digest': task['task_binding']['contract_digest'],
            'executor_identity': registration.trusted_agent_id,
            'executor_session_ref': registration.session_ref,
            'executor_native_session_id': registration.native_session_id,
            'executor_native_session': ('VERIFIED' if registration.native_session_id else 'UNKNOWN'),
            'execution_mode': 'DIRECT_D6', 'root': registration.root,
            'common_dir': registration.common_dir, 'branch': registration.branch,
            'source_sha': snapshot.head, 'result_head': None,
        },
        'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
        'pm_response': None, 'pm_recommendation': None,
        'pm_usage': None, 'pm_error_code': None,
    }
    store.compare_and_update(
        task['task_id'],
        expected={'schema_version': 2, 'state': 'PM_RESPONDED', 'developer': None,
                  'completion': None},
        developer=developer, completion=completion,
    )
    try:
        developer.update(state='DEVELOPER_DISPATCHING', provider_type='opencode-cli',
                         model=opencode_direct_developer.MODEL,
                         max_tokens=opencode_direct_developer.MAX_OUTPUT_TOKENS)
        store.update(task['task_id'], developer=developer)

        def validate_prelaunch_d6_binding():
            current_registration, current_snapshot = task_completion.bind_executor_task(
                registration.task_id, expected_common_dir=registration.common_dir,
                root=registration.root)
            if (current_registration != registration or current_snapshot != snapshot):
                raise LLMToolError('D6 or Git identity changed before OpenCode launch')
            return True

        result = opencode_direct_developer.run(
            root=registration.root, read_paths=read_paths,
            write_paths=write_paths, prompt=prompt,
            prelaunch_validate=validate_prelaunch_d6_binding)
    except opencode_direct_developer.DirectOpenCodeError as exc:
        if exc.attempted:
            developer.update(state='DEVELOPER_OUTCOME_UNKNOWN',
                provider_type='opencode-cli', model=opencode_direct_developer.MODEL,
                max_tokens=opencode_direct_developer.MAX_OUTPUT_TOKENS,
                error_code=exc.direct_opencode_code,
                execution={
                    'status': 'UNKNOWN', 'native_session_id': None,
                    'process_exit_code': exc.process_exit_code,
                    'stdout_sha256': exc.stdout_sha256,
                    'elapsed_ms': exc.elapsed_ms,
                    'cleanup_status': exc.cleanup_status,
                    'usage_source': 'UNKNOWN',
                    'source_bytes': exc.source_bytes,
                    'source_file_count': exc.source_file_count,
                    'source_manifest_sha256': exc.source_manifest_sha256,
                })
        else:
            developer.update(state='DEVELOPER_UNAVAILABLE',
                              error_code=exc.direct_opencode_code)
        saved = store.update(task['task_id'], developer=developer)
        _print(_task_view(saved))
        return 1
    except (KeyboardInterrupt, CLIHostTermination) as exc:
        # Preserve Ctrl-C/host termination semantics, but first prevent the
        # durable task from being stranded in DEVELOPER_DISPATCHING. The
        # adapter attaches only observed process/cleanup facts; absent facts
        # remain UNKNOWN, and this path never replays the provider request.
        saved = _persist_opencode_interruption(
            store, task['task_id'], developer, exc)
        _print_without_masking_interrupt(saved)
        raise
    except Exception:
        developer.update(state='DEVELOPER_OUTCOME_UNKNOWN',
            provider_type='opencode-cli', model=opencode_direct_developer.MODEL,
            max_tokens=opencode_direct_developer.MAX_OUTPUT_TOKENS,
            error_code='OPENCODE_ADAPTER_UNKNOWN',
            execution={
                'status': 'UNKNOWN', 'native_session_id': None,
                'process_exit_code': None, 'stdout_sha256': None,
                'elapsed_ms': None, 'cleanup_status': 'UNKNOWN',
                'usage_source': 'UNKNOWN',
                'source_bytes': None, 'source_file_count': None,
                'source_manifest_sha256': None,
            })
        try:
            saved = store.update(task['task_id'], developer=developer)
        except Exception:
            raise LLMToolError('OpenCode outcome is ambiguous and could not be persisted') from None
        _print(_task_view(saved))
        return 1

    try:
        after = task_completion.collect_git_snapshot(registration.root)
        if (after.root != registration.root or after.common_dir != registration.common_dir or
                after.branch != registration.branch or after.head != snapshot.head):
            raise LLMToolError('OpenCode changed the registered Git identity')
        untracked = task_completion._git(registration.root,
            ('ls-files', '--others', '--exclude-standard', '-z'), limit=64 * 1024)
        if untracked:
            raise LLMToolError('OpenCode created an untracked file outside the captured diff')
        artifact_raw = task_completion._git(registration.root,
            ('diff', '--binary', '--no-ext-diff', '--no-textconv', '--no-renames',
             snapshot.head, '--'), limit=64 * 1024)
        artifact = artifact_raw.decode('utf-8', 'strict')
        rejection = _developer_output_rejection(artifact, write_paths)
        execution = _direct_execution(result)
        if rejection is not None:
            developer.update(state='DEVELOPER_OUTPUT_REJECTED',
                provider_type='opencode-cli', model=opencode_direct_developer.MODEL,
                max_tokens=opencode_direct_developer.MAX_OUTPUT_TOKENS,
                usage=result.usage, error_code=rejection, execution=execution)
        else:
            developer.update(state='DEVELOPER_RESPONDED',
                provider_type='opencode-cli', model=opencode_direct_developer.MODEL,
                max_tokens=opencode_direct_developer.MAX_OUTPUT_TOKENS,
                artifact=artifact,
                artifact_sha256=hashlib.sha256(artifact.encode('utf-8')).hexdigest(),
                usage=result.usage, error_code=None, execution=execution)
    except (KeyboardInterrupt, CLIHostTermination) as exc:
        saved = _persist_opencode_interruption(
            store, task['task_id'], developer, exc, result=result)
        _print_without_masking_interrupt(saved)
        raise
    except Exception:
        developer.update(state='DEVELOPER_OUTCOME_UNKNOWN',
            provider_type='opencode-cli', model=opencode_direct_developer.MODEL,
            max_tokens=opencode_direct_developer.MAX_OUTPUT_TOKENS,
            error_code='OPENCODE_RESULT_EVIDENCE_UNKNOWN',
            execution=_direct_execution(result, status='UNKNOWN'))
    try:
        saved = store.update(task['task_id'], developer=developer)
    except (KeyboardInterrupt, CLIHostTermination) as exc:
        saved = _persist_opencode_interruption(
            store, task['task_id'], developer, exc, result=result)
        _print_without_masking_interrupt(saved)
        raise
    _print(_task_view(saved))
    return 0 if developer['state'] == 'DEVELOPER_RESPONDED' else 1


def run_task_delegate(args, *, store=None):
    store = TaskStore() if store is None else store
    if args.provider == 'opencode':
        return run_task_opencode_delegate(args, store=store)
    if not getattr(args, 'context_stdin', False) and not hasattr(args, 'context'):
        raise LLMToolError('company-gw delegation requires explicit --context-stdin')
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
    snapshot = task_completion.collect_git_snapshot(os.getcwd())
    registration, snapshot = task_completion.bind_executor_task(
        args.executor_task, expected_common_dir=snapshot.common_dir, root=snapshot.root)
    source_sha = snapshot.head
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
        'session_id': 'dev-' + new_id(), 'source_sha': snapshot.head,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': read_paths, 'write_paths': write_paths,
        'acceptance': acceptance,
        'context_sha256': hashlib.sha256(context.encode('utf-8')).hexdigest(),
        'egress_basis': egress_basis, 'input_bytes': input_bytes,
        'max_tokens': None,
        'artifact_format': 'unified_diff', 'artifact': None,
        'artifact_sha256': None, 'usage': None, 'error_code': None,
        'created_at': _timestamp(),
    }
    completion = {
        'state': 'WAITING_EXECUTOR',
        'executor_task_id': registration.task_id,
        'reviewer_task_id': None,
        'evidence': {
            'task_id': task['task_id'], 'run_id': task['run_id'],
            'contract_digest': task['task_binding']['contract_digest'],
            'executor_identity': registration.trusted_agent_id,
            'executor_session_ref': registration.session_ref,
            'executor_native_session_id': registration.native_session_id,
            'executor_native_session': ('VERIFIED' if registration.native_session_id else 'UNKNOWN'),
            'execution_mode': 'DIRECT_D6',
            'root': registration.root, 'common_dir': registration.common_dir,
            'branch': registration.branch, 'source_sha': snapshot.head,
            'result_head': snapshot.head,
        },
        'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
        'pm_response': None, 'pm_recommendation': None,
        'pm_usage': None, 'pm_error_code': None,
    }
    # Persist the one-shot claim before network I/O. A crash or ambiguous result
    # remains pending; this task cannot silently trigger a duplicate provider call.
    store.compare_and_update(
        task['task_id'],
        expected={'schema_version': 2, 'state': 'PM_RESPONDED', 'developer': None,
                  'completion': None},
        developer=developer, completion=completion,
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
                     pm_route_binding=_provider_route_binding(provider),
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


def run_task_record_pm(args, *, store=None, input_stream=None):
    """Persist a real active-PM decision; this path never resolves or calls a provider."""
    del args  # The required flag is enforced by argparse; no caller text is trusted from argv.
    store = TaskStore() if store is None else store
    prompt, response, assignment = _read_pm_record_input(input_stream)
    task_id, run_id = new_id(), new_id()
    digest = hashlib.sha256(prompt.encode('utf-8', 'strict')).hexdigest()
    contract = _contract(task_id, digest)
    record = _record(
        task_id, run_id, 'pm-' + new_id(), 'active-pm-runtime', prompt,
        state='PM_RESPONDED', provider_type='codex-cli', model=None,
        backend='ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL', request_sha=digest,
        contract=contract,
    )
    record.update(
        pm_route_binding={'type': 'codex-cli', 'model': None, 'base_url': None},
        response=response, usage=None, error_code=None, pm_assignment=assignment,
        pm_provenance={
            'source': 'ACTIVE_PM_RUNTIME',
            'provider_invoked': False,
            'native_session_id_status': 'UNAVAILABLE',
            'usage_status': 'UNKNOWN',
            'session_restored': False,
            'automatic_callback': False,
        },
    )
    try:
        store.create(record)
        saved = store.get(task_id)
    except Exception:
        raise LLMToolError('active PM outcome could not be saved; details omitted') from None
    _print(_task_view(saved))
    return 0


def run_task_show(args, *, store=None):
    store = TaskStore() if store is None else store
    _print(_task_view(store.get(args.task_id)))
    return 0


def run_task_complete(args, *, store=None):
    """Verify an Executor result and make one non-replayable original-profile PM call.

    Without reviewer/PR arguments this is the first callback: it reports only the
    D6-bound result and local test evidence and explicitly leaves review/CI pending.
    Supplying both arguments retains the existing post-review/CI path.
    """
    store = TaskStore() if store is None else store
    task = store.get(args.task_id)
    completion = task.get('completion')
    executor_result_only = args.reviewer_task is None and args.pr is None
    if (args.reviewer_task is None) != (args.pr is None):
        raise LLMToolError('reviewer task and PR must be supplied together')
    if (task['schema_version'] != 2 or task['state'] != 'PM_RESPONDED' or
            task.get('developer') is None or completion is None or
            completion['executor_task_id'] != args.executor_task or
            task.get('pm_route_binding') is None):
        raise LLMToolError('task completion binding is unavailable')
    if completion['pm_dispatch'] in ('CLAIMED', 'UNKNOWN', 'RETURNED'):
        raise LLMToolError('original PM request already claimed or completed; replay refused')
    developer = task['developer']
    initial = completion['evidence'] or {}
    try:
        task_completion.validate_task_binding(
            stored_binding=task['task_binding'], task_id=initial.get('task_id'),
            run_id=initial.get('run_id'), contract_digest=initial.get('contract_digest'))
    except LLMToolError as exc:
        code = getattr(exc, 'completion_code', 'TASK_RUN_CONTRACT_MISMATCH')
        waiting = {**completion, 'state': 'WAITING_EXECUTOR',
                   'reviewer_task_id': args.reviewer_task,
                   'missing_gates': [code], 'pm_dispatch': 'NOT_STARTED'}
        saved = store.compare_and_update(task['task_id'],
            expected={'completion': completion}, completion=waiting)
        _print(_task_view(saved))
        return 1
    # Preserve the immutable D6 admission binding across WAITING retries, but do
    # not carry forward result/test/review facts from a prior incomplete attempt.
    admission_fields = ('executor_identity', 'executor_session_ref',
        'executor_native_session_id', 'executor_native_session', 'root',
        'common_dir', 'branch', 'source_sha', 'execution_mode',
        'host_worker_receipt')
    evidence = {key: initial[key] for key in admission_fields if key in initial}
    evidence.update({'task_id': task['task_id'], 'run_id': task['run_id'],
                'contract_digest': task['task_binding']['contract_digest'],
                'execution_mode': initial.get('execution_mode', 'DIRECT_D6'),
                'developer': {'provider': developer['provider_profile'],
                    'model': developer['model'], 'session_id': developer['session_id'],
                    'usage': developer['usage']},
                'executor': {'task_id': completion['executor_task_id'],
                    'identity': initial.get('executor_identity'),
                    'session_ref': initial.get('executor_session_ref'),
                    'native_session': initial.get('executor_native_session')},
                'source_sha': initial.get('source_sha', developer['source_sha'])})
    missing = []
    try:
        if evidence['source_sha'] != developer['source_sha']:
            error = LLMToolError('Executor admission source does not match Developer source')
            error.completion_code = 'D6_ADMISSION_SOURCE_MISMATCH'
            raise error
        git_evidence = task_completion.collect_executor_result(
            task_id=args.executor_task, expected_common_dir=initial.get('common_dir'),
            expected_root=initial.get('root'), expected_branch=initial.get('branch'),
            source_sha=developer['source_sha'], task_binding=task['task_binding'],
            expected_agent=initial.get('executor_identity'),
            expected_session_ref=initial.get('executor_session_ref'),
            expected_native_session_id=initial.get('executor_native_session_id'),
            write_paths=developer['write_paths'], artifact=developer['artifact'],
            artifact_sha256=developer['artifact_sha256'])
        tests = task_completion.run_bounded_tests(root=git_evidence['root'],
            result_sha=git_evidence['result_head'], read_paths=developer['read_paths'],
            write_paths=developer['write_paths'])
        evidence['result'] = git_evidence
        evidence['tests'] = tests
        if any(item['exit_code'] != 0 for item in tests):
            missing.append('TEST_FAILED')
        elif evidence['execution_mode'] == 'HOST_WORKER' and not task_completion.validate_host_worker_receipt(
                evidence.get('host_worker_receipt'), task_binding=task['task_binding'],
                task_id=task['task_id'], run_id=task['run_id'],
                contract_digest=task['task_binding']['contract_digest'],
                source_sha=git_evidence['source_sha']):
            missing.append('HOST_WORKER_RECEIPT_UNAVAILABLE')
        elif evidence['execution_mode'] not in ('DIRECT_D6', 'HOST_WORKER'):
            missing.append('EXECUTION_MODE_INVALID')
        elif executor_result_only:
            evidence['review_pr_ci'] = {
                'stage': 'EXECUTOR_RESULT',
                'formal_review_state': 'NOT_STARTED',
                'ci_state': 'NOT_STARTED',
            }
        else:
            github = task_completion.collect_github_evidence(
                pr_number=args.pr, result_sha=git_evidence['result_head'],
                task_id=task['task_id'], run_id=task['run_id'],
                contract_digest=task['task_binding']['contract_digest'],
                executor_identity=git_evidence['executor_identity'],
                executor_session_ref=initial.get('executor_session_ref'),
                executor_native_session_id=initial.get('executor_native_session_id'),
                reviewer_task_id=args.reviewer_task,
                common_dir=git_evidence['common_dir'])
            evidence['review_pr_ci'] = github['evidence']
            missing.extend(github['missing_gates'])
    except LLMToolError as exc:
        code = getattr(exc, 'completion_code', 'COMPLETION_EVIDENCE_UNAVAILABLE')
        missing.append(code)
    except Exception:
        missing.append('COMPLETION_EVIDENCE_UNAVAILABLE')

    missing = sorted(set(missing))
    if missing:
        state = ('WAITING_REVIEW' if any(code.startswith(('REVIEW_', 'REVIEWER_')) or
                 code.startswith('FORMAL_REVIEW') for code in missing) else
                 'WAITING_CI' if any(code.startswith('PR_') or code.startswith('CI_') or
                 code.startswith('REQUIRED_CI') for code in missing) else
                 'WAITING_EXECUTOR')
        waiting = {**completion, 'state': state,
                   'reviewer_task_id': args.reviewer_task,
                   'evidence': evidence, 'missing_gates': missing,
                   'pm_dispatch': 'NOT_STARTED', 'pm_error_code': None}
        saved = store.compare_and_update(task['task_id'],
            expected={'completion': completion}, completion=waiting)
        _print(_task_view(saved))
        return 1

    # Never export code/diff/output. The PM receives only screened identifiers,
    # statuses and hashes needed to reason about this exact Task/Run.
    packet = {
        'task_id': task['task_id'], 'run_id': task['run_id'],
        'task_contract_digest': task['task_binding']['contract_digest'],
        'task_request_sha256': task['request_sha256'],
        'pm_assignment': (None if task['pm_assignment'] is None else {
            'route': task['pm_assignment']['route'],
            'risk': task['pm_assignment']['risk'],
            'goal': task['pm_assignment']['goal'],
            'acceptance': task['pm_assignment']['acceptance'],
        }),
        'developer': evidence['developer'], 'executor': evidence['executor'],
        'source_sha': evidence['result']['source_sha'],
        'result_head': evidence['result']['result_head'],
        'changed_paths': evidence['result']['changed_paths'],
        'diff_sha256': evidence['result']['diff_sha256'],
        'tests': [{'path': item['path'], 'exit_code': item['exit_code'],
                   'source_sha': item['source_sha'],
                   'output_sha256': item['output_sha256']} for item in evidence['tests']],
        'review_pr_ci': evidence['review_pr_ci'],
    }
    if evidence['execution_mode'] == 'HOST_WORKER':
        packet['host_worker_receipt'] = evidence['host_worker_receipt']
    content = json.dumps(packet, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    _screen_text(content, 16 * 1024)
    before_claim = {**completion, 'state': 'PM_DISPATCHING',
                    'reviewer_task_id': args.reviewer_task,
                    'evidence': evidence, 'missing_gates': [],
                    'pm_dispatch': 'CLAIMED', 'pm_response': None,
                    'pm_recommendation': None, 'pm_usage': None,
                    'pm_error_code': None}
    store.compare_and_update(task['task_id'],
        expected={'completion': completion}, completion=before_claim)
    try:
        config = resolve_profile_config(task['pm_provider_profile'], require_configured=True)
        provider = selected_provider(config)
        if _provider_route_binding(provider) != task['pm_route_binding']:
            raise LLMToolError('original PM provider identity changed')
        client = create_client(config, api_key(config))
    except Exception as exc:
        # No provider call was started; preserve the evidence and permit a later
        # retry after configuration is repaired.
        waiting = {**before_claim, 'state': 'WAITING_PM',
                   'pm_dispatch': 'NOT_STARTED',
                   'pm_error_code': _provider_error_code(exc)}
        saved = store.update(task['task_id'], completion=waiting)
        _print(_task_view(saved))
        return 1
    try:
        result = client.complete([
            {'role': 'system', 'content': (_EXECUTOR_RESULT_SYSTEM if executor_result_only
                                           else _COMPLETION_SYSTEM)},
            {'role': 'user', 'content': content},
        ])
    except Exception as exc:
        unknown = {**before_claim, 'state': 'PM_OUTCOME_UNKNOWN',
                   'pm_dispatch': 'UNKNOWN',
                   'pm_error_code': _provider_error_code(exc)}
        saved = store.update(task['task_id'], completion=unknown)
        _print(_task_view(saved))
        return 1
    try:
        response = _screen_text(result.content, RESPONSE_LIMIT)
    except LLMToolError:
        unknown = {**before_claim, 'state': 'PM_OUTCOME_UNKNOWN',
                   'pm_dispatch': 'UNKNOWN', 'pm_error_code': 'PM_RESPONSE_REJECTED'}
        saved = store.update(task['task_id'], completion=unknown)
        _print(_task_view(saved))
        return 1
    usage = reply_usage(result.metadata)
    recommendation = _pm_recommendation(response, executor_result=executor_result_only)
    returned = {**before_claim,
                'state': 'PM_RETURNED' if recommendation is not None else 'PM_RESPONSE_REJECTED',
                'pm_dispatch': 'RETURNED', 'pm_response': response,
                'pm_recommendation': recommendation,
                'pm_usage': None if usage is None else dict(usage),
                'pm_error_code': None if recommendation is not None else 'PM_RESPONSE_REJECTED'}
    try:
        saved = store.update(task['task_id'], completion=returned)
    except Exception:
        # A provider reply exists but is not durable. Never repeat the request.
        raise LLMToolError('PM response could not be persisted; details omitted') from None
    _print(_task_view(saved))
    return 0 if returned['pm_recommendation'] is not None else 1


def run_task_executor_event(task_id, executor_task_id, *, store=None):
    """Handle the existing Host's verified completion event exactly once.

    The event carries identifiers only. All Git, D6, artifact, receipt and test
    evidence is recollected by run_task_complete before the original PM is called.
    """
    from argparse import Namespace
    return run_task_complete(Namespace(task_id=task_id,
        executor_task=executor_task_id, reviewer_task=None, pr=None), store=store)
