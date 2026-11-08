"""Synthetic receipt writers are fixtures, never the original Claude Reviewer."""
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_exchange as x
from llmtool.errors import LLMToolError
from llmtool import task_lifecycle as t, task_verification as v
from test_review_bundle import records
import review_trust_fixture


def setUpModule(): review_trust_fixture.start_module()
def tearDownModule(): review_trust_fixture.stop_module()


# Fixture trust roots: workspace_root + '/codex' and canonical_root (#369 PR-6, Q6).
AUTHOR_ROOT = review_trust_fixture.OWNER_WORKSPACE_ROOT + '/codex'
REVIEW_ROOT = review_trust_fixture.OWNER_CANONICAL_ROOT


class TrustRootTests(unittest.TestCase):
    def test_roots_are_read_from_trust_at_use_time(self):
        self.assertEqual((x.author_root(), x.review_root()), (AUTHOR_ROOT, REVIEW_ROOT))
        other = review_trust_fixture.make_trust(canonical_root='/fixture/canonical',
                                                workspace_root='/fixture/workspace')
        with review_trust_fixture.use_trust(other):
            self.assertEqual((x.author_root(), x.review_root()),
                             ('/fixture/workspace/codex', '/fixture/canonical'))

    def test_no_legacy_root_constants_remain(self):
        self.assertFalse(hasattr(x, 'AUTHOR_ROOT') or hasattr(x, 'REVIEW_ROOT'))


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None)
        self.folder = Path(self.tmp.name); self.folder.chmod(0o700)
        self.root = Path.cwd()
        contract, expect, events = records(Path(AUTHOR_ROOT),'1'*40,'feat/synthetic')
        dev = replace(expect.agents[0],session_id=x.AUTHOR,
                      common_dir=REVIEW_ROOT+'/.git')
        reviewer = replace(expect.agents[1],session_id=x.REVIEWER,root=REVIEW_ROOT,
                           common_dir=dev.common_dir,branch='main')
        expect = replace(expect,agents=(dev,reviewer),authored_by=(dev,))
        changed = []
        for event in events:
            h = replace(event.handoff,from_agent=dev,to_agent=reviewer) if event.handoff else None
            changed.append(replace(event,agent=dev,handoff=h))
        self.contract,self.expect,self.events = contract,expect,tuple(changed)
        refs = {}
        for label,raw in (('contract',contract.to_json()),('expect',expect.to_json()),
                          ('events','\n'.join(e.to_json() for e in changed)+'\n')):
            path = self.folder/(label+'.json'); self.write(path,raw.encode())
            refs[label] = self.ref(path)
        index = self.folder/'evidence.json'
        self.write(index,x.canonical({'schema_version':1,'source_sha':expect.source_sha,'artifacts':[]}))
        refs['evidence'] = self.ref(index)
        state = t.inspect_task(contract,expect,self.events,x.stamp(x.now()))
        self.bundle = {'schema_version':1,'task':expect.task.to_dict(),'source_sha':expect.source_sha,
            'base_sha':'2'*40,'epoch':expect.epoch,'inputs':refs,
            'structural_validation':'SUCCESS','reported_state':'WAITING_REVIEW',
            'handoff_preview':state.to_dict()['handoff_preview'],
            'local_facts':{'root':dev.root,'branch':dev.branch,'common_dir':dev.common_dir,
                'source_sha':expect.source_sha,'repository':'davidcatcat/davidcattoolsproject','clean_tracked':True},
            'local_facts_provenance':'LOCAL_GIT_FACTS','identity_provenance':'CALLER_REPORTED',
            'identity_attestation':'NOT_VERIFIED','manifest':[{'path':'src/app.py','base':None,
                'source':{'mode':'100644','blob':'3'*40}}],'manifest_complete':True,
            'dependency_closure':False,'artifacts':[],'review_request':'Synthetic fixture DATA',
            'authorized':False,'runtime_evidence_status':'NOT_VERIFIED','provider_calls':0}
        self.bundle_path = self.folder/'bundle.json'; self.write(self.bundle_path,x.canonical(self.bundle))
        self.request_path = self.folder/'request.json'
        self.request_args = SimpleNamespace(action='review-request',bundle=str(self.bundle_path),
            bundle_sha256=self.ref(self.bundle_path)['sha256'],expect=refs['expect']['path'],
            output=str(self.request_path),json=True)

    def write(self,path,data):
        Path(path).write_bytes(data); Path(path).chmod(0o600)

    def ref(self,path):
        raw = Path(path).read_bytes()
        return {'path':str(path),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}

    def prepare(self):
        # Synthetic original-Session DATA, not a production identity bypass.
        with patch.object(x.Path,'cwd',return_value=Path(AUTHOR_ROOT)), \
             patch.dict(os.environ,{'CODEX_THREAD_ID':x.AUTHOR}):
            result = x.prepare_request(self.request_args,time.monotonic()+10)
        self.request = json.loads(self.request_path.read_text())
        self.request_sha = result['request_sha256']
        self.wait_args = SimpleNamespace(action='review-wait',request=str(self.request_path),
            request_sha256=self.request_sha,receipt=self.request['receipt_path'],
            verdict=self.request['verdict_path'],timeout=0,output=str(self.folder/'report.json'),json=True)
        return result

    def envelopes(self):
        receipt = {**x._common(self.request,self.request_sha,'REVIEW_RECEIPT'),
                   'received_at':x.stamp(x.now()),'bundle_read':True}
        raw = x.canonical(receipt)+b'\n'
        verdict = {**x._common(self.request,self.request_sha,'REVIEW_VERDICT'),
            'receipt_sha256':hashlib.sha256(raw).hexdigest(),
            'reviewed_sha':self.expect.source_sha,'completed_at':x.stamp(x.now()),
            'security':'APPROVE','functional':'APPROVE','findings':[],
            'criterion_evidence':[{'criterion_id':'artifact','evidence_refs':['source-manifest']}]}
        return receipt,verdict

    def publish(self,path,value):
        # Explicitly synthetic writer; never original Reviewer evidence.
        raw = x.canonical(value)+b'\n'; self.write(path,raw)
        self.write(path+'.ready',x.canonical({'schema_version':1,'artifact_sha256':hashlib.sha256(raw).hexdigest()}))

    def tearDown(self): self.tmp.cleanup()

    def test_no_module_import_task_io_or_spawn(self):
        import importlib
        # A reload makes new objects for every name in the module. Put every
        # old attribute back after the test, pass or fail, so modules that
        # imported names before still see the same objects.
        saved = dict(vars(x))
        def restore():
            namespace = vars(x)
            namespace.clear()
            namespace.update(saved)
        self.addCleanup(restore)
        with patch('subprocess.Popen',side_effect=AssertionError('spawn')), \
             patch('socket.create_connection',side_effect=AssertionError('network')):
            importlib.reload(x)

    def test_bounded_parser_rejects_duplicate_unicode_and_unknown(self):
        for text in ('{"a":1,"a":2}', '['*17+']'*17, '{"a":"\\ud800"}'):
            with self.subTest(text=text), self.assertRaises((LLMToolError,UnicodeError)):
                x.parse_envelope(text,1024)

    def test_private_reader_rejects_symlink_fifo_oversize_alias_and_mode(self):
        p = self.folder/'data.json'; p.write_bytes(b'{}'); p.chmod(0o600)
        reader = x.PrivateData(self.root,time.monotonic()+10)
        self.assertEqual(reader.read(str(p),64)[0],b'{}')
        with self.assertRaises(LLMToolError): reader.read(str(p),64)
        p.chmod(0o644)
        with self.assertRaises(LLMToolError): x.PrivateData(self.root,time.monotonic()+10).read(str(p),64)
        p.chmod(0o600); (self.folder/'link').symlink_to(p)
        os.mkfifo(self.folder/'fifo',0o600)
        for target in (self.folder/'link',self.folder/'fifo'):
            with self.assertRaises((LLMToolError,OSError)):
                x.PrivateData(self.root,time.monotonic()+10).read(str(target),64)
        with self.assertRaises(LLMToolError):
            x.PrivateData(self.root,time.monotonic()+10).read(str(p),1)

    def test_request_prepared_is_not_delivery_and_has_static_writer_examples(self):
        result = self.prepare()
        self.assertEqual(result['status'],'REQUEST_PREPARED')
        self.assertNotIn('RECEIVED',json.dumps(result))
        self.assertEqual(self.request['reviewer']['session_id'],x.REVIEWER)
        self.assertEqual(self.request['developer']['session_id'],x.AUTHOR)
        self.assertIn('Never overwrite',self.request['instructions'])
        self.assertEqual(self.request_path.stat().st_mode & 0o777,0o600)
        self.assertLessEqual((x.utc_time(self.request['expires_at'])-x.utc_time(self.request['created_at'])).total_seconds(),900)

    def test_actual_handoff_wins_over_earlier_same_session_binding(self):
        alternate = replace(self.expect.agents[1],agent_id='other-reviewer')
        self.expect = replace(self.expect,agents=(self.expect.agents[0],alternate,self.expect.agents[1]))
        expected_path = Path(self.request_args.expect)
        self.write(expected_path,self.expect.to_json().encode())
        self.bundle['inputs']['expect'] = self.ref(expected_path)
        self.write(self.bundle_path,x.canonical(self.bundle))
        self.request_args.bundle_sha256 = self.ref(self.bundle_path)['sha256']
        self.prepare()
        self.assertEqual(self.request['reviewer']['agent_id'],'reviewer')
        result = x.wait_report(self.wait_args,time.monotonic()+1)
        self.assertEqual(result['status'],'WAIT_TIMEOUT')

    def test_actual_file_receipt_then_result_roundtrip_without_fake_file_io(self):
        self.prepare(); self.wait_args.timeout = 3
        receipt,verdict = self.envelopes(); errors = []
        def actor():
            try:
                time.sleep(0.1); self.publish(self.wait_args.receipt,receipt)
                time.sleep(0.5); self.publish(self.wait_args.verdict,verdict)
            except Exception as error: errors.append(error)
        thread = threading.Thread(target=actor); thread.start()
        try:
            with patch('subprocess.Popen',side_effect=AssertionError('spawn')), \
                 patch('socket.create_connection',side_effect=AssertionError('network')):
                result = x.wait_report(self.wait_args,time.monotonic()+3)
        finally: thread.join(timeout=3)
        self.assertFalse(errors); self.assertFalse(thread.is_alive())
        self.assertEqual(result['status'],'BOUND_REVIEW_REPORT')
        self.assertTrue(result['observations']['RECEIPT_REPORTED'])
        self.assertTrue(result['observations']['VERDICT_REPORTED'])
        self.assertEqual(result['runtime_evidence_status'],'NOT_VERIFIED')
        self.assertFalse(result['authorized'])
        self.assertNotIn(x.REVIEWER,json.dumps(result))

    def test_immediate_and_finite_timeout_preserve_partial_receipt(self):
        self.prepare()
        # Uncommitted artifact is never parsed or counted as received.
        self.write(self.wait_args.receipt,b'{partial')
        result = x.wait_report(self.wait_args,time.monotonic()+1)
        self.assertEqual(result['status'],'WAIT_TIMEOUT')
        self.assertFalse(result['observations']['RECEIPT_REPORTED'])
        self.wait_args.output = str(self.folder/'partial-report.json')
        receipt,_ = self.envelopes(); self.publish(self.wait_args.receipt,receipt)
        result = x.wait_report(self.wait_args,time.monotonic()+1)
        self.assertEqual(result['status'],'WAIT_TIMEOUT')
        self.assertTrue(result['observations']['RECEIPT_REPORTED'])
        self.assertFalse(result['observations']['VERDICT_REPORTED'])

    def test_positive_timeout_is_bounded_without_background_worker(self):
        self.prepare(); self.wait_args.timeout = 1
        start = time.monotonic()
        result = x.wait_report(self.wait_args,start+1)
        self.assertEqual(result['status'],'WAIT_TIMEOUT')
        self.assertGreaterEqual(time.monotonic()-start,0.5)
        self.assertLess(time.monotonic()-start,1.5)

    def test_owned_foreground_cli_interrupt_really_exits(self):
        self.prepare(); ready = self.folder/'wait-ready.marker'
        command = review_trust_fixture.cli_command('task','review-wait',
            '--request',str(self.request_path),'--request-sha256',self.request_sha,
            '--receipt',self.wait_args.receipt,'--verdict',self.wait_args.verdict,
            '--output',self.wait_args.output,'--timeout','30','--json',
            wait_ready_file=ready)
        env = dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        child = subprocess.Popen(command,env=env,stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        try:
            # The child must reach the review-wait polling loop (request and
            # trust validated) and still be running before it is interrupted.
            limit = time.monotonic()+10
            while not ready.exists() and child.poll() is None and time.monotonic() < limit:
                time.sleep(0.05)
            self.assertIsNone(child.poll(),'child exited before it was interrupted')
            self.assertTrue(ready.exists(),'child never reached the wait loop')
            interrupted_at = time.monotonic()
            child.send_signal(signal.SIGINT)
            stdout,_ = child.communicate(timeout=5)
            self.assertLess(time.monotonic()-interrupted_at,5)
        finally:
            if child.poll() is None:
                child.kill(); child.communicate(timeout=5)
            child.stdout.close(); child.stderr.close()
        self.assertEqual(child.returncode,130)
        output = json.loads(stdout)
        self.assertEqual(output['status'],'BLOCKED')
        self.assertEqual(output['code'],'EXCHANGE_INTERRUPTED')
        self.assertFalse(output['authorized'])
        self.assertFalse(Path(self.wait_args.output).exists())

    def test_blocked_verdict_is_received_without_becoming_review_pass(self):
        self.prepare(); receipt,verdict = self.envelopes()
        verdict['security'] = verdict['functional'] = 'BLOCKED'
        self.publish(self.wait_args.receipt,receipt); self.publish(self.wait_args.verdict,verdict)
        result = x.wait_report(self.wait_args,time.monotonic()+1)
        self.assertEqual(result['status'],'BOUND_REVIEW_REPORT')
        data = json.loads(Path(self.wait_args.output).read_text())
        self.assertEqual(data['review_report']['security'],'BLOCKED')
        self.assertFalse(data['authorized'])
        self.assertEqual(data['formal_acceptance'],'NOT_VERIFIED')

    def test_complete_verdict_without_receipt_is_blocked(self):
        self.prepare(); _,verdict = self.envelopes(); self.publish(self.wait_args.verdict,verdict)
        with self.assertRaises(LLMToolError): x.wait_report(self.wait_args,time.monotonic()+1)

    def test_correlations_roles_epoch_source_and_review_time_fail_closed(self):
        self.prepare(); receipt,verdict = self.envelopes()
        for field,value in (('request_id','other'),('request_sha256','0'*64),
                ('epoch',True),('source_sha','5'*40),('bundle_sha256','0'*64),
                ('reviewer',self.request['developer']),('receipt_sha256','0'*64),
                ('completed_at','2020-01-01T00:00:00Z'),('reviewed_sha','0'*40)):
            changed = deepcopy(verdict); changed[field] = value
            with self.subTest(field=field),self.assertRaises(LLMToolError):
                x.validate_report(receipt,changed,self.request,self.request_sha,verdict['receipt_sha256'])

    def test_blocking_finding_cannot_approve_and_coverage_cannot_be_missing(self):
        self.prepare(); receipt,verdict = self.envelopes()
        finding = {'finding_id':'n1','category':'Security','severity':'SEV-2','priority':'P1',
            'release_blocker':True,'summary':'Synthetic problem','reproducer':'Fixture only',
            'path':'src/app.py','criterion_id':'artifact','evidence_refs':['source-manifest']}
        verdict['findings'] = [finding]
        with self.assertRaises(LLMToolError):
            x.validate_report(receipt,verdict,self.request,self.request_sha,verdict['receipt_sha256'])
        verdict['security'] = 'CHANGES_REQUIRED'
        x.validate_report(receipt,verdict,self.request,self.request_sha,verdict['receipt_sha256'])
        verdict['criterion_evidence'] = []
        with self.assertRaises(LLMToolError):
            x.validate_report(receipt,verdict,self.request,self.request_sha,verdict['receipt_sha256'])

    def test_ready_marker_bad_digest_replacement_and_hardlink_block(self):
        self.prepare(); receipt,_ = self.envelopes(); self.publish(self.wait_args.receipt,receipt)
        path = self.wait_args.receipt
        self.write(path+'.ready',x.canonical({'schema_version':1,'artifact_sha256':'0'*64}))
        with self.assertRaises(LLMToolError): x.wait_report(self.wait_args,time.monotonic()+1)
        self.publish(path,receipt)
        reader = x.PrivateData(self.root,time.monotonic()+2); cached = {}
        x._committed(path,reader,cached)
        replacement = self.folder/'replacement'; self.write(replacement,Path(path).read_bytes())
        replacement.replace(path)
        with self.assertRaises(LLMToolError): x._committed(path,reader,cached)

    def test_hardlink_and_oversized_marker_are_rejected(self):
        self.prepare(); receipt,_ = self.envelopes(); path = self.wait_args.receipt
        self.publish(path,receipt)
        alias = self.folder/'alias.json'; os.link(path,alias)
        with self.assertRaises(LLMToolError): x.wait_report(self.wait_args,time.monotonic()+1)
        alias.unlink()
        self.write(path+'.ready',b'x'*1025)
        with self.assertRaises(LLMToolError): x.wait_report(self.wait_args,time.monotonic()+1)

    def test_duplicate_coverage_and_sensitive_findings_reject(self):
        self.prepare(); receipt,verdict = self.envelopes()
        original = deepcopy(verdict)
        verdict['criterion_evidence'] *= 2
        with self.assertRaises(LLMToolError):
            x.validate_report(receipt,verdict,self.request,self.request_sha,verdict['receipt_sha256'])
        original['findings'] = [{'finding_id':'n1','category':'Security','severity':'SEV-3',
            'priority':'P3','release_blocker':False,'summary':'api_key=sk-'+('x'*32),
            'reproducer':'Fixture only','path':'src/app.py','criterion_id':'artifact',
            'evidence_refs':['source-manifest']}]
        with self.assertRaises(LLMToolError):
            x.validate_report(receipt,original,self.request,self.request_sha,original['receipt_sha256'])

    def test_request_or_result_output_cannot_overwrite_original(self):
        self.prepare()
        with patch.object(x.Path,'cwd',return_value=Path(AUTHOR_ROOT)), \
             patch.dict(os.environ,{'CODEX_THREAD_ID':x.AUTHOR}):
            with self.assertRaises(OSError): x.prepare_request(self.request_args,time.monotonic()+1)
        self.write(self.wait_args.output,b'preserved')
        with self.assertRaises(OSError): x.wait_report(self.wait_args,time.monotonic()+1)
        self.assertEqual(Path(self.wait_args.output).read_bytes(),b'preserved')

    def test_late_receipt_or_completion_cannot_be_reused(self):
        self.prepare(); receipt,verdict = self.envelopes()
        with patch.object(x,'now',return_value=x.utc_time(self.request['expires_at'])):
            with self.assertRaises(LLMToolError):
                x.validate_report(receipt,verdict,self.request,self.request_sha,verdict['receipt_sha256'])
        verdict['completed_at'] = x.stamp(x.now()+timedelta(seconds=1))
        with self.assertRaises(LLMToolError):
            x.validate_report(receipt,verdict,self.request,self.request_sha,verdict['receipt_sha256'])

    def test_request_digest_wrong_paths_expiry_output_overwrite(self):
        self.prepare()
        for field,value in (('request_sha256','0'*64),('receipt',str(self.folder/'wrong.json'))):
            old = getattr(self.wait_args,field); setattr(self.wait_args,field,value)
            with self.subTest(field=field),self.assertRaises(LLMToolError):
                x.wait_report(self.wait_args,time.monotonic()+1)
            setattr(self.wait_args,field,old)
        self.request['expires_at'] = x.stamp(x.now()-timedelta(seconds=1))
        self.write(self.request_path,x.canonical(self.request))
        self.wait_args.request_sha256 = self.ref(self.request_path)['sha256']
        with self.assertRaises(LLMToolError): x.wait_report(self.wait_args,time.monotonic()+1)

    def consumer_cli(self, label):
        # Real local CLI; artifacts below are synthetic, not original Reviewer DATA.
        self.wait_args.output = str(self.folder/(label+'-report.json'))
        args = self.wait_args
        child = subprocess.run(review_trust_fixture.cli_command('task','review-wait',
            '--request',args.request,'--request-sha256',args.request_sha256,
            '--receipt',args.receipt,'--verdict',args.verdict,'--timeout','0',
            '--output',args.output,'--json'),capture_output=True,timeout=5,
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(child.stderr,b'')
        public = json.loads(child.stdout)
        self.assertFalse(public['authorized'])
        self.assertNotIn(str(self.folder),child.stdout.decode())
        self.assertNotIn(x.REVIEWER,child.stdout.decode())
        return child,public

    def test_public_consumer_all_nine_pairs_and_private_projection(self):
        self.prepare()
        decisions = ('APPROVE','CHANGES_REQUIRED','BLOCKED')
        for number,(security,functional) in enumerate((s,f) for s in decisions for f in decisions):
            with self.subTest(security=security,functional=functional):
                receipt,verdict = self.envelopes()
                verdict.update(security=security,functional=functional)
                self.publish(self.wait_args.receipt,receipt)
                self.publish(self.wait_args.verdict,verdict)
                child,public = self.consumer_cli('pair-'+str(number))
                self.assertEqual(child.returncode,0,public)
                expected = ('BLOCKED' if 'BLOCKED' in (security,functional) else
                    'CHANGES_REQUIRED' if 'CHANGES_REQUIRED' in (security,functional) else
                    'DUAL_APPROVAL_REPORTED')
                for field,value in (('security',security),('functional',functional),
                        ('review_disposition',expected),
                        ('execution_disposition','STOP_NO_EXECUTION_AUTHORITY')):
                    self.assertEqual(public[field],value)
                    self.assertEqual(json.loads(Path(self.wait_args.output).read_text())[field],value)
                # Actual consumer checks correlation and enums, never transport exit alone.
                correlated = (public['status']=='BOUND_REVIEW_REPORT' and
                    public['source_sha']==self.request['source_sha'] and
                    public['task']==self.request['task'] and
                    public['epoch']==self.request['epoch'] and
                    public['request_sha256']==self.request_sha and
                    public['bundle_sha256']==self.request['bundle_sha256'])
                self.assertTrue(correlated)
                self.assertEqual(public['formal_acceptance'],'NOT_VERIFIED')
                self.assertEqual(public['runtime_evidence_status'],'NOT_VERIFIED')
                independent_action_authority = False
                action = ('STOP_NO_EXECUTION_AUTHORITY' if not independent_action_authority else
                          'REQUIRES_SEPARATE_ACTION_CONTRACT')
                self.assertEqual(action,public['execution_disposition'])

    def test_public_consumer_invalid_decisions_and_timeout_never_reuse_approval(self):
        self.prepare()
        receipt,good = self.envelopes()
        self.publish(self.wait_args.receipt,receipt)
        self.publish(self.wait_args.verdict,good)
        child,public = self.consumer_cli('initial-valid')
        self.assertEqual(child.returncode,0)
        self.assertEqual(public['review_disposition'],'DUAL_APPROVAL_REPORTED')
        mutations = [('missing',None),('unknown','PASS'),('bool',True),('integer',1),
                     ('list',[]),('null',None)]
        for label,value in mutations:
            verdict = deepcopy(good)
            if label=='missing': del verdict['security']
            else: verdict['security']=value
            self.publish(self.wait_args.verdict,verdict)
            child,public = self.consumer_cli(label)
            self.assertNotEqual(child.returncode,0)
            self.assertEqual(public['status'],'BLOCKED')
            self.assertIsNone(public.get('security'))
            self.assertIsNone(public.get('functional'))
            self.assertIsNone(public.get('review_disposition'))
            self.assertEqual(public['execution_disposition'],'STOP_NO_EXECUTION_AUTHORITY')
        # No completion marker means partial DATA, not a previously cached approval.
        Path(self.wait_args.verdict+'.ready').unlink()
        child,public = self.consumer_cli('timeout')
        self.assertNotEqual(child.returncode,0)
        self.assertEqual(public['status'],'WAIT_TIMEOUT')
        self.assertIsNone(public['review_disposition'])
        self.assertIsNone(public['security']); self.assertIsNone(public['functional'])
        self.assertEqual(public['execution_disposition'],'STOP_NO_EXECUTION_AUTHORITY')

    def test_public_consumer_wrong_bindings_and_duplicate_json_fail_closed(self):
        self.prepare(); receipt,good = self.envelopes()
        self.publish(self.wait_args.receipt,receipt)
        changes = {'source_sha':'9'*40,'request_sha256':'9'*64,'bundle_sha256':'9'*64,
                   'epoch':2,'reviewer':{**good['reviewer'],'session_id':x.AUTHOR},
                   'task':{**good['task'],'run_id':'other-run'},'functional':False}
        for field,value in changes.items():
            verdict = {**good,field:value}
            self.publish(self.wait_args.verdict,verdict)
            child,public = self.consumer_cli('wrong-'+field)
            self.assertNotEqual(child.returncode,0)
            self.assertEqual(public['status'],'BLOCKED')
            self.assertIsNone(public['security']); self.assertIsNone(public['functional'])
            self.assertIsNone(public['review_disposition'])
            self.assertEqual(public['execution_disposition'],'STOP_NO_EXECUTION_AUTHORITY')
        raw = x.canonical(good)[:-1]+b',"security":"APPROVE"}'
        self.write(self.wait_args.verdict,raw)
        self.write(self.wait_args.verdict+'.ready',x.canonical({'schema_version':1,
            'artifact_sha256':hashlib.sha256(raw).hexdigest()}))
        child,public = self.consumer_cli('duplicate-json')
        self.assertNotEqual(child.returncode,0)
        self.assertIsNone(public['review_disposition'])
        self.assertEqual(public['execution_disposition'],'STOP_NO_EXECUTION_AUTHORITY')

    def test_safe_cli_errors_and_interrupt_without_provider_config(self):
        import io
        from llmtool.cli import main
        out = io.StringIO()
        with patch('sys.stdout',out),patch('llmtool.cli.resolve_config',side_effect=AssertionError('config')):
            code = main(['task','review-wait','--request',str(self.folder/'missing'),
                '--request-sha256','0'*64,'--receipt',str(self.folder/'receipt'),
                '--verdict',str(self.folder/'verdict'),'--output',str(self.folder/'out'),
                '--timeout','0','--json'])
        self.assertNotEqual(code,0); self.assertEqual(json.loads(out.getvalue())['status'],'BLOCKED')
        self.assertNotIn(str(self.folder),out.getvalue())
        with patch.object(x,'wait_report',side_effect=KeyboardInterrupt),patch('sys.stdout',io.StringIO()):
            self.assertEqual(x.run_exchange(SimpleNamespace(action='review-wait',timeout=1)),130)


if __name__ == '__main__': unittest.main()
