import ast
import hashlib
import io
import json
import os
import re
import secrets
import selectors
import shutil
import stat
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .active_pm_inbox import record_remote_test_event, record_remote_test_outcome_event
from .errors import LLMToolError
from . import ai_github_exec, github_identity
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
_HOSTED_PAGE_LIMIT = 10
_HOSTED_ARTIFACT_PAGE_LIMIT = 10
_ARTIFACT_ZIP_PATH = re.compile(r'actions/artifacts/[0-9]+/zip\Z')
_HOSTED_DISPATCH_PATH = 'actions/workflows/llmtool-remote-test.yml/dispatches'
_GH_PATH = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'
_REMOTE_LIMIT = 32768
_FAILURE_EXCERPT_LIMIT = 4096
_FAILURE_EXCERPT_LINES = 40
_SSH_TIMEOUT = 1800
_RECEIPT_FIELDS = frozenset((
    'result', 'exit_code', 'source_sha', 'tree', 'fetch_head', 'modules',
    'identity', 'started_at', 'ended_at', 'elapsed_s', 'ran_total', 'final',
    'failures', 'log_bytes', 'log_sha256', 'remote_log_path'))
_RECEIPT_FIELDS_WITH_EXCERPT = _RECEIPT_FIELDS | frozenset(('failure_excerpt',))
# #2119 WP-1: module_results is optional until every producer emits it.
_MODULE_RESULTS_FIELD = frozenset(('module_results',))
_RECEIPT_SHAPES = (_RECEIPT_FIELDS, _RECEIPT_FIELDS_WITH_EXCERPT)
_RECEIPT_SHAPES += tuple(shape | _MODULE_RESULTS_FIELD for shape in _RECEIPT_SHAPES)


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
if len(sys.argv)<7: sys.exit(40)
sha,ref,repo,python,ml,tl,*mods=sys.argv[1:]
R=re.compile(r"[0-9a-f]{40}\Z"); F=re.compile(r"(?:refs/pull/[0-9]+/head|[A-Za-z0-9][A-Za-z0-9._/-]{0,99})\Z"); M=re.compile(r"test_[a-z0-9_]{1,80}\Z"); D=re.compile(r"[0-9]{1,9}\Z")
SENSITIVE=re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|(?:(?<![A-Za-z0-9_])(?:AKIA|ASIA)[A-Z0-9]{16}\b|(?<!(?-i:[A-Za-z0-9_]))(?:AKIA|ASIA)[A-Z0-9]{16}(?=(?-i:[^\W\x00-\x7f])))|\bsk-[A-Za-z0-9_-]{12,}|\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|\bxox[abprs]-[A-Za-z0-9-]{20,}|\b(?:bearer|basic)\s+\S+|\b[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@|\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b|[\"']?\b(?:api[_-]?key|password|passwd|token|secret|access[_-]?token|client[_-]?secret|aws_secret_access_key)[\"']?\s*[:=]",re.I)
if not R.fullmatch(sha) or not F.fullmatch(ref) or not mods or any(not M.fullmatch(x) for x in mods) or not D.fullmatch(ml) or not D.fullmatch(tl) or int(ml)<1 or int(tl)<61 or int(ml)>int(tl)-60: sys.exit(40)
started=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"); begin=time.monotonic(); work_deadline=begin+int(tl)-60; deadline=begin+int(tl); log=b""; result="FAIL"; code=1; tree=None; fetch=None; finals=[]; failures=[]; ran=0; uid=None; log_path=None; work=None
module_results=[]
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
def module_result(mod,test_output,final):
    ran=0
    for line in test_output.decode("utf8","replace").splitlines():
        found=re.match(r"Ran (\d+) tests? in ",line.strip())
        if found: ran=int(found.group(1))
    skip=re.search(r"skipped=(\d+)",final)
    return {"module":mod,"ran":ran,"skipped":int(skip.group(1)) if skip else 0,"final":final}
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
def mark(name):
    sys.stderr.write("LLMTOOL_REMOTE_PHASE:"+name+"\n")
    sys.stderr.flush()
def mark_module(index,total):
    sys.stderr.write(f"LLMTOOL_REMOTE_MODULE:{index}/{total}\n")
    sys.stderr.flush()
stage="workspace"; failed_outputs=[]
try:
    mark("workspace")
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
            stage="archive"; mark("archive")
            dirs=nobody_cli(["mkdir","-m","700","--",work+"/.home",work+"/.tmp"])
            log+=dirs.stdout
            if dirs.returncode: raise RuntimeError("environment")
            archive=run(["git","-C",repo,"archive",sha],limit=30)
            log+=archive.stdout[:0]
            extracted=nobody_run(work,["tar","-xf","-","-C",work],limit=30,input=archive.stdout)
            log+=extracted.stdout
            if archive.returncode or extracted.returncode: raise RuntimeError("archive")
            stage="venv"; mark("venv")
            v=nobody_run(work,[python,"-m","venv",work+"/venv"],limit=60); log+=v.stdout
            if v.returncode: raise RuntimeError("venv")
            stage="requirements"; mark("requirements")
            pip=nobody_run(work,[work+"/venv/bin/python","-m","pip","install","-r","tools/llmtool/requirements-ci.txt"],limit=180); log+=pip.stdout
            if pip.returncode: raise RuntimeError("requirements")
            stage="package"; mark("package")
            package=nobody_run(work,[work+"/venv/bin/python","-m","pip","install","--no-deps","-e","tools/llmtool"],limit=180); log+=package.stdout
            if package.returncode: raise RuntimeError("package")
            stage="tests"; mark("tests")
            for idx,mod in enumerate(mods,1):
                mark_module(idx,len(mods))
                test=nobody_run(work+"/tools/llmtool",[work+"/venv/bin/python","-m","unittest","discover","-s","tests","-p",mod+".py"],limit=int(ml),env={"HOME":work+"/.home","TMPDIR":work+"/.tmp"},capture_stderr=True)
                test_output=test.stdout+(test.stderr or b""); log+=test_output
                lines=[x.decode("utf8","replace").strip() for x in test_output.splitlines() if b"Ran " in x or b"OK" in x or b"FAILED" in x or b"ERROR" in x]
                final=final_line(lines)
                finals.append(final)
                entry=module_result(mod,test_output,final); module_results.append(entry); ran+=entry["ran"]
                if test.returncode==124:
                    result="TIMEOUT"; code=124; failures.append(mod); break
                if test.returncode or not passing_final(final) or entry["ran"]<=entry["skipped"]:
                    result="FAIL"; code=test.returncode or 1
                    failures.append(mod); failed_outputs.extend((test.stdout,test.stderr or b""))
            if result!="TIMEOUT" and code==1 and len(failures)==0 and ran>0: result="PASS"; code=0
        finally:
            if work:
                mark("cleanup")
                gone=nobody_cli(["rm","-rf","--",work],cleanup=True); log+=gone.stdout
except subprocess.TimeoutExpired as exc:
    result="TIMEOUT"; code=124; log+=exc.stdout or b""; log+=f"REMOTE_STAGE_FAILED {stage} {type(exc).__name__}\n".encode()
except Exception as exc:
    if result not in ("SHA_MISMATCH","TIMEOUT"): result="FAIL"
    code=code or 1; log+=f"REMOTE_STAGE_FAILED {stage} {type(exc).__name__}\n".encode()
mark("cleanup")
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
receipt={"result":result,"exit_code":code,"source_sha":sha,"tree":tree,"fetch_head":fetch,"modules":mods,"identity":{"uid":uid},"started_at":started,"ended_at":ended,"elapsed_s":round(time.monotonic()-begin,3),"ran_total":ran,"final":finals,"failures":failures[:10],"log_bytes":len(log),"log_sha256":hashlib.sha256(log).hexdigest(),"remote_log_path":log_path}
if result!="PASS": receipt["failure_excerpt"]=failure_excerpt(failed_outputs,work or "<REMOTE_WORKDIR>",os.path.expanduser("~"))
if module_results: receipt["module_results"]=module_results
raw=json.dumps(receipt,separators=(",",":"),sort_keys=True).encode()
mark("receipt")
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


def _ssh_timeout():
    from . import developer_routes
    try:
        return developer_routes.remote_test_timeout()
    except developer_routes.RouteError:
        raise LLMToolError('ADMISSION_DENIED: remote test timeout config is invalid') from None


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


_MAC_MANIFEST = Path(__file__).resolve().parents[1] / 'ci' / 'mac-modules.txt'
_TESTS_DIR = Path(__file__).resolve().parents[1] / 'tests'


def _darwin_skip(decorator):
    """True only for unittest.skipUnless(sys.platform == 'darwin', ...)."""
    if not isinstance(decorator, ast.Call) or not decorator.args:
        return False
    func = decorator.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)
    test = decorator.args[0]
    return (name == 'skipUnless' and isinstance(test, ast.Compare) and
            len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq) and
            isinstance(test.left, ast.Attribute) and test.left.attr == 'platform' and
            isinstance(test.left.value, ast.Name) and test.left.value.id == 'sys' and
            len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant) and
            test.comparators[0].value == 'darwin')


def _darwin_only_modules(stems, *, manifest=None, tests_dir=None):
    """#2573: test modules that are Darwin-only by design.

    Linux remote tests (EC2 and hosted) skip every test in such a module, so
    a receipt would count it as FAIL although nothing failed. A module counts
    only when every unittest TestCase class in it is listed in the Mac CI
    baseline manifest (ci/mac-modules.txt, always run by exact-head Mac CI)
    and carries skipUnless(sys.platform == 'darwin'), and it has no
    module-level test functions. The manifest and modules are read from the
    running llmtool checkout. Any read or parse doubt keeps the module in the
    remote set (fail closed).
    """
    manifest = _MAC_MANIFEST if manifest is None else Path(manifest)
    tests_dir = _TESTS_DIR if tests_dir is None else Path(tests_dir)
    try:
        listed = {line.partition('#')[0].strip()
                  for line in manifest.read_text(encoding='utf-8').splitlines()}
    except (OSError, UnicodeDecodeError):
        return frozenset()
    found = set()
    for stem in stems:
        if type(stem) is not str or not _MODULE.fullmatch(stem):
            continue
        try:
            tree = ast.parse((tests_dir / (stem + '.py')).read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            continue
        if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and
               node.name.startswith('test') for node in tree.body):
            continue
        cases = [node for node in tree.body if isinstance(node, ast.ClassDef) and any(
            (isinstance(base, ast.Name) and base.id == 'TestCase') or
            (isinstance(base, ast.Attribute) and base.attr == 'TestCase')
            for base in node.bases)]
        if cases and all(f'{stem}.{node.name}' in listed and
                         any(map(_darwin_skip, node.decorator_list)) for node in cases):
            found.add(stem)
    return frozenset(found)


def remote_required_modules(task, extra_modules=()):
    """Task test modules a Linux remote receipt must cover (#2573, #2491).

    extra_modules are the exact-head test modules the PR adds or changes.
    Darwin-only modules are left to the exact-head Mac CI merge gate.
    """
    required = set(_task_path_modules(task))
    for module in extra_modules:
        if type(module) is str and _MODULE.fullmatch(module):
            required.add(module)
    darwin = _darwin_only_modules(required)
    return tuple(module for module in sorted(required) if module not in darwin)


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
