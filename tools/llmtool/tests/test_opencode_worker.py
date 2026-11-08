"""Host-owned foreground jobs using private synthetic executables, never models."""
import ctypes
import errno as _errno
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import opencode_worker as w
from llmtool.action_contract import TaskBinding
from llmtool.run_budget import (RunBudget, UsageLedger, CallUsage, CallPhase,
                                MAX_WALL_CLOCK_MS)
from llmtool.task_lifecycle import AgentBinding, Role


_LIBC_WAITID = None
_WEXITED = getattr(os, 'WEXITED', 4)
_WNOHANG = getattr(os, 'WNOHANG', 1)
_WNOWAIT = getattr(os, 'WNOWAIT', 32)
# Unscaled; every real run passes it through scaled_budget().
LOAD_TOLERANT_BUDGET = RunBudget(1, 8, 0, 15000, 100, 100)


def _test_libc_waitid():
    """OS-native non-reaping waitid(2) binding for test land.

    Independent of the production module and its patch seams (an independent
    cross-check of opencode_worker._WAITID_FALLBACK). Same Darwin siginfo_t
    prefix: si_pid at offset 12, si_status at offset 20. Used only where the
    os.waitid binding is absent (Apple CommandLineTools CPython builds).
    """
    global _LIBC_WAITID
    if _LIBC_WAITID is not None:
        return _LIBC_WAITID
    class _Info(ctypes.Structure):
        # Darwin siginfo_t is 104 bytes; padding keeps the kernel's full write
        # inside this mapping (si_pid@12, si_status@20 are consumed only).
        _fields_ = (('si_signo', ctypes.c_int), ('si_errno', ctypes.c_int),
                    ('si_code', ctypes.c_int), ('si_pid', ctypes.c_int),
                    ('si_uid', ctypes.c_uint), ('si_status', ctypes.c_int),
                    ('si_addr', ctypes.c_void_p), ('_pad', ctypes.c_int * 24))
    if ctypes.sizeof(_Info) < 104:
        return None
    class _View:
        __slots__ = ('si_pid', 'si_status')
        def __init__(self, info):
            self.si_pid = int(info.si_pid)
            self.si_status = int(info.si_status)
    syscall = ctypes.CDLL(None, use_errno=True).waitid
    syscall.argtypes = (ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int)
    syscall.restype = ctypes.c_int
    def _wait(idtype, pid, options):
        info = _Info()
        ctypes.set_errno(0)
        result = syscall(idtype, pid, ctypes.byref(info), options)
        if result:
            number = ctypes.get_errno()
            if number == _errno.ECHILD:
                raise ChildProcessError(number)
            raise OSError(number, os.strerror(number), 'waitid')
        return None if info.si_pid == 0 else _View(info)
    _LIBC_WAITID = _wait
    return _wait


def _waitid_nowait(pid):
    """The platform's waitid(2) call with WEXITED|WNOHANG|WNOWAIT semantics.

    CPython's os.waitid binding when the platform exposes it; the same raw
    syscall through libc where the binding is missing. On Pythons whose real
    os module HAS waitid but where a patch installed a stub, the patch must
    win (mirrors what production resolves at call time).
    """
    waitid = getattr(os, 'waitid', None)
    if callable(waitid):
        return waitid(getattr(os, 'P_PID', 1), pid, _WEXITED | _WNOHANG | _WNOWAIT)
    return _test_libc_waitid()(getattr(os, 'P_PID', 1), pid,
                               _WEXITED | _WNOHANG | _WNOWAIT)


FAKE = '''#!PYTHON -IB
import json,signal,socketserver,sys,time,os,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request,urlopen
from pathlib import Path
signal.alarm(ALARM_SECONDS)
if sys.argv[1]=='serve':
 class Handler(BaseHTTPRequestHandler):
  protocol_version='HTTP/1.1'
  payload=None
  def log_message(self,*args): pass
  def _send(self,value,cursor=False):
   body={'data':value}
   if cursor: body['cursor']={}
   raw=json.dumps(body).encode();self.send_response(200)
   self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
   self.end_headers();self.wfile.write(raw)
  def do_POST(self):
   if self.path!='/__fixture': self.send_error(404);return
   type(self).payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
   self._send({'ok':True})
  def do_GET(self):
   if type(self).payload is None: self.send_error(404);return
   if self.path.startswith('/api/session/ses_syntheticWorker/message?'):
    self._send(type(self).payload['messages'],cursor=True);return
   if self.path=='/api/session/ses_syntheticWorker':
    self._send(type(self).payload['info']);return
   self.send_error(404)
 class Server(ThreadingHTTPServer):
  def server_bind(self):
   socketserver.TCPServer.server_bind(self)
   self.server_name='127.0.0.1';self.server_port=self.server_address[1]
 server=Server(('127.0.0.1',0),Handler)
 server.daemon_threads=True
 threading.Thread(target=lambda:server.serve_forever(poll_interval=.005),daemon=True).start()
 print(json.dumps({'url':'http://127.0.0.1:%d'%server.server_port}),flush=True)
 sys.stdin.buffer.read();server.shutdown();server.server_close()
else:
 mode=sys.stdin.read()
 sid='ses_syntheticWorker'
 def emit(kind,**values):print(json.dumps(dict(type=kind,timestamp=1,sessionID=sid,**values)),flush=True)
 if mode=='auth':
  emit('error',error={'type':'provider.auth','status':403,'message':'PRIVATE_ERROR_NOT_FOR_OUTPUT'})
  sys.exit(1)
 if mode=='cancel': os.kill(os.getppid(),signal.SIGINT);time.sleep(4)
 if mode=='timeout': time.sleep(4)
 if mode=='overflow': print('x'*1100000);sys.exit()
 if mode=='retry':
  for n in range(5): emit('step_start',part={'type':'step-start','messageID':'m1'});time.sleep(0.2)
  time.sleep(4)
 emit('step_start',part={'type':'step-start','messageID':'m1'})
 emit('text',part={'type':'text','messageID':'m1','text':'synthetic candidate; self-review recorded'})
 if mode!='no-proof':
  args=sys.argv;model=args[args.index('--model')+1].split('/',1);title=args[args.index('--title')+1]
  at=int(time.time()*1000)
  tokens={'input':4,'output':3,'reasoning':2,'cache':{'read':5,'write':1}}
  if mode in ('unknown','zero-usage','missing-cache'):
   tokens={'input':0,'output':0,'reasoning':0,'cache':{'read':0,'write':0}}
  item={'id':'msg_synthetic','type':'assistant','time':{'created':at,'completed':at},
        'agent':'llmtool-worker',
        'model':{'providerID':model[0],'id':model[1]},
        'finish':'length' if mode=='incomplete' else 'stop',
        'content':[{'type':'text','text':'synthetic candidate; self-review recorded'}],
        'tokens':tokens,'cost':0}
  if mode=='model-drift':item['model']['id']='UNAPPROVED'
  if mode=='zero-usage':item['tokens']={'input':0,'output':0,'reasoning':0,'cache':{'read':0,'write':0}}
  if mode=='missing-cache':item['tokens'].pop('cache',None)
  if mode=='nonzero-cost':item['cost']=1
  info={'id':sid,'projectID':'prj_synthetic','location':{'directory':os.getcwd()},
        'agent':'llmtool-worker','model':{'providerID':model[0],'id':model[1]},
        'title':title,'time':{'created':at,'updated':at,'idle':at},
        'outcome':'succeeded','cost':1 if mode=='nonzero-cost' else 0,
        'tokens':tokens}
  fixture={'info':info,'messages':[item,{'id':'msg_idle_synthetic','type':'idle',
          'time':{'created':at},'outcome':'succeeded'}]}
  req=Request(sys.argv[sys.argv.index('--server')+1]+'/__fixture',
      data=json.dumps(fixture).encode(),headers={'Content-Type':'application/json'})
  with urlopen(req,timeout=2) as response: response.read(1024)
 finish={'type':'step-finish','messageID':'m1','reason':'stop'}
 if mode!='unknown': finish['tokens']={'input':4,'output':3}
 if mode=='incomplete':finish['reason']='length'
 if mode!='omitted-terminal':emit('step_finish',part=finish)
'''


NATIVE_FAKE = FAKE.replace(
""" server=Server(('127.0.0.1',0),Handler)""",
""" server=Server(('127.0.0.1',0),Handler)
 import subprocess
 child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
 Path('SERVER_DESCENDANT.json').write_text(json.dumps({'pid':child.pid,'pgid':os.getpgid(child.pid)}))
 """)


SERVER_NONZERO = FAKE.replace(
"""sys.stdin.buffer.read();server.shutdown();server.server_close()""",
"""sys.stdin.buffer.read();server.shutdown();server.server_close();sys.exit(3)""")


# Readiness baseline: how long this machine takes to start the synthetic server.
# Production timeouts are not changed; only the budgets the tests pass grow.
_READINESS_PROBE_CAP_S = 60.0       # hard cap of the probe itself
_READINESS_MARGIN_FACTOR = 4        # margin = 4 x baseline (server + client starts)
_READINESS_FAST_START_S = 0.25      # at or below this, budgets stay as written
_FAKE_MIN_ALARM_S = 8               # the FAKE safety alarm is never shorter
_READINESS = None                   # (seconds or None, failure message or None)
_REAL_POPEN = subprocess.Popen      # the probe is immune to a test's Popen patch


def _fake_text(script, wall_clock_ms):
    """The synthetic CLI text; its own alarm always outlasts the given budget."""
    alarm = max(_FAKE_MIN_ALARM_S, math.ceil(wall_clock_ms / 1000) + 2)
    return script.replace('PYTHON', sys.executable).replace('ALARM_SECONDS', str(alarm))


def _end_probe(process):
    """End and reap the probe server and its whole owned group."""
    try:
        process.stdin.close()  # Lease release, as production does.
    except OSError:
        pass
    try:
        # The leader is not reaped yet, so this group id is still ours.
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    process.wait(timeout=30)
    for stream in (process.stdout, process.stderr):
        stream.close()


def _probe_readiness():
    """Start the FAKE server once like production and time its url line."""
    temp = tempfile.TemporaryDirectory(prefix='opencode-readiness-',
                                       dir=str(Path(tempfile.gettempdir()).resolve()))
    out, err = bytearray(), bytearray()
    seconds = None
    try:
        root = Path(temp.name)
        project = root/'project'; project.mkdir(mode=0o700)
        state = root/'runtime'; state.mkdir(mode=0o700)
        for name in ('home','config','cache','data','state','tmp'):
            (state/name).mkdir(mode=0o700)
        executable = root/'synthetic-cli'
        executable.write_text(_fake_text(FAKE, _READINESS_PROBE_CAP_S * 1000))
        executable.chmod(0o700)
        env = w._environment(SimpleNamespace(runtime=SimpleNamespace(state_root=str(state))),
                             b'{}', 'SYNTHETIC')
        started = time.monotonic()
        process = _REAL_POPEN([str(executable),'serve','--stdio','--port','0'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,
            cwd=str(project),env=env,shell=False,close_fds=True,start_new_session=True,
            umask=0o077)
        try:
            end = started + _READINESS_PROBE_CAP_S
            with selectors.DefaultSelector() as selector:
                for stream in (process.stdout, process.stderr):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ)
                while selector.get_map() and b'\n' not in out:
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        break
                    for key, _ in selector.select(remaining):
                        try:
                            chunk = os.read(key.fileobj.fileno(), 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                        elif key.fileobj is process.stdout:
                            out.extend(chunk)
                        else:
                            err.extend(chunk); del err[:-2000]
                    if b'\n' in out:
                        seconds = time.monotonic() - started
        finally:
            _end_probe(process)
    except Exception as error:  # Reported as the probe failure, never as a skip.
        err.extend(('\nprobe error: %s' % type(error).__name__).encode())
        seconds = None
    finally:
        temp.cleanup()
    if seconds is not None:
        try:
            url = json.loads(bytes(out).split(b'\n', 1)[0].decode())['url']
        except (ValueError, KeyError, TypeError):
            url = None
        if not (isinstance(url, str) and url.startswith('http://127.0.0.1:')):
            seconds = None
    if seconds is not None:
        return seconds, None
    tail = bytes(err[-2000:]).decode('utf-8', 'replace').strip() or '<empty>'
    return None, ('synthetic server readiness probe got no url line within %d s; '
                  'probe stderr tail:\n%s' % (_READINESS_PROBE_CAP_S, tail))


def _readiness_seconds():
    """The once-per-process baseline; a failed probe fails the calling test."""
    global _READINESS
    if _READINESS is None:
        _READINESS = _probe_readiness()
    seconds, failure = _READINESS
    if failure is not None:
        raise AssertionError(failure)
    return seconds


def scaled_budget(budget):
    """The one helper for every wall-clock budget a test passes to run_worker.

    Adds 4 x the readiness baseline to the written milliseconds, capped at the
    RunBudget maximum; a fast-starting machine keeps the written value.
    """
    seconds = _readiness_seconds()
    margin = (0 if seconds <= _READINESS_FAST_START_S
              else math.ceil(seconds * _READINESS_MARGIN_FACTOR * 1000))
    return replace(budget, wall_clock_ms=min(MAX_WALL_CLOCK_MS, budget.wall_clock_ms + margin))


def _still_waitable(process):
    """True iff the direct child is still ours and has not been consumed.

    WNOWAIT+WNOHANG returns None while the child is still running, its siginfo once
    it has exited without consuming it, and raises ChildProcessError if some other
    waiter already consumed it.
    """
    try:
        info=_waitid_nowait(process.pid)
    except ChildProcessError:
        return False
    return info is None or info.si_pid==process.pid


class Host:
    """Fixture capability, not a production issuer or identity attestation."""
    test_only = True

    def __init__(self):
        self.calls = []
        self.deny = False
        self.fail_final = False

    def admit(self, job, config_digest):
        self.calls.append(('admit', job.digest, config_digest))
        return None if self.deny else object()

    def consume(self, token, job):
        self.calls.append(('consume', job.digest))

    def prepare_server(self, token, job, url, password, deadline):
        self.calls.append(('prepare_server',))

    def check(self, token, job, stage):
        self.calls.append(('check', stage))

    def finalize(self, token, code, launched):
        self.calls.append(('finalize', code, launched))
        if self.fail_final:
            raise RuntimeError('private finalization error')


@unittest.skipUnless(os.name == 'posix', 'POSIX owned process groups required')
class OpenCodeWorkerTests(unittest.TestCase):
    def tight_budget(self):
        """Leave synthetic process startup ample time before timeout cases."""
        return scaled_budget(w.RunBudget(1, 8, 0, 15000, 100, 100))

    def run_client_timeout(self, job, host):
        """Deliver a timeout at the client pump without waiting on wall time."""
        original_pump = w._Pipes.pump

        def expire_at_client(pipes, lifecycles):
            if len(lifecycles) > 1:
                with patch.object(w.time, 'monotonic', return_value=pipes.deadline + 1):
                    return original_pump(pipes, lifecycles)
            return original_pump(pipes, lifecycles)

        with patch.object(w._Pipes, 'pump', expire_at_client):
            return w.run_worker(job, host)

    def job(self, prompt='success', script=FAKE, **changes):
        if 'budget' not in changes:
            changes['budget'] = scaled_budget(RunBudget(1,8,0,3000,100,100))
        temp = tempfile.TemporaryDirectory(prefix='opencode-worker-', dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        project = root/'project'; project.mkdir(mode=0o700)
        (project/'PUBLIC.txt').write_text('synthetic public source\n')
        state = root/'runtime'; state.mkdir(mode=0o700)
        for name in ('home','config','cache','data','state','tmp'):
            (state/name).mkdir(mode=0o700)
        executable = root/'synthetic-cli'
        executable.write_text(_fake_text(script, changes['budget'].wall_clock_ms))
        executable.chmod(0o700)
        values = dict(
            task=TaskBinding('worker-task','a'*32,1,'b'*64),
            agent=AgentBinding('developer','developer-session',Role.DEVELOPER,
                               str(project),str(root/'common/.git'),'feat/developer'),
            source_sha='c'*40, phase='DEVELOP',
            model='opencode/deepseek-v4.1-flash-free', prompt=prompt,
            read_paths=('PUBLIC.txt',), write_paths=('PUBLIC.txt',),
            runtime=w.RuntimePin(str(executable),hashlib.sha256(executable.read_bytes()).hexdigest(),
                                 '2.0.32',str(state)),
            usage=UsageLedger((),0,0),
            max_steps=2)
        values.update(changes)
        return w.WorkerJob(**values)

    def test_real_private_server_and_client_are_owned_reaped_and_source_bound(self):
        job=self.job();host=Host();spawn=subprocess.Popen;child_envs=[]
        def capture_spawn(*args,**kwargs):
            child_envs.append(kwargs['env'])
            return spawn(*args,**kwargs)
        with patch.object(w.subprocess,'Popen',side_effect=capture_spawn):
            result=w.run_worker(job,host)
        self.assertEqual(result.code,'OPENCODE_OUTPUT_RETURNED',result)
        self.assertEqual(result.failure_stage,'UNKNOWN')
        self.assertEqual(result.status,'RETURNED')
        self.assertFalse(result.authorized)
        self.assertEqual(result.evidence_class,'OFFLINE_FIXTURE')
        self.assertEqual(result.runtime_evidence_status,'NOT_VERIFIED')
        self.assertEqual(result.session_id,'ses_syntheticWorker')
        self.assertIn('self-review',result.content)
        self.assertTrue(result.cleanup_confirmed)
        self.assertEqual(len(result.processes),2)
        self.assertEqual([p['kind'] for p in result.processes],['server','client'])
        self.assertTrue(all(p['reaped'] and p['group_absent'] for p in result.processes))
        self.assertEqual(result.usage.calls[-1].input_tokens,10)
        self.assertEqual(result.usage.calls[-1].output_tokens,5)
        self.assertEqual(result.raw_usage,((4,3,2,5,1),))
        self.assertEqual(len(child_envs),2)
        self.assertEqual(child_envs[0],child_envs[1])
        self.assertEqual(child_envs[0]['OPENCODE_DB'],':memory:')
        state=Path(job.runtime.state_root)
        self.assertFalse((state/'data/opencode/opencode.db').exists())
        self.assertFalse(any(b'SYNTHETIC_ONLY' in p.read_bytes()
                             for p in state.rglob('*') if p.is_file()))
        self.assertEqual([x[0] for x in host.calls].count('consume'),1)
        self.assertEqual(job.pending,())
        with patch('llmtool.opencode_worker.subprocess.Popen') as spawn:
            second=w.run_worker(job,host)
        self.assertEqual(second.code,'OPENCODE_ATTEMPT_ALREADY_USED')
        self.assertEqual(second.failure_stage,'PRELAUNCH')
        spawn.assert_not_called()

    def test_stage_assignment_is_optional_for_compatible_host_objects(self):
        class HostWithoutStage:
            __slots__ = ('inner',)
            def __init__(self): object.__setattr__(self,'inner',Host())
            def __getattr__(self,name): return getattr(self.inner,name)
            def __setattr__(self,name,value):
                if name == 'failure_stage': raise AttributeError('stage is not part of this Host')
                object.__setattr__(self,name,value)
        host=HostWithoutStage()
        result=w.run_worker(self.job(),host)
        self.assertEqual(result.code,'OPENCODE_OUTPUT_RETURNED',result)
        self.assertEqual(result.failure_stage,'UNKNOWN')
        self.assertEqual(host.inner.calls[-1][0],'finalize')

    def test_host_denial_and_unknown_prior_usage_never_spawn(self):
        for reason in ('deny','unknown'):
            with self.subTest(reason=reason):
                job=self.job(**({'usage':UsageLedger((CallUsage(CallPhase.PRIMARY,None,None),),0,0)}
                                if reason=='unknown' else {}))
                host=Host();host.deny=reason=='deny'
                with patch('llmtool.opencode_worker.subprocess.Popen') as spawn:
                    result=w.run_worker(job,host)
                self.assertEqual(result.status,'BLOCKED');spawn.assert_not_called()

    def test_auth_unknown_and_incomplete_outputs_never_become_accepted_work(self):
        for mode,code in (('auth','OPENCODE_AUTH_REQUIRED'),('unknown','OPENCODE_USAGE_UNKNOWN'),
                          ('incomplete','OPENCODE_OUTPUT_INCOMPLETE')):
            with self.subTest(mode=mode):
                result=w.run_worker(self.job(mode),Host())
                self.assertEqual(result.code,code,result)
                self.assertEqual(result.status,'BLOCKED')
                self.assertIsNone(result.content)
                self.assertNotIn('PRIVATE_ERROR',repr(result))
                self.assertEqual(result.failure_stage,
                    'CLIENT' if mode == 'auth' else 'EVIDENCE')

    def test_credential_preparation_failure_maps_to_credential_stage(self):
        class CredentialFailureHost(Host):
            def prepare_server(self,*args):
                self.failure_stage='CREDENTIAL'
                raise w.WorkerError('OPENCODE_CREDENTIAL_INVALID')
        result=w.run_worker(self.job(),CredentialFailureHost())
        self.assertEqual(result.code,'OPENCODE_CREDENTIAL_INVALID')
        self.assertEqual(result.failure_stage,'CREDENTIAL')
        self.assertTrue(result.cleanup_confirmed)

    def test_successful_credential_stage_transition_is_not_persisted_as_failure(self):
        class CredentialTransitionHost(Host):
            def __init__(self):
                super().__init__();self.failure_stage='READINESS';self.stage_events=[]
            def prepare_server(self,*args):
                self.failure_stage='CREDENTIAL';self.stage_events.append(self.failure_stage)
                self.failure_stage='READINESS';self.stage_events.append(self.failure_stage)
        host=CredentialTransitionHost()
        result=w.run_worker(self.job(),host)
        self.assertEqual(host.stage_events,['CREDENTIAL','READINESS'])
        self.assertEqual(result.code,'OPENCODE_OUTPUT_RETURNED')
        self.assertEqual(result.failure_stage,'UNKNOWN')

    def test_omitted_native_stream_terminal_requires_actual_persisted_proof(self):
        result=w.run_worker(self.job('omitted-terminal',budget=self.tight_budget()),Host())
        self.assertEqual(result.code,'OPENCODE_OUTPUT_RETURNED',result)
        missing=w.run_worker(self.job('no-proof',budget=self.tight_budget()),Host())
        self.assertEqual(missing.status,'BLOCKED')
        self.assertIsNone(missing.content)

    def test_timeout_and_output_limit_are_bounded_and_no_resend(self):
        for mode,code in (('timeout','OPENCODE_TIMEOUT'),('overflow','OPENCODE_OUTPUT_LIMIT')):
            with self.subTest(mode=mode):
                job=self.job(mode,budget=self.tight_budget())
                result=(self.run_client_timeout(job,Host()) if mode=='timeout'
                        else w.run_worker(job,Host()))
                self.assertEqual(result.code,code,result)
                self.assertTrue(result.cleanup_confirmed)
                self.assertEqual(w.run_worker(job,Host()).code,'OPENCODE_ATTEMPT_ALREADY_USED')

    def test_server_json_enforces_absolute_deadline_during_slow_drip(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        body=json.dumps({'data':{'value':'x'*1000}}).encode()
        class DripHandler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                try:
                    for offset in range(0,len(body),27):
                        self.wfile.write(body[offset:offset+27]);self.wfile.flush()
                        time.sleep(.02)
                except (BrokenPipeError,ConnectionResetError):
                    pass
        server=ThreadingHTTPServer(('127.0.0.1',0),DripHandler)
        server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.005},daemon=True)
        thread.start()
        started=time.monotonic()
        try:
            with self.assertRaises(w.WorkerError) as caught:
                w._server_json('http://127.0.0.1:%d'%server.server_port,
                    '/api/session/ses_syntheticWorker','SYNTHETIC',started+.1)
            elapsed=time.monotonic()-started
            self.assertEqual(caught.exception.worker_code,'OPENCODE_TIMEOUT')
            self.assertLess(elapsed,.4,'slow-drip response exceeded the absolute deadline')
        finally:
            server.shutdown();server.server_close();thread.join(timeout=.2)

    def test_server_json_checks_deadline_after_response_close(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        body=b'{"data":{"ok":true}}'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        create_connection=w.socket.create_connection
        class SlowClose:
            def __init__(self,inner): self.inner=inner
            def __getattr__(self,name): return getattr(self.inner,name)
            def close(self): time.sleep(.15);self.inner.close()
        started=time.monotonic()
        try:
            with patch.object(w.socket,'create_connection',side_effect=lambda *a,**kw:
                              SlowClose(create_connection(*a,**kw))):
                with self.assertRaises(w.WorkerError) as caught:
                    w._server_json('http://127.0.0.1:%d'%server.server_port,
                                   '/api/session/ses_syntheticWorker','SYNTHETIC',started+.1)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=.2)
        self.assertEqual(caught.exception.worker_code,'OPENCODE_TIMEOUT')

    def test_server_json_decodes_keepalive_and_chunked_http11_responses(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        body=json.dumps({'data':{'ok':True}}).encode()
        class FramingHandler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(200)
                if self.path=='/chunked':
                    self.send_header('Transfer-Encoding','chunked');self.end_headers()
                    for chunk in (body[:7],body[7:19],body[19:]):
                        self.wfile.write(('%X\r\n'%len(chunk)).encode()+chunk+b'\r\n')
                    self.wfile.write(b'0\r\n\r\n');self.wfile.flush()
                    return
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Connection','keep-alive');self.end_headers()
                self.wfile.write(body);self.wfile.flush()
        server=ThreadingHTTPServer(('127.0.0.1',0),FramingHandler)
        server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.005},daemon=True)
        thread.start()
        try:
            base='http://127.0.0.1:%d'%server.server_port
            for route in ('/keepalive','/chunked'):
                with self.subTest(route=route):
                    result=w._server_json(base,route,'SYNTHETIC',time.monotonic()+1)
                    self.assertEqual(result,{'ok':True})
        finally:
            server.shutdown();server.server_close();thread.join(timeout=.2)

    def test_server_json_rejects_truncated_content_length(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        body=json.dumps({'data':{'ok':True}}).encode()
        class TruncatedHandler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length',str(len(body)+100))
                self.send_header('Connection','close')
                self.end_headers()
                self.wfile.write(body);self.wfile.flush()
                self.close_connection=True
        server=ThreadingHTTPServer(('127.0.0.1',0),TruncatedHandler)
        server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.005},daemon=True)
        thread.start()
        try:
            with self.assertRaises(w.WorkerError) as caught:
                w._server_json('http://127.0.0.1:%d'%server.server_port,
                    '/truncated','SYNTHETIC',time.monotonic()+1)
            self.assertEqual(caught.exception.worker_code,'OPENCODE_COMPLETION_UNKNOWN')
        finally:
            server.shutdown();server.server_close();thread.join(timeout=.2)

    def test_server_json_enforces_deadline_on_persistent_unframed_response(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        body=json.dumps({'data':{'ok':True}}).encode()
        class PersistentHandler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(200);self.end_headers()
                self.wfile.write(body);self.wfile.flush()
                time.sleep(.3)
        server=ThreadingHTTPServer(('127.0.0.1',0),PersistentHandler)
        server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.005},daemon=True)
        thread.start();started=time.monotonic()
        try:
            with self.assertRaises(w.WorkerError) as caught:
                w._server_json('http://127.0.0.1:%d'%server.server_port,
                    '/persistent','SYNTHETIC',started+.1)
            self.assertEqual(caught.exception.worker_code,'OPENCODE_TIMEOUT')
            self.assertLess(time.monotonic()-started,.4)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=.2)

    def test_server_json_deadline_interrupts_slow_chunk_size_line(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class SlowChunkHandler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(200);self.send_header('Transfer-Encoding','chunked');self.end_headers()
                try:
                    for char in b'0000000000000000000000000000001\r\n':
                        self.wfile.write(bytes([char]));self.wfile.flush();time.sleep(.02)
                except (BrokenPipeError,ConnectionResetError): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),SlowChunkHandler);server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.005},daemon=True)
        thread.start();started=time.monotonic()
        try:
            with self.assertRaises(w.WorkerError) as caught:
                w._server_json('http://127.0.0.1:%d'%server.server_port,
                    '/slow-chunk-line','SYNTHETIC',started+.1)
            self.assertEqual(caught.exception.worker_code,'OPENCODE_TIMEOUT')
            self.assertLess(time.monotonic()-started,.4)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=.2)

    def test_server_json_rejects_conflicting_duplicate_content_lengths(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        body=json.dumps({'data':{'ok':True}}).encode()
        class DuplicateLengthHandler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Content-Length',str(len(body)+100))
                self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),DuplicateLengthHandler);server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with self.assertRaises(w.WorkerError) as caught:
                w._server_json('http://127.0.0.1:%d'%server.server_port,
                    '/duplicate-content-length','SYNTHETIC',time.monotonic()+1)
            self.assertEqual(caught.exception.worker_code,'OPENCODE_COMPLETION_UNKNOWN')
        finally:
            server.shutdown();server.server_close();thread.join(timeout=.2)

    def test_pinned_executable_and_paths_checked_before_any_launch(self):
        job=self.job();Path(job.runtime.executable).write_text('drift')
        with patch('llmtool.opencode_worker.subprocess.Popen') as spawn:
            result=w.run_worker(job,Host())
        self.assertEqual(result.code,'OPENCODE_RUNTIME_INVALID');spawn.assert_not_called()
        self.assertEqual(result.failure_stage,'PRELAUNCH')

    def test_offline_preflight_validates_without_consuming_attempt_or_following_symlink(self):
        job=self.job()
        self.assertIsNone(w.preflight(job))
        self.assertFalse(job._attempted)
        self.assertEqual(job.pending,())
        executable=Path(job.runtime.executable)
        link=executable.parent/'synthetic-preflight-link'
        link.symlink_to(executable)
        object.__setattr__(job.runtime,'executable',str(link))
        with self.assertRaises(w.WorkerError) as caught:
            w.preflight(job)
        self.assertEqual(caught.exception.worker_code,'OPENCODE_RUNTIME_INVALID')
        self.assertFalse(job._attempted)
        self.assertEqual(job.pending,())
        bad_sha=self.job()
        object.__setattr__(bad_sha.runtime,'executable_sha256','0'*64)
        with self.assertRaises(w.WorkerError) as caught:
            w.preflight(bad_sha)
        self.assertEqual(caught.exception.worker_code,'OPENCODE_RUNTIME_INVALID')
        self.assertFalse(bad_sha._attempted)
        self.assertEqual(bad_sha.pending,())
        with self.assertRaises(w.WorkerError) as wrong_type:
            w.preflight(object())
        self.assertEqual(wrong_type.exception.worker_code,'OPENCODE_JOB_INVALID')
        bad_scope=replace(self.job(),read_paths=('PUBLIC.txt','PUBLIC.txt'))
        with self.assertRaises(w.WorkerError) as scope_error:
            w.preflight(bad_scope)
        self.assertEqual(scope_error.exception.worker_code,'OPENCODE_SCOPE_INVALID')
        self.assertFalse(bad_scope._attempted)

    def test_scope_rejects_non_nfc_collisions_and_newly_denied_names(self):
        invalid_paths = ('e\u0301.txt', '.gitmodules', '.gitattributes', '.lfsconfig',
                         '.github/workflows/check.yml')
        for path in invalid_paths:
            with self.subTest(path=path):
                job = replace(self.job(), read_paths=(path,))
                with self.assertRaises(w.WorkerError) as caught:
                    w.preflight(job)
                self.assertEqual(caught.exception.worker_code, 'OPENCODE_SCOPE_INVALID')
        collision = replace(self.job(), read_paths=('FILE.txt', 'file.txt'))
        with self.assertRaises(w.WorkerError) as caught:
            w.preflight(collision)
        self.assertEqual(caught.exception.worker_code, 'OPENCODE_SCOPE_INVALID')
        casefold_collision = replace(self.job(),
            read_paths=('STRASSE.txt', 'Straße.txt'), write_paths=())
        with self.assertRaises(w.WorkerError) as caught:
            w.preflight(casefold_collision)
        self.assertEqual(caught.exception.worker_code, 'OPENCODE_SCOPE_INVALID')
        cross_list_collision = replace(self.job(), read_paths=('Straße.txt',),
            write_paths=('STRASSE.txt',))
        with self.assertRaises(w.WorkerError) as caught:
            w.preflight(cross_list_collision)
        self.assertEqual(caught.exception.worker_code, 'OPENCODE_SCOPE_INVALID')

    def test_scope_component_swap_to_symlink_is_refused_by_no_follow_walk(self):
        job = self.job()
        root = Path(job.agent.root)
        parent = root/'parent'
        parent.mkdir()
        (parent/'file.txt').write_text('synthetic')
        outside = root/'outside'
        outside.mkdir()
        original_open = os.open
        swapped = False

        def swap_before_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == 'parent' and not swapped:
                parent.rename(root/'parent-original')
                parent.symlink_to(outside, target_is_directory=True)
                swapped = True
            return original_open(path, flags, *args, **kwargs)

        with patch.object(w.os, 'open', side_effect=swap_before_open):
            with self.assertRaises(w.WorkerError) as caught:
                w._scope_path_nofollow(root, 'parent/file.txt')
        self.assertTrue(swapped)
        self.assertEqual(caught.exception.worker_code, 'OPENCODE_SCOPE_INVALID')

    def test_preflight_uses_runtime_exception_mapping_without_converting_to_pin_failure(self):
        job=self.job()
        with patch.object(w,'_validate',side_effect=OSError('synthetic')):
            with self.assertRaises(w.WorkerError) as caught:
                w.preflight(job)
        self.assertEqual(caught.exception.worker_code,'OPENCODE_RUNTIME_UNKNOWN')
        self.assertFalse(job._attempted)
        job=self.job()
        with patch.object(w,'_validate',side_effect=RuntimeError('synthetic')):
            with self.assertRaises(w.WorkerError) as caught:
                w.preflight(job)
        self.assertEqual(caught.exception.worker_code,'OPENCODE_HOST_UNKNOWN')
        self.assertFalse(job._attempted)

    def test_hard_linked_pinned_runtime_is_admitted_and_runs(self):
        job=self.job(budget=scaled_budget(LOAD_TOLERANT_BUDGET));executable=Path(job.runtime.executable);alias=executable.parent/'synthetic-cli-alias'
        os.link(executable,alias)
        self.assertEqual(os.lstat(executable).st_nlink,2)
        self.assertEqual(os.lstat(alias).st_ino,os.lstat(executable).st_ino)
        result=w.run_worker(job,Host())
        self.assertEqual(result.code,'OPENCODE_OUTPUT_RETURNED',result)
        self.assertTrue(result.cleanup_confirmed)

    def test_symlinked_group_writable_or_wrong_hash_runtime_is_rejected_before_launch(self):
        def symlink(exe):
            target=exe.parent/'synthetic-target';exe.rename(target);exe.symlink_to(target)
        for name,mutate,pin in (('symlink',symlink,None),('group-writable',lambda exe:exe.chmod(0o770),None),
                                ('wrong-hash',lambda exe:None,'0'*64)):
            with self.subTest(name=name):
                job=self.job();executable=Path(job.runtime.executable)
                if pin is not None:object.__setattr__(job.runtime,'executable_sha256',pin)
                mutate(executable)
                with patch('llmtool.opencode_worker.subprocess.Popen') as spawn:
                    result=w.run_worker(job,Host())
                self.assertEqual(result.code,'OPENCODE_RUNTIME_INVALID',result)
                spawn.assert_not_called()

    def test_native_config_caps_every_request_at_the_output_budget(self):
        job=self.job(budget=scaled_budget(RunBudget(1,8,0,15000,200,128)))
        cfg=w.configuration(job)
        self.assertEqual(cfg['providers'],{'opencode':{'models':{'deepseek-v4.1-flash-free':{
            'limit':{'context':200,'output':128}}}}})
        self.assertEqual(w.run_worker(job,Host()).code,'OPENCODE_OUTPUT_RETURNED')

    def test_native_retry_is_stopped_at_the_first_extra_request(self):
        job=self.job('retry',max_steps=1,budget=scaled_budget(RunBudget(1,8,0,6000,100,100)))
        started=time.monotonic()
        result=w.run_worker(job,Host())
        self.assertEqual(result.code,'OPENCODE_PROVIDER_RETRY_BLOCKED',result)
        self.assertLess(time.monotonic()-started,3)       # well before the fake's 4 s sleep
        self.assertIsNone(result.content)
        self.assertTrue(result.cleanup_confirmed)
        self.assertEqual(w.run_worker(job,Host()).code,'OPENCODE_ATTEMPT_ALREADY_USED')
        self.assertEqual(w._allowed_requests(self.job(max_steps=3,budget=RunBudget(1,2,0,3000,100,100))),2)
        self.assertEqual(w._request_starts(b'{"type":"step_start"}\nnot json\n{"type":"text"}\n'
                                           b'{"type":"text","text":"\\"type\\":\\"step_start\\""}\n'),1)

    def test_prereview_has_no_write_permission_or_other_tool_bypass(self):
        job=self.job(phase='PREREVIEW',model='opencode/glm-5.3-flash-free',write_paths=())
        cfg=w.configuration(job)
        rules=cfg['agents']['llmtool-worker']['permissions']
        self.assertEqual(rules[0],dict(action='*',resource='*',effect='deny'))
        self.assertEqual(rules[1:], [dict(action='read',resource='PUBLIC.txt',effect='allow')])
        self.assertEqual(cfg['permissions'],rules)
        self.assertFalse(cfg['warming']);self.assertFalse(cfg['snapshots'])
        self.assertEqual(cfg['mcp'],{'servers':{}})
        self.assertEqual(w.run_worker(job,Host()).code,'OPENCODE_OUTPUT_RETURNED')

    def test_every_step_usage_model_and_free_cost_are_required(self):
        for mode,code in (('model-drift','OPENCODE_MODEL_DRIFT'),
                          ('zero-usage','OPENCODE_USAGE_UNKNOWN'),
                          ('missing-cache','OPENCODE_USAGE_UNKNOWN'),
                          ('nonzero-cost','OPENCODE_COST_UNKNOWN')):
            with self.subTest(mode=mode):
                result=w.run_worker(self.job(mode,budget=scaled_budget(LOAD_TOLERANT_BUDGET)),Host())
                self.assertEqual(result.code,code,result)
                self.assertIsNone(result.content)
                self.assertTrue(result.cleanup_confirmed)
                self.assertFalse(result.usage.calls[-1].known)

    def test_cancellation_reaps_both_owned_processes_and_retains_attempt_fence(self):
        job=self.job('cancel');host=Host()
        with self.assertRaises(KeyboardInterrupt):
            w.run_worker(job,host)
        self.assertEqual(job.pending,())
        self.assertEqual(host.calls[-1][0],'finalize')
        self.assertIn(('check','after'),host.calls)
        self.assertEqual(w.run_worker(job,host).code,'OPENCODE_ATTEMPT_ALREADY_USED')

    def test_source_drift_and_prepare_failures_cannot_return_content(self):
        for stage in ('before','before-client','after','prepare'):
            with self.subTest(stage=stage):
                host=Host();original=host.check
                def check(token,job,current):
                    if current==stage:raise RuntimeError('PRIVATE_SOURCE_DRIFT')
                    original(token,job,current)
                host.check=check
                if stage=='prepare':
                    host.prepare_server=lambda *args: (_ for _ in ()).throw(RuntimeError('PRIVATE_AUTH'))
                result=w.run_worker(self.job(),host)
                self.assertEqual(result.status,'BLOCKED')
                self.assertIsNone(result.content)
                self.assertNotIn('PRIVATE_',repr(result))
                self.assertTrue(result.cleanup_confirmed)

    def test_unknown_failed_attempt_usage_prevents_fresh_job_admission(self):
        result=w.run_worker(self.job('auth'),Host())
        self.assertFalse(result.usage.calls[-1].known)
        with patch('llmtool.opencode_worker.subprocess.Popen') as spawn:
            later=w.run_worker(self.job(usage=result.usage),Host())
        spawn.assert_not_called()
        self.assertEqual(later.code,'OPENCODE_BUDGET_BLOCKED')

    def test_after_source_reconciliation_runs_on_every_client_failure(self):
        for mode in ('auth','unknown','incomplete','timeout','overflow'):
            with self.subTest(mode=mode):
                host=Host()
                job=self.job(mode,budget=self.tight_budget())
                result=(self.run_client_timeout(job,host) if mode=='timeout'
                        else w.run_worker(job,host))
                self.assertEqual(result.status,'BLOCKED')
                self.assertEqual(host.calls.count(('check','after')),1)

    def test_unknown_cleanup_and_finalize_failure_retain_no_success(self):
        job=self.job()
        with patch('llmtool.providers.cli_lifecycle._group_absent',return_value=False):
            result=w.run_worker(job,Host())
        self.assertEqual(result.code,'OPENCODE_CLEANUP_UNKNOWN')
        self.assertEqual(result.failure_stage,'CLEANUP')
        self.assertIsNone(result.content);self.assertTrue(job.pending)
        self.assertTrue(all(x.process.returncode is not None for x in job.pending))
        host=Host();host.fail_final=True
        self.assertEqual(w.run_worker(self.job(),host).code,'OPENCODE_FINALIZE_UNKNOWN')

    def test_pending_child_poll_allows_slow_exit_without_confirming_group_cleanup(self):
        class SlowExitProcess:
            def __init__(self):
                self.returncode=None
                self.polls=0
            def poll(self):
                self.polls+=1
                if self.polls==1:
                    return None
                self.returncode=-9
                return self.returncode

        process=SlowExitProcess()
        lifecycle=type('Lifecycle',(),{'process':process,'cleanup_confirmed':False})()
        w._reap_pending_processes([lifecycle],timeout=.1,interval=0)
        self.assertEqual(process.returncode,-9)
        self.assertGreaterEqual(process.polls,2)
        self.assertFalse(lifecycle.cleanup_confirmed)

    def test_server_leader_stays_unreaped_until_owned_lifecycle_stop(self):
        observed=[]
        real_exit=w.CLIProcessLifecycle.__exit__
        def record_exit(lifecycle,*args):
            process=lifecycle.process
            observed.append((None if process is None else process.pid,
                             None if process is None else process.returncode,
                             None if process is None or process.returncode is not None
                             else _still_waitable(process)))
            return real_exit(lifecycle,*args)
        with patch.object(w.CLIProcessLifecycle,'__exit__',record_exit):
            result=w.run_worker(self.job(),Host())
        self.assertEqual(result.code,'OPENCODE_OUTPUT_RETURNED',result)
        server=[p for p in result.processes if p['kind']=='server'][0]
        at_stop=[(code,waitable) for pid,code,waitable in observed if pid==server['pid']]
        self.assertEqual(len(at_stop),1)
        self.assertIsNone(at_stop[0][0],'server leader was reaped before lifecycle stop')
        # A bare Popen.returncode proxy is insufficient: a non-WNOWAIT waitid can
        # consume the child without updating returncode. The leader must still be
        # waitable (WNOWAIT returns its siginfo) when the lifecycle takes over.
        self.assertTrue(at_stop[0][1],'server leader was already consumed before lifecycle stop')

    def test_missing_nonreaping_observation_api_fails_closed_without_reaping(self):
        class OsWithout:
            def __init__(self,name): self._missing=name
            def __getattr__(self,name):
                if name==self._missing: raise AttributeError(name)
                return getattr(os,name)
        real_exit=w.CLIProcessLifecycle.__exit__
        real_reject=w.reject
        for missing in ('waitid','WNOWAIT'):
            with self.subTest(missing=missing):
                observed=[];order=[]
                def record_exit(lifecycle,*args):
                    process=lifecycle.process
                    order.append(('exit',None if process is None else process.pid))
                    observed.append((None if process is None else process.pid,
                                     None if process is None else process.returncode,
                                     None if process is None or process.returncode is not None
                                     else _still_waitable(process)))
                    return real_exit(lifecycle,*args)
                def record_reject(code):
                    order.append(('reject',code))
                    return real_reject(code)
                with patch.object(w,'os',OsWithout(missing)), \
                     patch.object(w,'_WAITID_FALLBACK',None,create=True), \
                     patch.object(w.CLIProcessLifecycle,'__exit__',record_exit), \
                     patch.object(w,'reject',record_reject):
                    result=w.run_worker(self.job(budget=scaled_budget(LOAD_TOLERANT_BUDGET)),Host())
                self.assertEqual(result.code,'OPENCODE_CLEANUP_UNKNOWN',result)
                self.assertEqual(result.status,'BLOCKED')
                self.assertIsNone(result.content)
                # No Popen.poll/wait fallback: the leader is still unreaped and
                # waitable when the owned lifecycle takes over and cleans the group.
                server=[p for p in result.processes if p['kind']=='server'][0]
                at_stop=[(code,waitable) for pid,code,waitable in observed if pid==server['pid']]
                self.assertEqual(len(at_stop),1)
                self.assertIsNone(at_stop[0][0])
                self.assertTrue(at_stop[0][1])
                self.assertTrue(result.cleanup_confirmed)
                self.assertTrue(server['reaped'] and server['group_absent'])
                # The fail-closed rejection is applied only after lifecycle teardown,
                # never from inside the finally.
                server_stop=next(i for i,(kind,value) in enumerate(order)
                                 if kind=='exit' and value==server['pid'])
                self.assertTrue(
                    any(kind=='reject' and value=='OPENCODE_CLEANUP_UNKNOWN'
                        for kind,value in order[server_stop+1:]),
                    'fail-closed rejection was not applied after lifecycle teardown')

    def test_nonzero_server_exit_is_rejected_only_after_owned_cleanup(self):
        order=[]
        real_exit=w.CLIProcessLifecycle.__exit__
        real_reject=w.reject
        def record_exit(lifecycle,*args):
            order.append(('exit',None if lifecycle.process is None else lifecycle.process.pid))
            return real_exit(lifecycle,*args)
        def record_reject(code):
            order.append(('reject',code))
            return real_reject(code)
        with patch.object(w.CLIProcessLifecycle,'__exit__',record_exit), \
             patch.object(w,'reject',record_reject):
            result=w.run_worker(self.job(script=SERVER_NONZERO,budget=self.tight_budget()),Host())
        self.assertEqual(result.code,'OPENCODE_RUNTIME_UNKNOWN',result)
        self.assertEqual(result.status,'BLOCKED')
        self.assertIsNone(result.content)
        self.assertTrue(result.cleanup_confirmed)
        facts={p['kind']:p for p in result.processes}
        self.assertTrue(facts['server']['reaped'])
        self.assertEqual(facts['server']['exit'],3)
        self.assertTrue(facts['server']['group_absent'])
        self.assertTrue(facts['client']['reaped'])
        # Ordering proof: the non-zero check must run AFTER the server lifecycle
        # exit (reap), not before it on an unreaped (None != 0) leader.
        server_stop=next(i for i,(kind,value) in enumerate(order)
                         if kind=='exit' and value==facts['server']['pid'])
        runtime_reject=next(i for i,(kind,value) in enumerate(order)
                            if kind=='reject' and value=='OPENCODE_RUNTIME_UNKNOWN')
        self.assertLess(server_stop,runtime_reject,
                        'non-zero check ran before owned cleanup')

    def test_nonreaping_observation_is_bounded_and_does_not_reap(self):
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(.5)'])
        try:
            start=time.monotonic()
            self.assertFalse(w._observe_exit_without_reap(
                process,.05,time.monotonic()+2))
            self.assertLess(time.monotonic()-start,.3)
            self.assertIsNone(process.returncode)
            # A deadline sooner than the caller timeout must bound the wait.
            start=time.monotonic()
            self.assertFalse(w._observe_exit_without_reap(
                process,2,time.monotonic()+.05))
            self.assertLess(time.monotonic()-start,.3)
            self.assertIsNone(process.returncode)
            self.assertFalse(w._observe_exit_without_reap(
                process,0,time.monotonic()))
            self.assertIsNone(process.returncode)
            start=time.monotonic()
            self.assertFalse(w._observe_exit_without_reap(
                process,.2,time.monotonic()-1))
            self.assertLess(time.monotonic()-start,.05)
            self.assertIsNone(process.returncode)
            self.assertTrue(w._observe_exit_without_reap(
                process,2,time.monotonic()+2))
            self.assertIsNone(process.returncode)
            # WNOWAIT must leave the child waitable: a second WNOWAIT observation still
            # returns its siginfo rather than ChildProcessError, proving no early reap.
            info=_waitid_nowait(process.pid)
            self.assertIsNotNone(info)
            self.assertEqual(info.si_pid,process.pid)
            self.assertIsNone(process.returncode)
        finally:
            process.wait(timeout=5)

    def test_nonreaping_observation_errors_fail_closed(self):
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(.2)'])
        try:
            with patch.object(w.os,'waitid',side_effect=ChildProcessError('gone'),create=True):
                with self.assertRaises(w.WorkerError) as caught:
                    w._observe_exit_without_reap(process,.2,time.monotonic()+2)
            self.assertEqual(caught.exception.worker_code,'OPENCODE_CLEANUP_UNKNOWN')
            self.assertIsNone(process.returncode)
        finally:
            process.wait(timeout=5)

    def test_failed_observation_is_cleanup_stage_after_successful_lifecycle_teardown(self):
        host=Host()
        with patch.object(w.os,'waitid',side_effect=ChildProcessError('gone'),create=True):
            result=w.run_worker(self.job(),host)
        self.assertEqual(result.code,'OPENCODE_CLEANUP_UNKNOWN',result)
        self.assertTrue(result.cleanup_confirmed)
        self.assertEqual(result.failure_stage,'CLEANUP')
        self.assertEqual(host.failure_stage,'CLEANUP')
        self.assertEqual(host.calls[-1],('finalize','OPENCODE_CLEANUP_UNKNOWN',True))

    def test_nonreaping_observation_rejects_foreign_identity_and_error_types(self):
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(.4)'])
        try:
            foreign=type('ForeignSignalInfo',(),{'si_pid':process.pid+999999})()
            for label,effect,result in (('foreign-pid',None,foreign),
                                        ('missing-si-pid',None,type('NoPid',(),{})()),
                                        ('os-error',OSError(5,'i/o error'),None),
                                        ('value-error',ValueError('bad id'),None),
                                        ('type-error',TypeError('bad option'),None)):
                with self.subTest(label=label):
                    with patch.object(w.os,'waitid',side_effect=effect,return_value=result,create=True):
                        with self.assertRaises(w.WorkerError) as caught:
                            w._observe_exit_without_reap(process,.2,time.monotonic()+2)
                    self.assertEqual(caught.exception.worker_code,'OPENCODE_CLEANUP_UNKNOWN')
                    self.assertIsNone(process.returncode)
        finally:
            process.wait(timeout=5)

    def test_cancellation_survives_failed_nonreaping_observation(self):
        # A fail-closed observation must not mask an in-flight cancellation: the
        # unsupported-API condition is recorded, the still-unreaped leader passes to
        # owned lifecycle teardown, and the cancellation still unwinds.
        class OsWithout:
            def __init__(self,name): self._missing=name
            def __getattr__(self,name):
                if name==self._missing: raise AttributeError(name)
                return getattr(os,name)
        job=self.job('cancel')
        host=Host()
        with patch.object(w,'os',OsWithout('WNOWAIT')):
            with self.assertRaises(KeyboardInterrupt):
                w.run_worker(job,host)
        self.assertEqual(job.pending,())
        self.assertEqual(host.failure_stage,'CLEANUP')

    def test_earlier_worker_error_wins_over_failed_observation(self):
        # A fail-closed observation must also not replace an earlier non-cancellation
        # WorkerError: the original code is preserved after owned lifecycle teardown.
        class OsWithout:
            def __init__(self,name): self._missing=name
            def __getattr__(self,name):
                if name==self._missing: raise AttributeError(name)
                return getattr(os,name)
        with patch.object(w,'os',OsWithout('WNOWAIT')):
            host=Host()
            result=w.run_worker(self.job('auth'),host)
        self.assertEqual(result.code,'OPENCODE_AUTH_REQUIRED',result)
        self.assertTrue(result.cleanup_confirmed)
        self.assertEqual(result.failure_stage,'CLEANUP')
        self.assertEqual(host.failure_stage,'CLEANUP')

    def test_nonreaping_observation_rejects_missing_constants_and_bad_pid(self):
        class OsWithout:
            def __init__(self,name): self._missing=name
            def __getattr__(self,name):
                if name==self._missing: raise AttributeError(name)
                return getattr(os,name)
        for missing in ('P_PID','WEXITED','WNOHANG'):
            with self.subTest(missing=missing):
                with patch.object(w,'os',OsWithout(missing)):
                    with self.assertRaises(w.WorkerError) as caught:
                        w._observe_exit_without_reap(
                            type('Pid',(),{'pid':1})(),.2,time.monotonic()+2)
                self.assertEqual(caught.exception.worker_code,'OPENCODE_CLEANUP_UNKNOWN')
        for pid in (0,-1,None,'1',1.5):
            with self.subTest(pid=pid):
                with self.assertRaises(w.WorkerError) as caught:
                    w._observe_exit_without_reap(
                        type('Pid',(),{'pid':pid})(),.2,time.monotonic()+2)
                self.assertEqual(caught.exception.worker_code,'OPENCODE_CLEANUP_UNKNOWN')

    def test_nonreaping_observation_rejects_zero_wait_flags(self):
        # A zero WEXITED/WNOHANG/WNOWAIT would drop the exit/non-blocking/non-consuming
        # flag and change the wait semantics.
        for zero in ('WEXITED','WNOHANG','WNOWAIT'):
            with self.subTest(zero=zero):
                with patch.object(w.os,zero,0):
                    with self.assertRaises(w.WorkerError) as caught:
                        w._observe_exit_without_reap(
                            type('Pid',(),{'pid':1})(),.2,time.monotonic()+2)
                self.assertEqual(caught.exception.worker_code,'OPENCODE_CLEANUP_UNKNOWN')

    def test_native_owned_group_descendant_cleanup_is_bounded_and_trusted(self):
        job=self.job(script=NATIVE_FAKE,budget=scaled_budget(RunBudget(1,8,0,8000,100,100)))
        start=time.monotonic()
        result=w.run_worker(job,Host())
        elapsed=time.monotonic()-start
        self.assertEqual(result.code,'OPENCODE_OUTPUT_RETURNED',result)
        self.assertTrue(result.cleanup_confirmed)
        self.assertIn('self-review',result.content)
        facts={p['kind']:p for p in result.processes}
        server=facts['server'];client=facts['client']
        self.assertTrue(server['reaped']);self.assertEqual(server['exit'],0)
        self.assertTrue(server['group_absent'])
        self.assertTrue(client['reaped']);self.assertEqual(client['exit'],0)
        self.assertLess(elapsed,8.0)
        descendant=json.loads((Path(job.agent.root)/'SERVER_DESCENDANT.json').read_text())
        self.assertEqual(descendant['pgid'],server['pid'])
        self.assertNotEqual(descendant['pid'],server['pid'])
        # Lifecycle-independent proof the descendant is gone, not merely that it would
        # have self-expired: the owned group cleanup must have killed it, so its PID
        # must be ESRCH within a short bound.
        end=time.monotonic()+2
        while True:
            try: os.kill(descendant['pid'],0)
            except ProcessLookupError: break
            if time.monotonic()>end: self.fail('descendant survived owned group cleanup')
            time.sleep(.02)


@unittest.skipUnless(os.name == 'posix', 'POSIX hard-link and fd identity required')
class RuntimeFileHashTests(unittest.TestCase):
    """Executable content pin: a hard-linked runtime stays compatible, while an alias,
    mode, owner or inode swap stays fail-closed."""

    def _runtime(self):
        temp=tempfile.TemporaryDirectory(prefix='opencode-hash-',dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temp.cleanup)
        executable=Path(temp.name)/'runtime'
        executable.write_bytes(b'synthetic pinned runtime\n');executable.chmod(0o700)
        return executable

    @staticmethod
    def _digest(executable):
        return hashlib.sha256(executable.read_bytes()).hexdigest()

    @staticmethod
    def _restat(path,ino=None,uid=None):
        info=os.lstat(path)
        return os.stat_result((info.st_mode,info.st_ino if ino is None else ino,info.st_dev,info.st_nlink,
            info.st_uid if uid is None else uid,info.st_gid,info.st_size,
            info.st_atime,info.st_mtime,info.st_ctime))

    def test_single_link_pinned_runtime_hash_is_unchanged(self):
        executable=self._runtime()
        self.assertEqual(os.lstat(executable).st_nlink,1)
        self.assertEqual(w._file_hash(executable,time.monotonic()+5),self._digest(executable))

    def test_same_inode_hard_link_count_two_is_admitted(self):
        executable=self._runtime();alias=executable.parent/'runtime-alias'
        os.link(executable,alias)
        self.assertEqual(os.lstat(executable).st_nlink,2)
        self.assertEqual(os.lstat(alias).st_ino,os.lstat(executable).st_ino)
        self.assertEqual(w._file_hash(executable,time.monotonic()+5),self._digest(executable))

    def test_symlink_runtime_is_rejected(self):
        executable=self._runtime();link=executable.parent/'runtime-link';link.symlink_to(executable)
        with self.assertRaises(w.WorkerError) as caught:
            w._file_hash(link,time.monotonic()+5)
        self.assertEqual(caught.exception.worker_code,'OPENCODE_RUNTIME_INVALID')

    def test_group_writable_runtime_is_rejected(self):
        executable=self._runtime();executable.chmod(0o770)
        with self.assertRaises(w.WorkerError) as caught:
            w._file_hash(executable,time.monotonic()+5)
        self.assertEqual(caught.exception.worker_code,'OPENCODE_RUNTIME_INVALID')

    def test_foreign_owner_is_rejected(self):
        executable=self._runtime()
        with patch.object(Path,'lstat',return_value=self._restat(executable,uid=os.getuid()+1)):
            with self.assertRaises(w.WorkerError) as caught:
                w._file_hash(executable,time.monotonic()+5)
        self.assertEqual(caught.exception.worker_code,'OPENCODE_RUNTIME_INVALID')

    def test_inode_swap_between_stat_and_open_is_rejected(self):
        executable=self._runtime()
        with patch.object(w.os,'fstat',return_value=self._restat(executable,ino=os.lstat(executable).st_ino+1)):
            with self.assertRaises(w.WorkerError) as caught:
                w._file_hash(executable,time.monotonic()+5)
        self.assertEqual(caught.exception.worker_code,'OPENCODE_RUNTIME_INVALID')


if __name__=='__main__':
    unittest.main()
