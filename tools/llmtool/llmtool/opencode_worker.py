"""One trusted-host OpenCode V2 job, not a planner, provider or review authority.

The embedding host must independently authorize the exact job, source exposure,
runtime/configuration and selected free model. Python objects authenticate none
of those claims. No CLI/environment/file grants, automatic retry, shared service,
Git mutation or durable queue are installed here. Returned text is untrusted DATA.
"""
import hashlib
import os
from pathlib import Path
import re
import secrets
import selectors
import sqlite3
import stat
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from .action_contract import TaskBinding, _hex, _path
from .errors import LLMToolError
from .run_budget import (RunBudget, UsageLedger, CallUsage, CallPhase,
                         admit_next, assess_budget, BudgetCode, BudgetStatus)
from .task_lifecycle import AgentBinding, Role
from .task_review import canonical, load_json
from .task_review_collector import physical_directory
from .providers.cli_lifecycle import CLIProcessLifecycle, _Cancelled

LIMIT = 1024 * 1024
TEXT_LIMIT = 128 * 1024
MODELS = {'DEVELOP': 'opencode/deepseek-v4.1-flash-free',
          'PREREVIEW': 'opencode/glm-5.3-flash-free'}
AGENT = 'llmtool-worker'


class WorkerError(LLMToolError):
    def __init__(self, code):
        super().__init__('OpenCode job blocked; details omitted')
        self.worker_code = code


def reject(code):
    raise WorkerError(code)


@dataclass(frozen=True)
class RuntimePin:
    executable: str
    executable_sha256: str
    version: str
    state_root: str


@dataclass(frozen=True)
class WorkerJob:
    task: TaskBinding
    agent: AgentBinding
    source_sha: str
    phase: str
    model: str
    prompt: str
    read_paths: tuple
    write_paths: tuple
    runtime: RuntimePin
    budget: RunBudget
    usage: UsageLedger
    max_steps: int = 2
    pending: tuple = field(default=(), init=False, repr=False, compare=False)
    _attempted: bool = field(default=False, init=False, repr=False, compare=False)
    _lock: object = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    @property
    def digest(self):
        return hashlib.sha256(canonical(dict(task=self.task.to_dict(),agent=self.agent.to_dict(),
            source=self.source_sha,phase=self.phase,model=self.model,
            prompt_sha256=hashlib.sha256(self.prompt.encode('utf-8')).hexdigest(),
            read=self.read_paths,write=self.write_paths,runtime=self.runtime.__dict__,
            budget=self.budget.to_dict(),usage=self.usage.to_dict(),steps=self.max_steps))).hexdigest()


class TrustedHost(Protocol):
    """Host-code capabilities only; no constructor or bool is an attestation.

    admit must cover actual registered sole-writer root, TaskContract, executable,
    exact native config, model/free/privacy/auth/egress and token/cost policy BEFORE
    any server starts or native tool reads. consume is one-use admission, check
    independently reconciles actual before/after source and allowed file changes.
    finalize must retain uncertain attempts; no refund/retry is implied.
    """
    test_only: bool
    def admit(self, job: WorkerJob, config_digest: str): ...
    def consume(self, token, job: WorkerJob) -> None: ...
    def prepare_server(self, token, job: WorkerJob, url: str, password: str,
                       deadline: float) -> None:
        """Bounded native readiness/config/auth verification, before any prompt.

        May use the authenticated private API; never log credential responses.
        This callback must not perform inference or access/export project source.
        """
        ...
    def check(self, token, job: WorkerJob, stage: str) -> None: ...
    def finalize(self, token, code: str, launched: bool) -> None: ...


@dataclass(frozen=True)
class WorkerResult:
    code: str
    status: str
    content: object
    session_id: object
    usage: UsageLedger
    cleanup_confirmed: bool
    processes: tuple
    output_sha256: object
    evidence_class: str
    authorized: bool = False
    runtime_evidence_status: str = 'NOT_VERIFIED'
    provider_requests: str = 'UNKNOWN'
    raw_usage: tuple = ()


def configuration(job):
    """V2 ordered permissions: no implicit shell/search/subagent/MCP access."""
    rules = [dict(action='*',resource='*',effect='deny')]
    rules += [dict(action='read',resource=p,effect='allow') for p in job.read_paths]
    if job.phase == 'DEVELOP':
        rules += [dict(action='edit',resource=p,effect='allow') for p in job.write_paths]
    return dict(model=job.model,default_agent=AGENT,share='disabled',snapshots=False,
        formatter=False,lsp=False,warming=False,compaction=dict(auto=False),
        skills=[],instructions=[],mcp=dict(servers={}),permissions=rules,
        policies=[dict(action='provider.use',resource='*',effect='deny'),
                  dict(action='provider.use',resource='opencode',effect='allow'),
                  dict(action='integration.use',resource='*',effect='deny')],
        agents={AGENT:dict(mode='primary',model=job.model,steps=job.max_steps,permissions=rules),
                'title':dict(disabled=True),'summary':dict(disabled=True),
                'compaction':dict(disabled=True)})


def _clock(deadline):
    if time.monotonic() >= deadline:
        reject('OPENCODE_TIMEOUT')


def _file_hash(path, deadline):
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid not in (0,os.getuid()) or
            before.st_mode & 0o022 or before.st_nlink != 1 or before.st_size > 256*LIMIT):
        reject('OPENCODE_RUNTIME_INVALID')
    digest = hashlib.sha256()
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as stream:
        if (os.fstat(stream.fileno()).st_dev,os.fstat(stream.fileno()).st_ino) != (before.st_dev,before.st_ino):
            reject('OPENCODE_RUNTIME_INVALID')
        while True:
            _clock(deadline)
            chunk = stream.read(65536)
            if not chunk: break
            digest.update(chunk)
    after = path.lstat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) != (
            after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
        reject('OPENCODE_RUNTIME_INVALID')
    return digest.hexdigest()


def _validate(job, deadline):
    if (type(job.task) is not TaskBinding or type(job.agent) is not AgentBinding or
            job.agent.role is not Role.DEVELOPER or type(job.runtime) is not RuntimePin or
            type(job.budget) is not RunBudget or type(job.usage) is not UsageLedger or
            job.phase not in MODELS or job.model != MODELS[job.phase] or
            type(job.max_steps) is not int or not 1 <= job.max_steps <= 16 or
            not 0 < job.budget.wall_clock_ms <= 86400000 or
            job.runtime.version != '2.0.32' or type(job.prompt) is not str or
            not job.prompt.strip() or len(job.prompt.encode('utf-8')) > TEXT_LIMIT):
        reject('OPENCODE_JOB_INVALID')
    _hex(job.source_sha,(40,64)); _hex(job.runtime.executable_sha256)
    root = Path(job.agent.root)
    physical_directory(root)
    for paths in (job.read_paths,job.write_paths):
        if type(paths) is not tuple or len(paths)>256 or tuple(sorted(set(paths)))!=paths:
            reject('OPENCODE_SCOPE_INVALID')
        for name in paths:
            _path(name)
            p = root/name
            for ancestor in (p,*p.parents):
                if ancestor == root: break
                if ancestor.is_symlink(): reject('OPENCODE_SCOPE_INVALID')
            if root not in p.resolve().parents: reject('OPENCODE_SCOPE_INVALID')
    if job.phase == 'PREREVIEW' and job.write_paths:
        reject('OPENCODE_SCOPE_INVALID')
    if job.max_steps > job.budget.max_provider_calls-len(job.usage.calls):
        reject('OPENCODE_BUDGET_BLOCKED')
    executable = Path(job.runtime.executable)
    physical_directory(executable.parent)
    if not executable.is_absolute() or _file_hash(executable,deadline)!=job.runtime.executable_sha256:
        reject('OPENCODE_RUNTIME_INVALID')
    state = Path(job.runtime.state_root)
    if any((parent/'.git').exists() for parent in (state,*state.parents)):
        reject('OPENCODE_RUNTIME_INVALID')
    for path in (state,*(state/x for x in ('home','config','cache','data','state','tmp'))):
        physical_directory(path)
        if stat.S_IMODE(path.stat().st_mode)!=0o700 or root==path or root in path.parents:
            reject('OPENCODE_RUNTIME_INVALID')


def _environment(job, config, password):
    state = Path(job.runtime.state_root)
    return dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(state/'home'),
        OPENCODE_TEST_HOME=str(state/'home'),XDG_CONFIG_HOME=str(state/'config'),
        XDG_DATA_HOME=str(state/'data'),XDG_CACHE_HOME=str(state/'cache'),
        XDG_STATE_HOME=str(state/'state'),TMPDIR=str(state/'tmp')+'/',
        OPENCODE_CONFIG_DIR=str(state/'config'),OPENCODE_CONFIG_CONTENT=config.decode(),
        OPENCODE_CONFIG_PROJECT_DISABLE='1',OPENCODE_DISABLE_CLAUDE_CODE='1',
        OPENCODE_DISABLE_FFF='1',OPENCODE_FILEWATCHER_DISABLE='1',
        OPENCODE_PASSWORD=password,NO_COLOR='1',TERM='dumb')


class _Pipes:
    def __init__(self, deadline):
        self.selector=selectors.DefaultSelector(); self.deadline=deadline
        self.buffers={}; self.total=0; self.input=b''; self.offset=0

    def output(self, stream, name):
        os.set_blocking(stream.fileno(),False); self.buffers[name]=bytearray()
        self.selector.register(stream,selectors.EVENT_READ,name)

    def pump(self, lifecycles):
        _clock(self.deadline)
        for lifecycle in lifecycles: lifecycle.check()
        for key,_ in self.selector.select(min(.05,max(0,self.deadline-time.monotonic()))):
            stream,name=key.fileobj,key.data
            try:
                if name=='input':
                    if self.offset<len(self.input):
                        self.offset+=os.write(stream.fileno(),self.input[self.offset:self.offset+8192])
                    else:
                        self.selector.unregister(stream);stream.close()
                    continue
                data=os.read(stream.fileno(),8192)
            except (BlockingIOError,InterruptedError): continue
            if not data:
                self.selector.unregister(stream);continue
            self.total+=len(data)
            if self.total>LIMIT: reject('OPENCODE_OUTPUT_LIMIT')
            self.buffers[name].extend(data)


def _session(raw):
    """Native JSON events identify DATA, never execution or completion authority."""
    sid=None
    for line in raw.decode('utf-8').splitlines():
        event=load_json(line,LIMIT)
        if type(event) is not dict: reject('OPENCODE_OUTPUT_INVALID')
        if event.get('type')=='error':
            error=event.get('error',{})
            reject('OPENCODE_AUTH_REQUIRED' if type(error) is dict and
                   error.get('type')=='provider.auth' else 'OPENCODE_PROVIDER_FAILED')
        value=event.get('sessionID')
        if (type(value) is not str or re.fullmatch(r'ses_[A-Za-z0-9]{1,100}',value) is None or
                (sid is not None and sid!=value) or
                event.get('type') not in ('step_start','step_finish','text','tool_use')):
            reject('OPENCODE_OUTPUT_INVALID')
        sid=value
    if sid is None: reject('OPENCODE_OUTPUT_INCOMPLETE')
    return sid


def _persisted(job, sid, title, started_ms, deadline):
    """Read only this invocation's native session, never credential/history rows.

    V2.0.32 can omit the final step_finish. CLI exit/EOF is insufficient: the
    actual private database must show a new matching succeeded session and final
    completed assistant message, with the exact pinned model/agent/directory.
    """
    _clock(deadline)
    path=Path(job.runtime.state_root)/'data/opencode/opencode.db'
    physical_directory(path.parent)
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or
            stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or info.st_size>16*LIMIT):
        reject('OPENCODE_COMPLETION_UNKNOWN')
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=min(.1,max(0,deadline-time.monotonic())))
    try:
        db.set_progress_handler(lambda: int(time.monotonic()>=deadline),1000)
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        row=db.execute('SELECT directory,version,agent,model,title,time_created,time_idle,'
            'idle_outcome,tokens_input,tokens_output,cost,tokens_reasoning,tokens_cache_read,'
            'tokens_cache_write FROM session_v2 WHERE id=?',(sid,)).fetchone()
        if (row is None or row[0]!=job.agent.root or row[1]!=job.runtime.version or
                row[2]!=AGENT or row[4]!=title or type(row[5]) is not int or row[5]<started_ms or
                type(row[6]) is not int or row[6]<row[5] or row[7]!='succeeded'):
            reject('OPENCODE_OUTPUT_INCOMPLETE')
        model=load_json(row[3],1024)
        if model.get('providerID')+'/'+model.get('id')!=job.model:
            reject('OPENCODE_MODEL_DRIFT')
        if type(row[10]) not in (int,float) or row[10]!=0:
            reject('OPENCODE_COST_UNKNOWN')
        rows=db.execute('SELECT id,type,CASE WHEN length(data)<=? THEN data END FROM session_message '
            'WHERE session_id=? ORDER BY seq LIMIT ?',
            (TEXT_LIMIT,sid,job.max_steps+9)).fetchall()
        if len(rows)>job.max_steps+8: reject('OPENCODE_OUTPUT_LIMIT')
        assistants=[]; idle=False
        for message_id,kind,data in rows:
            if data is None: reject('OPENCODE_OUTPUT_LIMIT')
            item=load_json(data,TEXT_LIMIT)
            if kind=='idle': idle=item.get('outcome')=='succeeded'
            if kind!='assistant': continue
            if (item.get('agent')!=AGENT or type(item.get('model')) is not dict or
                    item['model'].get('providerID')+'/'+item['model'].get('id')!=job.model):
                reject('OPENCODE_MODEL_DRIFT')
            if (type(item.get('time',{}).get('completed')) is not int or
                    item['time']['completed']<row[5]): reject('OPENCODE_OUTPUT_INCOMPLETE')
            if type(item.get('cost')) not in (int,float) or item['cost']!=0:
                reject('OPENCODE_COST_UNKNOWN')
            assistants.append(item)
        if (not idle or not assistants or len(assistants)>job.max_steps or
                assistants[-1].get('finish')!='stop'):
            reject('OPENCODE_OUTPUT_INCOMPLETE')
        calls=[]; raw_usage=[]
        for item in assistants:
            tokens=item.get('tokens',{})
            cache=tokens.get('cache',{}) if type(tokens) is dict else {}
            counts=([tokens.get('input'),tokens.get('output'),tokens.get('reasoning'),
                     cache.get('read'),cache.get('write')]
                    if type(tokens) is dict and type(cache) is dict else [])
            # Native components are disjoint. Missing/zero-filled provider usage
            # is not evidence of a completed paid step: require a positive total.
            known=(len(counts)==5 and all(type(x) is int and 0<=x<=10**9 for x in counts)
                   and sum(counts)>0)
            raw_usage.append(tuple(counts) if known else None)
            calls.append(CallUsage(CallPhase.PRIMARY if job.phase=='DEVELOP' else CallPhase.REVIEW,
                counts[0]+counts[3]+counts[4] if known else None,
                counts[1]+counts[2] if known else None))
        if all(x.known for x in calls) and tuple(sum(c[i] for c in raw_usage) for i in range(5))!=(
                row[8],row[9],row[11],row[12],row[13]):
            reject('OPENCODE_USAGE_UNKNOWN')
        text=''.join(x.get('text','') for x in assistants[-1].get('content',())
                     if type(x) is dict and x.get('type')=='text')
        if not text or len(text.encode('utf-8'))>TEXT_LIMIT: reject('OPENCODE_OUTPUT_INCOMPLETE')
        _clock(deadline)
        after=path.lstat()
        if (info.st_dev,info.st_ino,info.st_mode,info.st_uid)!=(
                after.st_dev,after.st_ino,after.st_mode,after.st_uid):
            reject('OPENCODE_COMPLETION_UNKNOWN')
        return text,tuple(calls),tuple(raw_usage)
    finally: db.close()


def run_worker(job, host):
    """Execute exactly one admitted foreground job; no subsequent phase is run.

    Private serve--stdio and run--server are BOTH direct owned children. Closing
    the server's stdin is its lease release. No sampled/escaped PID is signalled.
    One CLI job is atomic for token admission: several native model steps may
    run before usage can be assessed. Step count is bounded; per-step token/HTTP
    retry enforcement is NOT provided. Dependent jobs must stop on UNKNOWN/excess.
    A caller must keep job.pending on uncertainty and never resend this attempt.
    """
    if type(job) is not WorkerJob: reject('OPENCODE_JOB_INVALID')
    if type(job.budget) is not RunBudget or type(job.usage) is not UsageLedger:
        reject('OPENCODE_JOB_INVALID')
    with job._lock:
        if job._attempted:
            return WorkerResult('OPENCODE_ATTEMPT_ALREADY_USED','BLOCKED',None,None,
                job.usage,False,(),None,'NOT_VERIFIED')
        object.__setattr__(job,'_attempted',True)
    started=time.monotonic(); started_ms=int(time.time()*1000)
    deadline=started+min(600,max(0,(job.budget.wall_clock_ms-(job.usage.elapsed_ms or 0))/1000))
    work_deadline=deadline-min(5,(deadline-started)*.2)
    token=None; launched=False; code='OPENCODE_HOST_UNKNOWN'; content=None;sid=None
    ledger=job.usage; pipes=None; holders=[]; output=b''; facts=[]; raw_usage=()
    evidence='OFFLINE_FIXTURE' if getattr(host,'test_only',None) is True else 'HOST_OBSERVED'
    try:
        _validate(job,work_deadline)
        phase=CallPhase.PRIMARY if job.phase=='DEVELOP' else CallPhase.REVIEW
        admission=admit_next(job.budget,job.usage,phase)
        if admission is not BudgetCode.WITHIN_BUDGET:
            reject('OPENCODE_BUDGET_BLOCKED')
        config=canonical(configuration(job))
        token=host.admit(job,hashlib.sha256(config).hexdigest())
        if token is None: reject('OPENCODE_NOT_AUTHORIZED')
        host.check(token,job,'before');host.consume(token,job)
        _clock(work_deadline)
        env=_environment(job,config,secrets.token_hex(24))
        title='llmtool-'+secrets.token_hex(16)
        pipes=_Pipes(work_deadline)
        server=CLIProcessLifecycle('opencode-cli',deadline=deadline)
        holders.append(server);object.__setattr__(job,'pending',tuple(holders))
        with server:
            try:
                process=subprocess.Popen([job.runtime.executable,'serve','--stdio','--port','0'],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,
                    cwd=job.agent.root,env=env,shell=False,close_fds=True,start_new_session=True,umask=0o077)
                launched=True;server.own(process)
                pipes.output(process.stdout,'server_out');pipes.output(process.stderr,'server_err')
                while b'\n' not in pipes.buffers['server_out']:
                    pipes.pump(holders)
                ready=load_json(bytes(pipes.buffers['server_out']).split(b'\n',1)[0].decode(),65536)
                url=ready.get('url');parsed=urlsplit(url)
                if (type(url) is not str or parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or
                        parsed.username or parsed.password or not parsed.port or parsed.query or
                        parsed.fragment or parsed.path not in ('','/')):
                    reject('OPENCODE_RUNTIME_INVALID')
                host.prepare_server(token,job,url,env['OPENCODE_PASSWORD'],work_deadline)
                host.check(token,job,'before-client');_clock(work_deadline)
                client=CLIProcessLifecycle('opencode-cli',deadline=deadline)
                holders.append(client);object.__setattr__(job,'pending',tuple(holders))
                with client:
                    child=subprocess.Popen([job.runtime.executable,'run','--server',url,'--model',job.model,
                        '--agent',AGENT,'--format','json','--title',title],
                        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,
                        cwd=job.agent.root,env=env,shell=False,close_fds=True,start_new_session=True,umask=0o077)
                    client.own(child)
                    pipes.output(child.stdout,'client_out');pipes.output(child.stderr,'client_err')
                    os.set_blocking(child.stdin.fileno(),False)
                    pipes.input=job.prompt.encode('utf-8')
                    pipes.selector.register(child.stdin,selectors.EVENT_WRITE,'input')
                    while any(k.data in ('client_out','client_err','input')
                              for k in pipes.selector.get_map().values()):
                        pipes.pump(holders)
                    child.wait(timeout=max(0,work_deadline-time.monotonic()))
                    output=bytes(pipes.buffers['client_out'])
                    sid=_session(output)
                    if sid==job.agent.session_id: reject('OPENCODE_IDENTITY_REUSE')
                    if child.returncode!=0: reject('OPENCODE_PROVIDER_FAILED')
            finally:
                if server.process is not None and server.process.stdin is not None:
                    server.process.stdin.close()  # Release only this private server's lease.
                    try: server.process.wait(timeout=min(.2,max(0,deadline-time.monotonic())))
                    except subprocess.TimeoutExpired: pass
        if any(not x.cleanup_confirmed for x in holders): reject('OPENCODE_CLEANUP_UNKNOWN')
        if server.process.returncode!=0: reject('OPENCODE_RUNTIME_UNKNOWN')
        content,calls,raw_usage=_persisted(job,sid,title,started_ms,deadline)
        ledger=UsageLedger(job.usage.calls+calls,job.usage.repair_attempts,
                           (job.usage.elapsed_ms or 0)+int((time.monotonic()-started)*1000))
        assessment=assess_budget(job.budget,ledger)
        if assessment.status is BudgetStatus.UNKNOWN: reject('OPENCODE_USAGE_UNKNOWN')
        if assessment.status is not BudgetStatus.WITHIN: reject('OPENCODE_BUDGET_BLOCKED')
        code='OPENCODE_OUTPUT_RETURNED'
    except WorkerError as error: code=error.worker_code
    except _Cancelled: raise
    except (OSError,ValueError,TypeError,KeyError,LLMToolError,sqlite3.Error,subprocess.SubprocessError):
        code='OPENCODE_RUNTIME_UNKNOWN'
    except Exception:
        code='OPENCODE_HOST_UNKNOWN'
    finally:
        if pipes is not None: pipes.selector.close()
        for lifecycle in holders:
            process=lifecycle.process
            if process is not None:
                for stream in (process.stdin,process.stdout,process.stderr):
                    if stream is not None and not stream.closed: stream.close()
                facts.append(dict(kind='server' if lifecycle is holders[0] else 'client',
                    pid=process.pid,host_pid=os.getpid(),reaped=process.returncode is not None,
                    exit=process.returncode,group_absent=lifecycle.cleanup_confirmed))
        cleanup=all(x.process is None or x.cleanup_confirmed for x in holders)
        if not cleanup: code='OPENCODE_CLEANUP_UNKNOWN'
        else: object.__setattr__(job,'pending',())
        if token is not None:
            # Always reconcile after a client could have edited, including auth,
            # budget, timeout and cancellation failures. Cleanup is not a source
            # scope verdict; preserve the original BaseException on cancellation.
            if len(holders)>1:
                try: host.check(token,job,'after')
                except Exception: code='OPENCODE_SOURCE_UNKNOWN'
            try: host.finalize(token,code,launched)
            except Exception: code='OPENCODE_FINALIZE_UNKNOWN'
    if code!='OPENCODE_OUTPUT_RETURNED':
        content=None
        # A launched client with unavailable completion/usage must poison later
        # budget admission; a fresh WorkerJob is not permission to resend it.
        if len(holders)>1 and ledger is job.usage:
            ledger=UsageLedger(job.usage.calls+(CallUsage(phase,None,None),),
                job.usage.repair_attempts,job.usage.elapsed_ms+int((time.monotonic()-started)*1000))
    return WorkerResult(code,'RETURNED' if content is not None else 'BLOCKED',content,sid,
        ledger,cleanup,tuple(facts),hashlib.sha256(output).hexdigest() if output else None,evidence,raw_usage=raw_usage)
