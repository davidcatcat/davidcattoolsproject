"""One-shot, bounded EC2 test execution for an admitted D6 Task result."""

import hashlib
import json
import os
import re
import secrets
import selectors
import shutil
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .active_pm_inbox import record_remote_test_event, record_remote_test_outcome_event
from .errors import LLMToolError
from . import github_identity
from .task_completion import _github_get, _trusted_slug, read_d6_registration
from .task_store import TaskStore, _emitted_sensitive, _json_bytes

_SHA = re.compile(r'[0-9a-f]{40}\Z')
_REF = re.compile(r'(?:refs/pull/[0-9]+/head|[A-Za-z0-9][A-Za-z0-9._/-]{0,99})\Z')
_MODULE = re.compile(r'test_[a-z0-9_]{1,80}\Z')
_TASK = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
_NONCE = re.compile(r'[0-9a-f]{32}\Z')
_HOSTED_WORKFLOW_PATH = '.github/workflows/llmtool-remote-test.yml'
_HOSTED_JOB_NAME = 'remote-test'
_HOSTED_LABELS = ('ubuntu-24.04',)
_HOSTED_RUNNER_GROUP = 'GitHub Actions'
_HOSTED_APP_ID = 15368
_HOSTED_ACTORS = (github_identity.owner_login(), github_identity.ai_login())
_HOSTED_TITLE_PREFIX = 'llmtool remote-test '
_HOSTED_ARTIFACT_PREFIX = 'remote-test-receipt-'
_HOSTED_ZIP_LIMIT = 65536
_HOSTED_MEMBER_LIMIT = 32768
_HOSTED_ZIP_MEMBER_LIMIT = 32768
_ZIP_LIMIT = 65536
_ZIP_MEMBER_LIMIT = 32768
_MEMBER_LIMIT = 32768
_HOSTED_DISPATCH_PATH = 'actions/workflows/llmtool-remote-test.yml/dispatches'
_GH_PATH = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'
_REMOTE_LIMIT = 32768
_FAILURE_EXCERPT_LIMIT = 4096
_FAILURE_EXCERPT_LINES = 40
_SSH_TIMEOUT = 600
_MAX_REMOTE_MODULES = 8
_RECEIPT_FIELDS = frozenset((
    'result', 'exit_code', 'source_sha', 'tree', 'fetch_head', 'modules',
    'identity', 'started_at', 'ended_at', 'elapsed_s', 'ran_total', 'final',
    'failures', 'log_bytes', 'log_sha256', 'remote_log_path'))
_RECEIPT_FIELDS_WITH_EXCERPT = _RECEIPT_FIELDS | frozenset(('failure_excerpt',))


def _valid_failure_excerpt(value):
    if type(value) is not str or len(value.splitlines()) > _FAILURE_EXCERPT_LINES:
        return False
    try:
        return len(value.encode('utf-8')) <= _FAILURE_EXCERPT_LIMIT
    except UnicodeError:
        return False

# This is deliberately a fixed program. Dynamic values arrive as individually
# validated argv words; no Task value becomes a command or script fragment.
_REMOTE_SCRIPT = r'''import fcntl,hashlib,json,math,os,re,shutil,stat,subprocess,sys,tempfile,time
from datetime import datetime,timezone
sha,ref,repo,python,*mods=sys.argv[1:]
R=re.compile(r"[0-9a-f]{40}\Z"); F=re.compile(r"(?:refs/pull/[0-9]+/head|[A-Za-z0-9][A-Za-z0-9._/-]{0,99})\Z"); M=re.compile(r"test_[a-z0-9_]{1,80}\Z")
SENSITIVE=re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|(?:(?<![A-Za-z0-9_])(?:AKIA|ASIA)[A-Z0-9]{16}\b|(?<!(?-i:[A-Za-z0-9_]))(?:AKIA|ASIA)[A-Z0-9]{16}(?=(?-i:[^\W\x00-\x7f])))|\bsk-[A-Za-z0-9_-]{12,}|\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|\bxox[abprs]-[A-Za-z0-9-]{20,}|\b(?:bearer|basic)\s+\S+|\b[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@|\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b|[\"']?\b(?:api[_-]?key|password|passwd|token|secret|access[_-]?token|client[_-]?secret|aws_secret_access_key)[\"']?\s*[:=]",re.I)
if not R.fullmatch(sha) or not F.fullmatch(ref) or not mods or len(mods)>8 or any(not M.fullmatch(x) for x in mods): sys.exit(40)
started=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"); begin=time.monotonic(); work_deadline=begin+480; deadline=begin+540; log=b""; result="FAIL"; code=1; tree=None; fetch=None; finals=[]; failures=[]; ran=0; uid=None; log_path=None; work=None
def failure_excerpt(outputs,work,home):
    blocks=[]
    def safe_line(line):
        line=line.replace(work,"<REMOTE_WORKDIR>").replace(home,"<REMOTE_HOME>")
        if not SENSITIVE.search(line): return line
        # A fully-qualified unittest test id has three dot-separated parts,
        # which can resemble the broad JWT detector. Ignore only that shape
        # inside a conventional FAIL/ERROR header, while still screening any
        # credential marker elsewhere on the header.
        if re.fullmatch(r"(?:FAIL|ERROR): [A-Za-z0-9_.-]+ \([A-Za-z0-9_.]+\)",line):
            without_test_id=re.sub(r"\([A-Za-z0-9_.]+\)","()",line)
            if not SENSITIVE.search(without_test_id): return line
        return None
    for output in outputs:
        lines=output.decode("utf8","replace").splitlines()
        index=0
        while index<len(lines):
            if not re.match(r"^(?:FAIL|ERROR): ",lines[index]): index+=1; continue
            block=[]
            header=safe_line(lines[index])
            if header is not None: block.append(header)
            index+=1
            # unittest places a dashed separator between the failure header
            # and its traceback. Skip it, then retain the traceback up to the
            # next report separator (or the closing dashes before the summary).
            if index<len(lines) and re.match(r"^(?:={10,}|-{10,})$",lines[index]):
                index+=1
            while index<len(lines) and not re.match(r"^(?:={10,}|-{10,})$",lines[index]):
                line=safe_line(lines[index])
                if line is not None: block.append(line)
                index+=1
            blocks.extend(block)
    kept=[]; size=0
    for line in blocks:
        encoded=(line+"\n").encode("utf8")
        if len(kept)>=40 or size+len(encoded)>4096: break
        kept.append(line); size+=len(encoded)
    return "\n".join(kept)
def run(argv,limit=None,cleanup=False,**kw):
    end=deadline if cleanup else work_deadline
    left=end-time.monotonic()
    if left<=0: raise subprocess.TimeoutExpired(argv,0)
    seconds=min(left,limit) if limit is not None else left
    capture_stderr=kw.pop("capture_stderr",False)
    return subprocess.run(argv,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE if capture_stderr else subprocess.STDOUT,
        check=False,timeout=max(.1,seconds),**kw)
def nobody(work,argv,env=None):
    values={"PATH":"/usr/bin:/bin","HOME":work+"/.home","TMPDIR":work+"/.tmp","GIT_OPTIONAL_LOCKS":"0","PYTHONNOUSERSITE":"1","PIP_DISABLE_PIP_VERSION_CHECK":"1","PIP_NO_INPUT":"1"}
    values.update(env or {})
    return ["sudo","-n","-u","nobody","env","-i","-C",work]+[k+"="+v for k,v in values.items()]+argv
def nobody_run(work,argv,limit=None,env=None,cleanup=False,**kw):
    end=deadline if cleanup else work_deadline
    left=end-time.monotonic()
    if left<=4: raise subprocess.TimeoutExpired(argv,0)
    seconds=min(left-1,limit) if limit is not None else left-1
    command_seconds=max(1,math.floor(seconds)-2)
    wrapped=nobody(work,["/usr/bin/timeout","--signal=TERM","--kill-after=2s",str(command_seconds),*argv],env)
    return run(wrapped,limit=command_seconds+4,cleanup=cleanup,**kw)
def nobody_cli(argv,limit=10,cleanup=False):
    end=deadline if cleanup else work_deadline
    left=end-time.monotonic()
    if left<=4: raise subprocess.TimeoutExpired(argv,0)
    command_seconds=max(1,math.floor(min(left-1,limit))-2)
    return run(["sudo","-n","-u","nobody","/usr/bin/timeout","--signal=TERM","--kill-after=2s",str(command_seconds),*argv],limit=command_seconds+4,cleanup=cleanup)
def open_lock(path):
    try: fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    except OSError: sys.exit(76)
    try: info=os.fstat(fd)
    except OSError: os.close(fd); sys.exit(76)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or
            info.st_mode&0o777!=0o600 or info.st_nlink!=1):
        os.close(fd); sys.exit(76)
    try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: os.close(fd); raise
    return fd
def final_line(lines):
    return next((line for line in reversed(lines)
                 if line.endswith("OK") or line.startswith("FAILED (") or
                 re.fullmatch(r"OK(?: \((?:skipped=\d+|expected failures=\d+)(?:, (?:skipped=\d+|expected failures=\d+))*\))?", line) or
                 line.endswith("FAILED") or line.startswith("ERROR")), "NO_FINAL")
def passing_final(line):
    return line.endswith("OK") or re.fullmatch(r"OK \(skipped=[1-9][0-9]{0,5}\)", line) is not None
try: lock_fd=open_lock(os.path.expanduser("~")+"/.llmtool-remote-test.lock")
except BlockingIOError: sys.exit(75)
cleanup_source="""import os,re,shutil,stat,sys,time
uid=os.getuid(); now=time.time(); keep=sys.argv[1] if len(sys.argv)>1 else ""; test=re.compile(r"llmtool-remote-test\\.[A-Za-z0-9]+\\Z"); logs=re.compile(r"llmtool-remote-log\\.[A-Za-z0-9]+\\Z"); owned_dirs=[]
for entry in os.scandir("/tmp"):
 if not (test.fullmatch(entry.name) or logs.fullmatch(entry.name)): continue
 try:
  info=entry.stat(follow_symlinks=False)
  if not stat.S_ISDIR(info.st_mode) or info.st_uid!=uid or info.st_mode&0o777!=0o700: continue
  if now-info.st_mtime>86400: shutil.rmtree(entry.path)
  else: owned_dirs.append((info.st_mtime,entry.path,entry.path==keep or entry.path+"/remote-test.log"==keep))
 except OSError: pass
owned_dirs.sort(key=lambda item:(item[2],item[0]),reverse=True)
for _,path,_ in owned_dirs[20:]:
 try: shutil.rmtree(path)
 except OSError: pass"""
def cleanup_stale():
    return nobody_cli([python,"-c",cleanup_source,log_path or ""],limit=20,cleanup=True)
stage="workspace"; failed_outputs=[]
try:
    cleanup_stale()
    f=run(["git","-C",repo,"fetch","--no-tags","--end-of-options","origin",ref],limit=120)
    log+=f.stdout
    q=run(["git","-C",repo,"rev-parse","--verify","FETCH_HEAD"],limit=10)
    fetch=q.stdout.decode("ascii","replace").strip() if q.returncode==0 else None
    if fetch != sha:
        result="SHA_MISMATCH"; code=41
    else:
        t=run(["git","-C",repo,"rev-parse",sha+"^{tree}"],limit=10); log+=t.stdout
        tree=t.stdout.decode("ascii","replace").strip() if t.returncode==0 else None
        made=nobody_cli(["mktemp","-d","/tmp/llmtool-remote-test.XXXXXX"])
        log+=made.stdout; work=made.stdout.decode("utf8","replace").strip()
        if made.returncode or not re.fullmatch(r"/tmp/llmtool-remote-test\.[A-Za-z0-9]+",work): raise RuntimeError("workspace")
        uidrun=nobody_cli(["id","-u"],limit=5); uid=int(uidrun.stdout.strip())
        info=os.stat(work)
        if info.st_uid!=uid or info.st_mode&0o777!=0o700: raise RuntimeError("workspace")
        try:
            stage="archive"
            dirs=nobody_cli(["mkdir","-m","700","--",work+"/.home",work+"/.tmp"])
            log+=dirs.stdout
            if dirs.returncode: raise RuntimeError("environment")
            archive=run(["git","-C",repo,"archive",sha],limit=30)
            log+=archive.stdout[:0]
            extracted=nobody_run(work,["tar","-xf","-","-C",work],limit=30,input=archive.stdout)
            log+=extracted.stdout
            if archive.returncode or extracted.returncode: raise RuntimeError("archive")
            stage="venv"
            v=nobody_run(work,[python,"-m","venv",work+"/venv"],limit=60); log+=v.stdout
            if v.returncode: raise RuntimeError("venv")
            stage="requirements"
            pip=nobody_run(work,[work+"/venv/bin/python","-m","pip","install","-r","tools/llmtool/requirements-ci.txt"],limit=180); log+=pip.stdout
            if pip.returncode: raise RuntimeError("requirements")
            stage="package"
            package=nobody_run(work,[work+"/venv/bin/python","-m","pip","install","--no-deps","-e","tools/llmtool"],limit=180); log+=package.stdout
            if package.returncode: raise RuntimeError("package")
            stage="tests"
            for index,mod in enumerate(mods):
                allowance=(deadline-time.monotonic())/(len(mods)-index)-3
                if allowance<=1: raise subprocess.TimeoutExpired(mod,0)
                test=nobody_run(work+"/tools/llmtool",[work+"/venv/bin/python","-m","unittest","discover","-s","tests","-p",mod+".py"],limit=min(180,allowance),env={"HOME":work+"/.home","TMPDIR":work+"/.tmp"},capture_stderr=True)
                test_output=test.stdout+(test.stderr or b""); log+=test_output
                lines=[x.decode("utf8","replace").strip() for x in test_output.splitlines() if b"Ran " in x or b"OK" in x or b"FAILED" in x or b"ERROR" in x]
                final=final_line(lines)
                finals.append(final)
                ran+=int(re.search(rb"Ran (\d+) tests?",test_output).group(1)) if re.search(rb"Ran (\d+) tests?",test_output) else 0
                if test.returncode==124:
                    result="TIMEOUT"; code=124; failures.append(mod); break
                if test.returncode or not passing_final(final):
                    result="FAIL"; code=test.returncode or 1
                    failures.append(mod); failed_outputs.extend((test.stdout,test.stderr or b""))
            if result!="TIMEOUT" and code==1 and len(failures)==0 and ran>0: result="PASS"; code=0
        finally:
            if work:
                gone=nobody_cli(["rm","-rf","--",work],cleanup=True); log+=gone.stdout
except subprocess.TimeoutExpired as exc:
    result="TIMEOUT"; code=124; log+=exc.stdout or b""; log+=f"REMOTE_STAGE_FAILED {stage} {type(exc).__name__}\n".encode()
except Exception as exc:
    if result not in ("SHA_MISMATCH","TIMEOUT"): result="FAIL"
    code=code or 1; log+=f"REMOTE_STAGE_FAILED {stage} {type(exc).__name__}\n".encode()
log_dir_run=nobody_cli(["mktemp","-d","/tmp/llmtool-remote-log.XXXXXX"],cleanup=True)
log_dir=log_dir_run.stdout.decode("utf8","replace").strip()
if log_dir_run.returncode==0:
    nobody_uid=int(nobody_cli(["id","-u"],limit=5,cleanup=True).stdout.strip())
    log_info=os.stat(log_dir)
    if log_info.st_uid!=nobody_uid or log_info.st_mode&0o777!=0o700: log_dir_run.returncode=1
log_path=log_dir+"/remote-test.log" if log_dir_run.returncode==0 else None
# Full output remains on EC2 in a private, unique directory owned by nobody.
saver=nobody_run(log_dir,[python,"-c","import sys; open(sys.argv[1], 'wb').write(sys.stdin.buffer.read())",log_path],limit=10,env={"HOME":log_dir,"TMPDIR":log_dir},cleanup=True,input=log) if log_path and re.fullmatch(r"/tmp/llmtool-remote-log\.[A-Za-z0-9]+/remote-test\.log",log_path) else None
if saver is None or saver.returncode: result="UNKNOWN"; code=1
cleanup_stale()
ended=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
receipt={"result":result,"exit_code":code,"source_sha":sha,"tree":tree,"fetch_head":fetch,"modules":mods,"identity":{"uid":uid},"started_at":started,"ended_at":ended,"elapsed_s":round(time.monotonic()-begin,3),"ran_total":ran,"final":finals[-len(mods)*2:],"failures":failures[:10],"log_bytes":len(log),"log_sha256":hashlib.sha256(log).hexdigest(),"remote_log_path":log_path}
if result!="PASS": receipt["failure_excerpt"]=failure_excerpt(failed_outputs,work or "<REMOTE_WORKDIR>",os.path.expanduser("~"))
raw=json.dumps(receipt,separators=(",",":"),sort_keys=True).encode()
print(raw.decode() if len(raw)<=32768 else "{}")
os.close(lock_fd)
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


def _task_path_modules(task):
    developer = task.get('developer') if type(task.get('developer')) is dict else {}
    declared_paths = set()
    for field in ('read_paths', 'write_paths'):
        paths = developer.get(field)
        if type(paths) is list:
            declared_paths.update(
                path for path in paths
                if type(path) is str and
                re.fullmatch(r'tools/llmtool/tests/test_[a-z0-9_]{1,80}\.py', path))
    return tuple(sorted(Path(path).stem for path in declared_paths))


def _admitted_modules(task):
    declared = _task_path_modules(task)
    if declared:
        return declared
    completion = task.get('completion')
    evidence = completion.get('evidence') if type(completion) is dict else None
    callback = task.get('developer', {}).get('direct_result_callback') if type(task.get('developer')) is dict else None
    callback_evidence = callback.get('evidence') if type(callback) is dict else None
    if (type(callback) is dict and type(callback_evidence) is dict and
            (callback.get('state') == 'PM_EVIDENCE_READY' or
             (callback.get('state') == 'WAITING_EXECUTOR' and
              callback.get('missing_gates') == ['TEST_FAILED'] and
              callback_evidence.get('verification_state') == 'FAILED'))):
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
    if (type(receipt) is not dict or
            set(receipt) not in (_RECEIPT_FIELDS, _RECEIPT_FIELDS_WITH_EXCERPT)):
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
    return receipt


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


def run_remote_test(task_id, *, executor_task, sha, ref, modules=(), pr=None,
                    store=None, config_path=None, transport=None):
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
    admitted = _task_path_modules(task)
    selected = tuple(sorted(set(modules or admitted)))
    if (not selected or len(selected) > _MAX_REMOTE_MODULES or
            any(type(x) is not str or not _MODULE.fullmatch(x) or x not in admitted for x in selected)):
        return denied('MODULE_NOT_ADMITTED')
    try:
        config = _config(config_path)
    except LLMToolError:
        return denied('CONFIG_INVALID')
    argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            config['user'] + '@' + config['host'], 'python3', '-', sha, ref,
            config['repo_path'], config['python'], *selected]
    command_digest = hashlib.sha256(json.dumps(argv, separators=(',', ':')).encode() +
                                    _REMOTE_SCRIPT.encode()).hexdigest()
    for known in task.get('remote_test_receipts', []):
        if (known.get('executor_task') == executor_task and known.get('source_sha') == sha and
                known.get('modules') == list(selected) and
                known.get('command_digest') == command_digest and
                known.get('result') in ('PASS', 'SHA_MISMATCH')):
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
    if parsed is None:
        return _record_outcome(store, task_id, task, result='UNKNOWN', code='RECEIPT_INVALID',
                               executor_task=executor_task, sha=sha, modules=selected)
    receipt = dict(parsed, task_id=task_id, run_id=task['run_id'],
                   executor_task=executor_task, pm_session=task['pm_session_id'],
                   command_digest=command_digest)
    receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
    saved = store.append_remote_test_receipt(task_id, receipt)
    record_remote_test_event(store, task_id, saved)
    return saved


def _new_nonce():
    """Return 32 lowercase hex characters from the secrets module."""
    return secrets.token_hex(16)


def _hosted_post(path, body):
    """Make one POST to the single accepted hosted dispatch path using gh CLI."""
    if path != _HOSTED_DISPATCH_PATH:
        raise ValueError(f'Unsupported dispatch path: {path}')
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
    if not (1 <= len(modules) <= 8):
        raise ValueError('modules must contain 1 to 8 items')
    if any(type(m) is not str or not _MODULE.fullmatch(m) for m in modules):
        raise ValueError('Each module must match _MODULE')
    if list(modules) != sorted(set(modules)):
        raise ValueError('modules must be 1 to 8 sorted distinct items')

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


