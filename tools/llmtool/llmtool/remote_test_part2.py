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
            (receipt['remote_log_path'] is not None and
             (type(receipt['remote_log_path']) is not str or
              re.fullmatch(r'/tmp/llmtool-remote-log\.[A-Za-z0-9]+/remote-test\.log',
                           receipt['remote_log_path']) is None))):
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


def _record_outcome(store, task_id, task, *, result, code, executor_task, sha, modules):
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
        return dict(result=result, code=code, outcome=outcome,
                    persisted=False, delivery='NOT_DELIVERED_TASK_UNREADABLE', event_id=None)
    saved = persisted['outcome']
    try:
        event = record_remote_test_outcome_event(
            store, task_id, saved, persisted['counter'])
        return dict(result=result, code=code, outcome=saved,
                    persisted=True, event_id=event['event_id'])
    except (KeyError, LLMToolError):
        return dict(result=result, code=code, outcome=outcome,
                    persisted=True, delivery='NOT_DELIVERED_INBOX_UNAVAILABLE', event_id=None)


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


def _ssh_argv(config, sha, ref, modules):
    """The one EC2 SSH command; every dynamic value is a validated argv word."""
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            config['user'] + '@' + config['host'], 'python3', '-', sha, ref,
            config['repo_path'], config['python'], *modules]


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
    admitted = remote_required_modules(task)
    if (type(modules) not in (tuple, list, set, frozenset) or
            any(type(module) is not str for module in modules)):
        return denied('MODULE_NOT_ADMITTED')
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
    argv = _ssh_argv(config, sha, ref, selected)
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
    timeout = _ssh_timeout()
    try:
        result = transport(argv, _REMOTE_SCRIPT, timeout)
    except subprocess.TimeoutExpired:
        return _record_outcome(store, task_id, task, result='TIMEOUT', code='SSH_TIMEOUT',
                               executor_task=executor_task, sha=sha, modules=selected)
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


# #1706 R9: reproduce a main incident's failing modules on EC2 ------------------

INCIDENT_UNKNOWN = 'INCIDENT_UNKNOWN'
INCIDENT_UNREADABLE = 'INCIDENT_UNREADABLE'
INCIDENT_NOT_OPEN = 'INCIDENT_NOT_OPEN'
INCIDENT_ARGUMENT_INVALID = 'INCIDENT_ARGUMENT_INVALID'
INCIDENT_SHA_NOT_RECORDED = 'INCIDENT_SHA_NOT_RECORDED'
INCIDENT_MODULE_NOT_FAILING = 'INCIDENT_MODULE_NOT_FAILING'
INCIDENT_MODULE_CAP = 'INCIDENT_MODULE_CAP'
INCIDENT_CONFIG_INVALID = 'CONFIG_INVALID'
INCIDENT_RECEIPT_SENSITIVE = 'RECEIPT_SENSITIVE'
INCIDENT_RECEIPT_INCONSISTENT = 'RECEIPT_INCONSISTENT'
_INCIDENT_RECORD_ATTEMPTS = 3


def _screen_incident_receipt(parsed):
    """Screen every output-derived field of a validated receipt (PR #1763, CWE-532).

    ``final`` lines and ``failure_excerpt`` are the only receipt strings taken
    from test output. Each goes through the sensitive-content screen; any hit
    refuses the whole receipt (None). A final line that passes the screen but
    is not in the unittest summary grammar (``main_incident_store.FINAL_SUMMARY``)
    is redacted to ``main_incident_store.FINAL_REDACTED``; the store checks both
    again before it records the receipt. The Task remote-test path does not use
    this function.
    """
    from . import main_incident_store as incident_store
    finals = parsed['final']
    excerpt = parsed.get('failure_excerpt')
    values = list(finals) + ([excerpt] if excerpt is not None else [])
    if any(_emitted_sensitive(value) for value in values):
        return None
    screened = dict(parsed)
    screened['final'] = [line if incident_store.FINAL_SUMMARY.fullmatch(line)
                         else incident_store.FINAL_REDACTED for line in finals]
    return screened


def _incident_denied(code, incident_id):
    """A refusal before anything ran; nothing is recorded anywhere."""
    from . import main_incident_store as incident_store
    safe = (incident_id if type(incident_id) is str and
            incident_store.INCIDENT_ID.fullmatch(incident_id) else None)
    return {'result': 'ADMISSION_DENIED', 'code': code, 'incident_id': safe,
            'persisted': False}


def _record_incident_result(incident_store, incident_id, entry, root):
    """Append entry to the incident, rereading after a concurrent change.

    The record is forward only, so a newer version still holds every SHA and
    failing module the run was admitted on; the store re-checks that anyway.
    """
    code = incident_store.INCIDENT_RECORD_CONFLICT
    for _ in range(_INCIDENT_RECORD_ATTEMPTS):
        try:
            current = incident_store.get(incident_id, root)
            saved = incident_store.append_remote_test(
                incident_id, current['record_sha256'], entry, root)
        except LLMToolError as exc:
            code = getattr(exc, 'incident_code', None) or incident_store.INCIDENT_RECORD_UNAVAILABLE
            if code == incident_store.INCIDENT_RECORD_CONFLICT:
                continue
            break
        return dict(entry, incident_id=incident_id, persisted=True,
                    record_sha256=saved['record_sha256'])
    return dict(entry, incident_id=incident_id, persisted=False, record_code=code)

