"""B1 synthetic host/route fixtures: no real auth, provider or project copies."""
import copy
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from llmtool import review_authority as a, task_review as r, authorization_ledger as l
from llmtool import authorization_policy as p
from llmtool.task_review_collector import LocalFacts
from test_task_review import fixture, FakeHost, NOW

REF='https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6130032828'
FAKE_SCRIPT='''#!INTERPRETER -IB
import json,sys
v=json.loads(sys.stdin.read().split('\\n\\nUSER:\\n',1)[1])
p=v['review_packet_data']
out=dict(v['verdict_binding'],schema_version=1,review_id='native-fake-review',
 security='APPROVE',functional='APPROVE',findings=[],
 criterion_evidence=[dict(criterion_id=c['criterion_id'],evidence_refs=['exact-ci'])
 for c in p['acceptance_contract']['criteria']])
print(json.dumps(out))
'''


class FixtureObserver(a.HostAuthorshipObserver):
    """Synthetic local facts ONLY; actual Host loader/pair/policy still exercised."""
    def _facts(self,root):
        dev=self.expect.authored_by[0]
        return LocalFacts(str(root),dev.branch,self.expect.source_sha,dev.common_dir,
            'davidcatcat/davidcattoolsproject','a'*64,'b'*64,'c'*64,'d'*64,True)


class FixtureAuthority(a.ProductionReviewAuthority):
    test_only=True


class FixtureLauncher(a.ProductionReviewerLauncher):
    # Runs an approved temporary script, never an installed Claude binary.
    test_only=True


class Harness:
    def __init__(self,*,cap=2):
        self.tmp=tempfile.TemporaryDirectory(prefix='b1-authority-',dir=str(Path(tempfile.gettempdir()).resolve()))
        self.root=Path(self.tmp.name)
        self.author=self.root/'author';self.common=self.root/'common/.git'
        self.venv=self.author/'tools/llmtool/.venv';self.auth=self.root/'fake-auth'
        self.hostdir=self.root/'host';self.reviewer=self.root/'reviewer'
        for d in (self.author,self.common,self.venv,self.auth,self.hostdir,self.reviewer):
            d.mkdir(parents=True,exist_ok=True);d.chmod(0o700)
        self.ready,self.final,expect,events=fixture()
        dev=replace(expect.agents[0],root=str(self.author),common_dir=str(self.common),branch='feat/writer-a')
        rev=replace(expect.agents[1],root=str(self.reviewer),common_dir=str(self.common),branch='review/host-a')
        self.expect=replace(expect,agents=(dev,rev),authored_by=(dev,))
        self.events=tuple(replace(e,agent=dev,handoff=replace(e.handoff,from_agent=dev,to_agent=rev) if e.handoff else None)
                          for e in events)
        self.fake=FakeHost(self.ready,self.final,self.expect,self.events)
        self.clock=self.fake.clock
        self.policy=a.ProviderRoutePolicy('host-route',1,'claude-cli','fake-profile','fake-model',
            ('DIFF','REVIEW_METADATA','SOURCE'),256*1024,2,180,1,0,0,1,cap,True,
            '2026-10-13T00:00:00Z','2026-10-14T00:10:00Z',REF)
        self.fake.data['route']=self.policy.policy_id
        self.packet_input=self.fake.packet_input
        self.ledger_path=self.hostdir/'provider.sqlite3'
        self.ledger=l.ReceiptLedger.create(self.ledger_path,clock=self.clock.utc,kind='PROVIDER_REVIEW')
        self.exe=self.hostdir/'approved-fake-transport'
        self.exe.write_text(FAKE_SCRIPT.replace('INTERPRETER',sys.executable));self.exe.chmod(0o700)
        ast=self.author.stat();lst=self.ledger_path.stat()
        self.hostdata={'schema_version':1,
            'agents':[dict(agent_id=dev.agent_id,root=str(self.author),device=ast.st_dev,inode=ast.st_ino,
                common_dir=str(self.common),origin='git@github.com:davidcatcat/davidcattoolsproject.git',
                branch_prefix='feat/writer-',venv_prefix=str(self.venv))],
            'reviewer':dict(role='llmtool-review/claude-cli',executable=str(self.exe),auth_home=str(self.auth)),
            'route_policies':[dict(policy=self.policy.to_dict(),revoked=False)],
            'ledger':dict(path=str(self.ledger_path),device=lst.st_dev,inode=lst.st_ino,
                          creation_nonce=self.ledger.creation_nonce),
            'issuer':dict(capability_id='approved-test-host',authorizer_ref=REF)}
        self.hostpath=self.hostdir/'registration.json'
        self.write_host()
        self.pin=a.HostPin(str(self.hostpath),hashlib.sha256(self.hostpath.read_bytes()).hexdigest(),REF,
                          a.pair_pin(self.fake.approval),hashlib.sha256(self.packet_input).hexdigest(),(str(self.author),))
        self.observer=FixtureObserver(self.pin,self.expect,self.events,self.clock,
                                      baseline_check=lambda reg,f:True,writers_check=lambda reg,f:True)
        self.context=a.ReviewContext(self.ready,self.final,self.expect,self.events,self.fake.approval,self.packet_input)
        self.issuer=a.ProductionReviewIssuer(self.pin,self.clock,self.observer,self.context)
        self.authority=FixtureAuthority(self.issuer,self.ledger)
        self.launcher=FixtureLauncher(self.authority)

    def close(self): self.ledger.close();self.tmp.cleanup()
    def write_host(self): self.hostpath.write_bytes(r.canonical(self.hostdata));self.hostpath.chmod(0o600)
    def route(self):
        return dict(id=self.policy.policy_id,version=self.policy.version,digest=self.policy.digest,
                    profile=self.policy.profile,model=self.policy.model)
    def services(self,**changes):
        values=dict(authority=self.authority,launcher=self.launcher,observer=self.observer,clock=self.clock,
            approval=self.fake.approval,packet_input=self.packet_input,route=self.route(),
            reviewer_launch_nonce=self.issuer.reviewer_launch_nonce,issuer_capability_id=self.issuer.capability_id,redeem_run_id=self.issuer.redeem_run_id,
            budget=self.fake.budget,hard_cost_cap=False,unknown_cost_permitted=True)
        values.update(changes);return r.HostServices(**values)
    def run(self,**changes): return r.run_review(self.ready,self.final,self.expect,self.events,self.services(**changes))
    def binding(self,**changes):
        o=self.observer.observe()
        packet=r.make_packet(self.ready,self.final,self.expect,
            a.inspect_task(self.ready,self.expect,self.events,NOW),self.fake.approval,o,
            r.load_json(self.packet_input.decode()),expected_route=self.policy.policy_id)
        scope=r.digest({'data_classes':('SOURCE','DIFF','REVIEW_METADATA'),
            'path_list_digest':r.digest([s['path'] for s in packet.to_dict()['sources']],'llmtool.review-path-list.v1')})
        values=dict(task_id=self.expect.task.task_id,ready_task=self.expect.task,
            acceptance_task=self.fake.approval.acceptance_task,criterion_map=self.fake.approval.criterion_map,
            run_id=self.expect.task.run_id,epoch=self.expect.epoch,sequence=4,source_sha=self.expect.source_sha,
            packet_digest=packet.digest,observation_digest=o.digest,author_agents=(self.expect.authored_by[0].agent_id,),
            reviewer_role='llmtool-review/claude-cli',reviewer_launch_nonce=self.issuer.reviewer_launch_nonce,route_id=self.policy.policy_id,
            route_version=1,route_digest=self.policy.digest,provider='claude-cli',profile=self.policy.profile,
            model=self.policy.model,export_scope_digest=scope,issuer_class='PRODUCTION_REVIEW',
            issuer_capability_id=self.issuer.capability_id,redeem_run_id=self.issuer.redeem_run_id,deadline=self.fake.approval.expires_at)
        values.update(changes);return r.ReviewCallBinding(**values),packet


class AuthorityTests(unittest.TestCase):
    def setUp(self): self.h=Harness();self.addCleanup(self.h.close)
    def zero(self,result,code=None):
        self.assertEqual(result['provider_calls'],0);self.assertFalse(result['authorized'])
        self.assertEqual(result['production_handoff'],'NOT_DELIVERED')
        if code:self.assertEqual(result['code'],code)
    def test_complete_real_adapters_fake_transport(self):
        result=self.h.run()
        self.assertEqual(result['code'],'REVIEW_FAKE_APPROVED',result)
        self.assertEqual(result['provider_calls'],1);self.assertTrue(result['test_only'])
        self.assertFalse(result['authorized']);self.assertEqual(result['runtime_evidence_status'],'NOT_VERIFIED')
        self.assertIsNone(result['input_tokens']);self.assertIsNone(result['cost'])
        receipt=next(iter(self.h.authority.receipts.values()))
        self.assertEqual(self.h.ledger.status(receipt.nonce),'EFFECT_DONE')
        self.assertEqual(set(result['outstanding_final_criteria']),{'security','functional'})
    def test_default_never_loads_host_or_provider(self):
        with patch.object(a,'read_host_file',side_effect=AssertionError()),patch('subprocess.Popen',side_effect=AssertionError()):
            self.assertIsInstance(a.review_authority_factory(),r.UnavailableReviewAuthority)
    def test_host_bundle_immutable_and_instructions_echo(self):
        services=self.h.services()
        with self.assertRaises(TypeError):services.route['id']='other'
        b,packet=self.h.binding();messages=packet.messages()
        wrapper=json.loads(messages[1]['content'])
        self.assertEqual(wrapper['verdict_binding']['packet_digest'],packet.digest)
        for key in ('review_id','criterion_evidence','release_blocker','reproducer','evidence_refs'):
            self.assertIn(key,messages[0]['content'])
    def test_post_consume_unexpected_errors_preserve_attempt(self):
        for error in (OSError('private secret'),RuntimeError('private secret')):
            h=Harness()
            try:
                with patch.object(h.launcher,'launch',side_effect=error): result=h.run()
                self.assertEqual(result['provider_calls'],1,result)
                self.assertEqual(result['invocation_status'],'FAKE_ATTEMPTED')
                self.assertEqual(result['code'],'REVIEW_PROVIDER_UNAVAILABLE')
                self.assertNotIn('private secret',json.dumps(result))
                receipt=next(iter(h.authority.receipts.values()))
                self.assertEqual(h.ledger.status(receipt.nonce),'EFFECT_UNKNOWN')
            finally:h.close()
    def test_replay_new_nonce_or_run_still_same_object(self):
        b,packet=self.h.binding();ad=self.h.authority.admit(b);token=self.h.authority.consume(ad)
        self.assertTrue(token.committed)
        with self.assertRaises(Exception):self.h.authority.consume(ad)
        b2=replace(b,reviewer_launch_nonce='e'*32,redeem_run_id='d'*32)
        self.assertFalse(self.h.authority.admit(b2).admitted)
        self.assertEqual(self.h.ledger.status(ad.grant_ref),'CONSUMED')
    def test_forged_every_authority_binding_field(self):
        b,_=self.h.binding()
        for changes in ({'source_sha':'9'*40},{'author_agents':('forged-author',)},
            {'route_digest':'9'*64},{'model':'other-model'},{'export_scope_digest':'9'*64},
            {'observation_digest':'9'*64},{'criterion_map':(('tests','security'),)},
            {'packet_digest':'9'*64},{'epoch':8},{'issuer_capability_id':'forged-issuer'}):
            with self.subTest(changes=changes):
                self.assertFalse(self.h.authority.admit(replace(b,**changes)).admitted)
        self.assertEqual(self.h.ledger._db.execute('SELECT count(*) FROM receipts').fetchone()[0],0)
    def test_forged_session_fails_against_host_expectation(self):
        expect=replace(self.h.expect,agents=(replace(self.h.expect.agents[0],session_id='forged-session'),self.h.expect.agents[1]))
        result=r.run_review(self.h.ready,self.h.final,expect,self.h.events,self.h.services())
        self.zero(result)
    def test_missing_baseline_writer_proof_denied(self):
        self.h.observer.baseline_check=None
        self.zero(self.h.run(),'REVIEW_IDENTITY_NOT_ATTESTED')
    def test_revocation_after_admit_denied_before_launch(self):
        b,_=self.h.binding();ad=self.h.authority.admit(b)
        self.h.hostdata['route_policies'][0]['revoked']=True;self.h.write_host()
        token=self.h.authority.consume(ad)
        self.assertFalse(token.committed);self.assertEqual(self.h.ledger.status(ad.grant_ref),'DENIED')
    def test_timeout_or_exit_is_unknown_no_retry(self):
        self.h.exe.write_text('#!'+sys.executable+' -IB\nimport sys\nsys.exit(17)\n')
        result=self.h.run()
        self.assertEqual(result['provider_calls'],1);self.assertEqual(result['status'],'BLOCKED')
        receipt=next(iter(self.h.authority.receipts.values()))
        self.assertEqual(self.h.ledger.status(receipt.nonce),'EFFECT_UNKNOWN')
    def test_launch_once_and_only_after_committed_consume(self):
        b,packet=self.h.binding();ad=self.h.authority.admit(b)
        with self.assertRaises(Exception):self.h.launcher.launch(packet.data,r.ConsumeToken(b.digest,b.reviewer_launch_nonce,ad.grant_ref,False,True))
        token=self.h.authority.consume(ad)
        self.h.launcher.launch(packet.data,token)
        with self.assertRaises(Exception):self.h.launcher.launch(packet.data,token)
    def test_host_file_safety_and_pins(self):
        for mode in (0o644,0o666):
            self.h.hostpath.chmod(mode)
            with self.assertRaises(Exception):a.load_host_registration(self.h.pin)
        self.h.hostpath.chmod(0o600)
        with self.assertRaises(Exception):a.load_host_registration(replace(self.h.pin,host_digest='0'*64))
        link=self.h.hostdir/'link.json';link.symlink_to(self.h.hostpath)
        with self.assertRaises(Exception):a.read_host_file(link)
        with self.assertRaises(Exception):a.load_host_registration(replace(self.h.pin,repository_roots=(str(self.h.hostdir),)))
    def test_replaced_ledger_or_nonce_denied(self):
        wrong=replace(self.h.pin,host_digest='0'*64)
        with self.assertRaises(Exception):a.load_host_registration(wrong)
        old=self.h.ledger_path.with_suffix('.saved');self.h.ledger_path.rename(old)
        self.h.ledger_path.write_bytes(b'not a ledger');self.h.ledger_path.chmod(0o600)
        with self.assertRaises(Exception):a.load_host_registration(self.h.pin)
    def test_route_data_cost_bounds(self):
        for changes in ({'unknown_cost_accepted':False},{'data_classes':('COMPANY_LOG',)},
            {'provider':'other-provider'},{'max_attempts':2},{'max_packet_bytes':262145},
            {'retry_count':1},{'call_timeout_seconds':121},{'per_day_cap':True}):
            with self.subTest(changes=changes),self.assertRaises(Exception):replace(self.h.policy,**changes)
    def test_pair_originals_and_privacy_block_without_transform(self):
        h=self.h
        result=h.run(approval=replace(h.fake.approval,record_ref='forged-pair'))
        self.zero(result)
        for text in ('/Users/<user>/projects/example','/home/<username>/project/file.py'):
            r.screen(text)
        for text in ('/Users/alice/private.log','/home/alice/auth.json','/Users/<user>/.ssh/id_rsa',
                     '/private/var/folders/user/run.log','/Users/<user>/../alice/private.log'):
            with self.subTest(text=text),self.assertRaises(Exception):r.screen(text)
    def test_readonly_check_does_not_claim_pin_or_touch_ledger(self):
        from llmtool.cli import main
        import io
        before=self.h.ledger_path.read_bytes()
        out=io.StringIO()
        with patch('sys.stdout',out),patch('subprocess.Popen',side_effect=AssertionError()):
            self.assertEqual(main(['review-authority','check','--host',str(self.h.hostpath),'--json']),0)
        value=json.loads(out.getvalue());self.assertFalse(value['authorized'])
        self.assertEqual(value['published_pin_validation'],'NOT_VERIFIED')
        self.assertEqual(self.h.ledger_path.read_bytes(),before)
        self.assertNotIn(str(self.h.author),out.getvalue())

    def test_explicit_composition_has_no_public_wiring(self):
        s=a.compose_host_services(self.h.issuer,self.h.ledger,budget=self.h.fake.budget)
        self.assertIs(type(s),r.HostServices)
        self.assertEqual(s.reviewer_launch_nonce,self.h.issuer.reviewer_launch_nonce)
        self.assertFalse(s.authority.test_only)
        self.assertIsInstance(a.review_authority_factory(),r.UnavailableReviewAuthority)

    def test_transport_timeout_is_consumed_unknown(self):
        self.h.exe.write_text('#!'+sys.executable+' -IB\nimport time\ntime.sleep(4)\n')
        result=self.h.run()
        self.assertEqual(result['provider_calls'],1);self.assertEqual(result['status'],'BLOCKED')
        receipt=next(iter(self.h.authority.receipts.values()))
        self.assertEqual(self.h.ledger.status(receipt.nonce),'EFFECT_UNKNOWN')


if __name__ == '__main__':unittest.main()
