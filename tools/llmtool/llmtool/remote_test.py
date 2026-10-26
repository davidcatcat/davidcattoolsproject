"""One-shot, bounded EC2 test execution for an admitted D6 Task result."""

import hashlib
import json
import os
import re
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .active_pm_inbox import record_remote_test_event
from .errors import LLMToolError
from .task_completion import read_d6_registration
from .task_store import TaskStore

_SHA = re.compile(r'[0-9a-f]{40}\Z')
_REF = re.compile(r'(?:refs/pull/[0-9]+/head|[A-Za-z0-9][A-Za-z0-9._/-]{0,99})\Z')
_MODULE = re.compile(r'test_[a-z0-9_]{1,80}\Z')
_TASK = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
_REMOTE_LIMIT = 4096
_SSH_TIMEOUT = 600
_RECEIPT_FIELDS = frozenset((
    'result', 'exit_code', 'source_sha', 'tree', 'fetch_head', 'modules',
    'identity', 'started_at', 'ended_at', 'elapsed_s', 'ran_total', 'final',
    'failures', 'log_bytes', 'log_sha256', 'remote_log_path'))

# This is deliberately a fixed program. Dynamic values arrive as individually
# validated argv words; no Task value becomes a command or script fragment.
_REMOTE_SCRIPT = r'''import hashlib,json,os,re,subprocess,sys,tempfile,time
from datetime import datetime,timezone
sha,ref,repo,python,*mods=sys.argv[1:]
R=re.compile(r"[0-9a-f]{40}\Z"); F=re.compile(r"(?:refs/pull/[0-9]+/head|[A-Za-z0-9][A-Za-z0-9._/-]{0,99})\Z"); M=re.compile(r"test_[a-z0-9_]{1,80}\Z")
if not R.fullmatch(sha) or not F.fullmatch(ref) or not mods or any(not M.fullmatch(x) for x in mods): sys.exit(40)
started=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"); begin=time.monotonic(); log=b""; result="FAIL"; code=1; tree=None; fetch=None; finals=[]; failures=[]; ran=0; uid=None; log_path=None
def run(argv,**kw): return subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False,**kw)
def nobody(work,argv,env=None):
    values={"PATH":"/usr/bin:/bin","HOME":work,"TMPDIR":work,"PYTHONNOUSERSITE":"1","PIP_DISABLE_PIP_VERSION_CHECK":"1","PIP_NO_INPUT":"1"}
    values.update(env or {})
    return ["sudo","-n","-u","nobody","env","-i","-C",work]+[k+"="+v for k,v in values.items()]+argv
stage="workspace"
try:
    f=run(["git","-C",repo,"fetch","--no-tags","--end-of-options","origin",ref],timeout=120)
    log+=f.stdout
    q=run(["git","-C",repo,"rev-parse","--verify","FETCH_HEAD"],timeout=10)
    fetch=q.stdout.decode("ascii","replace").strip() if q.returncode==0 else None
    if fetch != sha:
        result="SHA_MISMATCH"; code=41
    else:
        t=run(["git","-C",repo,"rev-parse",sha+"^{tree}"],timeout=10); log+=t.stdout
        tree=t.stdout.decode("ascii","replace").strip() if t.returncode==0 else None
        made=run(["sudo","-n","-u","nobody","mktemp","-d","/tmp/llmtool-remote-test.XXXXXX"],timeout=10)
        log+=made.stdout; work=made.stdout.decode("utf8","replace").strip()
        if made.returncode or not work.startswith("/tmp/llmtool-remote-test."): raise RuntimeError("workspace")
        uidrun=run(["sudo","-n","-u","nobody","id","-u"],timeout=5); uid=int(uidrun.stdout.strip())
        info=os.stat(work)
        if info.st_uid!=uid or info.st_mode&0o777!=0o700: raise RuntimeError("workspace")
        try:
            stage="archive"
            archive=run(["git","-C",repo,"archive",sha],timeout=30)
            log+=archive.stdout[:0]
            extracted=run(nobody(work,["tar","-xf","-","-C",work]),input=archive.stdout,timeout=30)
            log+=extracted.stdout
            if archive.returncode or extracted.returncode: raise RuntimeError("archive")
            stage="venv"
            v=run(nobody(work,[python,"-m","venv",work+"/venv"]),timeout=60); log+=v.stdout
            if v.returncode: raise RuntimeError("venv")
            stage="requirements"
            pip=run(nobody(work,[work+"/venv/bin/python","-m","pip","install","-r","tools/llmtool/requirements-ci.txt"]),timeout=180); log+=pip.stdout
            if pip.returncode: raise RuntimeError("requirements")
            stage="package"
            package=run(nobody(work,[work+"/venv/bin/python","-m","pip","install","--no-deps","-e","tools/llmtool"]),timeout=180); log+=package.stdout
            if package.returncode: raise RuntimeError("package")
            stage="tests"
            for mod in mods:
                test=run(nobody(work+"/tools/llmtool",[work+"/venv/bin/python","-m","unittest","discover","-s","tests","-p",mod+".py"]),timeout=180); log+=test.stdout
                lines=[x.decode("utf8","replace").strip() for x in test.stdout.splitlines() if b"Ran " in x or b"OK" in x or b"FAILED" in x or b"ERROR" in x]
                finals.append(next((line for line in reversed(lines)
                                    if line.endswith("OK") or line.endswith("FAILED") or
                                    line.endswith("ERROR")), "NO_FINAL"))
                ran+=int(re.search(rb"Ran (\d+) tests?",test.stdout).group(1)) if re.search(rb"Ran (\d+) tests?",test.stdout) else 0
                if test.returncode or not any(x.endswith("OK") for x in lines):
                    result="FAIL"; code=test.returncode or 1
                    failures.append(mod)
            if code==1 and len(failures)==0: result="PASS"; code=0
        finally:
            gone=run(["sudo","-n","-u","nobody","rm","-rf","--",work],timeout=10); log+=gone.stdout
except subprocess.TimeoutExpired as exc:
    result="TIMEOUT"; code=124; log+=exc.stdout or b""; log+=f"REMOTE_STAGE_FAILED {stage} {type(exc).__name__}\n".encode()
except Exception as exc:
    if result not in ("SHA_MISMATCH","TIMEOUT"): result="FAIL"
    code=code or 1; log+=f"REMOTE_STAGE_FAILED {stage} {type(exc).__name__}\n".encode()
log_dir_run=run(["sudo","-n","-u","nobody","mktemp","-d","/tmp/llmtool-remote-log.XXXXXX"],timeout=10)
log_dir=log_dir_run.stdout.decode("utf8","replace").strip()
if log_dir_run.returncode==0:
    nobody_uid=int(run(["sudo","-n","-u","nobody","id","-u"],timeout=5).stdout.strip())
    log_info=os.stat(log_dir)
    if log_info.st_uid!=nobody_uid or log_info.st_mode&0o777!=0o700: log_dir_run.returncode=1
log_path=log_dir+"/remote-test.log" if log_dir_run.returncode==0 else None
# Full output remains on EC2 in a private, unique directory owned by nobody.
saver=run(nobody(log_dir,[python,"-c","import sys; open(sys.argv[1], 'wb').write(sys.stdin.buffer.read())",log_path],{"HOME":log_dir,"TMPDIR":log_dir}),input=log,timeout=10) if log_path and re.fullmatch(r"/tmp/llmtool-remote-log\.[A-Za-z0-9]+/remote-test\.log",log_path) else None
if saver is None or saver.returncode: result="UNKNOWN"; code=1
ended=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
receipt={"result":result,"exit_code":code,"source_sha":sha,"tree":tree,"fetch_head":fetch,"modules":mods,"identity":{"uid":uid},"started_at":started,"ended_at":ended,"elapsed_s":round(time.monotonic()-begin,3),"ran_total":ran,"final":finals[-len(mods)*2:],"failures":failures[:10],"log_bytes":len(log),"log_sha256":hashlib.sha256(log).hexdigest(),"remote_log_path":log_path}
raw=json.dumps(receipt,separators=(",",":"),sort_keys=True).encode()
print(raw.decode() if len(raw)<=4096 else "{}")
'''


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _config(path=None):
    path = Path(path or Path.home() / '.config/llmtool/remote-test.json')
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or
                info.st_size > 4096):
            raise ValueError
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, json.JSONDecodeError):
        raise LLMToolError('ADMISSION_DENIED: remote test config is missing or invalid') from None
    if (type(value) is not dict or set(value) != {'host', 'user', 'repo_path', 'python'} or
            any(type(value[k]) is not str or not value[k] for k in value)):
        raise LLMToolError('ADMISSION_DENIED: remote test config is missing or invalid')
    if (re.fullmatch(r'[A-Za-z0-9.-]{1,253}', value['host']) is None or
            re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,31}', value['user']) is None or
            re.fullmatch(r'/[A-Za-z0-9._/-]{1,512}', value['repo_path']) is None or
            '..' in value['repo_path'].split('/') or
            re.fullmatch(r'/[A-Za-z0-9._/-]{1,256}', value['python']) is None or
            '..' in value['python'].split('/')):
        raise LLMToolError('ADMISSION_DENIED: remote test config is invalid')
    return value


def _admitted_modules(task):
    completion = task.get('completion')
    evidence = completion.get('evidence') if type(completion) is dict else None
    callback = task.get('developer', {}).get('direct_result_callback') if type(task.get('developer')) is dict else None
    callback_evidence = callback.get('evidence') if type(callback) is dict else None
    if type(callback) is dict and callback.get('state') == 'PM_EVIDENCE_READY' and type(callback_evidence) is dict:
        evidence = callback_evidence
    tests = evidence.get('tests') if type(evidence) is dict else None
    if type(tests) is not list:
        return ()
    names = set()
    for test in tests:
        path = test.get('path') if type(test) is dict else None
        match = re.fullmatch(r'tools/llmtool/tests/(test_[a-z0-9_]{1,80})\.py', path or '')
        if match:
            names.add(match.group(1))
    return tuple(sorted(names))


def _validate_remote_receipt(raw, *, sha, modules):
    if type(raw) is not bytes or not raw or len(raw) > _REMOTE_LIMIT:
        return None
    try:
        receipt = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if type(receipt) is not dict or set(receipt) != _RECEIPT_FIELDS:
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
            receipt['tree'] is None or len(receipt['final']) < len(modules) or
            any(not line.endswith('OK') for line in receipt['final'])):
        return None
    if receipt['result'] in ('FAIL', 'TIMEOUT', 'SHA_MISMATCH') and receipt['exit_code'] == 0:
        return None
    return receipt


def run_remote_test(task_id, *, executor_task, sha, ref, modules=(), pr=None,
                    store=None, config_path=None, transport=None):
    """Run one SSH session after local Task, D6, module and config admission."""
    store = TaskStore() if store is None else store
    task = store.get(task_id)
    if (type(executor_task) is not str or not _TASK.fullmatch(executor_task) or
            type(sha) is not str or not _SHA.fullmatch(sha) or
            type(ref) is not str or not _REF.fullmatch(ref)):
        return {'result': 'ADMISSION_DENIED', 'code': 'ARGUMENT_INVALID'}
    admitted = _admitted_modules(task)
    selected = tuple(sorted(set(modules or admitted)))
    if not selected or any(type(x) is not str or not _MODULE.fullmatch(x) or x not in admitted for x in selected):
        return {'result': 'ADMISSION_DENIED', 'code': 'MODULE_NOT_ADMITTED'}
    evidence = task.get('completion', {}).get('evidence', {}) if type(task.get('completion')) is dict else {}
    common_dir = evidence.get('common_dir') if type(evidence) is dict else None
    expected = evidence.get('result', {}).get('result_head') if type(evidence.get('result')) is dict else None
    if pr is not None:
        review = evidence.get('review_pr_ci') if type(evidence) is dict else None
        if (type(pr) is not int or not 1 <= pr <= 9999999 or type(review) is not dict or
                review.get('pr_number') != pr or type(review.get('head_sha')) is not str):
            return {'result': 'ADMISSION_DENIED', 'code': 'PR_HEAD_NOT_TASK_RESULT'}
        expected = review['head_sha']
    try:
        registration = read_d6_registration(executor_task, common_dir)
        if expected is not None and expected != registration.result_sha:
            return {'result': 'ADMISSION_DENIED', 'code': 'D6_RESULT_BINDING_MISMATCH'}
        expected = registration.result_sha
    except (LLMToolError, TypeError):
        expected = None
    if expected != sha:
        return {'result': 'ADMISSION_DENIED', 'code': 'SHA_NOT_TASK_RESULT'}
    try:
        config = _config(config_path)
    except LLMToolError:
        return {'result': 'ADMISSION_DENIED', 'code': 'CONFIG_INVALID'}
    argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            config['user'] + '@' + config['host'], 'python3', '-', sha, ref,
            config['repo_path'], config['python'], *selected]
    command_digest = hashlib.sha256(json.dumps(argv, separators=(',', ':')).encode() +
                                    _REMOTE_SCRIPT.encode()).hexdigest()
    for known in task.get('remote_test_receipts', []):
        if (known.get('executor_task') == executor_task and known.get('source_sha') == sha and
                known.get('modules') == list(selected) and
                known.get('command_digest') == command_digest):
            record_remote_test_event(store, task_id, known)
            return known
    if transport is None:
        def transport(argv, script, timeout):
            return subprocess.run(argv, input=script.encode(), stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=timeout, check=False)
    started = _now()
    try:
        result = transport(argv, _REMOTE_SCRIPT, _SSH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {'result': 'TIMEOUT', 'code': 'SSH_TIMEOUT'}
    except (OSError, ConnectionError):
        return {'result': 'UNAVAILABLE', 'code': 'SSH_UNAVAILABLE'}
    stdout = result.stdout if type(result.stdout) is bytes else str(result.stdout).encode()
    if result.returncode != 0 and not stdout:
        return {'result': 'UNAVAILABLE', 'code': 'SSH_FAILED'}
    parsed = _validate_remote_receipt(stdout, sha=sha, modules=selected)
    if parsed is None:
        return {'result': 'UNKNOWN', 'code': 'RECEIPT_INVALID'}
    receipt = dict(parsed, task_id=task_id, run_id=task['run_id'],
                   executor_task=executor_task, pm_session=task['pm_session_id'],
                   command_digest=command_digest)
    receipt['receipt_digest'] = hashlib.sha256(json.dumps(
        receipt, sort_keys=True, separators=(',', ':')).encode() + b'\n').hexdigest()
    saved = store.append_remote_test_receipt(task_id, receipt)
    record_remote_test_event(store, task_id, saved)
    return saved
