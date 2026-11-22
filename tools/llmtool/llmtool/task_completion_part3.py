from __future__ import annotations

import sys as _sys
import types as _types

_task_completion = _sys.modules[__package__ + '.task_completion']


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


def _merge_resolution_paths(root, commit, first_parent, second_parent):
    """Return the paths a two-parent merge resolved by hand or conflicted on."""
    resolution_raw = _remerge_diff_paths_raw(root, commit)
    conflict_raw = _merge_tree_conflict_paths_raw(root, first_parent, second_parent)
    try:
        resolution_paths = set(
            path for path in resolution_raw.decode('utf-8', 'strict').split('\x00')
            if path)
        conflict_fields = conflict_raw.decode('utf-8', 'strict').split('\x00')
        if (len(conflict_fields) < 2 or
                re.fullmatch(r'[0-9a-f]{40}', conflict_fields[0]) is None or
                conflict_fields[-1] != ''):
            _stop('GIT_INSPECTION_FAILED')
        conflict_paths = conflict_fields[1:-1]
        if any(not path for path in conflict_paths):
            _stop('GIT_INSPECTION_FAILED')
    except UnicodeError:
        _stop('GIT_INSPECTION_FAILED')
    return resolution_paths | set(conflict_paths)


def _developer_tip(root, start_sha, head_sha, trusted_base_sha=None):
    """Return head_sha with its trailing clean trusted-main merges stripped.

    A stripped merge has exactly two parents, a trusted second parent and no
    resolution or conflict path, so it adds nothing to its first parent except
    trusted main. The first commit that fails is returned, or else start_sha.
    """
    if trusted_base_sha is None:
        trusted_base_sha = start_sha
    current = head_sha
    while current != start_sha:
        raw = _git(root, ('rev-list', '--parents', '-n', '1', current), limit=4096)
        parents = raw.decode('ascii').split()[1:]
        if (len(parents) != 2 or not _is_ancestor(root, parents[1], trusted_base_sha) or
                _merge_resolution_paths(root, current, parents[0], parents[1])):
            return current
        current = parents[0]
    return start_sha


def read_authenticated_main_sha():
    """Return the commit of main read from GitHub for the trusted repository.

    This is the only anchor for "trusted main": a PR base.sha, origin/main or any
    local ref can be stale or Developer-controlled. Every failure stops as
    MAIN_REF_PIN_INVALID.
    """
    try:
        response = _github_get('git/ref/heads/main')
    except Exception:
        _stop('MAIN_REF_PIN_INVALID')
    if (type(response) is not dict or response.get('ref') != 'refs/heads/main' or
            type(response.get('object')) is not dict or
            response['object'].get('type') != 'commit' or
            type(response['object'].get('sha')) is not str or
            _HEX40.fullmatch(response['object']['sha']) is None):
        _stop('MAIN_REF_PIN_INVALID')
    return response['object']['sha']


def _commit_present(root, sha):
    try:
        _git(root, ('cat-file', '-e', sha + '^{commit}'), limit=4096)
    except LLMToolError:
        return False
    return True


def ensure_main_commit(root, main_sha):
    """Return whether main_sha is local, fetching origin/main at most once."""
    if _commit_present(root, main_sha):
        return True
    source_freshness._fetch_origin_main(root)
    return _commit_present(root, main_sha)


def head_adds_only_main(root, recorded_head, head):
    """Return whether head is recorded_head plus only clean merges of fresh main.

    Main is read afresh for each decision. Anything unreadable is not admitted.
    """
    if any(type(sha) is not str or _HEX40.fullmatch(sha) is None
           for sha in (recorded_head, head)):
        return False
    try:
        main_sha = read_authenticated_main_sha()
        if not ensure_main_commit(root, main_sha):
            return False
        # A graft file could rewrite the parents that the walk below trusts.
        _reject_legacy_grafts(root)
        return _developer_tip(root, recorded_head, head, main_sha) == recorded_head
    except Exception:
        return False


def _collect_registered_result_diff(*, task_id, expected_common_dir, expected_root,
                                     expected_branch, source_sha, task_binding,
                                     expected_agent, expected_session_ref,
                                     expected_native_session_id, write_paths,
                                     artifact_segments=None, trusted_base_sha=None):
    """Share D6, ENV2, HEAD, ancestry, scope and diff-hash checks across routes."""
    registration = read_d6_registration(task_id, expected_common_dir,
                                        allowed_states=_D6_EVIDENCE_STATES)
    if (registration.root != expected_root or registration.branch != expected_branch or
            type(source_sha) is not str or _HEX40.fullmatch(source_sha) is None or
            registration.trusted_agent_id != expected_agent or
            registration.session_ref != expected_session_ref or
            registration.native_session_id != expected_native_session_id):
        _stop('D6_TASK_BINDING_MISMATCH')
    if (type(task_binding) is not dict or not task_binding or
            any(type(task_binding.get(key)) is not str for key in
                ('task_id', 'run_id', 'contract_digest'))):
        _stop('TASK_BINDING_MISSING')
    _reject_legacy_grafts(registration.root, registration.common_dir)
    snapshot = collect_git_snapshot(registration.root)
    if (snapshot.common_dir != registration.common_dir or snapshot.branch != registration.branch or
            snapshot.head != registration.result_sha or not snapshot.clean):
        _stop('RESULT_HEAD_MISMATCH')
    validate_env2_pass_marker(registration, expected_root=expected_root,
                              current_head=snapshot.head, write_paths=write_paths)
    try:
        _git(registration.root, ('merge-base', '--is-ancestor', registration.source_sha, source_sha),
             limit=4096)
    except LLMToolError:
        _stop('D6_TASK_BINDING_MISMATCH')
    if source_sha == snapshot.head:
        _stop('RESULT_DIFF_EMPTY')
    _, ancestor_status = _git(registration.root,
        ('merge-base', '--is-ancestor', source_sha, snapshot.head),
        limit=4096, allow_exit_1=True, return_exit_code=True)
    if ancestor_status != 0:
        _stop('SOURCE_NOT_ANCESTOR')
    # The 128-path limit counts Developer paths only: a merged main can bring in
    # any number of its own files.
    paths = _diff_paths(registration.root, source_sha, snapshot.head)
    if not paths:
        _stop('RESULT_DIFF_INVALID')
    developer_paths = _developer_changed_paths(
        registration.root, source_sha, snapshot.head, trusted_base_sha)
    if not developer_paths or len(developer_paths) > 128:
        _stop('RESULT_DIFF_INVALID')
    if not set(developer_paths).issubset(set(write_paths)):
        _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID' if artifact_segments is not None
              else 'RESULT_SCOPE_MISMATCH')
    patch = _git(registration.root, ('diff', '--binary', '--no-ext-diff', '--no-textconv',
                '--no-renames', source_sha, snapshot.head, '--'), limit=_GIT_LIMIT)
    return registration, snapshot, paths, developer_paths, patch


def _segment_shape_valid(segment):
    """An artifact segment (artifact, digest, start, end) or a recorded one.

    #1886 F1: a Claude record-only repair binds its recorded result HEAD, not
    an artifact (TaskStore.record_claude_result), so its segment is
    (None, None, start, end, recorded_head). Only that exact shape carries no
    artifact; an absent artifact alone never makes a segment.
    """
    return type(segment) is tuple and (len(segment) == 4 or (
        len(segment) == 5 and segment[0] is None and segment[1] is None and
        type(segment[4]) is str and _HEX40.fullmatch(segment[4]) is not None))


def _verify_recorded_segment(root, start_sha, end_sha, recorded_head, trusted_base_sha):
    """#1886 F1: a record-only repair segment ends at its recorded result HEAD.

    The recorded HEAD must be a new commit descending from the segment start,
    and the segment end must be that HEAD or that HEAD plus only clean
    trusted-main merges, as for the Claude root segment. A Developer commit
    after the recorded HEAD is unrecorded and refuses.
    """
    try:
        if (type(recorded_head) is not str or _HEX40.fullmatch(recorded_head) is None or
                recorded_head == start_sha or
                not _is_ancestor(root, start_sha, recorded_head) or
                _developer_tip(root, recorded_head, end_sha,
                               trusted_base_sha) != recorded_head):
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
    except LLMToolError:
        _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')


def _verify_artifact_segments(root, segments, start, write_paths, trusted_base_sha):
    """Verify consecutive (artifact, digest, start, end) segments from start.

    Each segment must start where the previous one ended, descend from its
    start, change only write paths, and its artifact must be exactly what its
    Developer commits apply. A record-only repair segment (see
    _segment_shape_valid) must instead end at its recorded result HEAD. Any
    failure is DEVELOPER_ARTIFACT_CHAIN_INVALID.
    """
    previous_end = start
    for segment in segments:
        if not _segment_shape_valid(segment):
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
        segment_artifact, segment_digest, start_sha, end_sha = segment[:4]
        if (start_sha != previous_end or
                type(start_sha) is not str or _HEX40.fullmatch(start_sha) is None or
                type(end_sha) is not str or _HEX40.fullmatch(end_sha) is None):
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
        try:
            _git(root, ('merge-base', '--is-ancestor', start_sha, end_sha), limit=4096)
        except LLMToolError:
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
        segment_paths = _diff_paths(root, start_sha, end_sha)
        segment_developer_paths = _developer_changed_paths(
            root, start_sha, end_sha, trusted_base_sha)
        if (not segment_paths or not segment_developer_paths or
                not set(segment_developer_paths).issubset(set(write_paths))):
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
        if len(segment) == 5:
            _verify_recorded_segment(root, start_sha, end_sha, segment[4],
                                     trusted_base_sha)
            previous_end = end_sha
            continue
        segment_tip = _developer_tip(root, start_sha, end_sha, trusted_base_sha)
        try:
            verify_artifact_applied(root, segment_artifact,
                segment_digest, start_sha, segment_tip,
                _diff_paths(root, start_sha, segment_tip), write_paths,
                scope_paths=segment_developer_paths)
        except LLMToolError:
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
        previous_end = end_sha


def validate_review_dispatch_receipt(intent, receipt):
    """Validate a typed-in-process Reviewer adapter result against the intent.

    The injected adapter is a trusted host boundary. This check binds its DATA
    to the exact Task/Run/contract/source/result and rejects self-review. It is
    not a signature, Session authentication, or provider-execution proof.
    """
    fields = {
        'schema_version', 'dispatch_id', 'task_id', 'run_id', 'contract_digest',
        'source_sha', 'result_head', 'diff_sha256', 'developer_task_id',
        'reviewer_task_id', 'reviewer_identity', 'reviewer_session_ref', 'verdict',
        'security', 'functional', 'execution_status', 'code', 'findings_sha256',
        'provenance', 'receipt_sha256',
    }
    if type(intent) is not dict or type(receipt) is not dict:
        _stop('REVIEW_RECEIPT_INVALID')
    schema_version = receipt.get('schema_version')
    legacy = schema_version == 1 and set(receipt) == fields
    current = (schema_version == 2 and
               set(receipt) == fields | {'review_packet_digest'})
    if not (legacy or current):
        _stop('REVIEW_RECEIPT_INVALID')
    validate_review_dispatch_intent(intent)
    value = dict(receipt)
    claimed = value.pop('receipt_sha256')
    actual = hashlib.sha256(_canonical_json_bytes(
        value, 'REVIEW_RECEIPT_INVALID')).hexdigest()
    if claimed != actual:
        _stop('REVIEW_RECEIPT_DIGEST_MISMATCH')
    packet_digest = None if legacy else receipt['review_packet_digest']
    if (type(schema_version) is not int or
            any(receipt.get(key) != intent.get(key) for key in (
                'dispatch_id', 'task_id', 'run_id', 'contract_digest', 'source_sha',
                'result_head', 'diff_sha256', 'developer_task_id')) or
            type(receipt['verdict']) is not str or receipt['verdict'] not in _REVIEW_VERDICTS or
            type(receipt['security']) is not str or receipt['security'] not in _REVIEW_VERDICTS or
            type(receipt['functional']) is not str or receipt['functional'] not in _REVIEW_VERDICTS or
            type(receipt['execution_status']) is not str or
            receipt['execution_status'] not in ('SUCCEEDED', 'FAILED', 'BLOCKED',
                                                 'UNKNOWN', 'NOT_DISPATCHED') or
            (packet_digest is not None and
             (type(packet_digest) is not str or _HEX64.fullmatch(packet_digest) is None)) or
            (current and receipt['execution_status'] == 'SUCCEEDED' and
             packet_digest is None) or
            type(receipt['code']) is not str or
            re.fullmatch(r'[A-Z0-9_]{1,64}', receipt['code']) is None or
            type(receipt['provenance']) is not str or
            receipt['provenance'] not in ('TRUSTED_REVIEW_ADAPTER', 'LOCAL_NO_ADAPTER')):
        _stop('REVIEW_RECEIPT_BINDING_MISMATCH')
    if receipt['execution_status'] == 'NOT_DISPATCHED':
        if (receipt['verdict'] != 'BLOCKED' or receipt['security'] != 'BLOCKED' or
                receipt['functional'] != 'BLOCKED' or
                receipt['provenance'] != 'LOCAL_NO_ADAPTER' or
                any(receipt.get(key) is not None for key in (
                    'reviewer_task_id', 'reviewer_identity', 'reviewer_session_ref',
                    'findings_sha256')) or packet_digest is not None):
            _stop('REVIEW_RECEIPT_INVALID')
    else:
        for key in ('reviewer_task_id', 'reviewer_identity', 'reviewer_session_ref'):
            if type(receipt.get(key)) is not str or not receipt[key] or len(receipt[key]) > 512:
                _stop('REVIEW_RECEIPT_INVALID')
        if (receipt['reviewer_task_id'] == intent['developer_task_id'] or
                receipt['reviewer_identity'] == intent['developer_identity'] or
                receipt['reviewer_session_ref'] == intent['developer_session_ref'] or
                type(receipt['findings_sha256']) is not str or
                _HEX64.fullmatch(receipt['findings_sha256']) is None or
                receipt['provenance'] != 'TRUSTED_REVIEW_ADAPTER'):
            _stop('REVIEWER_NOT_INDEPENDENT')
        if receipt['execution_status'] == 'SUCCEEDED':
            expected = ('PASS' if receipt['security'] == receipt['functional'] == 'PASS' else
                        'FAIL' if 'FAIL' in (receipt['security'], receipt['functional']) and
                        'BLOCKED' not in (receipt['security'], receipt['functional']) else 'BLOCKED')
            if receipt['verdict'] != expected:
                _stop('REVIEW_RECEIPT_INVALID')
        elif receipt['verdict'] != 'BLOCKED':
            _stop('REVIEW_RECEIPT_INVALID')
    return receipt


__all__ = [
    '_developer_changed_paths', '_merge_resolution_paths', '_developer_tip',
    'read_authenticated_main_sha', '_commit_present', 'ensure_main_commit',
    'head_adds_only_main', '_collect_registered_result_diff',
    '_segment_shape_valid', '_verify_recorded_segment', '_verify_artifact_segments',
    'validate_review_dispatch_receipt',
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
