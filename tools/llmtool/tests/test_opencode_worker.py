"""Host-owned foreground jobs using private synthetic executables, never models."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from llmtool import opencode_worker as w
from llmtool.action_contract import TaskBinding
from llmtool.run_budget import RunBudget, UsageLedger, CallUsage, CallPhase
from llmtool.task_lifecycle import AgentBinding, Role


FAKE = '''#!PYTHON -IB
import json,signal,sys,time,sqlite3,os
from pathlib import Path
signal.alarm(8)
if sys.argv[1]=='serve':
 print(json.dumps({'url':'http://127.0.0.1:45678'}),flush=True)
 sys.stdin.buffer.read()
else:
 mode=sys.stdin.read()
 if mode=='timeout': time.sleep(4)
 sid='ses_syntheticWorker'
 def emit(kind,**values):print(json.dumps(dict(type=kind,timestamp=1,sessionID=sid,**values)),flush=True)
 if mode=='auth':
  emit('error',error={'type':'provider.auth','status':403,'message':'PRIVATE_ERROR_NOT_FOR_OUTPUT'})
  sys.exit(1)
 if mode=='cancel': os.kill(os.getppid(),signal.SIGINT);time.sleep(4)
 if mode=='overflow': print('x'*1100000);sys.exit()
 emit('step_start',part={'type':'step-start','messageID':'m1'})
 emit('text',part={'type':'text','messageID':'m1','text':'synthetic candidate; self-review recorded'})
 if mode!='no-proof':
  dbdir=Path(os.environ['XDG_DATA_HOME'])/'opencode';dbdir.mkdir(mode=0o700,exist_ok=True)
  db=sqlite3.connect(dbdir/'opencode.db')
  db.execute('CREATE TABLE session_v2(id,directory,version,agent,model,title,time_created,time_idle,idle_outcome,tokens_input,tokens_output,cost,tokens_reasoning,tokens_cache_read,tokens_cache_write)')
  db.execute('CREATE TABLE session_message(id,session_id,type,seq,data)')
  args=sys.argv;model=args[args.index('--model')+1].split('/',1);title=args[args.index('--title')+1]
  at=int(time.time()*1000);tokens={'input':4,'output':3,'reasoning':2,'cache':{'read':5,'write':1}} if mode!='unknown' else {}
  item={'agent':'llmtool-worker','model':{'providerID':model[0],'id':model[1]},
        'time':{'completed':at},'finish':'length' if mode=='incomplete' else 'stop',
        'content':[{'type':'text','text':'synthetic candidate; self-review recorded'}],
        'tokens':tokens,'cost':0}
  if mode=='model-drift':item['model']['id']='UNAPPROVED'
  if mode=='zero-usage':item['tokens']={'input':0,'output':0,'reasoning':0,'cache':{'read':0,'write':0}}
  if mode=='missing-cache':item['tokens'].pop('cache',None)
  if mode=='nonzero-cost':item['cost']=1
  db.execute('INSERT INTO session_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
   (sid,os.getcwd(),'2.0.32','llmtool-worker',json.dumps(item['model']),title,at,at,'succeeded',4,3,0,2,5,1))
  db.execute('INSERT INTO session_message VALUES(?,?,?,?,?)',('m1',sid,'assistant',1,json.dumps(item)))
  db.execute('INSERT INTO session_message VALUES(?,?,?,?,?)',('i1',sid,'idle',2,json.dumps({'outcome':'succeeded'})))
  db.commit();db.close()
 finish={'type':'step-finish','messageID':'m1','reason':'stop'}
 if mode!='unknown': finish['tokens']={'input':4,'output':3}
 if mode=='incomplete':finish['reason']='length'
 if mode!='omitted-terminal':emit('step_finish',part=finish)
'''


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
    def job(self, prompt='success', **changes):
        temp = tempfile.TemporaryDirectory(prefix='opencode-worker-', dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        project = root/'project'; project.mkdir(mode=0o700)
        (project/'PUBLIC.txt').write_text('synthetic public source\n')
        state = root/'runtime'; state.mkdir(mode=0o700)
        for name in ('home','config','cache','data','state','tmp'):
            (state/name).mkdir(mode=0o700)
        executable = root/'synthetic-cli'
        executable.write_text(FAKE.replace('PYTHON', sys.executable)); executable.chmod(0o700)
        values = dict(
            task=TaskBinding('worker-task','a'*32,1,'b'*64),
            agent=AgentBinding('developer','developer-session',Role.DEVELOPER,
                               str(project),str(root/'common/.git'),'feat/developer'),
            source_sha='c'*40, phase='DEVELOP',
            model='opencode/deepseek-v4.1-flash-free', prompt=prompt,
            read_paths=('PUBLIC.txt',), write_paths=('PUBLIC.txt',),
            runtime=w.RuntimePin(str(executable),hashlib.sha256(executable.read_bytes()).hexdigest(),
                                 '2.0.32',str(state)),
            budget=RunBudget(1,8,0,3000,100,100), usage=UsageLedger((),0,0),
            max_steps=2)
        values.update(changes)
        return w.WorkerJob(**values)

    def test_real_private_server_and_client_are_owned_reaped_and_source_bound(self):
        job=self.job();host=Host()
        result=w.run_worker(job,host)
        self.assertEqual(result.code,'OPENCODE_OUTPUT_RETURNED',result)
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
        self.assertEqual([x[0] for x in host.calls].count('consume'),1)
        self.assertEqual(job.pending,())
        with patch('llmtool.opencode_worker.subprocess.Popen') as spawn:
            second=w.run_worker(job,host)
        self.assertEqual(second.code,'OPENCODE_ATTEMPT_ALREADY_USED')
        spawn.assert_not_called()

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

    def test_omitted_native_stream_terminal_requires_actual_persisted_proof(self):
        result=w.run_worker(self.job('omitted-terminal'),Host())
        self.assertEqual(result.code,'OPENCODE_OUTPUT_RETURNED',result)
        missing=w.run_worker(self.job('no-proof'),Host())
        self.assertEqual(missing.status,'BLOCKED')
        self.assertIsNone(missing.content)

    def test_timeout_and_output_limit_are_bounded_and_no_resend(self):
        for mode,code in (('timeout','OPENCODE_TIMEOUT'),('overflow','OPENCODE_OUTPUT_LIMIT')):
            with self.subTest(mode=mode):
                job=self.job(mode,budget=RunBudget(1,8,0,1200,100,100))
                result=w.run_worker(job,Host())
                self.assertEqual(result.code,code,result)
                self.assertTrue(result.cleanup_confirmed)
                self.assertEqual(w.run_worker(job,Host()).code,'OPENCODE_ATTEMPT_ALREADY_USED')

    def test_pinned_executable_and_paths_checked_before_any_launch(self):
        job=self.job();Path(job.runtime.executable).write_text('drift')
        with patch('llmtool.opencode_worker.subprocess.Popen') as spawn:
            result=w.run_worker(job,Host())
        self.assertEqual(result.code,'OPENCODE_RUNTIME_INVALID');spawn.assert_not_called()

    def test_hard_linked_pinned_runtime_is_admitted_and_runs(self):
        job=self.job();executable=Path(job.runtime.executable);alias=executable.parent/'synthetic-cli-alias'
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
                result=w.run_worker(self.job(mode),Host())
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
                result=w.run_worker(self.job(mode,budget=RunBudget(1,8,0,1200,100,100)),host)
                self.assertEqual(result.status,'BLOCKED')
                self.assertEqual(host.calls.count(('check','after')),1)

    def test_unknown_cleanup_and_finalize_failure_retain_no_success(self):
        job=self.job()
        with patch('llmtool.providers.cli_lifecycle._group_absent',return_value=False):
            result=w.run_worker(job,Host())
        self.assertEqual(result.code,'OPENCODE_CLEANUP_UNKNOWN')
        self.assertIsNone(result.content);self.assertTrue(job.pending)
        self.assertTrue(all(x.process.returncode is not None for x in job.pending))
        host=Host();host.fail_final=True
        self.assertEqual(w.run_worker(self.job(),host).code,'OPENCODE_FINALIZE_UNKNOWN')


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
