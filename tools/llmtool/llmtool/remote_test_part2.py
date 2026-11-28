from .remote_log_path import is_valid_remote_log_path

# #2281 row 9: fixed total-deadline reserve and SSH-timeout safety margin.
_REMOTE_WORK_RESERVE_SECONDS = 60
_REMOTE_SSH_TIMEOUT_MARGIN_SECONDS = 60


def _validate_remote_receipt(raw, *, sha, modules):
    if type(raw) is not bytes or not raw or len(raw) > _REMOTE_LIMIT:
        return None
    try:
        receipt = json.loads(raw.decode('utf-8'))
    except Exception:
        return None
    if (type(receipt) is not dict or
            set(receipt) not in _RECEIPT_SHAPES):
        return None
    if 'failure_excerpt' in receipt:
        excerpt = receipt['failure_excerpt']
        if (receipt['result'] == 'PASS' or not _valid_failure_excerpt(excerpt) or
                _emitted_sensitive(excerpt)):
            return None
    if (receipt['result'] not in ('PASS', 'FAIL', 'TIMEOUT', 'SHA_MISMATCH') or
            type(receipt['exit_code']) is not int or
            receipt['source_sha'] != sha or
            (receipt['fetch_head'] is not None and
             (type(receipt['fetch_head']) is not str or
              _SHA.fullmatch(receipt['fetch_head']) is None)) or
            (receipt['tree'] is not None and
             (type(receipt['tree']) is not str or _SHA.fullmatch(receipt['tree']) is None)) or
            type(receipt['modules']) is not list or receipt['modules'] != list(modules) or
            type(receipt['identity']) is not dict or set(receipt['identity']) != {'uid'} or
            (receipt['identity']['uid'] is not None and
             (type(receipt['identity']['uid']) is not int or receipt['identity']['uid'] < 0)) or
            type(receipt['elapsed_s']) not in (int, float) or receipt['elapsed_s'] < 0 or
            type(receipt['ran_total']) is not int or receipt['ran_total'] < 0 or
            type(receipt['final']) is not list or any(type(x) is not str or len(x) > 512 for x in receipt['final']) or
            type(receipt['failures']) is not list or len(receipt['failures']) > 10 or
            any(x not in modules for x in receipt['failures']) or
            type(receipt['log_bytes']) is not int or not 0 <= receipt['log_bytes'] <= 20_000_000 or
            type(receipt['log_sha256']) is not str or re.fullmatch(r'[0-9a-f]{64}', receipt['log_sha256']) is None or
            not is_valid_remote_log_path(receipt['remote_log_path'])):
        return None
    if receipt['result'] == 'SHA_MISMATCH' and receipt['fetch_head'] == sha:
        return None
    for stamp in ('started_at', 'ended_at'):
        if type(receipt[stamp]) is not str or re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', receipt[stamp]) is None:
            return None
    if receipt['result'] == 'PASS' and (receipt['exit_code'] != 0 or
            type(receipt['identity']['uid']) is not int or receipt['identity']['uid'] == 0 or
            receipt['fetch_head'] != sha or receipt['ran_total'] <= 0 or receipt['failures'] or
            receipt['tree'] is None or len(receipt['final']) < len(modules) or
            any(not (line.endswith('OK') or
                     re.fullmatch(r'OK \(skipped=[1-9][0-9]{0,5}\)', line))
                for line in receipt['final'])):
        return None
    if receipt['result'] in ('FAIL', 'TIMEOUT', 'SHA_MISMATCH') and receipt['exit_code'] == 0:
        return None
    if not valid_module_results(receipt):
        return None
    if (receipt['result'] == 'PASS' and 'module_results' in receipt and
            not pass_receipt_covers(receipt, modules)):
        return None
    return receipt


_MODULE_RESULT_KEYS = frozenset(('module', 'ran', 'skipped', 'final'))
_FINAL_SKIPPED = re.compile(r'skipped=(\d+)')
_PASSING_SKIP_FINAL = re.compile(r'OK \(skipped=[1-9][0-9]{0,5}\)')


def _final_skipped(final):
    match = _FINAL_SKIPPED.search(final)
    return int(match.group(1)) if match else 0


def _module_result_ok(entry, module, final):
    if type(module) is not str or type(final) is not str or len(final) > 512:
        return False
    if type(entry) is not dict or set(entry) != _MODULE_RESULT_KEYS:
        return False
    ran, skipped = entry['ran'], entry['skipped']
    if type(ran) is not int or type(skipped) is not int or ran < 0 or skipped < 0:
        return False
    if entry['module'] != module or entry['final'] != final:
        return False
    return skipped == _final_skipped(final)


def valid_module_results(receipt):
    """#2119 WP-1 part (a), not wired: check receipt module_results per design section 3.

    A receipt without the field is accepted, so pre-WP-1 receipts stay valid.
    Otherwise there is one {module, ran, skipped, final} entry per executed
    module, in modules order, and one per final line. ran and skipped are ints
    >= 0 with no rule between them. final equals the receipt final at the same
    index and skipped equals its skipped= count. sum(ran) equals ran_total. On
    PASS the entries cover every module and every final passes. The caller has
    already checked the other receipt fields.
    """
    if type(receipt) is not dict:
        return False
    if 'module_results' not in receipt:
        return True
    entries = receipt['module_results']
    modules, finals = receipt.get('modules'), receipt.get('final')
    if type(entries) is not list or type(modules) is not list or type(finals) is not list:
        return False
    if len(entries) != len(finals) or len(entries) > len(modules):
        return False
    if not all(map(_module_result_ok, entries, modules, finals)):
        return False
    total = receipt.get('ran_total')
    if type(total) is not int or sum(entry['ran'] for entry in entries) != total:
        return False
    if receipt.get('result') != 'PASS':
        return True
    passing = [final.endswith('OK') or _PASSING_SKIP_FINAL.fullmatch(final) for final in finals]
    return len(entries) == len(modules) and all(passing)


def pass_receipt_covers(receipt, required_modules):
    """Require proof that every required module ran in this successful invocation."""
    modules = receipt.get('modules')
    finals = receipt.get('final')
    if (receipt.get('result') != 'PASS' or type(modules) is not list or not modules or
            any(type(name) is not str or not _MODULE.fullmatch(name) for name in modules) or
            modules != sorted(set(modules)) or not set(required_modules) <= set(modules) or
            type(finals) is not list or len(finals) != len(modules) or
            any(type(line) is not str or not (
                line.endswith('OK') or _PASSING_SKIP_FINAL.fullmatch(line))
                for line in finals) or receipt.get('failures') != [] or
            type(receipt.get('ran_total')) is not int):
        return False
    entries = receipt.get('module_results')
    if entries is None:
        # An aggregate count is attributable only to a single-module legacy run.
        return len(modules) == 1 and receipt['ran_total'] > _final_skipped(finals[0])
    return valid_module_results(receipt) and all(
        entry['ran'] > entry['skipped'] for entry in entries)


_SSH_TIMEOUT_PHASES = {
    'workspace': ('PREPARATION', 'WORKSPACE'),
    'archive': ('PREPARATION', 'ARCHIVE'),
    'venv': ('PREPARATION', 'VENV'),
    'requirements': ('PREPARATION', 'REQUIREMENTS'),
    'package': ('PREPARATION', 'PACKAGE'),
    'tests': ('TESTS', 'TESTS'),
    'cleanup': ('CLEANUP_OR_RECEIPT', 'CLEANUP'),
    'receipt': ('CLEANUP_OR_RECEIPT', 'RECEIPT'),
}
_SSH_TIMEOUT_MARKER = re.compile(r'LLMTOOL_REMOTE_PHASE:([a-z]{1,24})')
_SSH_TIMEOUT_MODULE_MARKER = re.compile(r'LLMTOOL_REMOTE_MODULE:([1-9][0-9]{0,3})/([1-9][0-9]{0,3})')
_SSH_TIMEOUT_MAX_MODULES = 1024


def _ssh_timeout_diagnostic(exc, *, module_count=None):
    """Return a fixed, content-free phase diagnostic from TimeoutExpired stderr."""
    raw = getattr(exc, 'stderr', None)
    if type(raw) is bytes:
        text = raw[-65536:].decode('utf-8', 'replace')
    elif type(raw) is str:
        text = raw[-65536:]
    else:
        text = ''
    phase, stage = 'NONE', 'NONE'
    module_seen = False
    module_invalid = False
    expected_ordinal = 1
    observed_total = None
    last_ordinal = None
    for line in text.splitlines():
        stripped = line.strip()
        match = _SSH_TIMEOUT_MARKER.fullmatch(stripped)
        if match:
            mapped = _SSH_TIMEOUT_PHASES.get(match.group(1))
            if mapped is not None:
                if mapped[0] == 'TESTS' and (phase == 'TESTS' or module_seen):
                    module_invalid = True
                phase, stage = mapped
            continue
        if stripped.startswith('LLMTOOL_REMOTE_MODULE:'):
            module_seen = True
            if phase != 'TESTS':
                module_invalid = True
                continue
            mod_match = _SSH_TIMEOUT_MODULE_MARKER.fullmatch(stripped)
            if not mod_match:
                module_invalid = True
                continue
            ordinal = int(mod_match.group(1))
            total = int(mod_match.group(2))
            if (total > _SSH_TIMEOUT_MAX_MODULES or ordinal > total or
                    (module_count is not None and total != module_count) or
                    (observed_total is not None and total != observed_total) or
                    ordinal != expected_ordinal):
                module_invalid = True
                continue
            observed_total = total
            last_ordinal = ordinal
            expected_ordinal += 1
    diagnostic = {'phase': phase, 'stage': stage}
    if phase == 'TESTS' and module_seen:
        if module_invalid or last_ordinal is None or observed_total is None:
            diagnostic['module_stage'] = 'INVALID'
        else:
            diagnostic['module_stage'] = 'STARTED'
            diagnostic['module_ordinal'] = last_ordinal
            diagnostic['module_total'] = observed_total
    return diagnostic


def _record_outcome(store, task_id, task, *, result, code, executor_task, sha, modules,
                    diagnostic=None):
    safe_executor = executor_task if type(executor_task) is str and _TASK.fullmatch(executor_task) else None
    safe_sha = sha if type(sha) is str and _SHA.fullmatch(sha) else None
    safe_modules = sorted({item for item in modules
                           if type(item) is str and _MODULE.fullmatch(item)})[:32]
    outcome = {'result': result, 'code': code, 'executor_task': safe_executor,
               'sha': safe_sha, 'modules': safe_modules,
               'at': datetime.now(timezone.utc).isoformat(timespec='microseconds').replace(
                   '+00:00', 'Z')}
    try:
        persisted = store.append_remote_test_outcome(task_id, outcome)
    except (KeyError, LLMToolError):
        reply = dict(result=result, code=code, outcome=outcome,
                     persisted=False, delivery='NOT_DELIVERED_TASK_UNREADABLE', event_id=None)
        if diagnostic is not None:
            reply['diagnostic'] = diagnostic
        return reply
    saved = persisted['outcome']
    try:
        event = record_remote_test_outcome_event(
            store, task_id, saved, persisted['counter'])
        reply = dict(result=result, code=code, outcome=saved,
                     persisted=True, event_id=event['event_id'])
        if diagnostic is not None:
            reply['diagnostic'] = diagnostic
        return reply
    except (KeyError, LLMToolError):
        reply = dict(result=result, code=code, outcome=outcome,
                     persisted=True, delivery='NOT_DELIVERED_INBOX_UNAVAILABLE', event_id=None)
        if diagnostic is not None:
            reply['diagnostic'] = diagnostic
        return reply


def _fresh_pr_head(pr):
    """Return this PR's current head SHA from GitHub, or None if unreadable."""
    try:
        pr_data = _github_get(f'pulls/{pr}')
    except Exception:
        return None
    head = pr_data.get('head') if type(pr_data) is dict else None
    if (type(pr_data) is not dict or type(pr_data.get('number')) is not int or
            pr_data['number'] != pr or type(head) is not dict or
            type(head.get('sha')) is not str):
        return None
    return head['sha']


def _remote_test_timeouts(ssh_timeout=None):
    """Resolve and validate the #2281 row 9 remote test timeouts before SSH."""
    from . import repair_policy
    if ssh_timeout is None:
        ssh_timeout = _ssh_timeout()
    module_limit = repair_policy.remote_test_module_timeout_seconds()
    total_limit = repair_policy.remote_test_total_timeout_seconds()
    if (module_limit < 1 or total_limit < _REMOTE_WORK_RESERVE_SECONDS + 1 or
            module_limit > total_limit - _REMOTE_WORK_RESERVE_SECONDS):
        raise LLMToolError(
            'REMOTE_TEST_TIMEOUT_INVALID: module exceeds total minus reserve')
    if total_limit + _REMOTE_SSH_TIMEOUT_MARGIN_SECONDS >= ssh_timeout:
        raise LLMToolError(
            'REMOTE_TEST_TIMEOUT_INVALID: total plus margin reaches the SSH timeout')
    return module_limit, total_limit


def _ssh_argv(config, sha, ref, modules, module_timeout, total_timeout):
    """The one EC2 SSH command; every dynamic value is a validated argv word."""
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            config['user'] + '@' + config['host'], 'python3', '-', sha, ref,
            config['repo_path'], config['python'], str(module_timeout),
            str(total_timeout), *modules]


def _command_digest(argv):
    return hashlib.sha256(json.dumps(argv, separators=(',', ':')).encode() +
                          _REMOTE_SCRIPT.encode()).hexdigest()


def _ssh_transport(argv, script, timeout):
    return subprocess.run(argv, input=script.encode(), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, check=False)


def run_remote_test(task_id, *, executor_task, sha, ref, modules=(), pr=None,
                    store=None, config_path=None, transport=None,
                    hosted=False, get=None, post=None, get_bytes=None, now=None):
    """Run one SSH session after local Task, D6, module and config admission."""
    store = TaskStore() if store is None else store
    try:
        task = store.get(task_id)
    except (KeyError, LLMToolError):
        return {'result': 'UNKNOWN', 'code': 'TASK_UNREADABLE',
                'persisted': False, 'delivery': 'NOT_DELIVERED_TASK_UNREADABLE',
                'event_id': None}
    def denied(code):
        return _record_outcome(store, task_id, task, result='ADMISSION_DENIED',
                               code=code, executor_task=executor_task, sha=sha,
                               modules=modules if type(modules) in (tuple, list, set) else ())
    if (type(task.get('developer')) is dict and
            task['developer'].get('provider_profile') == 'claude' and
            task.get('abandonment') is not None):
        return denied('CLAUDE_TASK_ABANDONED')
    if (type(executor_task) is not str or not _TASK.fullmatch(executor_task) or
            type(sha) is not str or not _SHA.fullmatch(sha) or
            type(ref) is not str or not _REF.fullmatch(ref)):
        return denied('ARGUMENT_INVALID')
    evidence = task.get('completion', {}).get('evidence', {}) if type(task.get('completion')) is dict else {}
    callback = task.get('developer', {}).get('direct_result_callback') if type(task.get('developer')) is dict else None
    callback_evidence = callback.get('evidence') if type(callback) is dict else None
    if not (type(evidence) is dict and evidence.get('common_dir')):
        evidence = callback_evidence if type(callback_evidence) is dict else evidence
    common_dir = evidence.get('common_dir') if type(evidence) is dict else None
    result_head = evidence.get('result', {}).get('result_head') if type(evidence.get('result')) is dict else None
    expected = result_head
    review = evidence.get('review_pr_ci') if type(evidence) is dict else None
    if pr is not None:
        if type(pr) is not int or not 1 <= pr <= 9999999:
            return denied('RECEIPT_HEAD_UNRECORDED')
        if type(review) is dict and 'pr_number' in review:
            if (review.get('pr_number') != pr or
                    type(review.get('head_sha')) is not str):
                return denied('RECEIPT_HEAD_MISMATCH')
            expected = review['head_sha']
    try:
        registration = read_d6_registration(executor_task, common_dir)
        recorded_head = getattr(registration, 'result_sha', None)
        if type(recorded_head) is not str or _SHA.fullmatch(recorded_head) is None:
            return denied('RECEIPT_HEAD_UNRECORDED')
    except Exception:
        return denied('RECEIPT_HEAD_UNRECORDED')
    review_bound = type(review) is dict and 'pr_number' in review
    older_heads = []
    if pr is not None:
        for head in (result_head, expected):
            if head not in (None, recorded_head, *older_heads):
                older_heads.append(head)
    pr_head = None
    if pr is not None and (older_heads or not review_bound):
        pr_head = _fresh_pr_head(pr)
        if pr_head is None:
            return denied('RECEIPT_HEAD_MISMATCH' if older_heads
                          else 'RECEIPT_HEAD_UNRECORDED')
    if older_heads:
        # task_submission_cli imports this module and owns the repair chain.
        from .task_submission_cli import result_head_admitted
        if (pr_head != recorded_head or sha != recorded_head or
                not all(result_head_admitted(
                    task, store, evidence, executor_task=executor_task,
                    root=getattr(registration, 'root', None), recorded_head=head,
                    head=recorded_head) for head in older_heads)):
            return denied('RECEIPT_HEAD_MISMATCH')
        expected = recorded_head
    elif pr_head is not None:
        expected = pr_head
    if ((expected is not None and expected != recorded_head) or
            sha != recorded_head or (expected is not None and sha != expected)):
        return denied('RECEIPT_HEAD_MISMATCH')
    if pr is not None:
        store.record_published_pr(task_id, pr, recorded_head)
    if (type(modules) not in (tuple, list, set, frozenset) or
            any(type(module) is not str for module in modules)):
        return denied('MODULE_NOT_ADMITTED')
    admitted = remote_required_modules(task)
    if (admitted is not None and pr is not None and
            (not modules or not set(modules).issubset(admitted))):
        # Derived again even without extras: an unusable profile may be why.
        admitted = remote_required_modules(
            task, _pr_changed_test_modules_for_admission(pr, recorded_head))
    if admitted is None:
        # #2793: an unusable TestProfile admits nothing; not an admission denial.
        return _record_outcome(store, task_id, task, result='UNKNOWN',
                               code=TEST_PROFILE_INVALID, executor_task=executor_task,
                               sha=sha, modules=modules)
    selected = tuple(sorted(set(modules or admitted)))
    if not selected or not set(selected).issubset(admitted):
        return denied('MODULE_NOT_ADMITTED')
    if hosted:
        return _run_hosted(store, task_id, task, executor_task=executor_task,
                           sha=sha, ref=ref, modules=selected,
                           get=get, post=post, get_bytes=get_bytes, now=now)
    try:
        config = _config(config_path)
    except LLMToolError:
        return denied('CONFIG_INVALID')
    timeout = _ssh_timeout()
    module_timeout, total_timeout = _remote_test_timeouts(timeout)
    argv = _ssh_argv(config, sha, ref, selected, module_timeout, total_timeout)
    command_digest = _command_digest(argv)
    for known in reversed(task.get('remote_test_receipts', [])):
        if (known.get('executor_task') != executor_task or known.get('source_sha') != sha):
            continue
        if (known.get('modules') == list(selected) and known.get('command_digest') == command_digest and
                (known.get('result') == 'SHA_MISMATCH' or pass_receipt_covers(known, selected))):
            record_remote_test_event(store, task_id, known)
            return known
        # A newer same-head receipt supersedes older cached test evidence.
        break
    if transport is None:
        transport = _ssh_transport
    try:
        result = transport(argv, _REMOTE_SCRIPT, timeout)
    except subprocess.TimeoutExpired as exc:
        return _record_outcome(store, task_id, task, result='TIMEOUT', code='SSH_TIMEOUT',
                               executor_task=executor_task, sha=sha, modules=selected,
                               diagnostic=_ssh_timeout_diagnostic(exc, module_count=len(selected)))
    except (OSError, ConnectionError):
        return _record_outcome(store, task_id, task, result='UNAVAILABLE', code='SSH_UNAVAILABLE',
                               executor_task=executor_task, sha=sha, modules=selected)
    stdout = result.stdout if type(result.stdout) is bytes else str(result.stdout).encode()
    if result.returncode != 0 and not stdout:
        if result.returncode == 75:
            return _record_outcome(store, task_id, task, result='UNAVAILABLE', code='REMOTE_BUSY',
                                   executor_task=executor_task, sha=sha, modules=selected)
        if result.returncode == 76:
            return _record_outcome(store, task_id, task, result='UNAVAILABLE', code='REMOTE_LOCK_UNSAFE',
                                   executor_task=executor_task, sha=sha, modules=selected)
        return _record_outcome(store, task_id, task, result='UNAVAILABLE', code='SSH_FAILED',
                               executor_task=executor_task, sha=sha, modules=selected)
    parsed = _validate_remote_receipt(stdout, sha=sha, modules=selected)
    if (parsed is None or
            (parsed['result'] == 'PASS' and not pass_receipt_covers(parsed, selected))):
        return _record_outcome(store, task_id, task, result='UNKNOWN', code='RECEIPT_INVALID',
                               executor_task=executor_task, sha=sha, modules=selected)
    receipt = dict(parsed, task_id=task_id, run_id=task['run_id'],
                   executor_task=executor_task, pm_session=task['pm_session_id'],
                   command_digest=command_digest)
    receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
    saved = store.append_remote_test_receipt(task_id, receipt)
    record_remote_test_event(store, task_id, saved)
    return saved

