"""One trusted-host OpenCode V2 job, not a planner, provider or review authority.

The embedding host must independently authorize the exact job, source exposure,
runtime/configuration and selected free model. Python objects authenticate none
of those claims. No CLI/environment/file grants, automatic retry, shared service,
Git mutation or durable queue are installed here. Returned text is untrusted DATA.
"""
import hashlib
import base64
import os
from pathlib import Path
import re
import secrets
import selectors
import stat
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import quote, urlsplit

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
    """V2 ordered permissions: no implicit shell/search/subagent/MCP access.

    The pinned model's ``limit.output`` is overridden with the job budget, so every
    native provider request carries that completion cap (OpenCode 2.0.32 sends it as
    ``max_completion_tokens``) before any token is generated.
    """
    provider, model = job.model.split('/', 1)
    rules = [dict(action='*',resource='*',effect='deny')]
    rules += [dict(action='read',resource=p,effect='allow') for p in job.read_paths]
    if job.phase == 'DEVELOP':
        rules += [dict(action='edit',resource=p,effect='allow') for p in job.write_paths]
    return dict(model=job.model,default_agent=AGENT,share='disabled',snapshots=False,
        providers={provider: dict(models={model: dict(limit=dict(
            context=job.budget.max_input_tokens,output=job.budget.max_output_tokens))})},
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


def _observe_exit_without_reap(process, timeout, deadline):
    """Observe a direct child's exit while keeping it waitable for lifecycle teardown.

    A zombie leader is still group/identity evidence: the lifecycle must own the
    SIGKILL and bounded reap for the whole group. WNOWAIT observes without consuming
    that state. Missing primitives, wait errors and ambiguous identity are UNKNOWN,
    never a reason to fall back to Popen.poll/wait (which would reap early).
    """
    waitid=getattr(os,'waitid',None)
    p_pid=getattr(os,'P_PID',None)
    w_exited=getattr(os,'WEXITED',None)
    w_nohang=getattr(os,'WNOHANG',None)
    w_nowait=getattr(os,'WNOWAIT',None)
    pid=getattr(process,'pid',None)
    # WNOWAIT must be a real nonzero bit, or the flag would silently drop and reap;
    # WEXITED==0 cannot observe an exit and WNOHANG==0 would block past the deadline,
    # so all three are UNKNOWN rather than a guess.
    if (not callable(waitid) or
            any(not isinstance(x,int) for x in (p_pid,w_exited,w_nohang,w_nowait)) or
            w_exited==0 or w_nohang==0 or w_nowait==0 or
            type(pid) is not int or pid<=0):
        reject('OPENCODE_CLEANUP_UNKNOWN')
    end=min(time.monotonic()+max(0,timeout),deadline)
    while True:
        try:
            info=waitid(p_pid,pid,w_exited|w_nohang|w_nowait)
        except Exception:
            # Any waitid failure is UNKNOWN: never fall back to poll/wait (early reap).
            reject('OPENCODE_CLEANUP_UNKNOWN')
        if info is not None:
            if getattr(info,'si_pid',None)!=pid:
                reject('OPENCODE_CLEANUP_UNKNOWN')
            return True
        remaining=end-time.monotonic()
        if remaining<=0:
            return False
        time.sleep(min(.01,remaining))


def _file_hash(path, deadline):
    before = path.lstat()
    # A hard-linked runtime (st_nlink > 1, e.g. a package manager that also exposes
    # the pinned binary under an alias) is admitted. A hard link shares one inode, so
    # owner, write bits, size and the content hash computed below are identical for
    # every name, and the O_NOFOLLOW open plus the dev/ino/mtime re-observation still
    # reject a swap. Only the sole-link requirement is dropped; every other check
    # stays fail-closed and st_nlink < 1 remains only a defensive floor.
    if (not stat.S_ISREG(before.st_mode) or before.st_uid not in (0,os.getuid()) or
            before.st_mode & 0o022 or before.st_nlink < 1 or before.st_size > 256*LIMIT):
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
        OPENCODE_DB=':memory:',
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


def _request_starts(raw):
    """Count native step_start events: OpenCode emits one as each provider request starts."""
    count=0
    for line in raw.split(b'\n'):
        try: event=load_json(line.decode('utf-8'),LIMIT)
        except Exception: continue   # malformed lines are rejected later by _session
        if type(event) is dict and event.get('type')=='step_start': count+=1
    return count


def _allowed_requests(job):
    remaining=job.budget.max_provider_calls-len(job.usage.calls)
    return max(0,min(job.max_steps,remaining))


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _server_json(url, route, password, deadline):
    """Read one bounded response from the owned loopback OpenCode server."""
    _clock(deadline)
    parsed=urlsplit(url)
    if (parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or
            parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/')):
        reject('OPENCODE_RUNTIME_INVALID')
    request=urllib.request.Request(url+route,headers={
        'Authorization':'Basic '+base64.b64encode(('opencode:'+password).encode()).decode(),
        'Accept':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    try:
        with opener.open(request,timeout=min(1,max(.001,deadline-time.monotonic()))) as response:
            stream=getattr(response,'fp',None)
            raw=getattr(stream,'raw',None)
            sock=getattr(raw,'_sock',None)
            # Read through HTTPResponse so Content-Length and chunked framing are
            # decoded; the raw socket is used only to refresh its absolute timeout.
            read_chunk=getattr(response,'read1',None)
            if sock is None or not callable(getattr(sock,'settimeout',None)) or not callable(read_chunk):
                reject('OPENCODE_COMPLETION_UNKNOWN')
            headers=getattr(response,'headers',None)
            getheader=getattr(headers,'get',None)
            content_length=getheader('Content-Length') if callable(getheader) else None
            chunked=bool(getattr(response,'chunked',False))
            if chunked and content_length is not None:
                reject('OPENCODE_COMPLETION_UNKNOWN')
            expected=None
            if content_length is not None:
                try: expected=int(content_length)
                except (TypeError,ValueError): reject('OPENCODE_COMPLETION_UNKNOWN')
                if expected<0 or expected>LIMIT: reject('OPENCODE_OUTPUT_LIMIT')
            chunks=[];total=0
            while True:
                # A declared body is complete exactly at Content-Length. Stop
                # before HTTPResponse closes its socket, and never mistake an
                # early EOF for a complete shorter JSON document.
                if expected is not None and total==expected: break
                remaining=deadline-time.monotonic()
                if remaining<=0: reject('OPENCODE_TIMEOUT')
                sock.settimeout(min(1,remaining))
                chunk=read_chunk(min(8192,LIMIT+1-total))
                if not chunk: break
                chunks.append(chunk);total+=len(chunk)
                if total>LIMIT: reject('OPENCODE_OUTPUT_LIMIT')
                if expected is not None and total>expected:
                    reject('OPENCODE_COMPLETION_UNKNOWN')
                _clock(deadline)
    except WorkerError:
        raise
    except Exception:
        if time.monotonic()>=deadline: reject('OPENCODE_TIMEOUT')
        reject('OPENCODE_COMPLETION_UNKNOWN')
    _clock(deadline)  # Include response close/context-manager cleanup in the deadline.
    if expected is not None and total!=expected:
        reject('OPENCODE_COMPLETION_UNKNOWN')
    data=b''.join(chunks)
    try:
        result=load_json(data.decode('utf-8'),LIMIT)
    except (UnicodeError,WorkerError):
        reject('OPENCODE_COMPLETION_UNKNOWN')
    if type(result) is not dict or type(result.get('data')) not in (dict,list):
        reject('OPENCODE_COMPLETION_UNKNOWN')
    return result['data']


def _completed_session(job, sid, title, started_ms, url, password, deadline):
    """Verify one fresh completed session and its usage through the private API.

    OpenCode 2.0.32 supports an in-memory ``:memory:`` DB selected by
    ``OPENCODE_DB``. The session and message endpoints keep receipt evidence in
    the owning server process, so this check runs before that process is stopped.
    """
    encoded=quote(sid,safe='')
    info=_server_json(url,'/api/session/'+encoded,password,deadline)
    if type(info) is not dict: reject('OPENCODE_COMPLETION_UNKNOWN')
    timeinfo=info.get('time',{})
    location=info.get('location',{})
    model=info.get('model')
    def model_identity(value):
        if (type(value) is not dict or type(value.get('providerID')) is not str or
                type(value.get('id')) is not str):
            return None
        return value['providerID']+'/'+value['id']
    if (info.get('id')!=sid or location.get('directory')!=job.agent.root or
            info.get('agent')!=AGENT or info.get('title')!=title or
            model_identity(model)!=job.model):
        reject('OPENCODE_MODEL_DRIFT')
    created=timeinfo.get('created') if type(timeinfo) is dict else None
    idle=timeinfo.get('idle') if type(timeinfo) is dict else None
    if (type(created) is not int or created<started_ms or type(idle) is not int or
            idle<created or info.get('outcome')!='succeeded'):
        reject('OPENCODE_OUTPUT_INCOMPLETE')
    if type(info.get('cost')) not in (int,float) or info['cost']!=0:
        reject('OPENCODE_COST_UNKNOWN')

    limit=job.max_steps+9
    messages=_server_json(url,f'/api/session/{encoded}/message?order=asc&limit={limit}',
                          password,deadline)
    if type(messages) is not list or len(messages)>job.max_steps+8:
        reject('OPENCODE_OUTPUT_LIMIT')
    assistants=[]; message_ids=set()
    for item in messages:
        # The authenticated endpoint URL scopes these projected records to ``sid``;
        # v2.0.32 PublicSessionMessage has no sessionID field.
        if (type(item) is not dict or type(item.get('id')) is not str or
                not item['id'].startswith('msg_') or item['id'] in message_ids):
            reject('OPENCODE_COMPLETION_UNKNOWN')
        message_ids.add(item['id'])
        if item.get('type')!='assistant': continue
        if (item.get('agent')!=AGENT or model_identity(item.get('model'))!=job.model):
            reject('OPENCODE_MODEL_DRIFT')
        msgtime=item.get('time',{})
        if (type(msgtime) is not dict or type(msgtime.get('created')) is not int or
                type(msgtime.get('completed')) is not int or msgtime['completed']<created):
            reject('OPENCODE_OUTPUT_INCOMPLETE')
        if type(item.get('cost')) not in (int,float) or item['cost']!=0:
            reject('OPENCODE_COST_UNKNOWN')
        assistants.append(item)
    if (not assistants or len(assistants)>job.max_steps or
            assistants[-1].get('finish')!='stop'):
        reject('OPENCODE_OUTPUT_INCOMPLETE')

    def counts_for(value):
        tokens=value.get('tokens',{}) if type(value) is dict else {}
        cache=tokens.get('cache',{}) if type(tokens) is dict else {}
        return ([tokens.get('input'),tokens.get('output'),tokens.get('reasoning'),
                 cache.get('read'),cache.get('write')]
                if type(tokens) is dict and type(cache) is dict else [])

    calls=[]; raw_usage=[]
    for item in assistants:
        counts=counts_for(item)
        known=(len(counts)==5 and all(type(x) is int and 0<=x<=10**9 for x in counts)
               and sum(counts)>0)
        raw_usage.append(tuple(counts) if known else None)
        calls.append(CallUsage(CallPhase.PRIMARY if job.phase=='DEVELOP' else CallPhase.REVIEW,
            counts[0]+counts[3]+counts[4] if known else None,
            counts[1]+counts[2] if known else None))
    if all(call.known for call in calls):
        session_counts=counts_for(info)
        if (len(session_counts)!=5 or not all(type(x) is int and 0<=x<=10**9 for x in session_counts) or
                tuple(sum(counts[i] for counts in raw_usage) for i in range(5))!=tuple(session_counts)):
            reject('OPENCODE_USAGE_UNKNOWN')
    text=''.join(x.get('text','') for x in assistants[-1].get('content',())
                 if type(x) is dict and x.get('type')=='text')
    if not text or len(text.encode('utf-8'))>TEXT_LIMIT:
        reject('OPENCODE_OUTPUT_INCOMPLETE')
    _clock(deadline)
    return text,tuple(calls),tuple(raw_usage)


def run_worker(job, host):
    """Execute exactly one admitted foreground job; no subsequent phase is run.

    Private serve--stdio and run--server are BOTH direct owned children. Closing
    the server's stdin is its lease release. No sampled/escaped PID is signalled.
    One CLI job is atomic for token admission: several native model steps may
    run before usage can be assessed. Each request is capped at
    budget.max_output_tokens by the native config; total usage is still assessed
    afterwards. Native provider retries cannot be disabled in OpenCode 2.0.32: the
    job is stopped as soon as more requests start than min(max_steps, remaining
    provider calls), so at most ONE extra request can begin before termination.
    Dependent jobs must stop on UNKNOWN/excess.
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
    completion=None
    ledger=job.usage; pipes=None; holders=[]; output=b''; facts=[]; raw_usage=()
    observation_failed=False
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
                if env.get('OPENCODE_DB') != ':memory:':
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
                    # OpenCode retries failed provider requests itself (2.0.32: up to ~10
                    # times with backoff) and has no setting to disable it. Each request
                    # starts with a step_start event, so stop the job the moment more
                    # requests start than this job may make; the attempt stays UNKNOWN.
                    allowed=_allowed_requests(job);starts=0;scanned=0
                    while any(k.data in ('client_out','client_err','input')
                              for k in pipes.selector.get_map().values()):
                        pipes.pump(holders)
                        stream=pipes.buffers['client_out'];end=stream.rfind(b'\n')+1
                        if end>scanned:
                            starts+=_request_starts(bytes(stream[scanned:end]));scanned=end
                            if starts>allowed: reject('OPENCODE_PROVIDER_RETRY_BLOCKED')
                    child.wait(timeout=max(0,work_deadline-time.monotonic()))
                    output=bytes(pipes.buffers['client_out'])
                    sid=_session(output)
                    if sid==job.agent.session_id: reject('OPENCODE_IDENTITY_REUSE')
                    if child.returncode!=0: reject('OPENCODE_PROVIDER_FAILED')
                    completion=_completed_session(job,sid,title,started_ms,url,
                                                  env['OPENCODE_PASSWORD'],work_deadline)
            finally:
                if server.process is not None and server.process.stdin is not None:
                    server.process.stdin.close()  # Release only this private server's lease.
                    # Observe the leader exit without reaping it: the lifecycle owns
                    # group teardown, SIGKILL and the bounded reap. Bounded by the
                    # original .2s and the cleanup deadline. A non-observed exit is not
                    # an early-reap: the still-unreaped leader passes to lifecycle
                    # teardown. Record (do not raise here) an unsupported/errored
                    # observation so an in-flight cancellation/error still propagates
                    # unchanged; the fail-closed rejection is applied below once the
                    # lifecycle has run.
                    try:
                        _observe_exit_without_reap(server.process,
                            min(.2,max(0,deadline-time.monotonic())),deadline)
                    except WorkerError:
                        observation_failed=True
        if observation_failed: reject('OPENCODE_CLEANUP_UNKNOWN')
        if any(not x.cleanup_confirmed for x in holders): reject('OPENCODE_CLEANUP_UNKNOWN')
        if server.process.returncode!=0: reject('OPENCODE_RUNTIME_UNKNOWN')
        if completion is None: reject('OPENCODE_COMPLETION_UNKNOWN')
        content,calls,raw_usage=completion
        ledger=UsageLedger(job.usage.calls+calls,job.usage.repair_attempts,
                           (job.usage.elapsed_ms or 0)+int((time.monotonic()-started)*1000))
        assessment=assess_budget(job.budget,ledger)
        if assessment.status is BudgetStatus.UNKNOWN: reject('OPENCODE_USAGE_UNKNOWN')
        if assessment.status is not BudgetStatus.WITHIN: reject('OPENCODE_BUDGET_BLOCKED')
        code='OPENCODE_OUTPUT_RETURNED'
    except WorkerError as error: code=error.worker_code
    except _Cancelled: raise
    except (OSError,ValueError,TypeError,KeyError,LLMToolError,subprocess.SubprocessError):
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
