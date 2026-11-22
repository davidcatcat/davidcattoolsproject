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
from . import source_freshness
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
_TEST_TIMEOUT = 600
# #2543: wall-clock bound for one whole local run (all modules in sequence).
# Without it a Task with many eligible modules ran for hours on the Owner Mac
# while task advance held its locks. Three modules at the per-module cap; the
# slowest measured module is ~310 s (#1703 above), so about five slow modules fit.
_TEST_TOTAL_TIMEOUT = 3 * _TEST_TIMEOUT
_TEST_OUTPUT_LIMIT = 256 * 1024
# Legacy CI values, kept only for ``llmtool trust verify-legacy`` until #369
# PR-10. The completion gate reads ``ci_trust`` from trust.json at use time
# (see ``_ci_trust``); none of these is consulted by the runtime CI checks,
# except that ``_GITHUB_ACTIONS_APP_ID`` stays the fixed pin the trust value
# ``github_actions_app_id`` must equal (the trust schema pins it too).
_MAC_CHECK = 'check (macOS 3.13)'
_LINUX_CHECK = 'check (Linux 3.14)'
_DOCS_CHECK = 'check (docs)'
_REQUIRED_CHECKS = frozenset((_MAC_CHECK, _LINUX_CHECK))
_NOT_RUN_CHECKS = ('Linux Python 3.9', 'Windows CI')
_CI_RUNNER_NAMES = frozenset(('davidproject-self-action', 'davidproject-self-action-2'))
_CI_WORKFLOW_PATH = '.github/workflows/llmtool-mac-ci.yml'
_CI_WORKFLOW_NAME = 'llmtool Mac CI'
_LINUX_CI_WORKFLOW_PATH = '.github/workflows/llmtool-linux-ci.yml'
_LINUX_CI_WORKFLOW_NAME = 'llmtool Linux CI'
_LINUX_CI_RUNNER_NAMES = frozenset((
    'ec2-self-runner', 'ec2-self-runner-2', 'ec2-self-runner-3',
    'ec2-self-runner-4'))
_GITHUB_ACTIONS_APP_ID = 15368
_D6_EVIDENCE_STATES = frozenset(('ACTIVE', 'REVIEW_PENDING', 'REPAIR'))
# A GitHub-hosted job reports this runner group; self-hosted groups never do.
_GITHUB_HOSTED_RUNNER_GROUP = 'GitHub Actions'
_TEST_REGISTRY = (
    (re.compile(r'tools/llmtool/tests/test_[A-Za-z0-9_]+\.py\Z'), 'unittest'),
    (re.compile(r'scripts/test_[A-Za-z0-9_]+\.py\Z'), 'script'),
)
_REVIEW_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
_REVIEW_VERDICTS = frozenset(('PASS', 'FAIL', 'BLOCKED'))
_GH_PATH = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'
# The leading `<!-- activity: ... -->` line every verdict post starts with.
_VERDICT_ACTIVITY = re.compile(r'<!-- activity: (?:[^>]* )?type=review_verdict(?: [^>]*)? -->')
# The one standard formal verdict format: .claude/skills/ai-team-delivery/formal-verdict.md.
_VERDICT_MARKER = re.compile(
    r'<!-- activity: type=review_verdict actor=(Claude|Codex) target=PM status=(PASS|FIX|FAIL) -->')
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


def collect_executor_result(*, task_id, expected_common_dir, expected_root,
                           expected_branch, source_sha, task_binding,
                           expected_agent, expected_session_ref,
                           expected_native_session_id, write_paths, artifact, artifact_sha256,
                           artifact_segments=None, trusted_base_sha=None):
    """Compare the current registered Executor HEAD and actual source-to-result diff."""
    registration, snapshot, paths, developer_paths, patch = _collect_registered_result_diff(
        task_id=task_id, expected_common_dir=expected_common_dir,
        expected_root=expected_root, expected_branch=expected_branch,
        source_sha=source_sha, task_binding=task_binding,
        expected_agent=expected_agent, expected_session_ref=expected_session_ref,
        expected_native_session_id=expected_native_session_id,
        write_paths=write_paths, artifact_segments=artifact_segments,
        trusted_base_sha=trusted_base_sha)
    # An artifact describes the Developer's work, so it is compared with the
    # Developer tip. Paths, digest and scope above still cover the full head.
    if artifact_segments is None:
        tip = _developer_tip(registration.root, source_sha, snapshot.head, trusted_base_sha)
        verify_artifact_applied(registration.root, artifact, artifact_sha256,
                                source_sha, tip,
                                _diff_paths(registration.root, source_sha, tip),
                                write_paths, scope_paths=developer_paths)
    else:
        # The original proposal's own segment carries its artifact; a later
        # repair segment may be a record-only one bound to its result HEAD.
        if (type(artifact_segments) is not list or not artifact_segments or
                any(not _segment_shape_valid(segment) for segment in artifact_segments) or
                len(artifact_segments[0]) != 4):
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
        if (artifact_segments[0][2] != source_sha or
                artifact_segments[-1][3] != snapshot.head):
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
        _verify_artifact_segments(registration.root, artifact_segments, source_sha,
                                  write_paths, trusted_base_sha)
    _reject_legacy_grafts(registration.root, registration.common_dir)
    return {
        'task_id': task_binding.get('task_id'), 'run_id': task_binding.get('run_id'),
        'executor_task_id': registration.task_id,
        'executor_identity': registration.trusted_agent_id,
        'executor_session_ref': registration.session_ref,
        'executor_native_session_id': registration.native_session_id,
        'executor_native_session': ('VERIFIED' if registration.native_session_id else 'UNKNOWN'),
        'root': registration.root, 'common_dir': registration.common_dir,
        'branch': registration.branch,
        'source_sha': source_sha, 'result_head': snapshot.head,
        'changed_paths': list(paths),
        'developer_changed_paths': list(developer_paths),
        'diff_sha256': hashlib.sha256(patch).hexdigest(),
    }


def collect_claude_result(*, task_id, expected_common_dir, expected_root,
                          expected_branch, source_sha, task_binding,
                          expected_agent, expected_session_ref,
                          expected_native_session_id, write_paths, claude_result,
                          artifact_segments=None, trusted_base_sha=None):
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
        trusted_base_sha=trusted_base_sha)
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
    return {
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


def _committed_changed_paths(root, source_sha, result_sha):
    """Read all paths changed between the task source and exact result."""
    raw = _git(root, ('diff', '--name-only', '-z', '--no-ext-diff', '--no-textconv',
                      '--no-renames', source_sha, result_sha, '--'), limit=1024 * 1024)
    try:
        paths = sorted(set(path for path in raw.decode('utf-8', 'strict').split('\x00') if path))
    except UnicodeError:
        _stop('TEST_PATH_INVALID')
    if not paths or len(paths) > _PROPOSAL_FILE_LIMIT:
        _stop('TEST_PATH_INVALID')
    return paths


def _test_excerpt(home, output, code):
    """Return only bounded excerpts accepted by the sensitive-output screener."""
    if not code:
        return None
    # macOS may expose temporary directories through a /var symlink. Resolve
    # the trusted TemporaryDirectory path before the excerpt reader's strict
    # no-follow walk, which intentionally rejects symlinked components.
    capture = Path(home).resolve(strict=True) / 'test-output.log'
    capture.write_bytes(output)
    try:
        selected = select_test_excerpts(str(capture), code)
    except LLMToolError:
        return None
    return selected['excerpts']


def _run_registered_tests(root, source_sha, eligible, *, home, pycache=None):
    evidence = []
    run_deadline = time.monotonic() + _TEST_TOTAL_TIMEOUT
    for path, kind in eligible:
        if time.monotonic() >= run_deadline:
            _stop('TEST_RUNNER_TIMEOUT')
        env = {'PATH': os.path.dirname(sys.executable) + ':/usr/bin:/bin',
               'LC_ALL': 'C', 'HOME': home,
               'PYTHONPATH': str(Path(root) / 'tools' / 'llmtool'),
               'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': home}
        if pycache is not None:
            env['PYTHONPYCACHEPREFIX'] = pycache
        if kind == 'unittest':
            argv = [sys.executable, '-B', '-m', 'unittest', 'discover',
                    '-s', 'tools/llmtool/tests', '-p', Path(path).name]
        elif kind == 'script':
            argv = [sys.executable, '-I', '-B', '-m', 'unittest', 'discover',
                    '-s', 'scripts', '-p', Path(path).name]
        else:
            argv = [sys.executable, '-I', '-B', path]
        try:
            process = subprocess.Popen(argv, cwd=root, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, shell=False, close_fds=True,
                start_new_session=True)
        except OSError:
            _stop('TEST_RUNNER_UNAVAILABLE')
        output = bytearray()
        deadline = min(time.monotonic() + _TEST_TIMEOUT, run_deadline)
        selector = selectors.DefaultSelector()
        started = time.monotonic()
        try:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _stop('TEST_RUNNER_TIMEOUT')
                for key, _ in selector.select(min(remaining, .1)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        if len(output) + len(chunk) > _TEST_OUTPUT_LIMIT:
                            _stop('TEST_OUTPUT_TOO_LARGE')
                        output.extend(chunk)
            code = process.wait(timeout=max(0, deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            _stop('TEST_RUNNER_TIMEOUT')
        finally:
            selector.close()
            if process.poll() is None:
                try:
                    os.killpg(process.pid, 9)
                except OSError:
                    process.kill()
                process.wait()
            process.stdout.close()
        item = {
            'path': path, 'source_sha': source_sha, 'exit_code': code,
            'output_sha256': hashlib.sha256(output).hexdigest(),
            'duration_ms': max(0, int((time.monotonic()-started)*1000)),
            'command_sha256': hashlib.sha256(json.dumps(argv, separators=(',', ':')).encode()).hexdigest(),
        }
        ran = re.findall(rb'(?m)^Ran ([0-9]+) tests?\b', output) if kind == 'script' else None
        if code or (kind == 'script' and (not ran or int(ran[-1]) == 0)):
            item['output_excerpt'] = _test_excerpt(home, output, code)
            item['status'] = 'FAILED'
        else:
            item['status'] = 'PASSED'
        evidence.append(item)
    return evidence


def run_bounded_tests(*, root, source_sha, result_sha, read_paths, write_paths,
                      trusted_base_sha=None):
    """Run fixed registered tests or record an actual Git-derived docs-only result.

    With trusted_base_sha the docs-only decision uses only Developer-attributed
    paths: files that a clean merge of trusted main brought in are not Task
    changes. A merge whose second parent is not in trusted main, or that has a
    conflict or hand resolution, still counts its paths. Without it the plain
    source_sha..result_sha diff decides, as before.
    """
    eligible = sorted({(p, _registered_test(p)) for p in (*read_paths, *write_paths)
                       if _registered_test(p) is not None})
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
    eligible = sorted({(p, _registered_test(p)) for p in (*read_paths, *write_paths)
                       if _registered_test(p) is not None})
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


def _reviewer_author_family(developer):
    """Return the known family relevant to formal Reviewer separation.

    The route profile and the recorded run model both count: a multi-model
    route takes the family of its recorded model, and a single-family route
    whose recorded model belongs to the other Reviewer family fails closed.
    """
    if type(developer) is not dict:
        return None
    # A stored record may still carry the retired gw Agent id (#1502 WP-E).
    profile = worker_dispatch.stored_worker_id(developer.get('provider_profile'))
    if profile == 'claude':
        if _recorded_model_family(developer.get('model')) == 'codex':
            return None
        return 'claude'
    elif profile == 'codex':
        if _recorded_model_family(developer.get('model')) == 'claude':
            return None
        return 'codex'
    elif profile == 'agy':
        model = developer.get('model')
        if type(model) is str and model.startswith('claude-'):
            return 'claude'
        if type(model) is str and model.startswith('gemini-'):
            return 'other'
        if type(model) is str and model.startswith('gpt-'):
            return 'codex'  # #1604: OpenAI models (gpt-oss-*) share the codex family
        return None
    elif profile in ('opencode', 'company-gw', 'gw-agent'):
        return _recorded_model_family(developer.get('model')) or 'other'
    else:
        return None


def _review_family_compatible(developer, reviewer_identity):
    """Require a known Developer family and the matching formal Reviewer family."""
    developer_family = _reviewer_author_family(developer)
    reviewer_family = {
        'claude-reviewer': 'claude',
        'codex-reviewer': 'codex',
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


def _native_session_identity(value):
    """Return a comparable native session identity, else None.

    A Task stores a UUID-backed PM session as ``pm-<native UUID>`` while D6
    records hold the raw native UUID, so one ``pm-`` prefix is removed and the
    rest is case-folded. Empty or UNKNOWN identities give None.
    """
    if type(value) is not str:
        return None
    identity = value.strip()
    if identity[:3].casefold() == 'pm-':
        identity = identity[3:].strip()
    identity = identity.casefold()
    if not identity or identity == 'unknown':
        return None
    return identity


def _review_binding_line(task_id, run_id, contract_digest, result_sha):
    return (f'LLMTOOL-REVIEW-BINDING v1 task={task_id} run={run_id} '
            f'contract={contract_digest} head={result_sha}')


def _canonical_verdict_body(text):
    """Return the one canonical form used to match a PASS comment to its claim.

    A Codex PASS comment is accepted only when its body equals the bound
    claim's ``review_text`` after this single normalization of both texts:

    1. CRLF and lone CR line endings become LF;
    2. trailing whitespace and newlines at the end of the whole body are
       removed (nothing else: interior blank lines, leading text and
       per-line whitespace are kept and must match exactly).

    Return None for a non-string, so it never equals a real body.
    """
    if type(text) is not str:
        return None
    return text.replace('\r\n', '\n').replace('\r', '\n').rstrip()


def _newer_claim_pending(record, pr_number, head, body):
    """#1132 item 1: a newer open review claim at the same PR and head blocks an older PASS."""
    # Security v91 F1: read the status line with the acceptance parser's own
    # splitlines() semantics (CR, CRLF and Unicode separators included).
    match = next((found for line in (body if type(body) is str else '').splitlines()
                  if (found := re.fullmatch(r'Verdict: PASS \| Version: v(\d+) \| HEAD: '
                                            r'[0-9a-f]{40}', line.strip())) is not None),
                 None)
    claims = record.get('review_dispatch_claim') if type(record) is dict else None
    if type(claims) is dict:
        claims = [claims]
    if match is None or type(claims) is not list:
        return False
    version = int(match.group(1))
    return any(type(claim) is dict and claim.get('pr') == pr_number and
               claim.get('head_sha') == head and type(claim.get('version')) is int and
               claim['version'] > version and
               claim.get('state') in ('CLAIMED', 'DISPATCHED', 'UNKNOWN')
               for claim in claims)


def is_formal_claude_pass(body, *, task_id, run_id, contract_digest, result_sha):
    """Return whether a comment is the exact task-bound formal Claude PASS."""
    if type(body) is not str:
        return False
    raw_lines = [line for line in body.splitlines() if line.strip()]
    if len(raw_lines) < 2:
        return False
    marker = _VERDICT_MARKER.fullmatch(raw_lines[0])
    status = _VERDICT_STATUS.fullmatch(raw_lines[1])
    binding_line = _review_binding_line(task_id, run_id, contract_digest, result_sha)
    lines = [line.strip() for line in raw_lines]
    return (marker is not None and status is not None and
            marker.group(1) == 'Claude' and marker.group(2) == 'PASS' and
            status.group(1) == 'PASS' and status.group(2) == result_sha and
            _FORMAL_ACCEPT in lines and binding_line in lines)


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


def _collect_one_ci_workflow(*, pr_number, result_sha, workflow_path,
                             workflow_name, check_name, runner_names,
                             runner_labels=None, mode='self-hosted',
                             app_id=_GITHUB_ACTIONS_APP_ID,
                             pr_merged=False, pr_head_ref=None):
    if type(app_id) is not int or app_id != _GITHUB_ACTIONS_APP_ID:
        _stop('TRUST_CI_INVALID')
    slug = _trusted_slug()
    workflow = _github_get('actions/workflows/' + workflow_path.rsplit('/', 1)[-1])
    if (type(workflow) is not dict or type(workflow.get('id')) is not int or
            workflow['id'] <= 0 or workflow.get('path') != workflow_path or
            workflow.get('name') != workflow_name or workflow.get('state') != 'active'):
        _stop('CI_WORKFLOW_MISMATCH')
    workflow_id = workflow['id']
    runs = _github_get(
        f'actions/workflows/{workflow_id}/runs?head_sha={result_sha}&event=pull_request&per_page=100')
    if (type(runs) is not dict or type(runs.get('workflow_runs')) is not list or
            type(runs.get('total_count')) is not int or
            runs['total_count'] != len(runs['workflow_runs']) or
            len(runs['workflow_runs']) > 100):
        _stop('CI_WORKFLOW_RUNS_INVALID')
    candidates = []
    for run in runs['workflow_runs']:
        if type(run) is not dict:
            _stop('CI_WORKFLOW_RUNS_INVALID')
        pull_requests = run.get('pull_requests')
        matching_pr = (type(pull_requests) is list and
                       any(type(item) is dict and item.get('number') == pr_number and
                           type(item.get('head')) is dict and
                           item['head'].get('sha') == result_sha for item in pull_requests))
        merged_empty_prs_match = (
            pr_merged is True and type(pull_requests) is list and not pull_requests and
            run.get('head_branch') == pr_head_ref and
            type(run.get('head_repository')) is dict and
            run['head_repository'].get('full_name') == slug)
        if (run.get('workflow_id') != workflow_id or run.get('path') != workflow_path or
                run.get('event') != 'pull_request' or run.get('head_sha') != result_sha or
                not (matching_pr or merged_empty_prs_match)):
            _stop('CI_WORKFLOW_RUN_MISMATCH')
        if (type(run.get('id')) is not int or run['id'] <= 0 or
                type(run.get('run_number')) is not int or run['run_number'] <= 0 or
                type(run.get('run_attempt')) is not int or run['run_attempt'] <= 0):
            _stop('CI_WORKFLOW_RUNS_INVALID')
        candidates.append(run)
    if not candidates and type(pr_head_ref) is str and pr_head_ref:
        # Fall back to workflow_dispatch runs when no pull_request run exists.
        try:
            dispatch_runs = _github_get(
                f'actions/workflows/{workflow_id}/runs?head_sha={result_sha}&event=workflow_dispatch&per_page=100')
        except Exception:
            dispatch_runs = None
        if (type(dispatch_runs) is dict and
                type(dispatch_runs.get('workflow_runs')) is list and
                type(dispatch_runs.get('total_count')) is int and
                dispatch_runs['total_count'] == len(dispatch_runs['workflow_runs']) and
                len(dispatch_runs['workflow_runs']) <= 100):
            for run in dispatch_runs['workflow_runs']:
                if (type(run) is dict and
                        run.get('workflow_id') == workflow_id and
                        run.get('path') == workflow_path and
                        run.get('event') == 'workflow_dispatch' and
                        run.get('head_sha') == result_sha and
                        type(pr_head_ref) is str and pr_head_ref and
                        run.get('head_branch') == pr_head_ref and
                        type(run.get('id')) is int and run['id'] > 0 and
                        type(run.get('run_number')) is int and run['run_number'] > 0 and
                        type(run.get('run_attempt')) is int and run['run_attempt'] > 0):
                    candidates.append(run)
    if not candidates:
        return {'workflow_id': workflow_id, 'run_id': None, 'run_attempt': None,
                'run_number': None, 'successful_checks': [], 'check_ids': {},
                'check_outcomes': {check_name: 'MISSING'}}
    run = max(candidates, key=lambda item: (item['run_number'], item['run_attempt'], item['id']))
    run_id, attempt = run['id'], run['run_attempt']
    jobs = _github_get(f'actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100')
    if (type(jobs) is not dict or type(jobs.get('jobs')) is not list or
            type(jobs.get('total_count')) is not int or
            jobs['total_count'] != len(jobs['jobs']) or len(jobs['jobs']) > 100):
        _stop('CI_WORKFLOW_JOBS_INVALID')
    required_jobs = {}
    for job in jobs['jobs']:
        if type(job) is not dict:
            _stop('CI_WORKFLOW_JOBS_INVALID')
        if job.get('name') != check_name:
            continue
        check_url = job.get('check_run_url')
        check_id_match = (None if type(check_url) is not str else re.fullmatch(
            r'https://api\.github\.com/repos/' + re.escape(slug) + r'/'
            r'check-runs/([1-9][0-9]{0,18})', check_url))
        if (check_name in required_jobs or job.get('run_id') != run_id or
                not _trusted_ci_runner(job, mode=mode, runner_names=runner_names,
                                       runner_labels=runner_labels) or
                type(job.get('id')) is not int or job['id'] <= 0 or
                check_id_match is None):
            _stop('CI_WORKFLOW_JOBS_INVALID')
        required_jobs[check_name] = (job, int(check_id_match.group(1)))
    if set(required_jobs) != {check_name}:
        return {'workflow_id': workflow_id, 'run_id': run_id, 'run_attempt': attempt,
                'run_number': run['run_number'], 'successful_checks': [], 'check_ids': {},
                'check_outcomes': {check_name: 'MISSING'}}
    checks = _github_get(f'commits/{result_sha}/check-runs?per_page=100')
    if (type(checks) is not dict or type(checks.get('check_runs')) is not list or
            type(checks.get('total_count')) is not int or
            checks['total_count'] != len(checks['check_runs']) or
            len(checks['check_runs']) > 100):
        _stop('CI_EVIDENCE_INVALID')
    by_id = {}
    for check in checks['check_runs']:
        if type(check) is not dict or type(check.get('id')) is not int:
            _stop('CI_EVIDENCE_INVALID')
        if check['id'] in by_id and by_id[check['id']] != check:
            _stop('CI_EVIDENCE_INVALID')
        by_id[check['id']] = check
    successful, check_ids, check_outcomes = set(), {}, {}
    job, check_id = required_jobs[check_name]
    check = by_id.get(check_id)
    app = None if check is None else check.get('app')
    if (check is not None and check.get('name') == check_name and
            check.get('head_sha') == result_sha and type(app) is dict and
            app.get('id') == app_id and app.get('slug') == 'github-actions' and
            app.get('name') == 'GitHub Actions' and
            job.get('status') == 'completed' and job.get('conclusion') == 'success' and
            check.get('status') == 'completed' and check.get('conclusion') == 'success' and
            run.get('status') == 'completed' and run.get('conclusion') == 'success'):
        successful.add(check_name)
        check_ids[check_name] = check['id']
        check_outcomes[check_name] = 'SUCCESS'
    elif (check is None or check.get('name') != check_name or
          check.get('head_sha') != result_sha or type(app) is not dict or
          app.get('id') != app_id or app.get('slug') != 'github-actions' or
          app.get('name') != 'GitHub Actions'):
        check_outcomes[check_name] = 'MISSING'
    elif (run.get('status') != 'completed' or job.get('status') != 'completed' or
          check.get('status') != 'completed'):
        check_outcomes[check_name] = 'PENDING'
    elif (run.get('conclusion') in ('success', 'failure', 'cancelled', 'timed_out',
                                     'action_required', 'startup_failure', 'stale',
                                     'neutral') and
          job.get('conclusion') in ('success', 'failure', 'cancelled', 'timed_out',
                                     'action_required', 'startup_failure', 'stale',
                                     'neutral') and
          check.get('conclusion') in ('success', 'failure', 'cancelled', 'timed_out',
                                       'action_required', 'startup_failure', 'stale',
                                       'neutral')):
        check_outcomes[check_name] = 'FAILED'
    else:
        check_outcomes[check_name] = 'MISSING'
    return {'workflow_id': workflow_id, 'run_id': run_id, 'run_attempt': attempt,
            'run_number': run['run_number'], 'successful_checks': sorted(successful),
            'check_ids': check_ids, 'check_outcomes': check_outcomes}


def _collect_ci_evidence(*, pr_number, result_sha, changed_paths=None,
                         pr_merged=False, pr_head_ref=None):
    """Exact-head CI evidence for every trusted workflow on the selected path.

    Code path: every ci_trust workflow with a ``code_check`` must succeed and
    the first such workflow is primary; the docs-only path does the same with
    ``docs_check``. With the Owner's values this is the Mac + Linux code gate
    and the Linux ``check (docs)`` docs gate.
    """
    ci_trust = _ci_trust()
    docs_only = _docs_only_ci(changed_paths)
    linux_required = True
    field = 'docs_check' if docs_only else 'code_check'
    selected = [workflow for workflow in ci_trust['workflows']
                if workflow[field] is not None]
    # #1558: like task_advance._merge_checks, Mac only for Mac-workflow paths.
    if (not docs_only and type(changed_paths) is list and changed_paths and
            all(type(path) is str for path in changed_paths)):
        from . import task_advance  # imported here: task_advance imports this module
        if not task_advance._mac_ci_runs(changed_paths):
            selected = [workflow for workflow in selected
                        if workflow[field] != _MAC_CHECK]
    if not selected:
        _stop('TRUST_CI_INVALID')
    results = []
    for index, workflow in enumerate(selected):
        try:
            result = _collect_one_ci_workflow(
                pr_number=pr_number, result_sha=result_sha,
                workflow_path=workflow['path'], workflow_name=workflow['name'],
                check_name=workflow[field], runner_names=workflow['runner_names'],
                runner_labels=workflow['runner_labels'], mode=ci_trust['mode'],
                app_id=ci_trust['github_actions_app_id'], pr_merged=pr_merged,
                pr_head_ref=pr_head_ref)
        except LLMToolError as exc:
            # A malformed Mac job can otherwise mask a completed Linux failure.
            # Read the later required workflow only to surface that failure; if
            # it is not confirmed, preserve the original fail-closed error.
            if (index != 0 or getattr(exc, 'completion_code', None) !=
                    'CI_WORKFLOW_JOBS_INVALID' or
                    len(selected) < 2):
                raise
            later = []
            for later_workflow in selected[index + 1:]:
                try:
                    result = _collect_one_ci_workflow(
                        pr_number=pr_number, result_sha=result_sha,
                        workflow_path=later_workflow['path'],
                        workflow_name=later_workflow['name'],
                        check_name=later_workflow[field],
                        runner_names=later_workflow['runner_names'],
                        runner_labels=later_workflow['runner_labels'],
                        mode=ci_trust['mode'], app_id=ci_trust['github_actions_app_id'],
                        pr_merged=pr_merged, pr_head_ref=pr_head_ref)
                except LLMToolError:
                    # A secondary workflow cannot replace the original refusal
                    # unless it independently supplies trusted FAILED evidence.
                    continue
                later.append((later_workflow, result))
                if 'FAILED' in result['check_outcomes'].values():
                    break
            if not later or not any(
                    'FAILED' in item['check_outcomes'].values()
                    for _, item in later):
                raise
            successful = set()
            check_ids = {}
            check_outcomes = {}
            for _, item in later:
                successful.update(item['successful_checks'])
                check_ids.update(item['check_ids'])
                check_outcomes.update(item['check_outcomes'])
            primary_workflow, primary = next(
                (workflow, item) for workflow, item in later
                if 'FAILED' in item['check_outcomes'].values())
            return {'workflow_id': primary['workflow_id'], 'run_id': primary['run_id'],
                    'run_attempt': primary['run_attempt'], 'run_number': primary['run_number'],
                    'successful_checks': sorted(successful), 'check_ids': check_ids,
                    'check_outcomes': check_outcomes, 'linux_required': linux_required,
                    'docs_only': docs_only,
                    'required_checks': sorted(item[field] for item in selected),
                    'workflow_path': primary_workflow['path'],
                    'workflow_name': primary_workflow['name']}
        results.append(result)
    successful, check_ids, check_outcomes = set(), {}, {}
    for result in results:
        successful.update(result['successful_checks'])
        check_ids.update(result['check_ids'])
        check_outcomes.update(result['check_outcomes'])
    primary, primary_workflow = results[0], selected[0]
    return {'workflow_id': primary['workflow_id'], 'run_id': primary['run_id'],
            'run_attempt': primary['run_attempt'], 'run_number': primary['run_number'],
            'successful_checks': sorted(successful), 'check_ids': check_ids,
            'check_outcomes': check_outcomes,
            'linux_required': linux_required, 'docs_only': docs_only,
            'required_checks': sorted(workflow[field] for workflow in selected),
            'workflow_path': primary_workflow['path'],
            'workflow_name': primary_workflow['name']}


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
        if standard and marker.group(1) == 'Codex' and marker.group(2) == 'PASS':
            accepted_codex = False
            if reviewer.trusted_agent_id == 'codex-reviewer':
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
                    accepted_codex = (
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
                    accepted_codex = False
            if not accepted_codex:
                # Unbound or otherwise unverified Codex PASS stays advisory.
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
            (marker.group(1) == 'Codex' and marker.group(2) == 'PASS' and
             accepted_codex)))
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
                              pr_head_ref=pr['head'].get('ref'))
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


# #2281: the pieces above are re-exported here so every existing importer and
# every patch on this module path keeps working unchanged.
from .task_completion_part1 import *  # noqa: E402,F401,F403
from .task_completion_part2 import *  # noqa: E402,F401,F403
from .task_completion_part3 import *  # noqa: E402,F401,F403
from .task_completion_part4 import *  # noqa: E402,F401,F403

