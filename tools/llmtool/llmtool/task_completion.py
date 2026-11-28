"""Fail-closed evidence collection for an already completed D6 Executor task.

This module never applies a patch or writes Git state. A D6 registry record is a
cooperative local provenance source; it is not a same-UID security boundary.

Stable Reviewer completion codes: ``REVIEW_DISPATCH_BINDING_INVALID`` covers
invalid dispatch intents; ``REVIEW_RECEIPT_INVALID``,
``REVIEW_RECEIPT_BINDING_MISMATCH``, and ``REVIEW_RECEIPT_DIGEST_MISMATCH``
cover invalid or mismatched handoff/return receipts; ``REVIEW_REPAIR_BINDING_INVALID``,
``REVIEW_REPAIR_BINDING_MISMATCH``, and ``REVIEW_REPAIR_DIGEST_MISMATCH`` cover
invalid or mismatched deterministic repair-intent envelopes. Formal completion
refusal codes: ``FORMAL_REVIEW_MISSING`` covers a missing or unadmitted formal
review at the head, including when the only completed claim at the head is
security-kind or unkinded at version 91 or above (where an unverified Codex PASS
stays advisory); ``FORMAL_REVIEW_NOT_ACCEPTED`` covers an unaccepted disposition.
JSON-bound values are restricted to finite JSON scalars and containers no deeper
than 64 levels.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

from . import github_identity
from . import repair_policy
from . import source_freshness
from . import test_profile
from . import trust
from . import worker_dispatch
from .errors import LLMToolError
from .test_output_excerpts import select_test_excerpts

_TASK_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_REGISTRY_LIMIT = 64 * 1024
_GIT_LIMIT = 2 * 1024 * 1024
_PROPOSAL_FILE_LIMIT = 128
_PROPOSAL_TREE_LIMIT = 8 * 1024 * 1024
# #2281 (Owner 2026-11-19): read and write path counts of a Developer Task scope.
_TASK_PATH_LIMIT = 1024
_TIMEOUT = 10
# seconds per registered test module. #1703: test_task_completion alone needs about
# 310 s on the Mac (300 s caused TEST_RUNNER_TIMEOUT); the gw-agent wiring module ~240 s.
# #2281 row 10: the optional policy.json key local_test_timeout_seconds overrides
# this per-module default; the whole-run bound stays three times the chosen value.
_TEST_TIMEOUT = 600
# #2543: wall-clock bound for one whole local run (all modules in sequence).
# Without it a Task with many eligible modules ran for hours on the Owner Mac
# while task advance held its locks. Three modules at the per-module cap; the
# slowest measured module is ~310 s (#1703 above), so about five slow modules fit.
_TEST_TOTAL_TIMEOUT = 3 * _TEST_TIMEOUT
_TEST_OUTPUT_LIMIT = 256 * 1024
# The completion gate reads ``ci_trust`` from trust.json at use time (see
# ``_ci_trust``). The Mac check name is the code check the Linux-only path
# excludes, and ``_GITHUB_ACTIONS_APP_ID`` stays the fixed pin the trust
# value ``github_actions_app_id`` must equal (the trust schema pins it too).
_MAC_CHECK = 'check (macOS 3.13)'
_GITHUB_ACTIONS_APP_ID = 15368
_D6_EVIDENCE_STATES = frozenset(('ACTIVE', 'REVIEW_PENDING', 'REPAIR'))
# A GitHub-hosted job reports this runner group; self-hosted groups never do.
_GITHUB_HOSTED_RUNNER_GROUP = 'GitHub Actions'
_REVIEW_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
_REVIEW_VERDICTS = frozenset(('PASS', 'FAIL', 'BLOCKED'))
_GH_PATH = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'
# The leading `<!-- activity: ... -->` line every verdict post starts with.
_VERDICT_ACTIVITY = re.compile(r'<!-- activity: (?:[^>]* )?type=review_verdict(?: [^>]*)? -->')
# The one standard formal verdict format: .claude/skills/ai-team-delivery/formal-verdict.md.
_VERDICT_MARKER = re.compile(
    r'<!-- activity: type=review_verdict actor=(Claude|Codex|AGY) target=PM status=(PASS|FIX|FAIL) -->')
_VERDICT_STATUS = re.compile(
    r'Verdict: (PASS|FIX|FAIL) \| Version: v[1-9][0-9]{0,3} \| HEAD: ([0-9a-f]{40})')
_FORMAL_ACCEPT = 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT'
_REVIEW_VERDICT_ACTIVITY_LINE = '<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->'
_GITHUB_API_PATH = re.compile(
    r'(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?:compare/[0-9a-f]{40}\.\.\.[0-9a-f]{40}|'
    r'issues/[0-9]{1,7}/comments\?per_page=[0-9]{1,3}&page=[0-9]{1,3}'
    r'&since=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z|'
    r'(?!compare/)[A-Za-z0-9_./?=&-]{1,256})\Z')

BOOTSTRAP_SOURCES = frozenset((
    'tools/llmtool/Makefile',
    'tools/llmtool/.gitignore',
    'tools/llmtool/scripts/worktree_env.py',
    'tools/llmtool/worktree-env.lock',
    'tools/llmtool/pyproject.toml',
    'tools/llmtool/setup.py',
    'tools/llmtool/setup.cfg',
    'tools/llmtool/MANIFEST.in',
    'tools/llmtool/scripts/d6_worktree_env.py',
))
REQUIRED_BOOTSTRAP_SOURCES = frozenset((
    'tools/llmtool/Makefile',
    'tools/llmtool/.gitignore',
    'tools/llmtool/scripts/worktree_env.py',
    'tools/llmtool/worktree-env.lock',
    'tools/llmtool/pyproject.toml',
))
ENV2_PASS_MARKER_SCHEMA1_KEYS = frozenset({
    'schema_version',
    'result',
    'task_id',
    'root_realpath',
    'submitted_head_sha',
    'current_head_sha',
    'tooling_sha',
    'environment_fingerprint',
    'report_sha256',
})
ENV2_PASS_MARKER_SCHEMA2_KEYS = ENV2_PASS_MARKER_SCHEMA1_KEYS | frozenset({
    'environment_mode',
    'carried_from_head_sha',
    'allowed_bootstrap_paths',
})


def _stop(code):
    error = LLMToolError('Executor completion evidence is unavailable; details omitted')
    error.completion_code = code
    raise error from None


def _test_profile_snapshot():
    """Return one validated profile from the live provider, or refuse.

    ``test_profile.default_profile`` is looked up at use time so a caller can
    substitute a profile. A provider that is missing, raises, or returns
    anything other than a :class:`TestProfile` refuses with
    ``TEST_PROFILE_INVALID``. Callers classify every path of one completion
    pass against this single snapshot, so a provider that changes its answer
    between calls cannot split a path's admission from its runner kind.
    """
    provider = getattr(test_profile, 'default_profile', None)
    if not callable(provider):
        _stop('TEST_PROFILE_INVALID')
    try:
        profile = provider()
    except Exception:
        _stop('TEST_PROFILE_INVALID')
    if type(profile) is not test_profile.TestProfile:
        _stop('TEST_PROFILE_INVALID')
    # A compiled bytes pattern passed the TestProfile type check but cannot
    # fullmatch a str path, so refuse it here with the fixed code instead of
    # letting TypeError escape classification.
    if any(type(pattern.pattern) is not str for pattern, _ in profile.entries):
        _stop('TEST_PROFILE_INVALID')
    return profile


def _profile_kind(profile, path):
    """Return the kind one profile snapshot gives a path, or ``None``.

    A path that matches entries with different runner kinds refuses with
    ``TEST_PROFILE_INVALID``. Classification never falls back to an older
    registry.
    """
    if type(path) is not str:
        return None
    try:
        kinds = {kind for pattern, kind in profile.entries
                 if pattern.fullmatch(path) is not None}
    except Exception:
        _stop('TEST_PROFILE_INVALID')
    if len(kinds) > 1:
        _stop('TEST_PROFILE_INVALID')
    return kinds.pop() if kinds else None


def _classify_test_path(path):
    """Return the runner kind one live test-evidence profile gives a path."""
    return _profile_kind(_test_profile_snapshot(), path)


def _eligible_registered_tests(paths):
    """Return sorted ``(path, kind)`` pairs from one profile snapshot."""
    unique = sorted(set(paths))
    if not unique:
        return []
    profile = _test_profile_snapshot()
    eligible = []
    for path in unique:
        kind = _profile_kind(profile, path)
        if kind is not None:
            eligible.append((path, kind))
    return eligible


def _canonical_json_bytes(value, code):
    """Serialize a bounded JSON tree, mapping only encoding failures to code."""
    stack = [(value, 0, False)]
    ancestors = set()
    while stack:
        item, depth, leaving = stack.pop()
        if leaving:
            ancestors.remove(id(item))
            continue
        if depth > 64:
            _stop(code)
        item_type = type(item)
        if item_type is dict or item_type is list:
            identity = id(item)
            if identity in ancestors:
                _stop(code)
            ancestors.add(identity)
            stack.append((item, depth, True))
            if item_type is dict:
                entries = list(item.items())
                if any(type(key) is not str for key, _ in entries):
                    _stop(code)
                stack.extend((child, depth + 1, False)
                             for _, child in reversed(entries))
            else:
                stack.extend((child, depth + 1, False)
                             for child in reversed(item))
        elif item_type is float:
            if not math.isfinite(item):
                _stop(code)
        elif item is None or item_type in (str, int, bool):
            continue
        else:
            _stop(code)
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        _stop(code)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _stop('D6_REGISTRY_INVALID')
        result[key] = value
    return result


def review_dispatch_intent(*, task_id, run_id, contract_digest, developer_task_id,
                           developer_identity, developer_session_ref, source_sha,
                           result_head, diff_sha256, changed_paths, repair_round=0):
    """Build a bounded Reviewer request identity bound to one verified result.

    This is dispatch metadata only. It conveys no provider, Session, review,
    repair, Git, or integration authority.
    """
    if (type(task_id) is not str or re.fullmatch(r'[0-9a-f]{32}', task_id) is None or
            type(run_id) is not str or re.fullmatch(r'[0-9a-f]{32}', run_id) is None or
            type(contract_digest) is not str or _HEX64.fullmatch(contract_digest) is None or
            type(developer_task_id) is not str or _REVIEW_ID.fullmatch(developer_task_id) is None or
            type(developer_identity) is not str or not developer_identity or
            len(developer_identity) > 256 or type(developer_session_ref) is not str or
            not developer_session_ref or len(developer_session_ref) > 512 or
            type(source_sha) is not str or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', source_sha) or
            (result_head is not None and
             (type(result_head) is not str or _HEX40.fullmatch(result_head) is None)) or
            type(diff_sha256) is not str or _HEX64.fullmatch(diff_sha256) is None or
            type(changed_paths) is not list or not changed_paths or
            any(type(path) is not str for path in changed_paths) or
            changed_paths != sorted(set(changed_paths)) or
            any(not path or len(path) > 512 or
                path.startswith('/') or '\\' in path or
                any(part in ('', '.', '..') for part in path.split('/'))
                for path in changed_paths) or
            type(repair_round) is not int or repair_round not in (0, 1)):
        _stop('REVIEW_DISPATCH_BINDING_INVALID')
    value = {
        'schema_version': 1,
        'dispatch_id': secrets.token_hex(16),
        'task_id': task_id,
        'run_id': run_id,
        'contract_digest': contract_digest,
        'developer_task_id': developer_task_id,
        'developer_identity': developer_identity,
        'developer_session_ref': developer_session_ref,
        'source_sha': source_sha,
        'result_head': result_head,
        'diff_sha256': diff_sha256,
        'changed_paths': list(changed_paths),
        'repair_round': repair_round,
        'state': 'REQUESTED',
    }
    value['intent_sha256'] = hashlib.sha256(_canonical_json_bytes(
        value, 'REVIEW_DISPATCH_BINDING_INVALID')).hexdigest()
    return value


def validate_review_dispatch_intent(intent):
    fields = {
        'schema_version', 'dispatch_id', 'task_id', 'run_id', 'contract_digest',
        'developer_task_id', 'developer_identity', 'developer_session_ref',
        'source_sha', 'result_head', 'diff_sha256', 'changed_paths',
        'repair_round', 'state', 'intent_sha256',
    }
    if type(intent) is not dict or set(intent) != fields:
        _stop('REVIEW_DISPATCH_BINDING_INVALID')
    value = dict(intent)
    claimed = value.pop('intent_sha256')
    actual = hashlib.sha256(_canonical_json_bytes(
        value, 'REVIEW_DISPATCH_BINDING_INVALID')).hexdigest()
    if (claimed != actual or type(intent['schema_version']) is not int or
            intent['schema_version'] != 1 or intent['state'] != 'REQUESTED' or
            type(intent['dispatch_id']) is not str or
            re.fullmatch(r'[0-9a-f]{32}', intent['dispatch_id']) is None):
        _stop('REVIEW_DISPATCH_DIGEST_MISMATCH')
    # Reuse the constructor's strict field validation without changing the ID.
    review_dispatch_intent(
        task_id=intent['task_id'], run_id=intent['run_id'],
        contract_digest=intent['contract_digest'],
        developer_task_id=intent['developer_task_id'],
        developer_identity=intent['developer_identity'],
        developer_session_ref=intent['developer_session_ref'],
        source_sha=intent['source_sha'], result_head=intent['result_head'],
        diff_sha256=intent['diff_sha256'], changed_paths=intent['changed_paths'],
        repair_round=intent['repair_round'])
    return intent








def _git(root, args, *, limit=_GIT_LIMIT, allow_exit_1=False,
         return_exit_code=False, env_overrides=None):
    """Run one fixed Git read with bounded output and no inherited Git config."""
    env = {
        'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_ATTR_NOSYSTEM': '1',
        'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_LAZY_FETCH': '1',
        'GIT_NO_REPLACE_OBJECTS': '1',
        'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': 'cat', 'PAGER': 'cat',
        'GIT_LITERAL_PATHSPECS': '1', 'TMPDIR': '/tmp',
    }
    if env_overrides is not None:
        if (type(env_overrides) is not dict or
                any(type(key) is not str or type(value) is not str
                    for key, value in env_overrides.items())):
            _stop('GIT_INSPECTION_FAILED')
        env.update(env_overrides)
    safe = ('-c', 'core.useReplaceRefs=false', '-c', 'core.fsmonitor=false',
            '-c', 'core.hooksPath=/dev/null',
            '-c', 'core.untrackedCache=false', '-c', 'core.attributesFile=/dev/null',
            '-c', 'core.pager=cat', '-c', 'color.ui=false', '-c',
            'protocol.allow=never')
    try:
        process = subprocess.Popen(['/usr/bin/git', '--no-pager', *safe, *args],
            cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, shell=False, close_fds=True, start_new_session=True)
    except OSError:
        _stop('GIT_INSPECTION_UNAVAILABLE')
    selector = selectors.DefaultSelector()
    out, errors = bytearray(), 0
    deadline = time.monotonic() + _TIMEOUT
    try:
        for stream, kind in ((process.stdout, 'out'), (process.stderr, 'err')):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, kind)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop('GIT_INSPECTION_TIMEOUT')
            for key, _ in selector.select(min(remaining, .1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif key.data == 'out':
                    if len(out) + len(chunk) > limit:
                        _stop('GIT_EVIDENCE_TOO_LARGE')
                    out.extend(chunk)
                else:
                    errors += len(chunk)
                    if errors > 16 * 1024:
                        _stop('GIT_INSPECTION_FAILED')
        returncode = process.wait(timeout=max(0, deadline-time.monotonic()))
        if time.monotonic() >= deadline or returncode not in ((0, 1) if allow_exit_1 else (0,)):
            _stop('GIT_INSPECTION_FAILED')
        if errors:
            _stop('GIT_INSPECTION_FAILED')
        result = bytes(out)
        return (result, returncode) if return_exit_code else result
    except subprocess.TimeoutExpired:
        _stop('GIT_INSPECTION_TIMEOUT')
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, 9)
            except OSError:
                process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()


@dataclass(frozen=True)
class GitSnapshot:
    root: str
    common_dir: str
    branch: str
    head: str
    clean: bool


def collect_git_snapshot(root):
    root = os.path.realpath(root)
    actual_root = _git(root, ('rev-parse', '--show-toplevel'), limit=4096).decode().strip()
    common = _git(root, ('rev-parse', '--path-format=absolute', '--git-common-dir'),
                  limit=4096).decode().strip()
    _reject_legacy_grafts(root, common)
    branch = _git(root, ('branch', '--show-current'), limit=4096).decode().strip()
    head = _git(root, ('rev-parse', '--verify', 'HEAD'), limit=4096).decode().strip()
    status = _git(root, ('status', '--porcelain=v1', '-z', '--untracked-files=all'), limit=1024*1024)
    if (actual_root != root or not branch or _HEX40.fullmatch(head) is None or
            os.path.realpath(common) != common):
        _stop('GIT_BINDING_INVALID')
    _reject_legacy_grafts(root, common)
    return GitSnapshot(root, common, branch, head, status == b'')


def _reject_legacy_grafts(root, common_dir=None):
    """Refuse repositories whose legacy graft files can rewrite ancestry."""
    try:
        common_dir = common_dir or _git(
            root, ('rev-parse', '--path-format=absolute', '--git-common-dir'),
            limit=4096).decode('utf-8', 'strict').strip()
        git_dir = _git(root, ('rev-parse', '--path-format=absolute', '--git-dir'),
                       limit=4096).decode('utf-8', 'strict').strip()
        for info_dir in {Path(common_dir) / 'info', Path(git_dir) / 'info'}:
            grafts = info_dir / 'grafts'
            try:
                grafts.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                _stop('LEGACY_GRAFTS_UNSUPPORTED')
            # Any entry is unsupported: this includes symlinks, unreadable files,
            # directories, and other unexpected file types.
            _stop('LEGACY_GRAFTS_UNSUPPORTED')
    except LLMToolError:
        raise
    except Exception:
        _stop('LEGACY_GRAFTS_UNSUPPORTED')


def bind_executor_task(task_id, *, expected_common_dir, root=None):
    """Bind a proposal to the current D6 root and its registered branch HEAD."""
    root = os.getcwd() if root is None else root
    registration = read_d6_registration(task_id, expected_common_dir)
    snapshot = collect_git_snapshot(root)
    if (snapshot.root != registration.root or snapshot.common_dir != registration.common_dir or
            snapshot.branch != registration.branch or snapshot.head != registration.result_sha or
            not snapshot.clean):
        _stop('D6_SOURCE_BINDING_MISMATCH')
    try:
        _git(root, ('merge-base', '--is-ancestor', registration.source_sha, snapshot.head),
             limit=4096)
    except LLMToolError:
        _stop('D6_SOURCE_BINDING_MISMATCH')
    return registration, snapshot


def validate_task_binding(*, stored_binding, task_id, run_id, contract_digest):
    """Compare the durable TaskBinding to the identifiers bound at D6 admission."""
    if (type(stored_binding) is not dict or
            stored_binding.get('task_id') != task_id or
            stored_binding.get('run_id') != run_id or
            stored_binding.get('contract_digest') != contract_digest or
            type(task_id) is not str or type(run_id) is not str or
            type(contract_digest) is not str):
        _stop('TASK_RUN_CONTRACT_MISMATCH')
    return True


def validate_host_worker_receipt(packet, *, task_binding, task_id, run_id,
                                 contract_digest, source_sha):
    """Validate the minimal typed receipt/delivery fields linked by the Host seam."""
    required = {
        'task_binding', 'job_digest', 'agent_id', 'source_sha',
        'receipt_verdict', 'receipt_reason', 'receipt_accepted', 'receipt_digest',
        'delivery_state', 'delivery_reason', 'delivery_ack', 'delivery_accepted',
        'delivery_task_id', 'delivery_agent_id', 'delivery_job_digest',
        'delivery_source_sha', 'delivery_integrator_session',
        'delivery_receipt_digest', 'delivery_receipt_verdict',
        'delivery_receipt_reason', 'delivery_digest',
    }
    return (type(packet) is dict and set(packet) == required and
            type(task_binding) is dict and
            packet.get('task_binding') == task_binding and
            packet.get('task_binding') == {'task_id': task_id, 'run_id': run_id,
                'contract_version': task_binding.get('contract_version'),
                'contract_digest': contract_digest} and
            packet.get('source_sha') == source_sha and
            type(source_sha) is str and _HEX40.fullmatch(source_sha) is not None and
            type(packet.get('job_digest')) is str and
            re.fullmatch(r'[0-9a-f]{64}', packet['job_digest']) is not None and
            type(packet.get('agent_id')) is str and bool(packet['agent_id']) and
            packet.get('receipt_verdict') == 'SUCCESS' and
            packet.get('receipt_reason') == 'WORKER_RUN_OBSERVED' and
            packet.get('receipt_accepted') is False and
            type(packet.get('receipt_digest')) is str and
            re.fullmatch(r'[0-9a-f]{64}', packet['receipt_digest']) is not None and
            packet.get('delivery_state') == 'DELIVERED' and
            packet.get('delivery_reason') == 'RECEIPT_DELIVERED' and
            packet.get('delivery_ack') == 'CALLER_RETURNED' and
            packet.get('delivery_accepted') is False and
            packet.get('delivery_task_id') == task_id and
            packet.get('delivery_agent_id') == packet.get('agent_id') and
            packet.get('delivery_job_digest') == packet.get('job_digest') and
            packet.get('delivery_source_sha') == source_sha and
            type(packet.get('delivery_integrator_session')) is str and
            bool(packet['delivery_integrator_session']) and
            packet.get('delivery_receipt_digest') == packet.get('receipt_digest') and
            packet.get('delivery_receipt_verdict') == packet.get('receipt_verdict') and
            packet.get('delivery_receipt_reason') == packet.get('receipt_reason') and
            type(packet.get('delivery_digest')) is str and
            re.fullmatch(r'[0-9a-f]{64}', packet['delivery_digest']) is not None)


def _developer_changed_paths(root, source_sha, result_sha, trusted_base_sha=None):
    """Return Developer-attributed paths, excluding trusted main history."""
    if trusted_base_sha is None:
        trusted_base_sha = source_sha
    if type(trusted_base_sha) is not str or _HEX40.fullmatch(trusted_base_sha) is None:
        _stop('GIT_INSPECTION_FAILED')

    commits = _git(root, ('rev-list', '--reverse', '--topo-order',
                          source_sha + '..' + result_sha), limit=1024 * 1024)
    try:
        commit_ids = [item for item in commits.decode('ascii').splitlines() if item]
    except UnicodeError:
        _stop('GIT_INSPECTION_FAILED')
    if len(commit_ids) > 4096:
        _stop('RESULT_DIFF_INVALID')

    listed_commits = set(commit_ids)
    commit_parents = {}
    for commit in commit_ids:
        raw = _git(root, ('rev-list', '--parents', '-n', '1', commit), limit=4096)
        fields = raw.decode('ascii').split()
        if len(fields) < 2:
            _stop('SOURCE_NOT_ANCESTOR')
        commit_parents[commit] = fields[1:]

    current = result_sha
    while current != source_sha:
        if current not in listed_commits:
            _stop('SOURCE_NOT_ANCESTOR')
        current = commit_parents[current][0]

    inherited = set()
    merge_parents = {}
    merge_resolution_paths = {}
    for commit in commit_ids:
        fields = [commit, *commit_parents[commit]]
        if len(fields) < 3:
            continue
        second_parent = fields[2]
        _require_trusted_base(root, trusted_base_sha)
        if not _is_ancestor(root, second_parent, trusted_base_sha):
            continue
        merge_parents[commit] = second_parent
        history = _git(root, ('rev-list', '--max-count=4097', second_parent,
                              '^' + source_sha), limit=1024 * 1024)
        inherited_commits = history.decode('ascii').splitlines()
        if len(inherited_commits) > 4096:
            _stop('RESULT_DIFF_INVALID')
        inherited.update(inherited_commits)
        if len(fields) > 3:
            continue
        merge_resolution_paths[commit] = _merge_resolution_paths(
            root, commit, fields[1], second_parent)

    developer_paths = set()
    for commit in commit_ids:
        if commit in inherited:
            continue
        fields = [commit, *commit_parents[commit]]
        if len(fields) > 3 and commit in merge_parents:
            _stop('RESULT_DIFF_INVALID')
        first_parent = fields[1]
        # #2640: llmtool's own publish hash-refresh commit (#2497) is not
        # Developer work; it changes only guidance-drift.json to the exact
        # bytes the refresh computes from its parent (fail closed otherwise).
        if len(fields) == 2 and _guidance_refresh_commit(root, commit, first_parent):
            continue
        changed = _git(root, ('diff-tree', '--no-commit-id', '--name-only', '-r', '-z',
                              '--no-renames', first_parent, commit, '--'),
                       limit=1024 * 1024)
        try:
            paths = [item for item in changed.decode('utf-8', 'strict').split('\x00') if item]
        except UnicodeError:
            _stop('GIT_INSPECTION_FAILED')
        second_parent = merge_parents.get(commit)
        for path in paths:
            if (second_parent is not None and
                    path not in merge_resolution_paths.get(commit, set()) and
                    _tree_blob(root, commit, path) == _tree_blob(root, second_parent, path)):
                continue
            developer_paths.add(path)
            if len(developer_paths) > 128:
                _stop('RESULT_DIFF_INVALID')
        developer_paths.update(merge_resolution_paths.get(commit, set()))
        if len(developer_paths) > 128:
            _stop('RESULT_DIFF_INVALID')
    return tuple(sorted(developer_paths))





def collect_claude_result(*, task_id, expected_common_dir, expected_root,
                          expected_branch, source_sha, task_binding,
                          expected_agent, expected_session_ref,
                          expected_native_session_id, write_paths, claude_result,
                          artifact_segments=None, trusted_base_sha=None,
                          stacked_chain=None):
    """Verify a bound Claude result using D6 evidence, without an artifact claim.

    #1886: with artifact_segments, the D6 head is the Claude result plus
    verified repairs. The first segment is the Claude result's own and carries
    no artifact: it must run from source_sha to the recorded Claude head (or
    that head plus clean trusted-main merges). Every later segment must carry
    an artifact that matches its commits or, for a record-only repair, end at
    its own recorded result HEAD; the last one ends at the D6 head.
    """
    registration, snapshot, paths, developer_paths, patch = _collect_registered_result_diff(
        task_id=task_id, expected_common_dir=expected_common_dir,
        expected_root=expected_root, expected_branch=expected_branch,
        source_sha=source_sha, task_binding=task_binding,
        expected_agent=expected_agent, expected_session_ref=expected_session_ref,
        expected_native_session_id=expected_native_session_id,
        write_paths=write_paths, artifact_segments=artifact_segments,
        trusted_base_sha=trusted_base_sha, stacked_chain=stacked_chain)
    if (type(claude_result) is not dict or
            claude_result.get('executor_task_id') != registration.task_id):
        _stop('CLAUDE_RESULT_BINDING_INVALID')
    claimed_head = claude_result.get('head_sha')
    if artifact_segments is not None:
        if (type(artifact_segments) is not list or len(artifact_segments) < 2 or
                any(not _segment_shape_valid(segment) for segment in artifact_segments) or
                len(artifact_segments[0]) != 4):
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
        own_artifact, own_digest, own_start, own_end = artifact_segments[0]
        if (own_artifact is not None or own_digest is not None or
                own_start != source_sha or artifact_segments[-1][3] != snapshot.head or
                type(own_end) is not str or _HEX40.fullmatch(own_end) is None):
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
        if (type(claimed_head) is not str or _HEX40.fullmatch(claimed_head) is None or
                claimed_head == source_sha or
                not _is_ancestor(registration.root, source_sha, claimed_head) or
                (claimed_head != own_end and
                 _developer_tip(registration.root, claimed_head, own_end,
                                trusted_base_sha) != claimed_head)):
            _stop('CLAUDE_RESULT_BINDING_INVALID')
        own_paths = _developer_changed_paths(registration.root, source_sha, own_end,
                                             trusted_base_sha)
        if not own_paths or not set(own_paths).issubset(set(write_paths)):
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
        _verify_artifact_segments(registration.root, artifact_segments[1:], own_end,
                                  write_paths, trusted_base_sha)
    elif claimed_head != registration.result_sha:
        # The bound result stays valid when the head only adds clean main merges.
        if (type(claimed_head) is not str or _HEX40.fullmatch(claimed_head) is None or
                _developer_tip(registration.root, claimed_head, snapshot.head,
                               trusted_base_sha) != claimed_head):
            _stop('CLAUDE_RESULT_BINDING_INVALID')
    elif claimed_head != snapshot.head:
        _stop('RESULT_HEAD_MISMATCH')
    _reject_legacy_grafts(registration.root, registration.common_dir)
    result = {
        'task_id': task_binding.get('task_id'), 'run_id': task_binding.get('run_id'),
        'executor_task_id': registration.task_id,
        'executor_identity': registration.trusted_agent_id,
        'executor_session_ref': registration.session_ref,
        'executor_native_session_id': registration.native_session_id,
        'executor_native_session': ('VERIFIED' if registration.native_session_id else 'UNKNOWN'),
        'root': registration.root, 'common_dir': registration.common_dir,
        'branch': registration.branch, 'source_sha': source_sha,
        'result_head': snapshot.head, 'changed_paths': list(paths),
        'developer_changed_paths': list(developer_paths),
        'diff_sha256': hashlib.sha256(patch).hexdigest(),
    }
    if stacked_chain is not None:
        from . import task_completion_part5
        result.update(task_completion_part5._stacked_chain_evidence(
            registration.root, stacked_chain, source_sha))
    return result


def _worktree_snapshot(*, root, source_sha, read_paths, write_paths, expected_head=None):
    """Hash a registered uncommitted result without promoting it to a commit."""
    snapshot = collect_git_snapshot(root)
    if snapshot.head != (source_sha if expected_head is None else expected_head):
        _stop('WORKTREE_SOURCE_HEAD_MISMATCH')
    status = _git(root, ('status', '--porcelain=v1', '-z', '--untracked-files=all'),
                   limit=1024 * 1024)
    untracked_raw = _git(root, ('ls-files', '--others', '--exclude-standard', '-z'),
                         limit=64 * 1024)
    try:
        untracked = [path for path in untracked_raw.decode('utf-8', 'strict').split('\x00')
                     if path]
    except UnicodeError:
        _stop('WORKTREE_PATH_INVALID')
    if any(path not in set(write_paths) for path in untracked):
        _stop('WORKTREE_UNTRACKED_FILES')
    changed, patch = _worktree_diff(root, source_sha)
    if not changed and not patch:
        _stop('WORKTREE_SCOPE_MISMATCH')
    for path in changed:
        parts = Path(path).parts
        if (Path(path).is_absolute() or not parts or
                any(part in ('', '.', '..') for part in parts) or '\\' in path or
                any(ord(char) < 0x20 or ord(char) >= 0x7f for char in path)):
            _stop('WORKTREE_PATH_INVALID')
    if any(path not in set(write_paths) for path in changed):
        _stop('WORKTREE_SCOPE_MISMATCH')

    paths = sorted(set(read_paths) | set(write_paths))
    if not paths or len(paths) > _TASK_PATH_LIMIT:
        _stop('WORKTREE_SCOPE_INVALID')
    file_records = []
    total = 0
    for relative in paths:
        if (type(relative) is not str or not relative or relative.startswith('/') or
                '\\' in relative or any(part in ('', '.', '..') for part in relative.split('/'))):
            _stop('WORKTREE_PATH_INVALID')
        target = Path(root).joinpath(*relative.split('/'))
        try:
            before = target.lstat()
        except FileNotFoundError:
            file_records.append([relative, None, None, None])
            continue
        except OSError:
            _stop('WORKTREE_FILE_UNAVAILABLE')
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size > _GIT_LIMIT):
            _stop('WORKTREE_FILE_UNSUPPORTED')
        total += before.st_size
        if total > _PROPOSAL_TREE_LIMIT:
            _stop('WORKTREE_FILES_TOO_LARGE')
        try:
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                opened = os.fstat(fd)
                if (not stat.S_ISREG(opened.st_mode) or
                        (opened.st_dev, opened.st_ino, opened.st_size) !=
                        (before.st_dev, before.st_ino, before.st_size)):
                    _stop('WORKTREE_FILE_CHANGED')
                digest = hashlib.sha256()
                size = 0
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > _GIT_LIMIT:
                        _stop('WORKTREE_FILE_TOO_LARGE')
                    digest.update(chunk)
                after = os.fstat(fd)
                current = target.lstat()
                if ((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) !=
                        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or
                        (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino) or
                        size != opened.st_size):
                    _stop('WORKTREE_FILE_CHANGED')
            finally:
                os.close(fd)
        except LLMToolError:
            raise
        except Exception:
            _stop('WORKTREE_FILE_UNAVAILABLE')
        file_records.append([relative, before.st_mode & 0o777, size, digest.hexdigest()])
    manifest = json.dumps(file_records, ensure_ascii=True, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')
    return {
        'root': snapshot.root, 'common_dir': snapshot.common_dir,
        'branch': snapshot.branch, 'source_sha': snapshot.head,
        'changed_paths': list(changed), 'diff': patch,
        'diff_sha256': hashlib.sha256(patch).hexdigest(),
        'status_sha256': hashlib.sha256(status).hexdigest(),
        'file_manifest_sha256': hashlib.sha256(manifest).hexdigest(),
    }


def collect_executor_worktree_result(*, task_id, expected_common_dir, expected_root,
                                     expected_branch, source_sha, task_binding,
                                     expected_result_head=None,
                                     expected_agent, expected_session_ref,
                                     expected_native_session_id, write_paths, read_paths,
                                     artifact, artifact_sha256):
    """Bind a real D6 uncommitted diff and refuse to call it a result commit."""
    registration = read_d6_registration(task_id, expected_common_dir,
                                        allowed_states=_D6_EVIDENCE_STATES)
    result_head = source_sha if expected_result_head is None else expected_result_head
    if (type(result_head) is not str or _HEX40.fullmatch(result_head) is None or
            registration.root != expected_root or registration.branch != expected_branch or
            registration.result_sha != result_head or
            registration.trusted_agent_id != expected_agent or
            registration.session_ref != expected_session_ref or
            registration.native_session_id != expected_native_session_id or
            type(source_sha) is not str or _HEX40.fullmatch(source_sha) is None):
        _stop('D6_TASK_BINDING_MISMATCH')
    if (type(task_binding) is not dict or
            any(type(task_binding.get(key)) is not str for key in
                ('task_id', 'run_id', 'contract_digest'))):
        _stop('TASK_BINDING_MISSING')
    _reject_legacy_grafts(registration.root, registration.common_dir)
    untracked_raw = _git(registration.root,
        ('ls-files', '--others', '--exclude-standard', '-z'), limit=64 * 1024)
    try:
        untracked = [path for path in untracked_raw.decode('utf-8', 'strict').split('\x00')
                     if path]
    except UnicodeError:
        _stop('WORKTREE_PATH_INVALID')
    if any(path not in set(write_paths) for path in untracked):
        _stop('WORKTREE_UNTRACKED_FILES')
    live = _worktree_snapshot(root=registration.root, source_sha=source_sha,
                              read_paths=read_paths, write_paths=write_paths,
                              expected_head=result_head)
    if (live['common_dir'] != registration.common_dir or
            live['branch'] != registration.branch or live['source_sha'] != result_head):
        _stop('D6_TASK_BINDING_MISMATCH')
    validate_env2_pass_marker(registration, expected_root=expected_root,
                              current_head=live['source_sha'], write_paths=write_paths)
    try:
        _git(registration.root, ('merge-base', '--is-ancestor',
                                 registration.source_sha, source_sha), limit=4096)
        _git(registration.root, ('merge-base', '--is-ancestor', source_sha,
                                 result_head), limit=4096)
    except LLMToolError:
        _stop('D6_TASK_BINDING_MISMATCH')
    _reject_legacy_grafts(registration.root, registration.common_dir)
    if (type(artifact) is not str or type(artifact_sha256) is not str or
            hashlib.sha256(artifact.encode('utf-8')).hexdigest() != artifact_sha256 or
            artifact.encode('utf-8') != live['diff']):
        _stop('WORKTREE_ARTIFACT_MISMATCH')
    return {
        'task_id': task_binding['task_id'], 'run_id': task_binding['run_id'],
        'executor_task_id': registration.task_id,
        'executor_identity': registration.trusted_agent_id,
        'executor_session_ref': registration.session_ref,
        'executor_native_session_id': registration.native_session_id,
        'executor_native_session': ('VERIFIED' if registration.native_session_id else 'UNKNOWN'),
        'root': registration.root, 'common_dir': registration.common_dir,
        'branch': registration.branch, 'source_sha': source_sha,
        'result_head': None, 'result_mode': 'UNCOMMITTED_WORKTREE',
        'changed_paths': live['changed_paths'], 'diff_sha256': live['diff_sha256'],
        'status_sha256': live['status_sha256'],
        'file_manifest_sha256': live['file_manifest_sha256'],
    }








def _docs_only_ci(paths):
    """Classify the exact D6 result paths using Linux CI's `classify` step."""
    # Keep this aligned with the classify step in .github/workflows/llmtool-linux-ci.yml.
    if type(paths) is not list or not paths:
        return False
    for path in paths:
        if type(path) is not str or not path or path.startswith('/') or '\\' in path:
            return False
        parts = path.split('/')
        if any(part in ('', '.', '..') for part in parts):
            return False
        if path.startswith('tools/llmtool/'):
            relative_parts = parts[2:]
            if (len(relative_parts) == 1 and path.endswith('.md')):
                continue
            if (len(relative_parts) == 2 and path.endswith('.md')):
                return False
            return False
        if path.startswith('.claude/skills/') and path.endswith('.py'):
            return False
        if (path.endswith('.md') or path in ('AGENTS.md', 'CLAUDE.md') or
                path.startswith(('.claude/skills/', 'docs/'))):
            continue
        return False
    return True





def _local_test_timeouts():
    """#2281 row 10: per-module and whole-run local test bounds, in seconds.

    An absent policy key keeps the module defaults, so code and tests that
    patch _TEST_TIMEOUT and _TEST_TOTAL_TIMEOUT keep their existing meaning. A
    present key sets the per-module bound, and the whole-run bound stays three
    times it.
    """
    sentinel = object()
    per_module = repair_policy.local_test_timeout_seconds(default=sentinel)
    if per_module is sentinel:
        return _TEST_TIMEOUT, _TEST_TOTAL_TIMEOUT
    return per_module, 3 * per_module


def run_bounded_tests(*, root, source_sha, result_sha, read_paths, write_paths,
                      trusted_base_sha=None):
    """Run fixed registered tests or record an actual Git-derived docs-only result.

    With trusted_base_sha the docs-only decision uses only Developer-attributed
    paths: files that a clean merge of trusted main brought in are not Task
    changes. A merge whose second parent is not in trusted main, or that has a
    conflict or hand resolution, still counts its paths. Without it the plain
    source_sha..result_sha diff decides, as before.
    """
    eligible = _eligible_registered_tests((*read_paths, *write_paths))
    before = collect_git_snapshot(root)
    if before.head != result_sha or not before.clean:
        _stop('TEST_SOURCE_MISMATCH')
    if trusted_base_sha is None:
        changed_paths = _committed_changed_paths(root, source_sha, result_sha)
    else:
        changed_paths = list(_developer_changed_paths(
            root, source_sha, result_sha, trusted_base_sha))
        if not changed_paths or len(changed_paths) > _PROPOSAL_FILE_LIMIT:
            _stop('TEST_PATH_INVALID')
    if _docs_only(changed_paths):
        return [{'status': 'NOT_APPLICABLE_DOCS_ONLY', 'changed_paths': changed_paths,
                 'source_sha': result_sha, 'docs_only': True}]
    if not eligible:
        _stop('TEST_SCOPE_MISSING')
    evidence = []
    with tempfile.TemporaryDirectory(prefix='llmtool-task-tests-') as home:
        evidence = _run_registered_tests(root, result_sha, eligible, home=home)
    after = collect_git_snapshot(root)
    if after != before:
        _stop('TEST_SOURCE_CHANGED')
    return evidence


def run_bounded_worktree_tests(*, root, source_sha, diff_sha256,
                               changed_paths, read_paths, write_paths):
    """Run fixed unittest discovery against an exact, still-uncommitted D6 diff."""
    if (type(diff_sha256) is not str or _HEX64.fullmatch(diff_sha256) is None or
            type(changed_paths) is not list or not changed_paths):
        _stop('TEST_BINDING_INVALID')
    before = _worktree_snapshot(root=root, source_sha=source_sha,
                                read_paths=read_paths, write_paths=write_paths)
    if (before['diff_sha256'] != diff_sha256 or
            before['changed_paths'] != changed_paths):
        _stop('TEST_SOURCE_MISMATCH')
    if _docs_only(before['changed_paths']):
        return [{'status': 'NOT_APPLICABLE_DOCS_ONLY',
                 'changed_paths': before['changed_paths'], 'source_sha': source_sha,
                 'diff_sha256': diff_sha256, 'result_head': None, 'docs_only': True}]
    eligible = _eligible_registered_tests((*read_paths, *write_paths))
    if not eligible:
        _stop('TEST_SCOPE_MISSING')
    with tempfile.TemporaryDirectory(prefix='llmtool-task-worktree-tests-') as home, \
            tempfile.TemporaryDirectory(prefix='llmtool-task-python-cache-') as pycache:
        evidence = _run_registered_tests(root, source_sha, eligible,
                                         home=home, pycache=pycache)
        for item in evidence:
            item.update({'result_head': None, 'diff_sha256': diff_sha256})
    after = _worktree_snapshot(root=root, source_sha=source_sha,
                               read_paths=read_paths, write_paths=write_paths)
    if after != before:
        _stop('TEST_SOURCE_CHANGED')
    return evidence


def _trust_config():
    """Return the frozen TrustConfig, read at use time; fail closed with TRUST_*."""
    try:
        return trust.load_trust()
    except trust.TrustError as exc:
        code = exc.code
    _stop(code)


def _trusted_slug():
    return _trust_config().repository['slug']


def _ci_trust():
    return _trust_config().ci_trust


def _github_get(path):
    # The repository is private, so an anonymous request always gets 404. Read
    # through the signed-in GitHub CLI: llmtool never sees its credential, and
    # ambient token/proxy variables from this process are not forwarded.
    if type(path) is not str or not _GITHUB_API_PATH.fullmatch(path):
        _stop('GITHUB_EVIDENCE_UNAVAILABLE')
    gh = shutil.which('gh', path=_GH_PATH)
    if gh is None:
        _stop('GITHUB_EVIDENCE_UNAVAILABLE')
    slug = _trusted_slug()
    home = os.environ.get('HOME') or os.path.expanduser('~')
    env = {'PATH': _GH_PATH, 'HOME': home, 'LC_ALL': 'C',
           'GH_PROMPT_DISABLED': '1', 'GH_NO_UPDATE_NOTIFIER': '1', 'NO_COLOR': '1',
           'GH_CONFIG_DIR': github_identity.ai_gh_config_dir(home)}
    argv = [gh, 'api', '--method', 'GET',
            '-H', 'Accept: application/vnd.github+json',
            '-H', 'X-GitHub-Api-Version: 2022-11-28',
            'repos/' + slug + '/' + path]
    try:
        process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
            close_fds=True, start_new_session=True)
    except OSError:
        _stop('GITHUB_EVIDENCE_UNAVAILABLE')
    raw = bytearray()
    deadline = time.monotonic() + 8
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop('GITHUB_EVIDENCE_UNAVAILABLE')
            for key, _ in selector.select(min(remaining, .1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif len(raw) + len(chunk) > 1024 * 1024:
                    _stop('GITHUB_EVIDENCE_TOO_LARGE')
                else:
                    raw.extend(chunk)
        code = process.wait(timeout=max(0, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired):
        _stop('GITHUB_EVIDENCE_UNAVAILABLE')
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, 9)
            except OSError:
                process.kill()
            process.wait()
        process.stdout.close()
    if code != 0:
        _stop('GITHUB_EVIDENCE_UNAVAILABLE')
    raw = bytes(raw)
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _: _stop('GITHUB_EVIDENCE_INVALID'))
    except Exception:
        _stop('GITHUB_EVIDENCE_INVALID')


def _review_time(review, field='submitted_at'):
    submitted = review.get(field)
    if type(submitted) is not str:
        _stop('REVIEW_EVIDENCE_INVALID')
    try:
        parsed = datetime.fromisoformat(submitted.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            _stop('REVIEW_EVIDENCE_INVALID')
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        _stop('REVIEW_EVIDENCE_INVALID')


def _recorded_model_family(model):
    """Return 'claude', 'codex' or None for a recorded run model name.

    The name is normalised by dropping any provider prefix up to the last
    '/', dropping any variant suffix from the first '#', and lowercasing.
    """
    if type(model) is not str:
        return None
    name = model.rsplit('/', 1)[-1].split('#', 1)[0].lower()
    if name.startswith('claude'):
        return 'claude'
    if name.startswith(('gpt', 'codex')) or re.match(r'o[0-9]', name):
        return 'codex'
    return None


_REVIEW_AUTHOR_ROUTES = ('claude', 'codex', 'agy', 'opencode', 'company-gw', 'gw-agent')


def _recorded_run_family(profile, model):
    """Return the formal Reviewer family of a recorded run model, else None.

    #2302 part 1 (v91 finding 2, A1c): the family comes from the model the run
    recorded (developer_routes.model_family), never from its route. 'claude'
    and 'codex' are the Reviewer families; any other known family is 'other'.
    A model whose family cannot be determined fails closed (None) on the
    claude, codex and agy routes. The multi-vendor opencode, company-gw and
    gw-agent routes keep their former reading of such a name (Claude or Codex
    marker, else 'other'); a gw-agent model of unknown family is refused at
    dispatch, so only a legacy record or a gateway-served name reaches it.
    """
    from .developer_routes import MODEL_FAMILY_UNKNOWN, model_family
    if profile not in _REVIEW_AUTHOR_ROUTES:
        return None
    family = model_family(model)
    if family != MODEL_FAMILY_UNKNOWN:
        return family if family in ('claude', 'codex') else 'other'
    if profile in ('opencode', 'company-gw', 'gw-agent'):
        return _recorded_model_family(model) or 'other'
    return None


def _reviewer_author_family(developer):
    """Return the known family relevant to formal Reviewer separation.

    #2302 part 1 (v91 finding 2): a record that names its run model takes the
    family of that model on every route, so a Claude, AGY or gw-agent run of
    any vendor's model is separated from that vendor's Reviewer. A model of no
    determinable family fails closed. A legacy record with no model keeps the
    result its route profile gave before: claude, codex, the multi-vendor
    routes as 'other', and AGY unknown.
    """
    if type(developer) is not dict:
        return None
    # A stored record may still carry the retired gw Agent id (#1502 WP-E).
    profile = worker_dispatch.stored_worker_id(developer.get('provider_profile'))
    model = developer.get('model')
    if type(model) is str and model:
        return _recorded_run_family(profile, model)
    if profile in ('claude', 'codex'):
        return profile
    if profile in ('opencode', 'company-gw', 'gw-agent'):
        return 'other'
    return None


def _review_family_compatible(developer, reviewer_identity):
    """Require a known Developer family and the matching formal Reviewer family."""
    developer_family = _reviewer_author_family(developer)
    reviewer_family = {
        'claude-reviewer': 'claude',
        'codex-reviewer': 'codex',
        # #1019: the AGY Reviewer route authors Claude-family models.
        'agy-reviewer': 'claude',
    }.get(reviewer_identity)
    if developer_family is None or reviewer_family is None:
        return False
    return reviewer_family != developer_family


def _company_gw_text_proposal_pm_session(task_record, executor_identity):
    """Return the PM session of a PM-applied company-gw text-proposal Task, else None.

    Only an exact company-gw Developer record qualifies: no exposed native
    session, a responded unified diff with a sha256, applied in the D6
    registered to the company-gw trusted agent, and a known PM session.
    """
    if type(task_record) is not dict or executor_identity != 'company-gw':
        return None
    developer = task_record.get('developer')
    pm_session = task_record.get('pm_session_id')
    if (type(developer) is not dict or
            developer.get('provider_profile') != 'company-gw' or
            developer.get('backend_session_semantics') != 'NO_NATIVE_SESSION_ID_EXPOSED' or
            developer.get('state') != 'DEVELOPER_RESPONDED' or
            developer.get('artifact_format') != 'unified_diff' or
            type(developer.get('artifact_sha256')) is not str or
            _HEX64.fullmatch(developer['artifact_sha256']) is None or
            _native_session_identity(pm_session) is None):
        return None
    return pm_session





def _labels_equal(labels, expected):
    return (type(labels) is list and all(type(label) is str for label in labels) and
            set(labels) == set(expected))


def _runner_label_alternatives(runner_labels):
    """Turn github-hosted runner_labels into a list of label alternatives."""
    if not isinstance(runner_labels, (list, tuple)) or not runner_labels:
        return []
    if all(type(label) is str for label in runner_labels):
        return [runner_labels]
    if all(isinstance(alt, (list, tuple)) and alt and
           all(type(label) is str for label in alt) for alt in runner_labels):
        return list(runner_labels)
    return []


def _trusted_ci_runner(job, *, mode, runner_names, runner_labels):
    """Exact runner identity from ci_trust; never prefix, substring or wildcard."""
    labels = job.get('labels')
    if mode == 'self-hosted':
        runner_name = job.get('runner_name')
        return (type(runner_name) is str and runner_names is not None and
                runner_name in runner_names and
                (runner_labels is None or _labels_equal(labels, runner_labels)))
    if mode == 'github-hosted':
        alternatives = _runner_label_alternatives(runner_labels)
        return (runner_labels is not None and
                any(_labels_equal(labels, alt) for alt in alternatives) and
                'self-hosted' not in labels and
                job.get('runner_group_name') == _GITHUB_HOSTED_RUNNER_GROUP)
    return False





def _not_run_checks(docs_only):
    """Display-only NOT_RUN names: ci_trust not_run_checks, plus skipped code checks."""
    ci_trust = _ci_trust()
    base = tuple(ci_trust['not_run_checks'])
    skipped = tuple(workflow['code_check'] for workflow in ci_trust['workflows']
                    if docs_only and workflow['code_check'] is not None)
    return base + skipped


def collect_github_evidence(*, pr_number, result_sha, task_id, run_id,
                            contract_digest, executor_identity,
                            executor_session_ref, executor_native_session_id,
                            reviewer_task_id, common_dir, changed_paths=None,
                            task_record=None):
    """Read a public PR, formal review marker and exact-head required check runs."""
    profile = _resolve_ci_profile()
    if (type(pr_number) is not int or not 1 <= pr_number <= 9999999 or
            type(result_sha) is not str or _HEX40.fullmatch(result_sha) is None or
            type(task_id) is not str or re.fullmatch(r'[0-9a-f]{32}', task_id) is None or
            type(run_id) is not str or re.fullmatch(r'[0-9a-f]{32}', run_id) is None or
            type(contract_digest) is not str or _HEX64.fullmatch(contract_digest) is None):
        _stop('PR_NUMBER_INVALID')
    pr = _github_get(f'pulls/{pr_number}')
    is_open_pr = (type(pr) is dict and pr.get('state') == 'open' and
                  pr.get('merged') is False)
    is_merged_pr = (
        type(pr) is dict and pr.get('state') == 'closed' and pr.get('merged') is True and
        type(pr.get('merge_commit_sha')) is str and
        _HEX40.fullmatch(pr['merge_commit_sha']) is not None and
        type(pr.get('base')) is dict and pr['base'].get('ref') == 'main')
    if (type(pr) is not dict or not (is_open_pr or is_merged_pr) or
            type(pr.get('head')) is not dict or pr['head'].get('sha') != result_sha or
            type(pr.get('base')) is not dict or
            type(pr['base'].get('repo')) is not dict or
            pr['base']['repo'].get('full_name') != _trusted_slug() or
            type((pr.get('user') or {}).get('login')) is not str):
        _stop('PR_HEAD_MISMATCH')
    reviews = _github_get(f'pulls/{pr_number}/reviews?per_page=100')
    if type(reviews) is not list or len(reviews) >= 100 or any(
            type(review) is not dict for review in reviews):
        _stop('REVIEW_EVIDENCE_INVALID')
    try:
        reviewer = read_d6_registration(reviewer_task_id, common_dir,
                                        allowed_states=_D6_EVIDENCE_STATES)
    except LLMToolError:
        _stop('REVIEWER_D6_REGISTRY_UNAVAILABLE')
    latest = {}
    disposition_states = {'APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'}
    for candidate in reviews:
        if candidate.get('commit_id') != result_sha or candidate.get('state') not in disposition_states:
            continue
        login = (candidate.get('user') or {}).get('login')
        if type(login) is not str or not login:
            _stop('REVIEW_EVIDENCE_INVALID')
        submitted_at = _review_time(candidate)
        key = login.casefold()
        previous = latest.get(key)
        if previous is not None and submitted_at == previous[0]:
            if candidate != previous[1]:
                _stop('REVIEW_EVIDENCE_INVALID')
            continue
        if previous is None or submitted_at > previous[0]:
            latest[key] = (submitted_at, candidate)
    reviewer_login = reviewer.github_login
    binding_line = _review_binding_line(task_id, run_id, contract_digest, result_sha)
    review = None if reviewer_login is None else latest.get(reviewer_login.casefold(), (None, None))[1]
    # GitHub refuses APPROVE from a PR's own author, and every agent here posts
    # as the same account. The Reviewer therefore publishes its exact-HEAD
    # verdict as a PR conversation comment; its latest bound one is its
    # current disposition unless a newer PR review supersedes it. A later
    # verdict that names this HEAD without the binding line still supersedes
    # an earlier acceptance and then fails the binding gate.
    # Limitation: under one OS user and one shared GitHub account, nothing
    # binds a comment (or a PR review) to the Reviewer Session; any agent
    # with that account could post a bound ACCEPT. This gate is process
    # discipline, not adversarial independence, like the D6 registry itself.
    # The control that remains is the D6 Reviewer record (distinct agent,
    # Session and native Session, worktree at this HEAD) checked below.
    comments = _github_get(f'issues/{pr_number}/comments?per_page=100')
    if type(comments) is not list or len(comments) >= 100 or any(
            type(comment) is not dict for comment in comments):
        _stop('REVIEW_EVIDENCE_INVALID')
    verdict = None
    unsupported_family_comment = False
    codex_task_record = task_record
    codex_task_record_loaded = task_record is not None

    def load_completion_task_record():
        nonlocal codex_task_record, codex_task_record_loaded
        if not codex_task_record_loaded:
            from .task_store import TaskStore
            codex_task_record = TaskStore().get(task_id)
            codex_task_record_loaded = True
        return codex_task_record

    for comment in comments:
        login = (comment.get('user') or {}).get('login')
        body = comment.get('body')
        if (type(login) is not str or reviewer_login is None or
                login.casefold() != reviewer_login.casefold() or type(body) is not str):
            continue
        raw_lines = [line for line in body.splitlines() if line.strip()]
        lines = [line.strip() for line in raw_lines]
        if (not lines or not _VERDICT_ACTIVITY.fullmatch(lines[0]) or
                (binding_line not in lines and result_sha not in body)):
            continue
        # A padded marker or Verdict line is still a disposition for this
        # HEAD, but only the verbatim standard lines can be the standard form.
        marker = _VERDICT_MARKER.fullmatch(raw_lines[0])
        status = None if len(raw_lines) < 2 else _VERDICT_STATUS.fullmatch(raw_lines[1])
        standard = (marker is not None and status is not None and
                    status.group(1) == marker.group(2) and status.group(2) == result_sha)
        if standard and marker.group(2) == 'PASS':
            pass_task = load_completion_task_record()
            if (type(pass_task) is not dict or
                    not _review_family_compatible(
                        pass_task.get('developer'), reviewer.trusted_agent_id)):
                unsupported_family_comment = True
        # #1019: Codex and the AGY Reviewer both return a structured, fully
        # binding PASS. Each is accepted only from its own Reviewer D6 and its
        # one matching COMPLETED claim; any other PASS stays advisory.
        structured_actor = (marker.group(1) if standard and
                            marker.group(1) in ('Codex', 'AGY') else None)
        accepted_structured = False
        if structured_actor is not None and marker.group(2) == 'PASS':
            expected_reviewer = ('codex-reviewer' if structured_actor == 'Codex'
                                 else 'agy-reviewer')
            if reviewer.trusted_agent_id == expected_reviewer:
                try:
                    version_match = re.fullmatch(
                        r'Verdict: PASS \| Version: v([1-9][0-9]{0,3}) '
                        r'\| HEAD: [0-9a-f]{40}', raw_lines[1])
                    if version_match is None:
                        continue
                    version = int(version_match.group(1))
                    if not codex_task_record_loaded:
                        load_completion_task_record()
                    from . import review_result
                    classified = review_result._comment_verdict(
                        comment, head=result_sha,
                        version=version, task=codex_task_record)
                    claims = codex_task_record.get('review_dispatch_claim')
                    if type(claims) is dict:
                        claims = [claims]
                    task_binding = (codex_task_record.get('task_binding')
                                    if type(codex_task_record) is dict else None)
                    matching_claims = [claim for claim in claims
                                       if type(claim) is dict and
                                       claim.get('pr') == pr_number and
                                       claim.get('head_sha') == result_sha and
                                       claim.get('reviewer_task') == reviewer_task_id and
                                       claim.get('version') == version and
                                       review_result.review_kind(claim) == 'formal' and
                                       claim.get('state') == 'COMPLETED' and
                                       type(claim.get('review_result')) is dict and
                                       claim['review_result'].get('task_id') == task_id and
                                       claim['review_result'].get('run_id') == run_id and
                                       type(task_binding) is dict and
                                       task_binding.get('contract_digest') == contract_digest and
                                       claim['review_result'].get('contract_digest') ==
                                       task_binding.get('contract_digest') and
                                       claim['review_result'].get('head') == result_sha and
                                       claim['review_result'].get('verdict') == 'PASS'
                                       ] if type(claims) is list else []
                    comment_body = _canonical_verdict_body(body)
                    claim_body = (_canonical_verdict_body(
                                      matching_claims[0]['review_result'].get('review_text'))
                                  if len(matching_claims) == 1 else None)
                    accepted_structured = (
                        type(codex_task_record) is dict and
                        codex_task_record.get('task_id') == task_id and
                        codex_task_record.get('run_id') == run_id and
                        type(classified) is tuple and len(classified) >= 3 and
                        classified[2] == 'REVIEW_PASS' and
                        _review_family_compatible(
                            codex_task_record.get('developer'), reviewer.trusted_agent_id) and
                        len(matching_claims) == 1 and bool(claim_body) and
                        claim_body == comment_body)
                except (LLMToolError, KeyError, TypeError, ValueError, IndexError):
                    accepted_structured = False
            if not accepted_structured:
                # Unbound or otherwise unverified structured PASS stays advisory.
                # When the only completed claim at the head is security-kind or
                # unkinded at version 91 or above, no formal claim matches, so
                # formal review remains unadmitted and completion refuses
                # FORMAL_REVIEW_MISSING.
                continue
        # Anything else that claims to be a verdict for this HEAD is a
        # disposition; only the exact standard Claude PASS form accepts.
        if (standard and marker.group(1) == 'Claude' and marker.group(2) == 'PASS' and
                is_formal_claude_pass(
                    body, task_id=task_id, run_id=run_id,
                    contract_digest=contract_digest, result_sha=result_sha)):
            claude_task_record = load_completion_task_record()
            claude_family_compatible = (
                reviewer.trusted_agent_id == 'claude-reviewer' and
                type(claude_task_record) is dict and
                _review_family_compatible(claude_task_record.get('developer'),
                                          reviewer.trusted_agent_id))
        else:
            claude_family_compatible = False
        accepted = (standard and (
            (marker.group(1) == 'Claude' and marker.group(2) == 'PASS' and
             claude_family_compatible) or
            (structured_actor is not None and marker.group(2) == 'PASS' and
             accepted_structured)))
        if type(comment.get('id')) is not int:
            _stop('REVIEW_EVIDENCE_INVALID')
        created = _review_time(comment, 'created_at')
        updated = _review_time(comment, 'updated_at')
        # Verdicts are never edited; an edited one is a disposition that
        # cannot accept and is ordered by its last change, so it supersedes
        # any PASS posted before that edit.
        if updated != created:
            accepted = False
        key = (max(created, updated), comment['id'])
        if verdict is None or key[0] > verdict[0][0]:
            latest_outcomes = {accepted}
        elif key[0] == verdict[0][0]:
            latest_outcomes.add(accepted)
        if verdict is None or key > verdict[0]:
            verdict = (key, comment, accepted)
    review_source = None if review is None else 'PR_REVIEW'
    # Equal timestamps give no reliable order: across the two endpoints, or
    # between an accepting and a non-accepting verdict comment (the id is
    # creation order, not edit order).
    ordering_ambiguous = verdict is not None and (
        len(latest_outcomes) > 1 or
        (review is not None and verdict[0][0] == _review_time(review)))
    if ordering_ambiguous:
        verdict = (verdict[0], verdict[1], False)
    if verdict is not None and (review is None or verdict[0][0] > _review_time(review)):
        review = {'state': 'VERDICT_COMMENT' if verdict[2] else 'VERDICT_COMMENT_NOT_ACCEPTED',
                  'commit_id': result_sha, 'body': verdict[1]['body'],
                  'user': {'login': verdict[1]['user']['login']}}
        review_source = 'PR_COMMENT'
    formal_marker = re.compile(r'(?im)^\s*FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\s*$')
    review_body = '' if review is None or type(review.get('body')) is not str else review['body']
    body_lines = {line.strip() for line in review_body.splitlines()}
    review_binding_verified = binding_line in body_lines
    accepting_states = ('APPROVED', 'VERDICT_COMMENT')
    missing = []
    if unsupported_family_comment:
        missing.append('REVIEW_DEVELOPER_FAMILY_UNSUPPORTED')
    if ('REVIEW_DEVELOPER_FAMILY_UNSUPPORTED' not in missing and review is not None and
            (review.get('state') in accepting_states or review_source == 'PR_COMMENT')):
        completion_task = load_completion_task_record()
        developer = (completion_task.get('developer')
                     if type(completion_task) is dict else None)
        if not _review_family_compatible(developer, reviewer.trusted_agent_id):
            missing.append('REVIEW_DEVELOPER_FAMILY_UNSUPPORTED')
    if review is None:
        missing.append('FORMAL_REVIEW_MISSING')
    elif review.get('state') not in accepting_states or not formal_marker.search(review_body):
        missing.append('FORMAL_REVIEW_NOT_ACCEPTED')
    if review is not None and not review_binding_verified:
        missing.append('FORMAL_REVIEW_BINDING_MISMATCH')
    if any(item[1].get('state') == 'CHANGES_REQUESTED' for item in latest.values()):
        missing.append('FORMAL_REVIEW_NOT_ACCEPTED')
    if ordering_ambiguous:
        missing.append('FORMAL_REVIEW_NOT_ACCEPTED')
    if (verdict is not None and verdict[2] and codex_task_record_loaded and
            _newer_claim_pending(codex_task_record, pr_number, result_sha,
                                 verdict[1].get('body'))):
        missing.append('REVIEW_NEWER_CLAIM_PENDING')
    if (reviewer.trusted_agent_id == executor_identity or
            reviewer.session_ref == 'UNKNOWN' or reviewer.session_ref == '' or
            reviewer.session_ref == executor_session_ref):
        missing.append('REVIEWER_D6_SESSION_NOT_INDEPENDENT')
    # Public GitHub review proves actor/body/head, but cannot bind that actor to
    # a native Reviewer session unless the Integrator D6 record pins the login.
    if reviewer.native_session_id in (None, 'UNKNOWN'):
        missing.append('REVIEWER_NATIVE_SESSION_UNVERIFIED')
    elif executor_native_session_id in (None, 'UNKNOWN'):
        # #1241: company-gw only proposes a text diff and exposes no native
        # session; the PM applies it in the registered company-gw D6. There,
        # independence rests on the bound cross-family Reviewer on its own D6,
        # whose native session must differ from the PM session. Both sides are
        # compared as normalized identities (the Task stores `pm-<native>`).
        # Every other route, and any company-gw record that does not match
        # exactly, keeps the fail-closed rule.
        pm_session = None
        if executor_identity == 'company-gw':
            try:
                pm_session = _company_gw_text_proposal_pm_session(
                    load_completion_task_record(), executor_identity)
            except LLMToolError:
                pm_session = None
        pm_identity = _native_session_identity(pm_session)
        reviewer_identity = _native_session_identity(reviewer.native_session_id)
        if (pm_identity is None or reviewer_identity is None or
                reviewer_identity == pm_identity):
            missing.append('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT')
    elif reviewer.native_session_id == executor_native_session_id:
        missing.append('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT')
    if reviewer.github_login is None:
        missing.append('REVIEWER_ACTOR_D6_BINDING_MISSING')
    elif reviewer_login is not None and reviewer_login.casefold() != reviewer.github_login.casefold():
        missing.append('REVIEWER_ACTOR_D6_BINDING_MISMATCH')
    try:
        reviewer_snapshot = collect_git_snapshot(reviewer.root)
        if (reviewer_snapshot.common_dir != reviewer.common_dir or
                reviewer_snapshot.branch != reviewer.branch or
                reviewer_snapshot.head != result_sha):
            missing.append('REVIEWER_D6_HEAD_MISMATCH')
    except LLMToolError:
        missing.append('REVIEWER_D6_HEAD_UNAVAILABLE')
    reviewer_gates = {
        'FORMAL_REVIEW_MISSING', 'FORMAL_REVIEW_NOT_ACCEPTED',
        'REVIEWER_D6_SESSION_NOT_INDEPENDENT',
        'REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT',
        'REVIEWER_NATIVE_SESSION_UNVERIFIED',
        'FORMAL_REVIEW_BINDING_MISMATCH',
        'REVIEW_DEVELOPER_FAMILY_UNSUPPORTED',
        'REVIEWER_ACTOR_D6_BINDING_MISSING',
        'REVIEWER_ACTOR_D6_BINDING_MISMATCH',
        'REVIEWER_D6_HEAD_MISMATCH', 'REVIEWER_D6_HEAD_UNAVAILABLE',
        'REVIEW_NEWER_CLAIM_PENDING',
    }
    reviewer_bound = (review is not None and review.get('state') in accepting_states and
                      bool(formal_marker.search(review_body)) and review_binding_verified and
                      not reviewer_gates.intersection(missing))
    ci = _collect_ci_evidence(pr_number=pr_number, result_sha=result_sha,
                              changed_paths=changed_paths, pr_merged=is_merged_pr,
                              pr_head_ref=pr['head'].get('ref'), profile=profile)
    successful = set(ci['successful_checks'])
    required_checks = set(ci['required_checks'])
    if not required_checks.issubset(successful):
        missing.append('REQUIRED_CI_MISSING')
        if 'FAILED' in ci.get('check_outcomes', {}).values():
            missing.append('REQUIRED_CI_FAILED')
        elif 'PENDING' in ci.get('check_outcomes', {}).values():
            missing.append('REQUIRED_CI_PENDING')
    return {'evidence': {'pr_number': pr_number, 'head_sha': result_sha,
            'pr_state': 'MERGED' if is_merged_pr else 'OPEN',
            'merge_commit_sha': pr.get('merge_commit_sha') if is_merged_pr else None,
            'formal_review_state': 'ACCEPTED' if reviewer_bound else 'UNVERIFIED',
            'review_actor': None if review is None else review['user']['login'].casefold(),
            'reviewer_github_login': None if reviewer.github_login is None else reviewer.github_login.casefold(),
            'review_commit_sha': None if review is None else review['commit_id'],
            'review_source': review_source,
            'reviewer_d6_identity': reviewer.trusted_agent_id,
            'reviewer_task_id': reviewer_task_id,
            'reviewer_session_ref': reviewer.session_ref,
            'review_session_identity': ('D6_NATIVE_SESSION_VERIFIED'
                if reviewer.native_session_id not in (None, 'UNKNOWN') else 'UNKNOWN'),
            'review_task_id': task_id, 'review_run_id': run_id,
            'review_contract_digest': contract_digest,
            'review_binding_verified': review_binding_verified,
            'required_checks': sorted(required_checks),
            'successful_checks': sorted(successful), 'ci_head_sha': result_sha,
            'not_run_checks': [{'name': name, 'status': 'NOT_RUN'}
                               for name in _not_run_checks(ci['docs_only'])],
            'ci_workflow_path': ci['workflow_path'],
            'ci_workflow_name': ci['workflow_name'],
            'ci_workflow_id': ci['workflow_id'], 'ci_run_id': ci['run_id'],
            'ci_run_attempt': ci['run_attempt'], 'ci_run_number': ci['run_number'],
            'ci_check_ids': ci['check_ids']},
            'missing_gates': sorted(set(missing))}


# #2281: names moved to the part modules are re-exported here, so every
# importer and test that uses or patches this module path keeps working.
from .task_completion_part1 import (  # noqa: E402
    _canonical_verdict_body,
    _committed_changed_paths,
    _newer_claim_pending,
    is_formal_claude_pass,
    normalize_review_handoff_record,
    review_dispatch_blocked,
    review_handoff_dispatching,
    review_handoff_not_dispatched,
    review_handoff_pending,
    review_handoff_record,
    review_handoff_unknown,
    review_repair_intent,
    review_return_decision_allowed,
    review_return_record,
    validate_review_dispatch_receipt,
    validate_review_handoff_record,
    validate_review_repair_intent,
    validate_review_return_record,
)
from .task_completion_part2 import (  # noqa: E402
    D6Registration,
    _proposal_blob_size,
    _test_excerpt,
    _tree_blob,
    _tree_entry,
    _worktree_diff,
    read_d6_registration,
    validate_env2_pass_marker,
)
from .task_completion_part3 import (  # noqa: E402
    _collect_registered_result_diff,
    _commit_present,
    _developer_tip,
    _guidance_refresh_commit,
    _diff_paths,
    _is_ancestor,
    _merge_resolution_paths,
    _merge_tree_conflict_paths_raw,
    _native_session_identity,
    _private_merge_read,
    _remerge_diff_paths_raw,
    _require_trusted_base,
    _review_binding_line,
    _segment_shape_valid,
    _verify_artifact_segments,
    _verify_recorded_segment,
    _docs_only,
    _registered_test,
    collect_executor_result,
    ensure_main_commit,
    head_adds_only_main,
    read_authenticated_main_sha,
)
from .task_completion_part4 import (  # noqa: E402
    _collect_ci_evidence,
    _collect_one_ci_workflow,
    _resolve_ci_profile,
    verify_artifact_applied,
)
from .task_completion_part5 import (  # noqa: E402
    _run_registered_tests,
)
