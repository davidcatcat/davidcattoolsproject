def run_incident_remote_test(incident_id, *, sha, modules=(), root=None,
                             config_path=None, transport=None, now=None):
    """Run a main incident's failing modules at one of its SHAs over EC2 SSH.

    Admission (each refusal has a stable code and runs nothing): the incident
    must be readable and still open; ``sha`` must be recorded in it (a failing
    head, the last-good head or a merge SHA in a recorded range); every module
    must be recorded as failing in it (``modules`` empty means all of them);
    at most the incident store's admitted module limit.

    The run is the Task remote-test session: the same fixed remote program,
    SSH timeout, receipt validation and output screening, and the
    remote program's own workspace and log cleanup. Before recording, every
    output-derived receipt field (``final`` and ``failure_excerpt``) is
    screened again (``_screen_incident_receipt``): a sensitive hit records
    UNKNOWN/RECEIPT_SENSITIVE without the receipt, and a final line outside the
    unittest summary grammar is stored as ``main_incident_store.FINAL_REDACTED``.
    A receipt whose fields contradict its result
    (``main_incident_store.receipt_consistent``, e.g. PASS with a non-zero
    exit code) records UNKNOWN/RECEIPT_INCONSISTENT without the receipt.
    The store accepts only the exact allowlisted receipt fields. The SHA itself is the
    fetched ref, so the remote checks FETCH_HEAD against it; nothing pushes,
    resets or updates a ref, and no CI job is triggered or rerun.

    The result (sha, modules, result, code, receipt, where=EC2) is appended to
    the incident with ``main_incident_store.append_remote_test``, never to a
    Task. ``transport(argv, script, timeout)`` is injectable; ``root`` is the
    TaskStore root the incident store sits beside; ``now`` is an epoch or None.
    """
    from . import main_incident_store as incident_store
    if type(incident_id) is not str or not incident_store.INCIDENT_ID.fullmatch(incident_id):
        return _incident_denied(INCIDENT_UNKNOWN, incident_id)
    if (type(sha) is not str or not _SHA.fullmatch(sha) or
            type(modules) not in (tuple, list, set, frozenset) or
            any(type(item) is not str or not _MODULE.fullmatch(item) for item in modules)):
        return _incident_denied(INCIDENT_ARGUMENT_INVALID, incident_id)
    try:
        record = incident_store.get(incident_id, root)
    except LLMToolError as exc:
        missing = getattr(exc, 'incident_code', None) == incident_store.INCIDENT_RECORD_UNAVAILABLE
        return _incident_denied(INCIDENT_UNKNOWN if missing else INCIDENT_UNREADABLE,
                                incident_id)
    if record['outcome'] != incident_store.OPEN:
        return _incident_denied(INCIDENT_NOT_OPEN, incident_id)
    if sha not in incident_store.admitted_shas(record):
        return _incident_denied(INCIDENT_SHA_NOT_RECORDED, incident_id)
    failing = incident_store.failing_modules(record)
    selected = tuple(sorted(set(modules or failing)))
    if len(selected) > incident_store.MAX_REMOTE_MODULES:
        return _incident_denied(INCIDENT_MODULE_CAP, incident_id)
    if not selected or not set(selected) <= failing:
        return _incident_denied(INCIDENT_MODULE_NOT_FAILING, incident_id)
    try:
        config = _config(config_path)
    except LLMToolError:
        return _incident_denied(INCIDENT_CONFIG_INVALID, incident_id)
    try:
        module_timeout, total_timeout = _remote_test_timeouts()
    except LLMToolError:
        return _incident_denied(INCIDENT_CONFIG_INVALID, incident_id)
    argv = _ssh_argv(config, sha, sha, selected, module_timeout, total_timeout)
    command_digest = _command_digest(argv)

    def finish(result, code, receipt=None):
        entry = {'sha': sha, 'modules': list(selected), 'result': result, 'code': code,
                 'receipt': receipt, 'where': incident_store.REMOTE_TEST_WHERE,
                 'recorded_at': incident_store.utc_stamp(now)}
        return _record_incident_result(incident_store, incident_id, entry, root)

    if transport is None:
        transport = _ssh_transport
    timeout = _ssh_timeout()
    try:
        result = transport(argv, _REMOTE_SCRIPT, timeout)
    except subprocess.TimeoutExpired:
        return finish('TIMEOUT', 'SSH_TIMEOUT')
    except (OSError, ConnectionError):
        return finish('UNAVAILABLE', 'SSH_UNAVAILABLE')
    stdout = result.stdout if type(result.stdout) is bytes else str(result.stdout).encode()
    if result.returncode != 0 and not stdout:
        code = {75: 'REMOTE_BUSY', 76: 'REMOTE_LOCK_UNSAFE'}.get(result.returncode, 'SSH_FAILED')
        return finish('UNAVAILABLE', code)
    parsed = _validate_remote_receipt(stdout, sha=sha, modules=selected)
    if parsed is None:
        return finish('UNKNOWN', 'RECEIPT_INVALID')
    # #2119 WP-1: keep the incident receipt shape unchanged (design section 5.2).
    parsed.pop('module_results', None)
    screened = _screen_incident_receipt(parsed)
    if screened is None:
        return finish('UNKNOWN', INCIDENT_RECEIPT_SENSITIVE)
    receipt = dict(screened, incident_id=incident_id, command_digest=command_digest)
    if not incident_store.receipt_consistent(receipt):
        return finish('UNKNOWN', INCIDENT_RECEIPT_INCONSISTENT)
    receipt['receipt_digest'] = incident_store.remote_test_digest(receipt)
    return finish(parsed['result'], None, receipt)


def _new_nonce():
    """Return 32 lowercase hex characters from the secrets module."""
    return secrets.token_hex(16)


def _hosted_post(path, body):
    """Make one POST to the single accepted hosted dispatch path using gh CLI."""
    if path != _HOSTED_DISPATCH_PATH:
        raise ValueError(f'Unsupported dispatch path: {path}')
    try:
        env = ai_github_exec.verified_ai_env()
    except ai_github_exec.AiIdentityError as exc:
        raise LLMToolError(f'GITHUB_AI_IDENTITY_REFUSED {exc.code}') from exc
    gh = shutil.which('gh', path=_GH_PATH)
    if gh is None:
        raise LLMToolError('gh executable not found')
    slug = _trusted_slug()
    env.update({
        'PATH': _GH_PATH,
        'HOME': os.path.expanduser('~'),
        'LC_ALL': 'C',
        'GH_PROMPT_DISABLED': '1',
        'GH_NO_UPDATE_NOTIFIER': '1',
        'NO_COLOR': '1',
    })
    argv = [
        gh, 'api', '--method', 'POST',
        '-H', 'Accept: application/vnd.github+json',
        '-H', 'X-GitHub-Api-Version: 2022-11-28',
        '--input', '-',
        'repos/' + slug + '/' + path,
    ]
    if isinstance(body, bytes):
        payload = body
    else:
        payload = json.dumps(body, separators=(',', ':')).encode('utf-8')
    try:
        process = subprocess.Popen(
            argv, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, close_fds=True, start_new_session=True,
        )
    except OSError as exc:
        raise LLMToolError(f'Failed to execute gh: {exc}') from exc

    deadline = time.monotonic() + 10
    raw_out = bytearray()
    raw_err = bytearray()
    selector = selectors.DefaultSelector()
    try:
        if process.stdin:
            process.stdin.write(payload)
            process.stdin.close()
        for stream, kind in ((process.stdout, 'out'), (process.stderr, 'err')):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, kind)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, 10)
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif key.data == 'out':
                    if len(raw_out) + len(chunk) > 65536:
                        raise LLMToolError('POST output exceeded limit')
                    raw_out.extend(chunk)
                else:
                    if len(raw_err) + len(chunk) > 65536:
                        raise LLMToolError('POST stderr exceeded limit')
                    raw_err.extend(chunk)
        code = process.wait(timeout=max(0, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LLMToolError(f'gh command failure: {exc}') from exc
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, 9)
            except OSError:
                process.kill()
            process.wait()
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
    if code != 0:
        raise LLMToolError(f'gh POST failed with code {code}')
    return True


def _hosted_dispatch(sha, ref, modules, nonce, *, post=None):
    """Validate parameters and dispatch the GitHub-hosted remote-test workflow."""
    if type(sha) is not str or not _SHA.fullmatch(sha):
        raise ValueError('Invalid sha')
    if type(ref) is not str or not _REF.fullmatch(ref):
        raise ValueError('Invalid ref')
    if type(nonce) is not str or not _NONCE.fullmatch(nonce):
        raise ValueError('Invalid nonce')
    if not isinstance(modules, (list, tuple)):
        raise ValueError('modules must be a sequence')
    if not modules:
        raise ValueError('modules must be non-empty')
    if any(type(m) is not str or not _MODULE.fullmatch(m) for m in modules):
        raise ValueError('Each module must match _MODULE')
    if list(modules) != sorted(set(modules)):
        raise ValueError('modules must be sorted distinct items')

    body = {
        'ref': 'main',
        'inputs': {
            'sha': sha,
            'ref': ref,
            'modules': ' '.join(modules),
            'nonce': nonce,
        },
    }
    if post is None:
        post = _hosted_post
    try:
        res = post(_HOSTED_DISPATCH_PATH, body)
        if res is False or res == 'UNCONFIRMED':
            return 'UNCONFIRMED'
        return 'DISPATCHED'
    except LLMToolError as exc:
        if str(exc).startswith('GITHUB_AI_IDENTITY_REFUSED'):
            raise
        return 'UNCONFIRMED'
    except Exception:
        return 'UNCONFIRMED'


def _hosted_find_run(nonce, *, get=None):
    """Find the single matching workflow run for the given nonce."""
    if type(nonce) is not str or not _NONCE.fullmatch(nonce):
        raise ValueError('Invalid nonce')
    if get is None:
        get = _github_get
    workflow_file = _HOSTED_WORKFLOW_PATH.rsplit('/', 1)[-1]
    path = f'actions/workflows/{workflow_file}/runs?event=workflow_dispatch&branch=main'
    try:
        data = get(path)
    except Exception:
        return 'UNREADABLE'
    if not isinstance(data, dict):
        return 'UNREADABLE'
    runs = data.get('workflow_runs')
    if not isinstance(runs, list):
        return 'UNREADABLE'
    expected_title = _HOSTED_TITLE_PREFIX + nonce
    kept = []
    for run in runs:
        if not isinstance(run, dict):
            return 'UNREADABLE'
        if run.get('display_title') != expected_title:
            continue
        if run.get('head_branch') != 'main':
            continue
        if run.get('path') != _HOSTED_WORKFLOW_PATH:
            continue
        actor = run.get('actor')
        if not isinstance(actor, dict) or actor.get('login') not in _HOSTED_ACTORS:
            continue
        triggering = run.get('triggering_actor')
        if not isinstance(triggering, dict) or triggering.get('login') not in _HOSTED_ACTORS:
            continue
        kept.append(run)
    if len(kept) == 1:
        return kept[0]
    if len(kept) == 0:
        return None
    return 'AMBIGUOUS'


def _extract_check_run_id(job):
    """Extract check-run ID string from a job dict, or return None."""
    if not isinstance(job, dict):
        return None
    check_url = job.get('check_run_url')
    if isinstance(check_url, str):
        match = re.search(r'check-runs/([1-9][0-9]{0,18})\b', check_url)
        if match:
            return match.group(1)
    check_id = job.get('check_run_id')
    if isinstance(check_id, int) and check_id > 0:
        return str(check_id)
    return None


def _hosted_job(run, *, get=None):
    """Read a hosted run's jobs, require exactly one remote-test job, and verify completed runner trust."""
    if not isinstance(run, dict) or not isinstance(run.get('id'), int) or run['id'] <= 0:
        return 'UNREADABLE'
    if get is None:
        get = _github_get
    run_id = run['id']
    path = f'actions/runs/{run_id}/jobs'
    try:
        data = get(path)
    except Exception:
        return 'UNREADABLE'
    if not isinstance(data, dict):
        return 'UNREADABLE'
    jobs = data.get('jobs')
    if not isinstance(jobs, list):
        return 'UNREADABLE'
    for job in jobs:
        if not isinstance(job, dict):
            return 'UNREADABLE'
    matching = [job for job in jobs if job.get('name') == _HOSTED_JOB_NAME]
    if len(matching) != 1:
        return 'HOSTED_JOB_MISSING'
    job = matching[0]
    status = job.get('status')
    if status in ('queued', 'in_progress'):
        return job
    if status != 'completed':
        return 'HOSTED_RUNNER_UNTRUSTED'
    if job.get('labels') != list(_HOSTED_LABELS):
        return 'HOSTED_RUNNER_UNTRUSTED'
    if job.get('runner_group_name') != _HOSTED_RUNNER_GROUP:
        return 'HOSTED_RUNNER_UNTRUSTED'
    check_run_id = _extract_check_run_id(job)
    if check_run_id is None:
        return 'HOSTED_RUNNER_UNTRUSTED'
    try:
        check_run = get(f'check-runs/{check_run_id}')
    except Exception:
        return 'UNREADABLE'
    if not isinstance(check_run, dict):
        return 'UNREADABLE'
    app = check_run.get('app')
    if not isinstance(app, dict) or app.get('id') != _HOSTED_APP_ID:
        return 'HOSTED_RUNNER_UNTRUSTED'
    run_head = run.get('head_sha')
    if not isinstance(run_head, str) or check_run.get('head_sha') != run_head:
        return 'HOSTED_RUNNER_UNTRUSTED'
    return job


def _hosted_get_bytes(path, *, limit=_HOSTED_ZIP_LIMIT + 1):
    """Make one GET to download an artifact zip using gh CLI."""
    if type(path) is not str or not _ARTIFACT_ZIP_PATH.fullmatch(path):
        raise ValueError(f'Unsupported artifact path: {path}')
    gh = shutil.which('gh', path=_GH_PATH)
    if gh is None:
        raise LLMToolError('gh executable not found')
    slug = _trusted_slug()
    env = {
        'PATH': _GH_PATH,
        'HOME': os.path.expanduser('~'),
        'LC_ALL': 'C',
        'GH_PROMPT_DISABLED': '1',
        'GH_NO_UPDATE_NOTIFIER': '1',
        'NO_COLOR': '1',
    }
    if os.environ.get('GH_CONFIG_DIR'):
        env['GH_CONFIG_DIR'] = os.environ['GH_CONFIG_DIR']
    argv = [
        gh, 'api', '--method', 'GET',
        '-H', 'Accept: application/vnd.github+json',
        '-H', 'X-GitHub-Api-Version: 2022-11-28',
        'repos/' + slug + '/' + path,
    ]
    try:
        process = subprocess.Popen(
            argv, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, close_fds=True, start_new_session=True,
        )
    except OSError as exc:
        raise LLMToolError(f'Failed to execute gh: {exc}') from exc

    deadline = time.monotonic() + 10
    raw_out = bytearray()
    raw_err = bytearray()
    selector = selectors.DefaultSelector()
    stopped_early = False
    try:
        for stream, kind in ((process.stdout, 'out'), (process.stderr, 'err')):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, kind)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, 10)
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif key.data == 'out':
                    needed = limit - len(raw_out)
                    if needed > 0:
                        raw_out.extend(chunk[:needed])
                    if len(raw_out) >= limit:
                        stopped_early = True
                        selector.unregister(key.fileobj)
                        break
                else:
                    if len(raw_err) + len(chunk) > 65536:
                        raise LLMToolError('GET stderr exceeded limit')
                    raw_err.extend(chunk)
        code = process.wait(timeout=max(0, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LLMToolError(f'gh command failure: {exc}') from exc
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, 9)
            except OSError:
                process.kill()
            process.wait()
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
    if not stopped_early and code != 0:
        raise LLMToolError(f'gh GET failed with code {code}')
    return bytes(raw_out)

