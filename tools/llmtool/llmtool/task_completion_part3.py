"""Git result-diff and artifact-chain checks for task_completion (#2281).

The code is moved verbatim. Parent helpers are resolved lazily through
task_completion so patching that module path keeps its effect.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from . import source_freshness
from .errors import LLMToolError

# #2281 A2: the parent module is already in sys.modules while it imports this
# part, so constant reads through it keep a patch on the task_completion path
# effective here too.
_m = sys.modules[__package__ + '.task_completion']


def _stop(*args, **kwargs):
    from . import task_completion as _m
    return _m._stop(*args, **kwargs)


def _git(*args, **kwargs):
    from . import task_completion as _m
    return _m._git(*args, **kwargs)


def _github_get(*args, **kwargs):
    from . import task_completion as _m
    return _m._github_get(*args, **kwargs)


def _reject_legacy_grafts(*args, **kwargs):
    from . import task_completion as _m
    return _m._reject_legacy_grafts(*args, **kwargs)


def collect_git_snapshot(*args, **kwargs):
    from . import task_completion as _m
    return _m.collect_git_snapshot(*args, **kwargs)


def read_d6_registration(*args, **kwargs):
    from . import task_completion as _m
    return _m.read_d6_registration(*args, **kwargs)


def validate_env2_pass_marker(*args, **kwargs):
    from . import task_completion as _m
    return _m.validate_env2_pass_marker(*args, **kwargs)


def _developer_changed_paths(*args, **kwargs):
    from . import task_completion as _m
    return _m._developer_changed_paths(*args, **kwargs)


def verify_artifact_applied(*args, **kwargs):
    from . import task_completion as _m
    return _m.verify_artifact_applied(*args, **kwargs)


def _merge_resolution_paths(root, commit, first_parent, second_parent):
    """Return the paths a two-parent merge resolved by hand or conflicted on."""
    from . import task_completion as _m
    resolution_raw = _m._remerge_diff_paths_raw(root, commit)
    conflict_raw = _m._merge_tree_conflict_paths_raw(root, first_parent, second_parent)
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


def _guidance_refresh_commit(root, commit, parent):
    """True for llmtool's own publish hash-refresh commit (#2497, #2640).

    It may change only guidance-drift.json, and only to the exact bytes the
    refresh writes from the parent tree (guidance_refresh.is_refresh_content),
    so it adds no Developer content. Any git failure or doubt is False.
    """
    from . import guidance_refresh as _g
    try:
        paths = _git(root, ('diff-tree', '--no-commit-id', '--no-renames', '-r',
                            '--name-only', '-z', parent, commit), limit=4096)
        if [p for p in paths.decode('utf-8').split('\x00') if p] != [_g.CONFIG_REL_PATH]:
            return False
        blob = lambda sha, path: _git(root, ('cat-file', 'blob', f'{sha}:{path}'),
                                      limit=1024 * 1024)
        return _g.is_refresh_content(
            blob(parent, _g.CONFIG_REL_PATH), blob(commit, _g.CONFIG_REL_PATH),
            {path: blob(parent, path) for path in _g.GUIDANCE_FILES})
    except (LLMToolError, UnicodeError):
        return False


def _developer_tip(root, start_sha, head_sha, trusted_base_sha=None):
    """Return head_sha with its trailing clean trusted-main merges stripped.

    llmtool's own guidance hash-refresh commit (_guidance_refresh_commit) is
    stripped too: it carries no Developer content (#2640).

    A stripped merge has exactly two parents, a trusted second parent and no
    resolution or conflict path, so it adds nothing to its first parent except
    trusted main. The first commit that fails is returned, or else start_sha.
    """
    from . import task_completion as _m
    if trusted_base_sha is None:
        trusted_base_sha = start_sha
    current = head_sha
    while current != start_sha:
        raw = _git(root, ('rev-list', '--parents', '-n', '1', current), limit=4096)
        parents = raw.decode('ascii').split()[1:]
        if len(parents) == 1 and _guidance_refresh_commit(root, current, parents[0]):
            current = parents[0]
            continue
        if len(parents) == 2:
            _require_trusted_base(root, trusted_base_sha)
        if (len(parents) != 2 or not _m._is_ancestor(root, parents[1], trusted_base_sha) or
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
            _m._HEX40.fullmatch(response['object']['sha']) is None):
        _stop('MAIN_REF_PIN_INVALID')
    return response['object']['sha']


def _commit_present(root, sha):
    try:
        _git(root, ('cat-file', '-e', sha + '^{commit}'), limit=4096)
    except LLMToolError:
        return False
    return True


def _require_trusted_base(root, trusted_base_sha):
    """Stop with a named code when the trusted main pin commit is not local.

    #2365: a head with main merges is checked against the trusted pin; when
    GitHub main moved and the local fetch has not landed yet, the ancestry
    read would fail as a generic GIT_INSPECTION_FAILED. Fetching main and
    rerunning resolves it, so the code says so. Fail-closed either way.
    """
    if not _commit_present(root, trusted_base_sha):
        _stop('MAIN_PIN_COMMIT_MISSING')


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
    if any(type(sha) is not str or _m._HEX40.fullmatch(sha) is None
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


def _is_ancestor(root, ancestor, descendant):
    result = subprocess.run(['/usr/bin/git', '-c', 'core.useReplaceRefs=false',
        '-C', root, 'merge-base', '--is-ancestor', ancestor, descendant],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=_m._TIMEOUT,
        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
             'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
             'GIT_NO_LAZY_FETCH': '1', 'GIT_NO_REPLACE_OBJECTS': '1'},
        shell=False, check=False)
    if result.returncode not in (0, 1):
        _stop('GIT_INSPECTION_FAILED')
    return result.returncode == 0


def _collect_registered_result_diff(*, task_id, expected_common_dir, expected_root,
                                     expected_branch, source_sha, task_binding,
                                     expected_agent, expected_session_ref,
                                     expected_native_session_id, write_paths,
                                     artifact_segments=None, trusted_base_sha=None,
                                     stacked_chain=None):
    """Share D6, ENV2, HEAD, ancestry, scope and diff-hash checks across routes."""
    registration = read_d6_registration(task_id, expected_common_dir,
                                        allowed_states=_m._D6_EVIDENCE_STATES)
    if (registration.root != expected_root or registration.branch != expected_branch or
            type(source_sha) is not str or _m._HEX40.fullmatch(source_sha) is None or
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
    normalized_chain = None
    if stacked_chain is not None:
        # #2084: no repair artifact segment check gets a chain union.
        from . import task_completion_part5
        normalized_chain = task_completion_part5._apply_stacked_chain(
            registration.root, stacked_chain, artifact_segments, source_sha,
            snapshot.head, write_paths, trusted_base_sha)
    # The 128-path limit counts Developer paths only: a merged main can bring in
    # any number of its own files.
    paths = _diff_paths(registration.root, source_sha, snapshot.head)
    if not paths:
        _stop('RESULT_DIFF_INVALID')
    developer_paths = _developer_changed_paths(
        registration.root, source_sha, snapshot.head, trusted_base_sha)
    if not developer_paths or len(developer_paths) > 128:
        _stop('RESULT_DIFF_INVALID')
    if normalized_chain is None and not set(developer_paths).issubset(set(write_paths)):
        _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID' if artifact_segments is not None
              else 'RESULT_SCOPE_MISMATCH')
    patch = _git(registration.root, ('diff', '--binary', '--no-ext-diff', '--no-textconv',
                '--no-renames', source_sha, snapshot.head, '--'), limit=_m._GIT_LIMIT)
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
        type(segment[4]) is str and _m._HEX40.fullmatch(segment[4]) is not None))


def _verify_recorded_segment(root, start_sha, end_sha, recorded_head, trusted_base_sha):
    """#1886 F1: a record-only repair segment ends at its recorded result HEAD.

    The recorded HEAD must be a new commit descending from the segment start,
    and the segment end must be that HEAD or that HEAD plus only clean
    trusted-main merges, as for the Claude root segment. A Developer commit
    after the recorded HEAD is unrecorded and refuses.
    """
    from . import task_completion as _m
    try:
        if (type(recorded_head) is not str or _m._HEX40.fullmatch(recorded_head) is None or
                recorded_head == start_sha or
                not _m._is_ancestor(root, start_sha, recorded_head) or
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
                type(start_sha) is not str or _m._HEX40.fullmatch(start_sha) is None or
                type(end_sha) is not str or _m._HEX40.fullmatch(end_sha) is None):
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


def collect_executor_result(*, task_id, expected_common_dir, expected_root,
                           expected_branch, source_sha, task_binding,
                           expected_agent, expected_session_ref,
                           expected_native_session_id, write_paths, artifact, artifact_sha256,
                           artifact_segments=None, trusted_base_sha=None,
                           stacked_chain=None):
    """Compare the current registered Executor HEAD and actual source-to-result diff."""
    registration, snapshot, paths, developer_paths, patch = _collect_registered_result_diff(
        task_id=task_id, expected_common_dir=expected_common_dir,
        expected_root=expected_root, expected_branch=expected_branch,
        source_sha=source_sha, task_binding=task_binding,
        expected_agent=expected_agent, expected_session_ref=expected_session_ref,
        expected_native_session_id=expected_native_session_id,
        write_paths=write_paths, artifact_segments=artifact_segments,
        trusted_base_sha=trusted_base_sha, stacked_chain=stacked_chain)
    # An artifact describes the Developer's work, so it is compared with the
    # Developer tip. Paths, digest and scope above still cover the full head.
    if artifact_segments is None:
        tip = _developer_tip(registration.root, source_sha, snapshot.head, trusted_base_sha)
        if stacked_chain is None:
            scope_write_paths, scope_paths = write_paths, developer_paths
        else:
            from . import task_completion_part5
            scope_write_paths, scope_paths = task_completion_part5._stacked_artifact_scope(
                registration.root, stacked_chain, source_sha, write_paths, tip,
                trusted_base_sha)
        verify_artifact_applied(registration.root, artifact, artifact_sha256,
                                source_sha, tip,
                                _diff_paths(registration.root, source_sha, tip),
                                scope_write_paths, scope_paths=scope_paths)
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
    result = {
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
    if stacked_chain is not None:
        from . import task_completion_part5
        result.update(task_completion_part5._stacked_chain_evidence(
            registration.root, stacked_chain, source_sha))
    return result


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


def _registered_test(path):
    """Return the runner kind the parent's live test-evidence profile gives."""
    return _m._classify_test_path(path)


def _docs_only(paths):
    return (bool(paths) and all(type(path) is str and path.endswith('.md')
            and not path.startswith(('tools/llmtool/llmtool/', 'scripts/'))
            for path in paths))
