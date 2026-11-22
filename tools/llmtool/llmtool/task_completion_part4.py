from __future__ import annotations

import sys as _sys
import types as _types

_task_completion = _sys.modules[__package__ + '.task_completion']


def _stop(code):
    error = LLMToolError('Executor completion evidence is unavailable; details omitted')
    error.completion_code = code
    raise error from None


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


def _tree_entry(root, tree_sha, path):
    raw = _git(root, ('ls-tree', '-z', tree_sha, '--', path), limit=4096)
    if not raw:
        return None
    if not raw.endswith(b'\0') or raw.count(b'\0') != 1:
        _stop('DEVELOPER_ARTIFACT_CHECK_UNKNOWN')
    metadata, entry_path = raw[:-1].split(b'\t', 1)
    fields = metadata.split(b' ')
    if (len(fields) != 3 or entry_path != path.encode('utf-8') or
            fields[1] != b'blob' or fields[0] not in (b'100644', b'100755') or
            re.fullmatch(rb'[0-9a-f]{40}', fields[2]) is None):
        _stop('DEVELOPER_ARTIFACT_TREE_ENTRY_UNSUPPORTED')
    return fields[0].decode('ascii'), fields[2].decode('ascii')


def _tree_blob(root, tree_sha, path):
    entry = _tree_entry(root, tree_sha, path)
    if entry is None:
        return None
    mode, blob_sha = entry
    return mode, _git(root, ('cat-file', 'blob', blob_sha))


def _proposal_blob_size(root, tree_sha, path):
    entry = _tree_entry(root, tree_sha, path)
    if entry is None:
        return 0
    raw_size = _git(root, ('cat-file', '-s', entry[1]), limit=32).strip()
    if not re.fullmatch(rb'(0|[1-9][0-9]{0,12})', raw_size):
        _stop('DEVELOPER_ARTIFACT_CHECK_UNKNOWN')
    size = int(raw_size)
    if size > _GIT_LIMIT:
        _stop('DEVELOPER_ARTIFACT_BLOB_TOO_LARGE')
    return size


def verify_artifact_applied(root, artifact, artifact_sha256, source_sha, result_sha,
                            changed_paths, write_paths, *, scope_paths=None):
    """Apply the proposal in a bounded temp tree and compare result blobs/modes."""
    if (type(artifact) is not str or not artifact or
            len(artifact.encode('utf-8')) > _GIT_LIMIT or
            type(artifact_sha256) is not str or
            hashlib.sha256(artifact.encode('utf-8')).hexdigest() != artifact_sha256 or
            type(source_sha) is not str or _HEX40.fullmatch(source_sha) is None or
            type(result_sha) is not str or _HEX40.fullmatch(result_sha) is None):
        _stop('DEVELOPER_ARTIFACT_BINDING_INVALID')
    patch_paths = []
    for line in artifact.splitlines():
        if line.startswith('diff --git '):
            match = re.fullmatch(r'diff --git a/([^\s]+) b/([^\s]+)', line)
            if match is None or match.group(1) != match.group(2):
                _stop('DEVELOPER_ARTIFACT_INVALID')
            patch_paths.append(match.group(1))
    scope_paths = patch_paths if scope_paths is None else scope_paths
    if (not patch_paths or len(patch_paths) > _PROPOSAL_FILE_LIMIT or
            len(patch_paths) != len(set(patch_paths)) or
            set(patch_paths) != set(changed_paths) or
            not set(scope_paths).issubset(set(write_paths))):
        _stop('DEVELOPER_ARTIFACT_RESULT_MISMATCH')
    for path in patch_paths:
        parts = Path(path).parts
        if (Path(path).is_absolute() or not parts or
                any(part in ('', '.', '..') for part in parts) or '\\' in path or
                '\x00' in path):
            _stop('DEVELOPER_ARTIFACT_INVALID')
    total_blob_bytes = 0
    for path in patch_paths:
        total_blob_bytes += _proposal_blob_size(root, source_sha, path)
        total_blob_bytes += _proposal_blob_size(root, result_sha, path)
        if total_blob_bytes > _PROPOSAL_TREE_LIMIT:
            _stop('DEVELOPER_ARTIFACT_TREE_TOO_LARGE')
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_ATTR_NOSYSTEM': '1', 'GIT_OPTIONAL_LOCKS': '0',
           'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_LAZY_FETCH': '1',
           'GIT_NO_REPLACE_OBJECTS': '1'}
    try:
        with tempfile.TemporaryDirectory(prefix='llmtool-proposal-tree-') as temporary:
            temp_root = Path(temporary)
            for path in patch_paths:
                source = _tree_blob(root, source_sha, path)
                if source is None:
                    continue
                mode, content = source
                target = temp_root.joinpath(*Path(path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    stream.write(content)
                target.chmod(0o755 if mode == '100755' else 0o644)
            proc = subprocess.run(
                ['/usr/bin/git', '-c', 'core.useReplaceRefs=false',
                 '-c', 'core.hooksPath=/dev/null', 'apply',
                 '--binary', '--whitespace=nowarn', '-'],
                cwd=temp_root, input=artifact.encode('utf-8'),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_TIMEOUT, env=env, shell=False, check=False)
            if proc.returncode != 0:
                _stop('DEVELOPER_ARTIFACT_NOT_APPLIED')
            for path in patch_paths:
                expected = _tree_blob(root, result_sha, path)
                target = temp_root.joinpath(*Path(path).parts)
                try:
                    actual_stat = target.lstat()
                except FileNotFoundError:
                    actual_stat = None
                if expected is None:
                    if actual_stat is not None:
                        _stop('DEVELOPER_ARTIFACT_RESULT_TREE_MISMATCH')
                    continue
                mode, content = expected
                if (actual_stat is None or not stat.S_ISREG(actual_stat.st_mode) or
                        target.read_bytes() != content or
                        bool(actual_stat.st_mode & 0o111) != (mode == '100755')):
                    _stop('DEVELOPER_ARTIFACT_RESULT_TREE_MISMATCH')
    except LLMToolError:
        raise
    except Exception:
        _stop('DEVELOPER_ARTIFACT_CHECK_UNKNOWN')


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


def _is_ancestor(root, ancestor, descendant):
    result = subprocess.run(['/usr/bin/git', '-c', 'core.useReplaceRefs=false',
        '-C', root, 'merge-base', '--is-ancestor', ancestor, descendant],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=_TIMEOUT,
        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
             'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
             'GIT_NO_LAZY_FETCH': '1', 'GIT_NO_REPLACE_OBJECTS': '1'},
        shell=False, check=False)
    if result.returncode not in (0, 1):
        _stop('GIT_INSPECTION_FAILED')
    return result.returncode == 0


__all__ = [
    '_stop', '_canonical_json_bytes', '_unique_pairs', '_worktree_snapshot',
    'collect_executor_worktree_result', '_tree_entry', '_tree_blob',
    '_proposal_blob_size', 'verify_artifact_applied', '_trust_config',
    '_trusted_slug', '_ci_trust', '_github_get', '_is_ancestor',
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
