"""One-shot read-only Codex diagnosis for a registered Executor D6."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .errors import LLMToolError
from . import task_completion, worker_dispatch
from .test_output_excerpts import _emitted_sensitive

REPORT_SECTIONS = ('Identity and source', 'Symptom and reproduction', 'Root Cause',
                   'Exactly what to modify', 'Verification', 'Risks and handoff',
                   'Outcome/cost')
_HEX40 = re.compile(r'[0-9a-f]{40}\Z')


def _refuse(code):
    error = LLMToolError('High-debug diagnosis refused; details omitted')
    error.high_debug_code = code
    raise error from None


def _create_d6(*, task_id, base_sha, repo_root, executor_task):
    lifecycle = Path(__file__).resolve().parents[1] / 'scripts' / 'd6_worktree_lifecycle.py'
    result = subprocess.run([sys.executable, '-B', str(lifecycle), '--task-id', task_id,
        '--agent-id', 'codex-high-debug-gpt-6.1-sol', '--session-ref', 'pending-' + task_id,
        '--lease-ref', f'high-debug {executor_task} at {base_sha}', '--branch',
        'llmtool/high-debug-' + task_id, '--base-sha', base_sha, '--repo-root', str(repo_root)],
        cwd=repo_root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True, timeout=180)
    marker = ('root=',)
    for field in result.stdout.decode('utf-8', 'replace').split():
        if field.startswith(marker[0]): return field[5:]
    _refuse('HIGH_DEBUG_D6_CREATE_FAILED')


def _run_codex(*, prompt, cwd, output_path):
    with open(output_path, 'wb') as output:
        return subprocess.run(['codex', 'exec', '-m', 'gpt-6.1-sol', '-c',
            'model_reasoning_effort=high', '-s', 'read-only', '--json', '-o', output_path, '-'],
            input=prompt.encode('utf-8'), cwd=cwd, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=1800)


def run_high_debug(*, store, task_id, executor_task, pr_number, family,
                   failed_corrections=None, early_reason=None, evidence=None,
                   runner=None, create_d6=None, repo_root=None,
                   read_registration=None):
    task = store.get(task_id)
    if type(task) is not dict or task.get('task_id') != task_id:
        _refuse('HIGH_DEBUG_TASK_INVALID')
    if (type(executor_task) is not str or
            re.fullmatch(r'[a-z0-9][a-z0-9-]{0,95}', executor_task) is None or
            type(pr_number) is not int or not 1 <= pr_number <= 9999999 or
            type(family) is not str or re.fullmatch(r'[a-z0-9._-]{1,64}', family) is None):
        _refuse('HIGH_DEBUG_ARGUMENT_INVALID')
    if ((type(failed_corrections) is int and failed_corrections >= 2 and early_reason is None) or
        (failed_corrections is None and type(early_reason) is str and 1 <= len(early_reason) <= 300)):
        pass
    else:
        _refuse('HIGH_DEBUG_ARGUMENT_INVALID')
    if evidence is not None:
        try:
            with open(evidence, 'rb') as evidence_stream:
                raw = evidence_stream.read(8193)
            if len(raw) > 8192: _refuse('HIGH_DEBUG_EVIDENCE_INVALID')
            evidence_text = raw.decode('utf-8')
            if _emitted_sensitive(evidence_text): _refuse('HIGH_DEBUG_EVIDENCE_SENSITIVE')
        except LLMToolError: raise
        except Exception: _refuse('HIGH_DEBUG_EVIDENCE_INVALID')
    else: evidence_text = ''
    policy = worker_dispatch.evaluate('codex-developer', None, task_id)
    if not policy.allowed:
        _refuse('HIGH_DEBUG_DISPATCH_REFUSED')
    completion = task.get('completion')
    ev = completion.get('evidence') if type(completion) is dict else None
    if type(ev) is not dict or completion.get('executor_task_id') != executor_task or type(ev.get('common_dir')) is not str:
        _refuse('HIGH_DEBUG_D6_UNAVAILABLE')
    try:
        reader = read_registration or task_completion.read_d6_registration
        registration = reader(executor_task, ev['common_dir'],
            allowed_states=task_completion._D6_EVIDENCE_STATES)
        head = registration.result_sha
        snap = task_completion.collect_git_snapshot(registration.root)
    except Exception:
        _refuse('HIGH_DEBUG_D6_UNAVAILABLE')
    if not isinstance(head, str) or _HEX40.fullmatch(head) is None or snap.head != head:
        _refuse('HIGH_DEBUG_HEAD_MISMATCH')
    root = str(registration.root)
    entries = task.get('high_debug', [])
    if type(entries) is not list or len(entries) >= 8: _refuse('HIGH_DEBUG_LIMIT')
    d6 = f'{executor_task}-hd{len(entries)+1}-{head[:7]}'
    create = create_d6 or _create_d6
    try: diagnosis_root = create(task_id=d6, base_sha=head, repo_root=repo_root or str(Path(registration.common_dir).parent), executor_task=executor_task)
    except Exception: _refuse('HIGH_DEBUG_D6_CREATE_FAILED')
    reason = f'failed corrections: {failed_corrections}' if failed_corrections is not None else f'early reason: {early_reason}'
    prompt = (f'Task: {task_id}\nPR: {pr_number}\nFamily: {family}\n{reason}\nExamined SHA: {head}\n'
              f'Evidence:\n{evidence_text}\n\nRequired report sections: {", ".join(REPORT_SECTIONS)}. '
              'Include exactly one of RCA_CONFIRMED, RCA_UNCONFIRMED, BLOCKED. Diagnose only; do not edit files.')
    report = ''; observed = 'UNKNOWN'; effort = 'UNKNOWN'; usage = 'UNKNOWN'
    try:
        with tempfile.NamedTemporaryFile(prefix='llmtool-high-debug-', delete=False) as stream:
            output_path = stream.name
        result = (runner or _run_codex)(prompt=prompt, cwd=diagnosis_root or d6, output_path=output_path)
        if result.returncode != 0: _refuse('HIGH_DEBUG_RUN_FAILED')
        events_raw = result.stdout
        if len(events_raw) > 1024 * 1024 or len(result.stderr) > 65536:
            _refuse('HIGH_DEBUG_RUN_FAILED')
        raw = Path(output_path).read_bytes()
        for line in events_raw.decode('utf-8', 'replace').splitlines():
            try: event=json.loads(line)
            except Exception: continue
            if type(event.get('model')) is str: observed=event['model']
            if type(event.get('effort')) is str: effort=event['effort']
            if type(event.get('model_reasoning_effort')) is str: effort=event['model_reasoning_effort']
            usage_candidate = event.get('usage')
            if type(usage_candidate) is dict: usage=usage_candidate
            turn = event.get('turn')
            if type(turn) is dict and type(turn.get('usage')) is dict: usage=turn['usage']
        report = raw.decode('utf-8')
        status_result = subprocess.run(['git', 'status', '--porcelain'], cwd=diagnosis_root or d6,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=60)
        if status_result.returncode != 0 or status_result.stdout:
            _refuse('HIGH_DEBUG_WROTE_FILES')
    except LLMToolError: raise
    except Exception: _refuse('HIGH_DEBUG_RUN_FAILED')
    finally:
        try: os.unlink(output_path)
        except Exception: pass
    if len(report.encode('utf-8')) > 16384 or _emitted_sensitive(report): _refuse('HIGH_DEBUG_REPORT_INVALID')
    if type(usage) is dict:
        usage = {'input': usage.get('input_tokens', usage.get('input')),
                 'cached': usage.get('cached_input_tokens', usage.get('cached')),
                 'output': usage.get('output_tokens', usage.get('output')),
                 'reasoning': usage.get('reasoning_tokens', usage.get('reasoning'))}
        if any(type(value) is not int or value < 0 for value in usage.values()):
            usage = 'UNKNOWN'
    tokens=[x for x in ('RCA_CONFIRMED','RCA_UNCONFIRMED','BLOCKED') if x in report]
    status=tokens[0] if len(tokens)==1 else 'REPORT_INVALID'
    if any(('## ' + h) not in report for h in REPORT_SECTIONS): status='REPORT_INVALID'
    entry={'family':family, 'failed_corrections':failed_corrections, 'early_reason':early_reason,
       'examined_sha':head,'d6':d6,'model_requested':'gpt-6.1-sol','model_observed':observed,
       'effort_observed':effort,'status':status,'report_sha256':hashlib.sha256(report.encode()).hexdigest(),
       'report':report,'usage':usage,'recorded_at':store._completion_now()}
    store.append_high_debug(task_id, entry)
    payload=entry['report_sha256'].encode()
    event_id=hashlib.sha256(('HIGH_DEBUG\0'+task_id+'\0'+head+'\0'+entry['report_sha256']).encode()).hexdigest()[:32]
    result=store.record_completion_event(task_id,event_id=event_id,run_id=task['run_id'],
       session_id='high-debug-'+d6,source='HIGH_DEBUG',source_sha=head,sequence=len(entries),
       payload_sha256=hashlib.sha256(payload).hexdigest())
    print(json.dumps({'state':'RECORDED','status':status,'d6':d6,'report_sha256':entry['report_sha256'],'event_id':event_id},sort_keys=True))
