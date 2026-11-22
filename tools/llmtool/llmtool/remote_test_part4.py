def _hosted_artifact(run_id, nonce, *, get=None, get_bytes=None):
    """Return receipt.json bytes from the run's single receipt artifact, else None."""
    if isinstance(run_id, int) and run_id > 0:
        run_id_str = str(run_id)
    elif isinstance(run_id, str) and run_id.isdigit() and int(run_id) > 0:
        run_id_str = run_id
    else:
        return None
    if type(nonce) is not str or not _NONCE.fullmatch(nonce):
        return None
    if get is None:
        get = _github_get
    if get_bytes is None:
        get_bytes = _hosted_get_bytes

    expected_name = _HOSTED_ARTIFACT_PREFIX + nonce
    all_artifacts = []
    total_count = None

    for page in range(1, _HOSTED_ARTIFACT_PAGE_LIMIT + 1):
        path = f'actions/runs/{run_id_str}/artifacts?per_page=100&page={page}'
        try:
            data = get(path)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        page_total = data.get('total_count')
        if type(page_total) is not int or page_total < 0:
            return None
        if total_count is None:
            total_count = page_total
        elif total_count != page_total:
            return None
        artifacts = data.get('artifacts')
        if not isinstance(artifacts, list):
            return None
        all_artifacts.extend(artifacts)
        if not artifacts or len(all_artifacts) >= total_count:
            break
    else:
        return None

    if total_count is None or total_count != len(all_artifacts):
        return None

    matching = []
    for artifact in all_artifacts:
        if not isinstance(artifact, dict):
            return None
        if artifact.get('name') == expected_name:
            matching.append(artifact)

    if len(matching) != 1:
        return None

    candidate = matching[0]
    if candidate.get('expired') is not False:
        return None

    size = candidate.get('size_in_bytes')
    if type(size) is not int or size < 0 or size > _HOSTED_ZIP_LIMIT:
        return None

    artifact_id = candidate.get('id')
    if type(artifact_id) is not int or artifact_id <= 0:
        return None

    download_path = f'actions/artifacts/{artifact_id}/zip'
    try:
        raw_zip = get_bytes(download_path)
    except Exception:
        return None

    if not isinstance(raw_zip, (bytes, bytearray)):
        return None
    if len(raw_zip) > _HOSTED_ZIP_LIMIT:
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            infolist = zf.infolist()
            if len(infolist) != 1:
                return None
            info = infolist[0]
            if info.filename != 'receipt.json':
                return None
            if info.is_dir():
                return None
            mode = info.external_attr >> 16
            if (mode & 0o170000) != 0 and not stat.S_ISREG(mode):
                return None
            if info.file_size < 0 or info.file_size > _HOSTED_MEMBER_LIMIT:
                return None
            member_bytes = zf.read(info)
            if len(member_bytes) > _HOSTED_MEMBER_LIMIT:
                return None
            return member_bytes
    except Exception:
        return None

_PENDING_STATUSES = frozenset({'queued', 'in_progress', 'waiting', 'requested', 'pending'})
_UNKNOWN_CONCLUSIONS = frozenset({
    'cancelled', 'skipped', 'neutral', 'stale', 'action_required', 'startup_failure'
})


def _hosted_actions_block(run, job, nonce):
    """Return type-checked actions block dict from run, job and nonce, else None."""
    if not isinstance(run, dict) or not isinstance(job, dict):
        return None
    if type(nonce) is not str or not _NONCE.fullmatch(nonce):
        return None

    run_id = run.get('id')
    if type(run_id) is not int or run_id <= 0:
        return None

    run_attempt = run.get('run_attempt')
    if type(run_attempt) is not int or run_attempt <= 0:
        return None

    workflow_path = run.get('path')
    if type(workflow_path) is not str or not workflow_path:
        return None

    workflow_sha = run.get('head_sha')
    if type(workflow_sha) is not str or not _SHA.fullmatch(workflow_sha):
        return None

    job_id = job.get('id')
    if type(job_id) is not int or job_id <= 0:
        return None

    check_run_id = job.get('check_run_id')
    if check_run_id is None and type(job.get('check_run_url')) is str:
        match = re.fullmatch(
            r'https://api\.github\.com/repos/[^/\s]+/[^/\s]+/check-runs/([1-9][0-9]{0,18})',
            job['check_run_url'])
        if match:
            check_run_id = int(match.group(1))
    if type(check_run_id) is not int or check_run_id <= 0:
        return None

    labels = job.get('labels')
    if not isinstance(labels, (list, tuple)) or not labels or any(type(x) is not str or not x for x in labels):
        return None

    runner_group = job.get('runner_group') or job.get('runner_group_name')
    if type(runner_group) is not str or not runner_group:
        return None

    return {
        'run_id': run_id,
        'run_attempt': run_attempt,
        'job_id': job_id,
        'check_run_id': check_run_id,
        'workflow_path': workflow_path,
        'workflow_sha': workflow_sha,
        'labels': list(labels),
        'runner_group': runner_group,
        'nonce': nonce,
    }


def _hosted_command_digest(workflow_path, workflow_sha, sha_or_inputs=None, ref=None, modules=None, nonce=None, *, sha=None, inputs=None):
    """Return SHA-256 of compact sorted JSON of workflow path, sha and 4 inputs."""
    if inputs is not None and isinstance(inputs, dict):
        sha = inputs.get('sha')
        ref = inputs.get('ref')
        modules = inputs.get('modules')
        nonce = inputs.get('nonce')
    elif sha_or_inputs is not None:
        if isinstance(sha_or_inputs, dict):
            sha = sha_or_inputs.get('sha')
            ref = sha_or_inputs.get('ref')
            modules = sha_or_inputs.get('modules')
            nonce = sha_or_inputs.get('nonce')
        else:
            sha = sha_or_inputs
    if isinstance(modules, (list, tuple)):
        modules = ' '.join(modules)
    data = {
        'workflow_path': workflow_path,
        'workflow_sha': workflow_sha,
        'sha': sha,
        'ref': ref,
        'modules': modules,
        'nonce': nonce,
    }
    raw = json.dumps(data, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _hosted_outcome(run, job, raw, *, sha, modules):
    """Map hosted run, job and receipt bytes to ('RECEIPT', receipt) or ('OUTCOME', result, code)."""
    if job == 'HOSTED_RUNNER_UNTRUSTED' or run == 'HOSTED_RUNNER_UNTRUSTED':
        return ('OUTCOME', 'UNKNOWN', 'HOSTED_RUNNER_UNTRUSTED')

    if isinstance(job, dict):
        if job.get('labels') is not None and job.get('labels') != list(_HOSTED_LABELS):
            return ('OUTCOME', 'UNKNOWN', 'HOSTED_RUNNER_UNTRUSTED')
        runner_grp = job.get('runner_group_name') or job.get('runner_group')
        if runner_grp is not None and runner_grp != _HOSTED_RUNNER_GROUP:
            return ('OUTCOME', 'UNKNOWN', 'HOSTED_RUNNER_UNTRUSTED')

    if not isinstance(run, dict) or not isinstance(job, dict):
        return ('OUTCOME', 'UNKNOWN', 'HOSTED_RUN_UNKNOWN')

    run_status = run.get('status')
    job_status = job.get('status')
    if (run_status in _PENDING_STATUSES or job_status in _PENDING_STATUSES or
            run.get('conclusion') in _PENDING_STATUSES or job.get('conclusion') in _PENDING_STATUSES):
        return ('OUTCOME', 'UNAVAILABLE', 'HOSTED_PENDING')

    run_conclusion = run.get('conclusion')
    job_conclusion = job.get('conclusion')

    for c in (run_conclusion, job_conclusion, run_status, job_status):
        if c in _UNKNOWN_CONCLUSIONS:
            return ('OUTCOME', 'UNKNOWN', f'HOSTED_RUN_{c.upper()}')

    if run_conclusion == 'timed_out' or job_conclusion == 'timed_out':
        receipt = _validate_remote_receipt(raw, sha=sha, modules=modules)
        if receipt is None:
            return ('OUTCOME', 'TIMEOUT', 'HOSTED_TIMEOUT')
        if (receipt.get('result') == 'TIMEOUT' and
                run_conclusion in ('timed_out', 'failure') and
                job_conclusion in ('timed_out', 'failure')):
            return ('RECEIPT', receipt)
        return ('OUTCOME', 'UNKNOWN', 'RECEIPT_CONFLICT')

    receipt = _validate_remote_receipt(raw, sha=sha, modules=modules)
    if receipt is None:
        if run_conclusion in ('success', 'failure') and job_conclusion in ('success', 'failure'):
            return ('OUTCOME', 'UNKNOWN', 'RECEIPT_INVALID')
        return ('OUTCOME', 'UNKNOWN', 'HOSTED_RUN_UNKNOWN')

    receipt_result = receipt.get('result')

    # Only run and job conclusion success with a PASS receipt yields PASS.
    if run_conclusion == 'success' and job_conclusion == 'success':
        if receipt_result == 'PASS':
            if pass_receipt_covers(receipt, modules):
                return ('RECEIPT', receipt)
            return ('OUTCOME', 'UNKNOWN', 'RECEIPT_INVALID')
        return ('OUTCOME', 'UNKNOWN', 'RECEIPT_CONFLICT')

    if run_conclusion == 'failure' and job_conclusion == 'failure':
        if receipt_result in ('FAIL', 'SHA_MISMATCH', 'TIMEOUT'):
            return ('RECEIPT', receipt)
        return ('OUTCOME', 'UNKNOWN', 'RECEIPT_CONFLICT')

    if run_conclusion in ('success', 'failure') and job_conclusion in ('success', 'failure'):
        return ('OUTCOME', 'UNKNOWN', 'RECEIPT_CONFLICT')

    return ('OUTCOME', 'UNKNOWN', 'HOSTED_RUN_UNKNOWN')


def _as_utc_datetime(val):
    if callable(val):
        val = val()
    if val is None:
        return datetime.now(timezone.utc)
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc)
    if isinstance(val, str):
        iso = val.replace('Z', '+00:00')
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raise TypeError(f'Unsupported now parameter: {val!r}')


def _parse_dispatched_at(val):
    iso = val.replace('Z', '+00:00')
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hosted_start(store, task_id, task, *, executor_task, sha, ref, modules,
                  get=None, post=None, now=None):
    """Start or advance one admitted hosted remote test dispatch step."""
    now_dt = _as_utc_datetime(now)
    modules_list = list(modules)

    entry = None
    dispatches = task.get('remote_test_dispatches')
    if isinstance(dispatches, list):
        for item in dispatches:
            if (isinstance(item, dict) and
                    item.get('executor_task') == executor_task and
                    item.get('sha') == sha and
                    item.get('modules') == modules_list and
                    item.get('state') in ('DISPATCHED', 'RUN_FOUND')):
                entry = item
                break

    if entry is not None:
        nonce = entry['nonce']
    else:
        nonce = _new_nonce()
        dispatched_at = now_dt.replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')
        candidate = {
            'nonce': nonce,
            'sha': sha,
            'executor_task': executor_task,
            'modules': modules_list,
            'dispatched_at': dispatched_at,
            'run_id': None,
            'state': 'DISPATCHED',
        }
        entry = store.append_remote_test_dispatch(task_id, candidate)
        if 'remote_test_dispatches' in task:
            task['remote_test_dispatches'].append(entry)
        else:
            task['remote_test_dispatches'] = [entry]

        dispatch_status = _hosted_dispatch(sha, ref, modules_list, nonce, post=post)
        if dispatch_status == 'UNCONFIRMED':
            return ('OUTCOME', 'UNAVAILABLE', 'HOSTED_DISPATCH_UNCONFIRMED', nonce)

    run = _hosted_find_run(nonce, get=get)
    if run == 'AMBIGUOUS':
        return ('OUTCOME', 'UNKNOWN', 'HOSTED_RUN_AMBIGUOUS', nonce)
    if run == 'UNREADABLE':
        return ('OUTCOME', 'UNAVAILABLE', 'HOSTED_PENDING', nonce)
    if run is None:
        dispatched_dt = _parse_dispatched_at(entry['dispatched_at'])
        if (now_dt - dispatched_dt).total_seconds() < 1800:
            return ('OUTCOME', 'UNAVAILABLE', 'HOSTED_PENDING', nonce)
        store.update_remote_test_dispatch(task_id, nonce, state='ABANDONED')
        entry['state'] = 'ABANDONED'
        return ('OUTCOME', 'UNAVAILABLE', 'HOSTED_DISPATCH_UNCONFIRMED', nonce)
    if isinstance(run, dict):
        if entry.get('state') == 'DISPATCHED':
            run_id = int(run['id'])
            store.update_remote_test_dispatch(task_id, nonce, state='RUN_FOUND', run_id=run_id)
            entry['state'] = 'RUN_FOUND'
            entry['run_id'] = run_id
        return ('RUN', run, nonce)

    return ('OUTCOME', 'UNAVAILABLE', 'HOSTED_PENDING', nonce)


def _hosted_finish(store, task_id, task, run, nonce, *, executor_task, sha, ref, modules,
                   get=None, get_bytes=None):
    """Read a found hosted run's job and receipt artifact, fail-closed, and store receipt or outcome."""
    modules_list = list(modules)
    if not isinstance(run, dict) or run.get('status') != 'completed':
        return _record_outcome(store, task_id, task, result='UNAVAILABLE',
                               code='HOSTED_PENDING', executor_task=executor_task,
                               sha=sha, modules=modules_list)

    job = _hosted_job(run, get=get)
    if isinstance(job, dict) and job.get('status') != 'completed':
        return _record_outcome(store, task_id, task, result='UNAVAILABLE',
                               code='HOSTED_PENDING', executor_task=executor_task,
                               sha=sha, modules=modules_list)

    run_id = run.get('id')
    raw = _hosted_artifact(run_id, nonce, get=get, get_bytes=get_bytes) if isinstance(job, dict) else None
    outcome = _hosted_outcome(run, job, raw, sha=sha, modules=modules_list)

    if outcome[0] == 'RECEIPT':
        receipt = outcome[1]
        actions = _hosted_actions_block(run, job, nonce)
        if actions is None:
            out = _record_outcome(store, task_id, task, result='UNKNOWN', code='RECEIPT_INVALID',
                                  executor_task=executor_task, sha=sha, modules=modules_list)
            store.update_remote_test_dispatch(task_id, nonce, state='RECORDED')
            dispatches = task.get('remote_test_dispatches')
            if isinstance(dispatches, list):
                for item in dispatches:
                    if isinstance(item, dict) and item.get('nonce') == nonce:
                        item['state'] = 'RECORDED'
                        break
            return out

        command_digest = _hosted_command_digest(
            actions['workflow_path'], actions['workflow_sha'],
            sha=sha, ref=ref, modules=modules_list, nonce=nonce,
        )
        stored_receipt = dict(
            receipt,
            task_id=task_id,
            run_id=task['run_id'],
            executor_task=executor_task,
            pm_session=task['pm_session_id'],
            command_digest=command_digest,
            actions=actions,
        )
        stored_receipt['receipt_digest'] = hashlib.sha256(_json_bytes(stored_receipt)).hexdigest()
        saved = store.append_remote_test_receipt(task_id, stored_receipt)
        record_remote_test_event(store, task_id, saved)
        store.update_remote_test_dispatch(task_id, nonce, state='RECORDED')
        dispatches = task.get('remote_test_dispatches')
        if isinstance(dispatches, list):
            for item in dispatches:
                if isinstance(item, dict) and item.get('nonce') == nonce:
                    item['state'] = 'RECORDED'
                    break
        return saved

    if outcome[0] == 'OUTCOME':
        _, result, code, *rest = outcome
        out = _record_outcome(store, task_id, task, result=result, code=code,
                              executor_task=executor_task, sha=sha, modules=modules_list)
        if code != 'HOSTED_PENDING':
            store.update_remote_test_dispatch(task_id, nonce, state='RECORDED')
            dispatches = task.get('remote_test_dispatches')
            if isinstance(dispatches, list):
                for item in dispatches:
                    if isinstance(item, dict) and item.get('nonce') == nonce:
                        item['state'] = 'RECORDED'
                        break
        return out

    out = _record_outcome(store, task_id, task, result='UNKNOWN', code='HOSTED_RUN_UNKNOWN',
                          executor_task=executor_task, sha=sha, modules=modules_list)
    store.update_remote_test_dispatch(task_id, nonce, state='RECORDED')
    return out


def _run_hosted(store, task_id, task, *, executor_task, sha, ref, modules,
                get=None, post=None, get_bytes=None, now=None):
    """Run one GitHub-hosted remote test step: start dispatch then finish if run is found."""
    start_res = _hosted_start(
        store, task_id, task,
        executor_task=executor_task, sha=sha, ref=ref, modules=modules,
        get=get, post=post, now=now,
    )
    if start_res[0] == 'OUTCOME':
        _, result, code, *rest = start_res
        return _record_outcome(store, task_id, task, result=result, code=code,
                               executor_task=executor_task, sha=sha, modules=modules)
    if start_res[0] == 'RUN':
        _, run, nonce = start_res
        return _hosted_finish(
            store, task_id, task, run, nonce,
            executor_task=executor_task, sha=sha, ref=ref, modules=modules,
            get=get, get_bytes=get_bytes,
        )
    return _record_outcome(store, task_id, task, result='UNKNOWN', code='HOSTED_RUN_UNKNOWN',
                           executor_task=executor_task, sha=sha, modules=modules)


_load_part('remote_test_part5.py')
