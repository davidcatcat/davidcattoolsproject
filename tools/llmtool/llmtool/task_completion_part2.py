from __future__ import annotations

import sys as _sys
import types as _types

_task_completion = _sys.modules[__package__ + '.task_completion']


def validate_env2_pass_marker(registration, *, expected_root, current_head, write_paths=None):
    """Require the owner-only registry marker and report for this exact D6 HEAD."""
    marker = registration.env2_pass_marker
    if marker is None:
        _stop('D6_ENV2_PASS_MARKER_MISSING')
    fingerprint_re = re.compile(
        r'[a-z][a-z0-9]*\d+\.\d+-[a-z0-9][a-z0-9._-]{0,31}-'
        r'[a-z0-9][a-z0-9._-]{0,31}-[0-9a-f]{12}\Z')
    if type(marker) is not dict:
        _stop('D6_ENV2_PASS_MARKER_INVALID')
    marker_keys = set(marker)
    if marker_keys == ENV2_PASS_MARKER_SCHEMA1_KEYS:
        expected_version = 1
    elif marker_keys == ENV2_PASS_MARKER_SCHEMA2_KEYS:
        expected_version = 2
    else:
        _stop('D6_ENV2_PASS_MARKER_INVALID')
    if (type(marker['schema_version']) is not int or
            marker['schema_version'] != expected_version or
            marker['result'] != 'PASS' or marker['task_id'] != registration.task_id or
            marker['root_realpath'] != expected_root or expected_root != registration.root or
            type(current_head) is not str or _HEX40.fullmatch(current_head) is None or
            marker['submitted_head_sha'] != current_head or
            marker['current_head_sha'] != current_head or
            current_head != registration.result_sha or
            marker['tooling_sha'] != (registration.env2_approved_tooling_sha or
                                      registration.source_sha) or
            type(marker['tooling_sha']) is not str or
            _HEX40.fullmatch(marker['tooling_sha']) is None or
            type(marker['environment_fingerprint']) is not str or
            fingerprint_re.fullmatch(marker['environment_fingerprint']) is None or
            type(marker['report_sha256']) is not str or
            _HEX64.fullmatch(marker['report_sha256']) is None):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    report_path = Path(expected_root) / 'tools' / 'llmtool' / '.d6-bootstrap-report.json'
    for directory in (Path(expected_root) / 'tools', Path(expected_root) / 'tools' / 'llmtool'):
        try:
            info = directory.lstat()
        except OSError:
            _stop('D6_ENV2_PASS_MARKER_MISMATCH')
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    try:
        before = report_path.lstat()
        if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or
                before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600 or
                before.st_nlink != 1 or before.st_size > 2 * 1024 * 1024):
            _stop('D6_ENV2_PASS_MARKER_MISMATCH')
        fd = os.open(report_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            raw = bytearray()
            while len(raw) <= 2 * 1024 * 1024:
                chunk = os.read(fd, min(65536, 2 * 1024 * 1024 + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(fd)
            current = report_path.lstat()
        finally:
            os.close(fd)
    except LLMToolError:
        raise
    except Exception:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if (len(raw) > 2 * 1024 * 1024 or identity(before) != identity(opened) or
            identity(opened) != identity(after) or
            (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino) or
            hashlib.sha256(raw).hexdigest() != marker['report_sha256']):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    try:
        report = json.loads(bytes(raw).decode('utf-8'), object_pairs_hook=_unique_pairs,
                            parse_constant=lambda _: _stop('D6_ENV2_PASS_MARKER_INVALID'))
    except LLMToolError:
        raise
    except Exception:
        _stop('D6_ENV2_PASS_MARKER_INVALID')
    if (type(report) is not dict or report.get('result') != 'PASS' or
            report.get('task_id') != registration.task_id or
            report.get('root_realpath') != registration.root or
            report.get('head_sha') != current_head or
            report.get('tooling_sha') != marker['tooling_sha'] or
            report.get('cache_fingerprint') != marker['environment_fingerprint']):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    if expected_version == 1:
        return marker

    if (marker.get('environment_mode') != 'CARRIED' or
            type(write_paths) is not list or
            any(type(p) is not str for p in write_paths)):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    if type(report.get('schema_version')) is not int or report.get('schema_version') != 2:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    if (registration.env2_approved_tooling_sha is not None or
            marker['tooling_sha'] != registration.source_sha):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    carried_from = marker.get('carried_from_head_sha')
    if (type(carried_from) is not str or _HEX40.fullmatch(carried_from) is None or
            carried_from == current_head):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    try:
        is_source_ancestor = (registration.source_sha == carried_from or
                              _is_ancestor(expected_root, registration.source_sha, carried_from))
        is_carried_ancestor = _is_ancestor(expected_root, carried_from, current_head)
    except LLMToolError:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    if not is_source_ancestor or not is_carried_ancestor:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    allowed = marker.get('allowed_bootstrap_paths')
    if (type(allowed) is not list or not allowed or
            any(type(p) is not str for p in allowed) or
            allowed != sorted(set(allowed)) or
            not set(allowed).issubset(BOOTSTRAP_SOURCES) or
            not set(allowed).issubset(set(write_paths))):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    try:
        diff_raw = _git(expected_root, ('diff', '--name-only', '-z', '--no-ext-diff',
                                        '--no-textconv', '--no-renames', marker['tooling_sha'],
                                        current_head, '--', *sorted(BOOTSTRAP_SOURCES)),
                        limit=1024 * 1024)
        diff_paths = sorted(set(p for p in diff_raw.decode('utf-8', 'strict').split('\x00') if p))
    except (LLMToolError, UnicodeError):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    if allowed != diff_paths:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    try:
        for req_path in sorted(REQUIRED_BOOTSTRAP_SOURCES):
            if _tree_entry(expected_root, current_head, req_path) is None:
                _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    except LLMToolError:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    if (report.get('status') != 'env_carried' or
            report.get('operations') != 'NONE' or
            report.get('carried_from_head_sha') != carried_from or
            report.get('allowed_bootstrap_paths') != allowed):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    venv_identity = report.get('venv_identity')
    expected_venv_keys = {
        'schema_version', 'path', 'device', 'inode', 'uid',
        'pyvenv_cfg_sha256', 'interpreter_realpath', 'interpreter_stat',
        'site_packages_sha256',
    }
    if type(venv_identity) is not dict or set(venv_identity) != expected_venv_keys:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    if (type(venv_identity['schema_version']) is not int or
            venv_identity['schema_version'] != 1 or
            venv_identity['path'] != 'tools/llmtool/.venv' or
            type(venv_identity['device']) is not int or isinstance(venv_identity['device'], bool) or
            type(venv_identity['inode']) is not int or isinstance(venv_identity['inode'], bool) or
            type(venv_identity['uid']) is not int or isinstance(venv_identity['uid'], bool) or
            type(venv_identity['pyvenv_cfg_sha256']) is not str or
            _HEX64.fullmatch(venv_identity['pyvenv_cfg_sha256']) is None or
            type(venv_identity['interpreter_realpath']) is not str or
            not venv_identity['interpreter_realpath'] or
            type(venv_identity['site_packages_sha256']) is not str or
            _HEX64.fullmatch(venv_identity['site_packages_sha256']) is None):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    stat_obj = venv_identity['interpreter_stat']
    expected_stat_keys = {'device', 'inode', 'size', 'mtime_ns'}
    if (type(stat_obj) is not dict or set(stat_obj) != expected_stat_keys or
            any(type(stat_obj[k]) is not int or isinstance(stat_obj[k], bool)
                for k in expected_stat_keys)):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    return marker


def _worktree_diff(root, source_sha):
    """Return a bounded source diff including safe untracked files."""
    tracked_patch = _git(root, ('diff', '--binary', '--no-ext-diff', '--no-textconv',
        '--no-renames', source_sha, '--'), limit=_GIT_LIMIT)
    tracked_raw = _git(root, ('diff', '--name-only', '-z', '--no-ext-diff',
        '--no-textconv', '--no-renames', source_sha, '--'), limit=1024 * 1024)
    untracked_raw = _git(root, ('ls-files', '--others', '--exclude-standard', '-z'),
                         limit=64 * 1024)
    try:
        tracked = set(path for path in tracked_raw.decode('utf-8', 'strict').split('\x00') if path)
        untracked = [path for path in untracked_raw.decode('utf-8', 'strict').split('\x00') if path]
    except UnicodeError:
        _stop('WORKTREE_PATH_INVALID')
    if len(untracked) > _PROPOSAL_FILE_LIMIT or len(set(untracked)) != len(untracked):
        _stop('WORKTREE_UNTRACKED_FILES')
    extra = []
    for path in untracked:
        parts = Path(path).parts
        if (not path or Path(path).is_absolute() or '\\' in path or
                any(part in ('', '.', '..') for part in parts) or
                any(ord(char) < 0x20 or ord(char) >= 0x7f for char in path)):
            _stop('WORKTREE_PATH_INVALID')
        target = Path(root).joinpath(*parts)
        try:
            before = target.lstat()
        except OSError:
            _stop('WORKTREE_FILE_UNAVAILABLE')
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size > _GIT_LIMIT):
            _stop('WORKTREE_FILE_UNSUPPORTED')
        raw = _git(root, ('diff', '--no-index', '--binary', '--no-ext-diff',
            '--no-textconv', '--no-renames', '/dev/null', path),
            limit=_GIT_LIMIT, allow_exit_1=True)
        try:
            after = target.lstat()
        except OSError:
            _stop('WORKTREE_FILE_CHANGED')
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            _stop('WORKTREE_FILE_CHANGED')
        # --no-index names /dev/null in its header. Emit Git's standard
        # new-file patch shape so the direct Developer parser can validate it.
        try:
            text = raw.decode('utf-8', 'strict')
        except UnicodeError:
            _stop('WORKTREE_PATH_INVALID')
        standard_header = f'diff --git a/{path} b/{path}\n'
        null_header = f'diff --git a/dev/null b/{path}\n'
        if text.startswith(null_header):
            text = standard_header + text[len(null_header):]
        elif not text.startswith(standard_header):
            _stop('WORKTREE_FILE_UNSUPPORTED')
        extra.append(text.encode('utf-8'))
    paths = tuple(sorted(tracked | set(untracked)))
    if len(paths) > _PROPOSAL_FILE_LIMIT:
        _stop('WORKTREE_SCOPE_MISMATCH')
    patch = tracked_patch + b''.join(extra)
    if len(patch) > _GIT_LIMIT:
        _stop('GIT_EVIDENCE_TOO_LARGE')
    return paths, patch


def review_repair_intent(handoff, review_return):
    """Build a deterministic, source-bound request for the one allowed repair.

    This pure value is an idempotency envelope, not a Worker dispatch. A
    caller must separately enforce at-most-once consumption and recheck the
    original Task/Worker authority before any future execution.
    """
    if type(handoff) is not dict or set(handoff) != {
            'intent', 'dispatch_state', 'receipt', 'next_step', 'error_code'}:
        _stop('REVIEW_REPAIR_BINDING_INVALID')
    expected_handoff = review_handoff_record(handoff['intent'], handoff['receipt'])
    if (_canonical_json_bytes(expected_handoff, 'REVIEW_REPAIR_BINDING_INVALID') !=
            _canonical_json_bytes(handoff, 'REVIEW_REPAIR_BINDING_INVALID')):
        _stop('REVIEW_REPAIR_BINDING_INVALID')
    validate_review_return_record(review_return, handoff)
    intent = handoff['intent']
    receipt = handoff['receipt']
    recommendation = review_return['pm_recommendation']
    if (review_return['state'] != 'RETURNED' or
            review_return['pm_dispatch'] != 'RETURNED' or
            review_return['next_step'] != 'RETURN_TO_DEVELOPER_ONCE' or
            type(recommendation) is not dict or
            set(recommendation) != {'decision', 'summary'} or
            recommendation.get('decision') != 'RETURN_TO_DEVELOPER' or
            type(recommendation.get('summary')) is not str or
            not recommendation['summary'] or
            intent['repair_round'] != 0 or receipt['verdict'] != 'FAIL'):
        _stop('REVIEW_REPAIR_BINDING_INVALID')
    try:
        summary_bytes = recommendation['summary'].encode('utf-8')
    except UnicodeEncodeError:
        _stop('REVIEW_REPAIR_BINDING_INVALID')
    if len(summary_bytes) > 2048:
        _stop('REVIEW_REPAIR_BINDING_INVALID')
    payload = {
        'schema_version': 1,
        'task_id': intent['task_id'],
        'run_id': intent['run_id'],
        'contract_digest': intent['contract_digest'],
        'source_sha': intent['source_sha'],
        'result_head': intent['result_head'],
        'diff_sha256': intent['diff_sha256'],
        'changed_paths': list(intent['changed_paths']),
        'developer_task_id': intent['developer_task_id'],
        'developer_identity': intent['developer_identity'],
        'developer_session_ref': intent['developer_session_ref'],
        'review_dispatch_id': intent['dispatch_id'],
        'review_receipt_sha256': receipt['receipt_sha256'],
        'prior_repair_round': 0,
        'repair_round': 1,
        'decision': 'RETURN_TO_DEVELOPER',
        'state': 'REQUESTED',
    }
    canonical = _canonical_json_bytes(payload, 'REVIEW_REPAIR_BINDING_INVALID')
    payload['repair_intent_id'] = hashlib.sha256(
        b'llmtool/review-repair-intent/v1\0' + canonical).hexdigest()
    payload['intent_sha256'] = hashlib.sha256(_canonical_json_bytes(
        payload, 'REVIEW_REPAIR_BINDING_INVALID')).hexdigest()
    return payload


def validate_review_repair_intent(repair_intent, handoff, review_return):
    """Validate an envelope against its exact persisted Reviewer/PM inputs."""
    fields = {
        'schema_version', 'task_id', 'run_id', 'contract_digest', 'source_sha',
        'result_head', 'diff_sha256', 'changed_paths', 'developer_task_id',
        'developer_identity', 'developer_session_ref', 'review_dispatch_id',
        'review_receipt_sha256', 'prior_repair_round', 'repair_round',
        'decision', 'state', 'repair_intent_id', 'intent_sha256',
    }
    if type(repair_intent) is not dict or set(repair_intent) != fields:
        _stop('REVIEW_REPAIR_BINDING_INVALID')
    value = dict(repair_intent)
    claimed_digest = value.pop('intent_sha256')
    if type(claimed_digest) is not str or _HEX64.fullmatch(claimed_digest) is None:
        _stop('REVIEW_REPAIR_DIGEST_MISMATCH')
    actual_digest = hashlib.sha256(_canonical_json_bytes(
        value, 'REVIEW_REPAIR_BINDING_INVALID')).hexdigest()
    if claimed_digest != actual_digest:
        _stop('REVIEW_REPAIR_DIGEST_MISMATCH')
    expected = review_repair_intent(handoff, review_return)
    if (_canonical_json_bytes(expected, 'REVIEW_REPAIR_BINDING_INVALID') !=
            _canonical_json_bytes(repair_intent, 'REVIEW_REPAIR_BINDING_INVALID')):
        _stop('REVIEW_REPAIR_BINDING_MISMATCH')
    return repair_intent


def _diff_paths(root, start_sha, end_sha):
    """Return the sorted paths that differ between two commits."""
    changed = _git(root, ('diff', '--name-only', '-z', '--no-ext-diff', '--no-textconv',
                          '--no-renames', start_sha, end_sha, '--'), limit=1024 * 1024)
    return tuple(sorted(set(path for path in changed.decode('utf-8', 'strict').split('\x00')
                            if path)))


def _private_merge_read(root, args, **git_options):
    """Run one merge read in a private temporary bare repository.

    A merge in the Developer repository would honour its merge.* config,
    custom merge drivers, info/attributes and .gitattributes: a driver could
    run a command here and hide a conflict path. The private repository has
    only core.bare=true config, an unborn HEAD (so no attribute tree), no
    index and no attributes. Repository objects are reachable only as
    read-only alternates, and every object the merge writes stays private.
    Every failure stops as GIT_INSPECTION_FAILED, including the timeout,
    unavailable and oversized-output codes that _git classifies.
    """
    try:
        objects = _git(root, ('rev-parse', '--path-format=absolute', '--git-path', 'objects'),
                       limit=4096).decode('utf-8', 'strict').strip()
        if not objects or not os.path.isabs(objects):
            _stop('GIT_INSPECTION_FAILED')
        with tempfile.TemporaryDirectory(prefix='llmtool-merge-read-') as temporary:
            private = Path(temporary).resolve()
            (private / 'objects').mkdir(mode=0o700)
            (private / 'refs').mkdir(mode=0o700)
            (private / 'HEAD').write_text('ref: refs/heads/llmtool-merge-read\n',
                                          encoding='ascii')
            (private / 'config').write_text(
                '[core]\n\trepositoryformatversion = 0\n\tbare = true\n',
                encoding='ascii')
            return _git(str(private), args, env_overrides={
                'GIT_DIR': str(private),
                'GIT_ALTERNATE_OBJECT_DIRECTORIES': objects,
            }, **git_options)
    except Exception:
        _stop('GIT_INSPECTION_FAILED')


def _remerge_diff_paths_raw(root, commit):
    """Read resolution paths without repository merge config or attributes."""
    return _private_merge_read(root, ('show', '--remerge-diff', '--format=', '--no-renames',
                                      '--name-only', '-z', commit), limit=1024 * 1024)


def _merge_tree_conflict_paths_raw(root, first_parent, second_parent):
    """List conflict paths without repository merge config or attributes."""
    return _private_merge_read(root, ('merge-tree', '--write-tree', '--name-only',
                                      '--no-messages', '-z', first_parent, second_parent),
                               limit=1024 * 1024, allow_exit_1=True)


def review_return_decision_allowed(verdict, repair_round, decision):
    if (type(verdict) is not str or type(decision) is not str or
            type(repair_round) is not int or repair_round not in (0, 1)):
        return False
    if verdict == 'PASS':
        allowed = ('CONTINUE_INTEGRATION_GATES', 'STOP')
    elif verdict == 'FAIL' and repair_round == 0:
        allowed = ('RETURN_TO_DEVELOPER', 'STOP')
    elif verdict == 'FAIL' and repair_round == 1:
        allowed = ('STOP',)
    elif verdict == 'BLOCKED':
        allowed = ('WAIT_FOR_REVIEWER', 'STOP')
    else:
        return False
    return decision in allowed


__all__ = [
    'validate_env2_pass_marker', '_worktree_diff', 'review_repair_intent',
    'validate_review_repair_intent', '_diff_paths', '_private_merge_read',
    '_remerge_diff_paths_raw', '_merge_tree_conflict_paths_raw',
    'review_return_decision_allowed',
]

for _task_completion_name in __all__:
    _task_completion_value = globals()[_task_completion_name]
    if isinstance(_task_completion_value, _types.FunctionType):
        _task_completion_rebound = _types.FunctionType(
            _task_completion_value.__code__, _task_completion.__dict__,
            _task_completion_value.__name__, _task_completion_value.__defaults__,
            _task_completion_value.__closure__)
        _task_completion_rebound.__kwdefaults__ = _task_completion_value.__kwdefaults__
        _task_completion_rebound.__dict__.update(_task_completion_value.__dict__)
        _task_completion_rebound.__module__ = _task_completion_value.__module__
        _task_completion_rebound.__qualname__ = _task_completion_value.__qualname__
        _task_completion_rebound.__doc__ = _task_completion_value.__doc__
        globals()[_task_completion_name] = _task_completion_rebound
del _task_completion_name, _task_completion_value
