"""Fail-closed evidence collection for an already completed D6 Executor task.

This module never applies a patch or writes Git state. A D6 registry record is a
cooperative local provenance source; it is not a same-UID security boundary.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

from .errors import LLMToolError

_TASK_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_REGISTRY_LIMIT = 64 * 1024
_GIT_LIMIT = 2 * 1024 * 1024
_TIMEOUT = 10
_TEST_TIMEOUT = 180
_TEST_OUTPUT_LIMIT = 256 * 1024
_REQUIRED_CHECKS = frozenset(('check (3.9)', 'check (3.14)'))
_TEST_PATH = re.compile(r'tools/llmtool/tests/test_[A-Za-z0-9_]+\.py\Z')


def _stop(code):
    error = LLMToolError('Executor completion evidence is unavailable; details omitted')
    error.completion_code = code
    raise error from None


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _stop('D6_REGISTRY_INVALID')
        result[key] = value
    return result


@dataclass(frozen=True)
class D6Registration:
    task_id: str
    trusted_agent_id: str
    session_ref: str
    root: str
    common_dir: str
    branch: str
    source_sha: str
    result_sha: str
    state: str
    lock_state: str
    device: int
    inode: int
    uid: int
    native_session_id: str | None
    github_login: str | None


def read_d6_registration(task_id, common_dir):
    """Read one bounded owner-only D6 record without following links."""
    if (type(task_id) is not str or _TASK_ID.fullmatch(task_id) is None or
            type(common_dir) is not str or not os.path.isabs(common_dir) or
            os.path.realpath(common_dir) != common_dir or not common_dir.endswith('/.git')):
        _stop('D6_REGISTRY_ID_INVALID')
    path = Path(common_dir) / 'llmtool-worktrees' / (task_id + '.json')
    try:
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or
                stat.S_IMODE(parent.st_mode) != 0o700):
            _stop('D6_REGISTRY_UNAVAILABLE')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                before.st_size > _REGISTRY_LIMIT):
            os.close(fd)
            _stop('D6_REGISTRY_INVALID')
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(_REGISTRY_LIMIT + 1)
            again = os.fstat(stream.fileno())
        after = path.lstat()
        if (len(raw) > _REGISTRY_LIMIT or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (again.st_dev, again.st_ino, again.st_size, again.st_mtime_ns) or
                (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            _stop('D6_REGISTRY_CHANGED')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _: _stop('D6_REGISTRY_INVALID'))
    except LLMToolError:
        raise
    except Exception:
        _stop('D6_REGISTRY_UNAVAILABLE')
    if (type(value) is not dict or value.get('schema_version') != 1 or
            value.get('task_id') != task_id or value.get('state') != 'ACTIVE' or
            value.get('lock_state') != 'LOCKED' or
            value.get('git_common_dir') != common_dir or
            type(value.get('trusted_agent_id')) is not str or not value['trusted_agent_id'] or
            type(value.get('session_ref')) is not str or not value['session_ref'] or
            type(value.get('root_realpath')) is not str or
            not os.path.isabs(value['root_realpath']) or
            os.path.realpath(value['root_realpath']) != value['root_realpath'] or
            type(value.get('branch')) is not str or not value['branch'] or
            type(value.get('base_sha')) is not str or _HEX40.fullmatch(value['base_sha']) is None or
            type(value.get('head_sha')) is not str or _HEX40.fullmatch(value['head_sha']) is None):
        _stop('D6_REGISTRY_INVALID')
    identity = value.get('physical_identity')
    if (type(identity) is not dict or
            set(identity) != {'device', 'inode', 'uid'} or
            any(type(identity[k]) is not int or identity[k] < 0 for k in identity)):
        _stop('D6_REGISTRY_INVALID')
    try:
        root_stat = Path(value['root_realpath']).lstat()
    except OSError:
        _stop('D6_ROOT_MISSING')
    if ((root_stat.st_dev, root_stat.st_ino, root_stat.st_uid) !=
            (identity['device'], identity['inode'], identity['uid']) or
            identity['uid'] != os.getuid() or
            stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode)):
        _stop('D6_ROOT_IDENTITY_MISMATCH')
    native_session = value.get('native_session_id')
    if native_session is not None and (type(native_session) is not str or
            not native_session or len(native_session) > 256 or
            any(ord(c) < 0x20 or ord(c) == 0x7f for c in native_session)):
        _stop('D6_REGISTRY_INVALID')
    github_login = value.get('github_login')
    if github_login is not None and (type(github_login) is not str or
            not re.fullmatch(r'[A-Za-z0-9-]{1,39}', github_login)):
        _stop('D6_REGISTRY_INVALID')
    return D6Registration(task_id, value['trusted_agent_id'], value['session_ref'],
                          value['root_realpath'], common_dir, value['branch'],
                          value['base_sha'], value['head_sha'], value['state'],
                          value['lock_state'], identity['device'], identity['inode'],
                          identity['uid'], native_session, github_login)


def _git(root, args, *, limit=_GIT_LIMIT):
    """Run one fixed Git read with bounded output and no inherited Git config."""
    env = {
        'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_ATTR_NOSYSTEM': '1',
        'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_LAZY_FETCH': '1',
        'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': 'cat', 'PAGER': 'cat',
        'GIT_LITERAL_PATHSPECS': '1',
    }
    safe = ('-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
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
        if time.monotonic() >= deadline or process.wait(timeout=max(0, deadline-time.monotonic())) != 0:
            _stop('GIT_INSPECTION_FAILED')
        if errors:
            _stop('GIT_INSPECTION_FAILED')
        return bytes(out)
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
    branch = _git(root, ('branch', '--show-current'), limit=4096).decode().strip()
    head = _git(root, ('rev-parse', '--verify', 'HEAD'), limit=4096).decode().strip()
    status = _git(root, ('status', '--porcelain=v1', '-z', '--untracked-files=all'), limit=1024*1024)
    if (actual_root != root or not branch or _HEX40.fullmatch(head) is None or
            os.path.realpath(common) != common):
        _stop('GIT_BINDING_INVALID')
    return GitSnapshot(root, common, branch, head, status == b'')


def bind_executor_task(task_id, *, expected_common_dir, root=None):
    """Bind a company proposal to the current exact D6 task root and source HEAD."""
    root = os.getcwd() if root is None else root
    registration = read_d6_registration(task_id, expected_common_dir)
    snapshot = collect_git_snapshot(root)
    if (snapshot.root != registration.root or snapshot.common_dir != registration.common_dir or
            snapshot.branch != registration.branch or snapshot.head != registration.result_sha or
            registration.source_sha != snapshot.head or
            registration.result_sha != registration.source_sha or
            not snapshot.clean):
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
                           expected_native_session_id, write_paths, artifact, artifact_sha256):
    """Compare the current registered Executor HEAD and actual source-to-result diff."""
    registration = read_d6_registration(task_id, expected_common_dir)
    if (registration.root != expected_root or registration.branch != expected_branch or
            registration.source_sha != source_sha or
            registration.trusted_agent_id != expected_agent or
            registration.session_ref != expected_session_ref or
            registration.native_session_id != expected_native_session_id):
        _stop('D6_TASK_BINDING_MISMATCH')
    if (type(task_binding) is not dict or not task_binding or
            any(type(task_binding.get(key)) is not str for key in
                ('task_id', 'run_id', 'contract_digest'))):
        _stop('TASK_BINDING_MISSING')
    snapshot = collect_git_snapshot(registration.root)
    if (snapshot.common_dir != registration.common_dir or snapshot.branch != registration.branch or
            snapshot.head != registration.result_sha or not snapshot.clean):
        _stop('RESULT_HEAD_MISMATCH')
    if source_sha == snapshot.head:
        _stop('RESULT_DIFF_EMPTY')
    ancestor = subprocess.run(['/usr/bin/git', '-C', registration.root, 'merge-base',
        '--is-ancestor', source_sha, snapshot.head], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=_TIMEOUT,
        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
             'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
             'GIT_NO_LAZY_FETCH': '1'}, shell=False, check=False)
    if ancestor.returncode != 0:
        _stop('SOURCE_NOT_ANCESTOR')
    changed = _git(registration.root, ('diff', '--name-only', '-z', '--no-ext-diff',
                  '--no-textconv', '--no-renames', source_sha, snapshot.head, '--'),
                   limit=1024*1024)
    paths = tuple(sorted(set(path for path in changed.decode('utf-8', 'strict').split('\x00') if path)))
    if not paths or len(paths) > 128:
        _stop('RESULT_DIFF_INVALID')
    if not set(paths).issubset(set(write_paths)):
        _stop('RESULT_SCOPE_MISMATCH')
    patch = _git(registration.root, ('diff', '--binary', '--no-ext-diff', '--no-textconv',
                '--no-renames', source_sha, snapshot.head, '--'), limit=_GIT_LIMIT)
    verify_artifact_applied(registration.root, artifact, artifact_sha256,
                            paths, write_paths)
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
        'diff_sha256': hashlib.sha256(patch).hexdigest(),
    }


def verify_artifact_applied(root, artifact, artifact_sha256, changed_paths, write_paths):
    """Prove the stored proposal applies in reverse to the actual result tree."""
    if (type(artifact) is not str or not artifact or
            type(artifact_sha256) is not str or
            hashlib.sha256(artifact.encode('utf-8')).hexdigest() != artifact_sha256):
        _stop('DEVELOPER_ARTIFACT_BINDING_INVALID')
    patch_paths = []
    for line in artifact.splitlines():
        if line.startswith('diff --git '):
            match = re.fullmatch(r'diff --git a/([^\s]+) b/([^\s]+)', line)
            if match is None or match.group(1) != match.group(2):
                _stop('DEVELOPER_ARTIFACT_INVALID')
            patch_paths.append(match.group(1))
    if (not patch_paths or len(patch_paths) != len(set(patch_paths)) or
            set(patch_paths) != set(changed_paths) or
            not set(patch_paths).issubset(set(write_paths))):
        _stop('DEVELOPER_ARTIFACT_RESULT_MISMATCH')
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}
    try:
        proc = subprocess.run(['/usr/bin/git', '-C', root, '-c', 'core.hooksPath=/dev/null',
            'apply', '--check', '--reverse', '--whitespace=nowarn', '-'],
            input=artifact.encode('utf-8'), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=_TIMEOUT, env=env,
            shell=False, check=False)
    except Exception:
        _stop('DEVELOPER_ARTIFACT_CHECK_UNKNOWN')
    if proc.returncode != 0:
        _stop('DEVELOPER_ARTIFACT_NOT_APPLIED')


def run_bounded_tests(*, root, result_sha, read_paths, write_paths):
    """Run only fixed unittest discovery for test files in the stored task scope."""
    eligible = sorted({p for p in (*read_paths, *write_paths)
                       if type(p) is str and _TEST_PATH.fullmatch(p)})
    if not eligible:
        _stop('TEST_SCOPE_MISSING')
    before = collect_git_snapshot(root)
    if before.head != result_sha or not before.clean:
        _stop('TEST_SOURCE_MISMATCH')
    evidence = []
    with tempfile.TemporaryDirectory(prefix='llmtool-task-tests-') as home:
        for path in eligible:
            env = {'PATH': os.path.dirname(sys.executable) + ':/usr/bin:/bin',
                   'LC_ALL': 'C', 'HOME': home,
                   'PYTHONPATH': str(Path(root) / 'tools' / 'llmtool'),
                   'PYTHONDONTWRITEBYTECODE': '1'}
            argv = [sys.executable, '-B', '-m', 'unittest', 'discover',
                    '-s', 'tools/llmtool/tests', '-p', Path(path).name]
            try:
                process = subprocess.Popen(argv, cwd=root, env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, shell=False, close_fds=True,
                    start_new_session=True)
            except OSError:
                _stop('TEST_RUNNER_UNAVAILABLE')
            output = bytearray()
            deadline = time.monotonic() + _TEST_TIMEOUT
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
            evidence.append({
                'path': path, 'source_sha': result_sha, 'exit_code': code,
                'output_sha256': hashlib.sha256(output).hexdigest(),
                'duration_ms': max(0, int((time.monotonic()-started)*1000)),
                'command_sha256': hashlib.sha256(json.dumps(argv, separators=(',', ':')).encode()).hexdigest(),
            })
    after = collect_git_snapshot(root)
    if after != before:
        _stop('TEST_SOURCE_CHANGED')
    return evidence


def _github_get(path):
    url = 'https://api.github.com/repos/davidcatcat/davidcattoolsproject/' + path
    request = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'llmtool-task-completion/1',
        'X-GitHub-Api-Version': '2022-11-28',
    }, method='GET')
    # Do not forward ambient credentials or proxy credentials from this process.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=8) as response:
            if response.status != 200:
                _stop('GITHUB_EVIDENCE_UNAVAILABLE')
            raw = response.read(1024 * 1024 + 1)
    except LLMToolError:
        raise
    except Exception:
        _stop('GITHUB_EVIDENCE_UNAVAILABLE')
    if len(raw) > 1024 * 1024:
        _stop('GITHUB_EVIDENCE_TOO_LARGE')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _: _stop('GITHUB_EVIDENCE_INVALID'))
    except Exception:
        _stop('GITHUB_EVIDENCE_INVALID')


def collect_github_evidence(*, pr_number, result_sha, executor_identity,
                            executor_session_ref, executor_native_session_id,
                            reviewer_task_id, common_dir):
    """Read a public PR, formal review marker and exact-head required check runs."""
    if type(pr_number) is not int or not 1 <= pr_number <= 9999999:
        _stop('PR_NUMBER_INVALID')
    pr = _github_get(f'pulls/{pr_number}')
    if (type(pr) is not dict or pr.get('state') != 'open' or pr.get('merged') is not False or
            type(pr.get('head')) is not dict or pr['head'].get('sha') != result_sha or
            type(pr.get('base')) is not dict or
            type(pr['base'].get('repo')) is not dict or
            pr['base']['repo'].get('full_name') != 'davidcatcat/davidcattoolsproject' or
            type((pr.get('user') or {}).get('login')) is not str):
        _stop('PR_HEAD_MISMATCH')
    reviews = _github_get(f'pulls/{pr_number}/reviews?per_page=100')
    if type(reviews) is not list or len(reviews) >= 100:
        _stop('REVIEW_EVIDENCE_INVALID')
    formal = []
    marker = re.compile(r'(?im)^\s*FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\s*$')
    for review in reviews:
        if (type(review) is dict and
                review.get('commit_id') == result_sha and
                type((review.get('user') or {}).get('login')) is str and
                marker.search(review.get('body') if type(review.get('body')) is str else '')):
            formal.append(review)
    review = formal[-1] if formal else None
    reviewer_login = None if review is None else review['user']['login']
    author_login = pr['user']['login']
    try:
        reviewer = read_d6_registration(reviewer_task_id, common_dir)
    except LLMToolError:
        _stop('REVIEWER_D6_REGISTRY_UNAVAILABLE')
    missing = []
    if review is None:
        missing.append('FORMAL_REVIEW_MISSING')
    elif review.get('state') != 'APPROVED':
        missing.append('FORMAL_REVIEW_NOT_ACCEPTED')
    if (reviewer.trusted_agent_id == executor_identity or
            reviewer.session_ref == 'UNKNOWN' or reviewer.session_ref == '' or
            reviewer.session_ref == executor_session_ref):
        missing.append('REVIEWER_D6_SESSION_NOT_INDEPENDENT')
    # Public GitHub review proves actor/body/head, but cannot bind that actor to
    # a native Reviewer session unless the Integrator D6 record pins the login.
    if (reviewer.native_session_id not in (None, 'UNKNOWN') and
            executor_native_session_id is not None and
          reviewer.native_session_id == executor_native_session_id):
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
        'REVIEWER_ACTOR_D6_BINDING_MISSING',
        'REVIEWER_ACTOR_D6_BINDING_MISMATCH',
        'REVIEWER_D6_HEAD_MISMATCH', 'REVIEWER_D6_HEAD_UNAVAILABLE',
    }
    reviewer_bound = (review is not None and review.get('state') == 'APPROVED' and
                      not reviewer_gates.intersection(missing))
    checks = _github_get(f'commits/{result_sha}/check-runs?per_page=100')
    if (type(checks) is not dict or checks.get('total_count') != len(checks.get('check_runs', [])) or
            type(checks.get('check_runs')) is not list or len(checks['check_runs']) > 100):
        _stop('CI_EVIDENCE_INVALID')
    successful = {check.get('name') for check in checks['check_runs']
                  if type(check) is dict and check.get('head_sha') == result_sha and
                  check.get('status') == 'completed' and check.get('conclusion') == 'success'}
    if not _REQUIRED_CHECKS.issubset(successful):
        missing.append('REQUIRED_CI_MISSING')
    return {'evidence': {'pr_number': pr_number, 'head_sha': result_sha,
            'formal_review_state': 'ACCEPTED' if reviewer_bound else 'UNVERIFIED',
            'review_actor': reviewer_login,
            'review_commit_sha': None if review is None else review['commit_id'],
            'reviewer_d6_identity': reviewer.trusted_agent_id,
            'reviewer_session_ref': reviewer.session_ref,
            'review_session_identity': ('D6_NATIVE_SESSION_VERIFIED'
                if reviewer.native_session_id not in (None, 'UNKNOWN') else 'UNKNOWN'),
            'required_checks': sorted(_REQUIRED_CHECKS),
            'successful_checks': sorted(successful), 'ci_head_sha': result_sha},
            'missing_gates': sorted(set(missing))}
