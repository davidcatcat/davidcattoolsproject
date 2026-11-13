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
from llmtool.developer_routes import load_route_config
from llmtool.task_review_collector import LocalFacts
from test_task_review import fixture, FakeHost, NOW
import review_trust_fixture


def setUpModule(): review_trust_fixture.start_module()
def tearDownModule(): review_trust_fixture.stop_module()

REF='https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6130032828'
LOAD_TOLERANT_TIMEOUT_SECONDS=15
# #1502 WP-E R1: both claude-cli reviewer roles launch only a model the shared
# registry check admits for claude-reviewer, so the fixture route uses it.
_ROLES=load_route_config()['roles']
REVIEW_MODEL=_ROLES['claude-reviewer']['allowed_models'][0]
CROSS_FAMILY_MODEL=_ROLES['codex-reviewer']['allowed_models'][0]
UNLISTED_MODEL='claude-unlisted-model'
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


def ticking(h, step=0.2):
    """Make the synthetic Host clock advance on every monotonic read, like a real one."""
    from datetime import datetime, timezone
    from llmtool.task_lifecycle import utc_time
    base=utc_time(h.fake.utc).timestamp(); mono0=h.fake.mono
    def advance(seconds):
        h.fake.mono+=seconds
        h.fake.utc=datetime.fromtimestamp(int(base+(h.fake.mono-mono0)),timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    def monotonic():
        advance(step);return h.fake.mono
    h.clock.monotonic=monotonic
    return advance


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
    def __init__(self,*,cap=2,provider_layout=1,model=REVIEW_MODEL):
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
        self.policy=a.ProviderRoutePolicy('host-route',1,'claude-cli','fake-profile',model,
            ('DIFF','REVIEW_METADATA','SOURCE'),256*1024,2,180,1,0,0,1,cap,True,
            '2026-10-13T00:00:00Z','2026-10-14T00:10:00Z',REF,'llmtool-review/claude-cli')
        self.fake.data['route']=self.policy.policy_id
        self.packet_input=self.fake.packet_input
        self.ledger_path=self.hostdir/'provider.sqlite3'
        self.ledger=l.ReceiptLedger.create(self.ledger_path,clock=self.clock.utc,
                                           kind='PROVIDER_REVIEW',provider_layout=provider_layout)
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
            packet_digest=packet.digest,reviewed_object_digest=packet.reviewed_object_digest,
            observation_digest=o.digest,author_agents=(self.expect.authored_by[0].agent_id,),
            reviewer_role='llmtool-review/claude-cli',reviewer_launch_nonce=self.issuer.reviewer_launch_nonce,route_id=self.policy.policy_id,
            route_version=1,route_digest=self.policy.digest,provider='claude-cli',profile=self.policy.profile,
            model=self.policy.model,export_scope_digest=scope,issuer_class='PRODUCTION_REVIEW',
            issuer_capability_id=self.issuer.capability_id,redeem_run_id=self.issuer.redeem_run_id,deadline=self.fake.approval.expires_at)
        values.update(changes);return r.ReviewCallBinding(**values),packet


class AuthorityTests(unittest.TestCase):
    def setUp(self): self.h=Harness();self.addCleanup(self.h.close)
    def test_registered_agent_allows_automation_branch_prefix_but_not_main(self):
        hostdata=copy.deepcopy(self.h.hostdata)
        hostdata['agents'][0]['branch_prefix']='automation/ai-loop'
        registration=a._decode_host(r.canonical(hostdata).decode())
        self.assertEqual(registration.agents[0].branch_prefix,'automation/ai-loop')
        hostdata['agents'][0]['branch_prefix']='main'
        with self.assertRaises(Exception):
            a._decode_host(r.canonical(hostdata).decode())

    def test_registered_agent_accepts_only_exact_review_origins(self):
        for origin in (
            'git@github.com:davidcatcat/davidcattoolsproject.git',
            'https://github.com/davidcatcat/davidcattoolsproject.git',
        ):
            with self.subTest(origin=origin):
                hostdata=copy.deepcopy(self.h.hostdata)
                hostdata['agents'][0]['origin']=origin
                registration=a._decode_host(r.canonical(hostdata).decode())
                self.assertEqual(registration.agents[0].origin,origin)
        for origin in (
            'git@github.com:davidcatcat/davidcattoolsproject.git/',
            'git@github.com:davidcatcat/davidcattoolsproject',
            'Git@github.com:davidcatcat/davidcattoolsproject.git',
            'git@github.com:someoneelse/davidcattoolsproject.git',
            'ssh://git@github.com/davidcatcat/davidcattoolsproject.git',
            'https://github.com/davidcatcat/davidcattoolsproject.git/',
            'https://github.com/davidcatcat/davidcattoolsproject',
            'HTTPS://github.com/davidcatcat/davidcattoolsproject.git',
            'https://github.com/someoneelse/davidcattoolsproject.git',
        ):
            with self.subTest(origin=origin), self.assertRaises(Exception):
                hostdata=copy.deepcopy(self.h.hostdata)
                hostdata['agents'][0]['origin']=origin
                a._decode_host(r.canonical(hostdata).decode())
    def test_host_policy_reference_follows_trust_issue_and_slug(self):
        # #369 PR-6: reviewer_host_policy_issue and slug come from trust.json.
        a._reference(REF)
        new_issue=review_trust_fixture.make_trust(host_policy_issue=777)
        new_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/777#issuecomment-1'
        with review_trust_fixture.use_trust(new_issue):
            a._reference(new_ref)
            with self.assertRaises(Exception) as caught: a._reference(REF)
            self.assertEqual(caught.exception.review_code,'REVIEW_HOST_POLICY_INVALID')
            with self.assertRaises(Exception):
                a._reference('https://github.com/davidcatcat/davidcattoolsproject/issues/7777#issuecomment-1')
        with review_trust_fixture.use_trust(review_trust_fixture.make_trust('otherorg/other-repo')):
            a._reference('https://github.com/otherorg/other-repo/issues/120#issuecomment-1')
            with self.assertRaises(Exception): a._reference(REF)

    def test_registered_agent_origin_and_observation_follow_trust_slug(self):
        other=review_trust_fixture.make_trust('otherorg/other-repo',host_policy_issue=120)
        self.assertEqual(self.h.observer.observe().repository,'davidcatcat/davidcattoolsproject')
        with review_trust_fixture.use_trust(other):
            for origin in ('git@github.com:otherorg/other-repo.git',
                           'https://github.com/otherorg/other-repo.git'):
                with self.subTest(origin=origin):
                    agent=a.RegisteredAgent(**dict(self.h.hostdata['agents'][0],origin=origin))
                    self.assertEqual(agent.origin,origin)
            with self.assertRaises(Exception) as caught:
                a.RegisteredAgent(**self.h.hostdata['agents'][0])
            self.assertEqual(caught.exception.review_code,'REVIEW_REGISTRATION_INVALID')
            # A host registration still carrying the old origin now fails closed.
            with self.assertRaises(Exception) as caught: self.h.observer.observe()
            self.assertEqual(caught.exception.review_code,'REVIEW_REGISTRATION_INVALID')
        # With a registration accepted earlier, the Observation carries the current trust slug.
        registration=a.load_host_registration(self.h.pin)
        with review_trust_fixture.use_trust(other), \
             patch.object(a,'load_host_registration',return_value=registration):
            self.assertEqual(self.h.observer.observe().repository,'otherorg/other-repo')

    def zero(self,result,code=None):
        self.assertEqual(result['provider_calls'],0);self.assertFalse(result['authorized'])
        self.assertEqual(result['production_handoff'],'NOT_DELIVERED')
        if code:self.assertEqual(result['code'],code)
    def test_complete_real_adapters_fake_transport(self):
        # The fake reviewer transport reports no token usage. Under the Owner
        # correction, unknown retrospective usage is recorded telemetry only and
        # cannot veto the otherwise valid, independently bound APPROVE verdict.
        result=self.h.run()
        self.assertEqual(result['code'],'REVIEW_FAKE_APPROVED',result)
        self.assertEqual(result['status'],'APPROVE');self.assertFalse(result['authorized'])
        self.assertEqual(result['verdict_validation'],'SUCCESS')
        self.assertEqual((result['security'],result['functional']),('APPROVE','APPROVE'))
        self.assertEqual(result['provider_calls'],1);self.assertTrue(result['test_only'])
        self.assertEqual(result['runtime_evidence_status'],'NOT_VERIFIED')
        self.assertEqual(result['invocation_status'],'FAKE_RETURNED')
        self.assertIsNone(result['input_tokens']);self.assertIsNone(result['output_tokens'])
        self.assertIsNone(result['cost'])
        self.assertEqual(set(result['outstanding_final_criteria']),{'security','functional'})
        receipt=next(iter(self.h.authority.receipts.values()))
        self.assertEqual(self.h.ledger.status(receipt.nonce),'EFFECT_DONE')
    def test_real_advancing_clock_is_admitted_and_reviewed(self):
        # D1: a clock read taken before observe() is older than the observation.
        # Unknown usage does not block the one admitted/reviewed verdict.
        ticking(self.h)
        result=self.h.run()
        self.assertEqual(result['code'],'REVIEW_FAKE_APPROVED',result)
        self.assertEqual(result['status'],'APPROVE')
        self.assertEqual(result['provider_calls'],1);self.assertEqual(result['invocation_status'],'FAKE_RETURNED')
        self.assertIsNone(result['input_tokens']);self.assertIsNone(result['output_tokens'])
        receipt=next(iter(self.h.authority.receipts.values()))
        self.assertEqual(self.h.ledger.status(receipt.nonce),'EFFECT_DONE')

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

    def test_same_object_retry_with_new_issuer_is_refused_before_provider_call(self):
        h=Harness(provider_layout=2);self.addCleanup(h.close)
        first=h.run()
        self.assertEqual(first['provider_calls'],1)
        self.assertEqual(first['code'],'REVIEW_FAKE_APPROVED')
        h.fake.utc='2026-10-13T00:00:11Z'
        h.issuer=a.ProductionReviewIssuer(h.pin,h.clock,h.observer,h.context)
        h.authority=FixtureAuthority(h.issuer,h.ledger)
        h.launcher=FixtureLauncher(h.authority)
        second=h.run()
        self.assertEqual(second['provider_calls'],0)
        self.assertEqual(second['code'],'REVIEW_NOT_ALLOWED')
        self.assertEqual(first['provider_calls'] + second['provider_calls'],1)
    def test_reviewed_object_digest_omits_only_observation_and_sequence(self):
        b,packet=self.h.binding()
        payload=packet.to_dict()
        payload['observation_digest']='9'*64
        payload['sequence']+=1
        refreshed=r.ReviewPacket(r.canonical(payload))
        self.assertNotEqual(packet.digest,refreshed.digest)
        self.assertEqual(packet.reviewed_object_digest,refreshed.reviewed_object_digest)
        payload['ci']['run_id']='ci-other-run'
        distinct=r.ReviewPacket(r.canonical(payload))
        self.assertNotEqual(packet.reviewed_object_digest,distinct.reviewed_object_digest)
        self.assertEqual(b.reviewed_object_digest,packet.reviewed_object_digest)
    def test_forged_every_authority_binding_field(self):
        b,_=self.h.binding()
        for changes in ({'source_sha':'9'*40},{'author_agents':('forged-author',)},
            {'route_digest':'9'*64},{'model':'other-model'},{'export_scope_digest':'9'*64},
            {'observation_digest':'9'*64},{'reviewed_object_digest':'9'*64},
            {'criterion_map':(('tests','security'),)},
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




class WorkerRoleTests(unittest.TestCase):
    def test_required_policy_role_and_cross_mode_deny(self):
        h=Harness();self.addCleanup(h.close)
        b,_=h.binding()
        worker=replace(b,reviewer_role='llmtool-worker/claude-cli')
        self.assertNotEqual(worker.digest,b.digest)
        self.assertFalse(h.authority.admit(worker).admitted)
        self.assertEqual(h.ledger._db.execute('SELECT count(*) FROM receipts').fetchone()[0],0)


class WorkerHarness(Harness):
    def __init__(self, *, mode='success', timeout=2, model=REVIEW_MODEL):
        super().__init__(model=model)
        # Same accepted synthetic fixture authority; new role/runtime policy is
        # independently host-pinned, never a model/CLI flag or old Session grant.
        self.argv_file=self.hostdir/'argv.json'
        self.descendant_release=self.hostdir/'descendant-release'
        self.descendant_ready=self.hostdir/'descendant-ready'
        descendant_script=('import signal,time;from pathlib import Path;signal.alarm(5);'
            'Path('+repr(str(self.descendant_ready))+').touch();'
            '\nwhile not Path('+repr(str(self.descendant_release))+').exists(): time.sleep(0.01)\n')
        script=FAKE_SCRIPT.replace('print(json.dumps(out))', """
mode=MODE
if mode == 'changes': out['functional']='CHANGES_REQUIRED'
if mode == 'blocked': out['functional']='BLOCKED'
if mode == 'invalid-inner': out['source_sha']='9'*40
if mode == 'private': out['private']='Bearer SECRET_FAKE_VALUE_12345678'
if mode == 'timeout':
 import time
 time.sleep(10)
if mode == 'stderr': sys.stderr.write('x'*1100000)
if mode == 'blocked-input':
 import time
 time.sleep(10)
if mode == 'exit': sys.exit(7)
if mode == 'descendant':
 import subprocess
 subprocess.Popen([sys.executable,'-IB','-c','import time;time.sleep(10)'])
if mode == 'silent-descendant':
 import signal,subprocess,time
 from pathlib import Path
 signal.alarm(5)
 subprocess.Popen([sys.executable,'-IB','-c',DESCENDANT_SCRIPT],
  stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 while not Path(DESCENDANT_READY).exists(): time.sleep(0.01)
print(json.dumps(dict(type='result',subtype='success',is_error=False,
 session_id=SESSION,result=json.dumps(out),usage=dict(input_tokens=12,output_tokens=8))))
""").replace('DESCENDANT_SCRIPT',repr(descendant_script)).replace('DESCENDANT_READY',repr(str(self.descendant_ready))).replace('INTERPRETER',sys.executable).replace('mode=MODE','mode='+repr(mode)).replace('session_id=SESSION',
 'session_id='+repr('081de22e-d514-4288-9d1f-48f488979271' if mode=='spoof' else 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'))
        script=script.replace('v=json.loads', 'open('+repr(str(self.argv_file))+',"w").write(json.dumps(sys.argv[1:]))\nv=json.loads')
        self.exe.write_text(script)
        runtime_reg=replace(a.load_host_registration(self.pin),reviewer_role='llmtool-worker/claude-cli',
            reviewer_version='2.1.301',reviewer_executable_digest=hashlib.sha256(self.exe.read_bytes()).hexdigest())
        self.policy=replace(self.policy,reviewer_role='llmtool-worker/claude-cli',call_timeout_seconds=timeout,
                            worker_runtime_digest=a.worker_runtime_pin(runtime_reg))
        self.hostdata['reviewer'].update(role=self.policy.reviewer_role,version='2.1.301',
            executable_sha256=hashlib.sha256(self.exe.read_bytes()).hexdigest())
        self.hostdata['route_policies']=[dict(policy=self.policy.to_dict(),revoked=False)]
        self.write_host()
        self.pin=replace(self.pin,host_digest=hashlib.sha256(self.hostpath.read_bytes()).hexdigest())
        self.observer=FixtureObserver(self.pin,self.expect,self.events,self.clock,
                                      baseline_check=lambda reg,f:True,writers_check=lambda reg,f:True)
        self.issuer=a.ProductionReviewIssuer(self.pin,self.clock,self.observer,self.context)
        self.authority=FixtureAuthority(self.issuer,self.ledger)
        self.launcher=FixtureLauncher(self.authority)

    def services(self,**changes):
        changes.setdefault('reviewer_role',self.policy.reviewer_role)
        changes.setdefault('worker_runtime_digest',a.worker_runtime_pin(a.load_host_registration(self.pin)))
        return super().services(**changes)

    def binding(self,**changes):
        changes.setdefault('reviewer_role',self.policy.reviewer_role)
        return super().binding(**changes)


class WorkerJobIntegrationTests(unittest.TestCase):
    def harness(self,**changes):
        h=WorkerHarness(**changes);self.addCleanup(h.close);return h

    def test_finalize_failure_skips_worker_completion_validation(self):
        h=self.harness()
        with patch.object(h.authority,'finalize',side_effect=RuntimeError('private')), \
                patch.object(h.launcher,'validate_completion',side_effect=AssertionError('must not validate')) as validate:
            result=h.run()
        self.assertEqual(result['code'],'REVIEW_FINALIZE_UNKNOWN',result)
        validate.assert_not_called()

    def test_reaped_worker_with_silent_descendant_is_unknown_not_approved(self):
        import time
        from llmtool.errors import LLMToolError
        h=self.harness(mode='silent-descendant')
        def release():
            h.descendant_release.touch()
            pending=h.launcher.pending_job
            if pending is not None:
                until=time.monotonic()+3
                while True:
                    try:os.killpg(pending.process.pid,0)
                    except ProcessLookupError:break
                    except PermissionError:pass  # Still unconfirmed; existing deadline applies.
                    self.assertLess(time.monotonic(),until,'fixture group still present')
                    time.sleep(0.01)
        self.addCleanup(release)
        result=h.run()
        self.assertEqual(result['status'],'BLOCKED',result)
        self.assertEqual(result['code'],'REVIEW_WORKER_CLEANUP_UNKNOWN',result)
        self.assertEqual(result['provider_calls'],1)
        self.assertNotIn('job_evidence',result)
        pending=h.launcher.pending_job
        self.assertIsNotNone(pending)
        self.assertEqual(pending.process.returncode,0)
        self.assertFalse(pending.cleanup_confirmed)
        self.assertTrue(pending.process._llmtool_cleanup_pending)
        receipt=next(iter(h.authority.receipts.values()))
        self.assertEqual(h.ledger.status(receipt.nonce),'EFFECT_UNKNOWN')
        with patch('subprocess.Popen',side_effect=AssertionError('resend')) as spawn:
            with self.assertRaises(LLMToolError) as caught:
                h.launcher.launch(b'',r.ConsumeToken('', '', '',True,True))
        self.assertEqual(caught.exception.review_code,'REVIEW_WORKER_CLEANUP_UNKNOWN')
        spawn.assert_not_called()

    def test_fresh_worker_strict_verdict_owned_exit_and_no_legacy_cleanup(self):
        h=self.harness()
        import subprocess
        wait=subprocess.Popen.wait
        def bounded_wait(process,*args,**kwargs):
            self.assertIsNotNone(kwargs.get('timeout'), 'worker reached unbounded wait')
            return wait(process,*args,**kwargs)
        with patch('llmtool.providers.cli_lifecycle.terminate',side_effect=AssertionError('legacy cleanup')), \
                patch.object(subprocess.Popen,'wait',bounded_wait):
            result=h.run()
        self.assertEqual(result['code'],'REVIEW_FAKE_APPROVED',result)
        self.assertEqual((result['security'],result['functional']),('APPROVE','APPROVE'))
        self.assertFalse(result['authorized'])
        self.assertEqual(result['execution_disposition'],'STOP_NO_EXECUTION_AUTHORITY')
        self.assertEqual(result['review_authority'],'WORKER_DATA_ONLY')
        e=result['job_evidence']
        self.assertEqual(e['identity_source'],'CLI_REPORTED_RUNTIME_ID')
        self.assertEqual(e['runtime_session_id'],'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')
        self.assertEqual(e['launch_nonce'],h.issuer.reviewer_launch_nonce)
        self.assertEqual(e['wrapper_launches'],1)
        self.assertTrue(all(e[k] for k in ('reaped','pipes_drained','pipes_closed','runtime_closed')))
        self.assertIsNone(h.launcher.pending_job)
        self.assertEqual(result['input_tokens'],12)
        self.assertNotEqual(e['runtime_session_id'],h.expect.agents[1].session_id)
        argv=json.loads(h.argv_file.read_text())
        self.assertEqual(argv,['--print','--output-format','json','--no-session-persistence',
                               '--safe-mode','--tools','WebSearch,WebFetch','--model',REVIEW_MODEL])
        allowed_tools=argv[argv.index('--tools')+1].split(',')
        for tool in ('Read','Write','Edit','Bash'):
            self.assertNotIn(tool,allowed_tools)
        receipt=next(iter(h.authority.receipts.values()))
        self.assertEqual(h.ledger.status(receipt.nonce),'EFFECT_DONE')
        with self.assertRaises(Exception):h.launcher.launch(h.packet_input,
            r.ConsumeToken(receipt.binding.call.digest,h.issuer.reviewer_launch_nonce,receipt.nonce,True,True))

    def test_both_cross_roles_denied_before_any_spawn(self):
        for worker in (False,True):
            h=self.harness() if worker else Harness()
            if not worker:self.addCleanup(h.close)
            role='llmtool-review/claude-cli' if worker else 'llmtool-worker/claude-cli'
            changes=dict(reviewer_role=role,worker_runtime_digest=None if worker else 'd'*64)
            with patch('subprocess.Popen',side_effect=AssertionError('spawn')):
                result=h.run(**changes)
            self.assertEqual(result['provider_calls'],0,result)
            self.assertEqual(h.ledger._db.execute('SELECT count(*) FROM receipts').fetchone()[0],0)

    def test_known_invalid_inner_is_not_approve_unknown_transport_stays_counted(self):
        for mode in ('invalid-inner','private','spoof','exit','stderr'):
            with self.subTest(mode=mode):
                h=self.harness(mode=mode)
                result=h.run()
                self.assertEqual(result['provider_calls'],1,result)
                self.assertEqual(result['status'],'BLOCKED',result)
                self.assertFalse(result['authorized'])
                self.assertNotIn('SECRET_FAKE',json.dumps(result))
                receipt=next(iter(h.authority.receipts.values()))
                self.assertEqual(h.ledger.status(receipt.nonce),
                    'EFFECT_DONE' if mode=='invalid-inner' else 'EFFECT_UNKNOWN')
                self.assertIsNone(h.launcher.pending_job)

    def test_real_fake_child_timeout_and_inherited_pipe_drain_no_unbounded_wait(self):
        import time
        from llmtool.providers import cli_lifecycle
        for mode in ('timeout','descendant'):
            with self.subTest(mode=mode):
                # Keep the real deadline assertion, with enough wall-clock
                # margin for a loaded runner to schedule the fixture process.
                h=self.harness(mode=mode,timeout=5)
                started=time.monotonic()
                observations=[]
                actual_absent=cli_lifecycle._group_absent
                def observe(pgid):
                    absent=actual_absent(pgid);observations.append(absent);return absent
                with patch('llmtool.providers.cli_lifecycle._group_absent',side_effect=observe), \
                        patch('llmtool.providers.cli_lifecycle.terminate',side_effect=AssertionError('unbounded')):
                    result=h.run()
                self.assertLess(time.monotonic()-started,10)
                self.assertEqual(result['code'],'REVIEW_TIMEOUT',result)
                self.assertEqual(result['provider_calls'],1)
                receipt=next(iter(h.authority.receipts.values()))
                self.assertEqual(h.ledger.status(receipt.nonce),'EFFECT_UNKNOWN')
                pending=h.launcher.pending_job
                if pending is None:
                    self.assertTrue(observations[-1])
                else:
                    self.assertFalse(pending.cleanup_confirmed)
                    self.assertTrue(pending.process._llmtool_cleanup_pending)
                    self.assertTrue(pending.process._llmtool_group_unconfirmed is True or
                                    (observations and observations[-1] is False))

    def test_pinned_executable_change_zero_launch_and_source_drift(self):
        h=self.harness()
        h.exe.write_text(h.exe.read_text()+'\n# drift')
        with patch('subprocess.Popen',side_effect=AssertionError('spawn')):result=h.run()
        self.assertEqual(result['code'],'REVIEW_RUNTIME_INVALID',result)
        self.assertEqual(result['provider_calls'],1) # committed attempt counted even before actual spawn
        self.assertFalse(h.argv_file.exists())

    def test_required_constructor_and_legacy_file_decode_only_default(self):
        h=self.harness()
        fields=h.policy.to_dict();fields.pop('reviewer_role');fields['data_classes']=tuple(fields['data_classes'])
        with self.assertRaises(TypeError):a.ProviderRoutePolicy(**fields)
        legacy=Harness();self.addCleanup(legacy.close)
        legacy.hostdata['route_policies'][0]['policy'].pop('reviewer_role')
        reg=a._decode_host(r.canonical(legacy.hostdata).decode())
        self.assertEqual(reg.route_policies[0][0].reviewer_role,'llmtool-review/claude-cli')
        h.hostdata['route_policies'][0]['policy'].pop('reviewer_role')
        with self.assertRaises(Exception):a._decode_host(r.canonical(h.hostdata).decode())

    def test_finalization_uncertainty_blocks_success(self):
        h=self.harness()
        with patch.object(h.authority,'finalize',side_effect=RuntimeError('private')):result=h.run()
        self.assertEqual(result['code'],'REVIEW_FINALIZE_UNKNOWN')
        self.assertNotIn('job_evidence',result)
        self.assertEqual(result['provider_calls'],1)


    def test_cancellation_restores_signals_and_no_unbounded_cleanup(self):
        import signal
        from llmtool.providers.cli_lifecycle import CLIProcessLifecycle
        original=CLIProcessLifecycle.check
        for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
            h=self.harness()
            before={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
            def cancel(lifecycle):
                if lifecycle.deadline is not None and lifecycle.process is not None:
                    lifecycle._record(sig,None)
                original(lifecycle)
            with self.subTest(sig=sig),patch.object(CLIProcessLifecycle,'check',cancel), \
                    patch('llmtool.providers.cli_lifecycle.terminate',side_effect=AssertionError('unbounded')):
                if sig==signal.SIGINT:
                    self.assertEqual(h.run()['code'],'REVIEW_CANCELLED')
                else:
                    # Finalize failure must not replace the original Unix exit.
                    with patch.object(h.authority,'finalize',side_effect=RuntimeError('private')):
                        with self.assertRaises(SystemExit) as caught:h.run()
                    self.assertEqual(caught.exception.code,128+sig)
            self.assertEqual(before,{s:signal.getsignal(s) for s in before})
            self.assertIsNone(h.launcher.pending_job)

    def test_reap_unknown_retains_owned_child_and_forbids_relaunch(self):
        import signal,subprocess
        # 'timeout' keeps the owned fixture child alive until the bounded teardown
        # kills it. An already-exited child would instead be reaped by the
        # EPERM->kill() poll on Darwin and then confirmed absent with signal zero,
        # so the unknown-reap branch would never be exercised on this platform.
        h=self.harness(mode='timeout')
        with patch.object(subprocess.Popen,'wait',side_effect=subprocess.TimeoutExpired('fake',1)), \
                patch('llmtool.providers.cli_lifecycle.terminate',side_effect=AssertionError('unbounded')):
            result=h.run()
        pending=h.launcher.pending_job
        self.assertIsNotNone(pending)
        try:
            self.assertFalse(pending.cleanup_confirmed)
            self.assertTrue(pending.process._llmtool_cleanup_pending)
            self.assertEqual(result['status'],'BLOCKED')
            receipt=next(iter(h.authority.receipts.values()))
            self.assertEqual(h.ledger.status(receipt.nonce),'EFFECT_UNKNOWN')
            with patch('subprocess.Popen',side_effect=AssertionError('resend')):
                with self.assertRaises(Exception):h.launcher.launch(b'',r.ConsumeToken('', '', '',True,True))
        finally:
            # Test-owned unreaped fake child only; restore actual bounded wait.
            pending.process.wait(timeout=2)
        # The retained child was a live owned process ended by SIGKILL, not an
        # already-exited one whose reap was merely patched (that would be 0).
        self.assertEqual(pending.process.returncode,-signal.SIGKILL)
        with patch('llmtool.providers.cli_lifecycle.os.killpg') as probe:
            pending.stop()
        self.assertTrue(all(call.args == (pending.process.pid, 0)
                            for call in probe.call_args_list),
                        'reaped PGID may only receive existence queries')
        self.assertFalse(pending.cleanup_confirmed)
        self.assertTrue(pending.process._llmtool_cleanup_pending)

    def test_expired_route_and_registration_role_deny_without_spawn(self):
        h=self.harness()
        h.fake.utc='2026-10-14T00:10:00Z'
        with patch('subprocess.Popen',side_effect=AssertionError('spawn')):result=h.run()
        self.assertEqual(result['provider_calls'],0)
        h=self.harness()
        b,_=h.binding()
        forged=replace(b,reviewer_launch_nonce='a'*32)
        with patch('subprocess.Popen',side_effect=AssertionError('spawn')):
            self.assertFalse(h.authority.admit(forged).admitted)


    def run_with_long_call(self, h, seconds, after=None):
        from llmtool.providers import claude_cli
        advance=ticking(h)
        real=claude_cli.ClaudeCLIProvider._invoke_review_job
        def slow(provider,*args,**kwargs):
            out=real(provider,*args,**kwargs)
            advance(seconds)          # advance the injected host clock, without sleeping
            if after: after()
            return out
        with patch.object(claude_cli.ClaudeCLIProvider,'_invoke_review_job',slow):
            return h.run()

    def test_post_call_check_survives_a_review_longer_than_five_seconds(self):
        # D2: elapsed review time does not turn an admitted object into a replay.
        for seconds in (6,60):
            with self.subTest(seconds=seconds):
                h=self.harness(timeout=120)
                result=self.run_with_long_call(h,seconds)
                self.assertEqual(result['code'],'REVIEW_FAKE_APPROVED',result)
                self.assertEqual(result['provider_calls'],1)

    def test_post_call_still_rejects_drift_and_calls_past_the_deadline(self):
        h=self.harness()
        drift=self.run_with_long_call(h,30,after=lambda:setattr(h.observer,'baseline_check',None))
        self.assertEqual(drift['status'],'BLOCKED',drift)
        self.assertFalse(drift['authorized'])
        h=self.harness()
        late=self.run_with_long_call(h,400)       # beyond the 00:05:00 pair/call deadline
        self.assertEqual(late['status'],'BLOCKED',late)
        self.assertNotEqual(late['code'],'REVIEW_FAKE_APPROVED')

    def test_deadline_and_route_rechecked_after_verdict_and_finalize(self):
        import time
        for stage in ('parse','finalize','route','source'):
            h=self.harness()
            real=time.monotonic
            expired=[False]
            parse=r.parse_verdict
            finalize=h.authority.finalize
            def late_parse(*args):
                verdict=parse(*args)
                if stage=='parse':expired[0]=True
                if stage=='route':h.fake.utc='2026-10-14T00:10:00Z'
                if stage=='source':h.observer.baseline_check=None
                return verdict
            def late_finalize(*args):
                result=finalize(*args)
                if stage=='finalize':expired[0]=True
                return result
            with self.subTest(stage=stage),patch.object(r,'parse_verdict',late_parse), \
                    patch.object(h.authority,'finalize',late_finalize), \
                    patch('time.monotonic',side_effect=lambda:real()+(3 if expired[0] else 0)):
                result=h.run()
            self.assertEqual(result['status'],'BLOCKED',result)
            self.assertEqual(result['code'], 'REVIEW_TIMEOUT' if stage in ('parse','finalize') else
                             'REVIEW_ROUTE_NOT_AUTHORIZED' if stage=='route' else 'REVIEW_IDENTITY_NOT_ATTESTED')
            self.assertFalse(result['authorized'])
            self.assertNotIn('job_evidence',result)

    def test_executable_hash_deadline_checked_after_last_read(self):
        h=self.harness()
        reg=a.load_host_registration(h.pin)
        with patch('time.monotonic',side_effect=(0,2)):
            with self.assertRaises(Exception) as caught:a.verify_worker_executable(reg,1)
        self.assertEqual(caught.exception.review_code,'REVIEW_TIMEOUT')


def _policy_with_model(policy,model):
    """The policy with model swapped in after construction (no safe_id gate).

    Lets a launch test reach the launcher's own registry check with a model
    the Host route constructor would refuse for other reasons.
    """
    changed=replace(policy)
    object.__setattr__(changed,'model',model)
    return changed


class RegistryModelTests(unittest.TestCase):
    """#1502 WP-E R1: policy and both launcher branches admit only the listed model."""

    def assert_model_refused(self,caught):
        self.assertEqual(caught.exception.review_code,'ROLE_MODEL_NOT_ALLOWED')

    def test_policy_refuses_unlisted_and_cross_family_route_model(self):
        for model in (UNLISTED_MODEL,'gpt-unlisted-model'):
            with self.subTest(model=model):
                h=Harness(model=model);self.addCleanup(h.close)
                with self.assertRaises(Exception) as caught:h.issuer.policy(h.policy.digest)
                self.assert_model_refused(caught)
                self.assertFalse(h.issuer.vouches_for(h.policy.digest))
                with patch('subprocess.Popen',side_effect=AssertionError('spawn')):
                    result=h.run()
                self.assertEqual(result['provider_calls'],0,result)
                self.assertFalse(result['authorized'])
                self.assertEqual(h.ledger._db.execute('SELECT count(*) FROM receipts').fetchone()[0],0)

    def test_policy_admits_the_listed_route_model(self):
        h=Harness();self.addCleanup(h.close)
        self.assertEqual(h.issuer.policy(h.policy.digest).model,REVIEW_MODEL)
        self.assertEqual(a.admit_reviewer_model(REVIEW_MODEL),REVIEW_MODEL)
        for model in (UNLISTED_MODEL,CROSS_FAMILY_MODEL,'anthropic/'+REVIEW_MODEL,
                      REVIEW_MODEL.upper(),None):
            with self.subTest(model=model),self.assertRaises(Exception) as caught:
                a.admit_reviewer_model(model)
            self.assert_model_refused(caught)

    def test_review_launch_refuses_unlisted_and_cross_family_model_before_spawn(self):
        for model in (UNLISTED_MODEL,CROSS_FAMILY_MODEL):
            with self.subTest(model=model):
                h=Harness();self.addCleanup(h.close)
                b,packet=h.binding();ad=h.authority.admit(b)
                self.assertTrue(ad.admitted)
                token=h.authority.consume(ad);self.assertTrue(token.committed)
                with patch.object(h.issuer,'policy',return_value=_policy_with_model(h.policy,model)), \
                        patch('subprocess.Popen',side_effect=AssertionError('spawn')) as spawn:
                    with self.assertRaises(Exception) as caught:h.launcher.launch(packet.data,token)
                self.assert_model_refused(caught)
                spawn.assert_not_called()

    def test_worker_launch_refuses_unlisted_and_cross_family_model_before_spawn(self):
        import time
        for model in (UNLISTED_MODEL,CROSS_FAMILY_MODEL):
            with self.subTest(model=model):
                h=WorkerHarness(timeout=30);self.addCleanup(h.close)
                b,packet=h.binding();ad=h.authority.admit(b)
                self.assertTrue(ad.admitted)
                token=h.authority.consume(ad);self.assertTrue(token.committed)
                with patch.object(h.issuer,'policy',return_value=_policy_with_model(h.policy,model)), \
                        patch.object(h.issuer,'matches',return_value=True), \
                        patch('subprocess.Popen',side_effect=AssertionError('spawn')) as spawn:
                    with self.assertRaises(Exception) as caught:
                        h.launcher.launch(packet.data,token,deadline=time.monotonic()+30)
                self.assert_model_refused(caught)
                spawn.assert_not_called()
                self.assertFalse(h.argv_file.exists())

    def test_foreground_worker_refuses_unlisted_route_model_before_effect(self):
        for model in (UNLISTED_MODEL,'gpt-unlisted-model'):
            with self.subTest(model=model):
                h=WorkerHarness(model=model);self.addCleanup(h.close)
                with patch('subprocess.Popen',side_effect=AssertionError('spawn')):
                    with self.assertRaises(Exception) as caught:
                        a.run_foreground_review(h.issuer,h.ledger,budget=h.fake.budget)
                self.assert_model_refused(caught)
                self.assertFalse(h.argv_file.exists())
                self.assertEqual(h.ledger._db.execute('SELECT count(*) FROM receipts').fetchone()[0],0)


class ForegroundEntryTests(unittest.TestCase):
    """Actual composition + temporary synthetic Host/ledger/executable, no Live.

    Production components intentionally have test_only=False. These controlled
    fixture invocations are FAKE_TEST_VERIFIED, never runtime/Live authority.
    """
    def harness(self,**changes):
        h=WorkerHarness(**changes);self.addCleanup(h.close);return h

    def invoke(self,h,budget=None):
        return a.run_foreground_review(h.issuer,h.ledger,budget=budget or h.fake.budget)

    def test_actual_composition_and_issuer_context_complete_fake_journey(self):
        h=self.harness(timeout=120)
        with patch.object(h,'services',side_effect=AssertionError('fixture bypass')), \
                patch.object(a,'compose_host_services',wraps=a.compose_host_services) as compose:
            result=self.invoke(h)
        compose.assert_called_once()
        services=h.issuer.foreground_services
        self.assertIs(type(services.authority),a.ProductionReviewAuthority)
        self.assertIs(type(services.launcher),a.ProductionReviewerLauncher)
        self.assertIs(services.authority.ledger,h.ledger)
        self.assertIs(services.authority.issuer,h.issuer)
        self.assertIs(services.observer,h.observer)
        self.assertEqual(result['code'],'REVIEW_APPROVED',result)
        self.assertEqual(result['review_authority'],'WORKER_DATA_ONLY')
        self.assertFalse(result['test_only']) # production composition, SYNTHETIC fixtures
        self.assertFalse(result['authorized'])
        self.assertEqual(result['execution_disposition'],'STOP_NO_EXECUTION_AUTHORITY')
        self.assertEqual(result['production_handoff'],'NOT_DELIVERED')
        self.assertEqual(result['runtime_evidence_status'],'NOT_VERIFIED')
        self.assertEqual(result['provider_calls'],1)
        self.assertEqual(result['job_evidence']['wrapper_launches'],1)
        self.assertEqual(result['job_evidence']['launch_nonce'],h.issuer.reviewer_launch_nonce)
        self.assertTrue(all(result['job_evidence'][k] for k in
                            ('reaped','pipes_drained','pipes_closed','runtime_closed')))
        self.assertIsNone(h.issuer.pending_job)
        receipt=next(iter(services.authority.receipts.values()))
        self.assertEqual(h.ledger.status(receipt.nonce),'EFFECT_DONE')
        self.assertEqual(h.ledger._db.execute('SELECT count(*) FROM receipts').fetchone()[0],1)
        with self.assertRaises(AttributeError):h.issuer.foreground_services=None

    def test_repeat_and_failed_attempt_cannot_replace_services_or_launch(self):
        h=self.harness();self.invoke(h);retained=h.issuer.foreground_services
        with patch('subprocess.Popen',side_effect=AssertionError('resend')),self.assertRaises(Exception):
            self.invoke(h)
        self.assertIs(h.issuer.foreground_services,retained)
        h=self.harness()
        with patch.object(a,'compose_host_services',side_effect=ValueError('fixture failure')):
            with self.assertRaises(ValueError):self.invoke(h)
        with patch.object(a,'compose_host_services',side_effect=AssertionError('retry')):
            with self.assertRaises(Exception):self.invoke(h)
        self.assertIsNone(h.issuer.foreground_services)

    def test_legacy_route_and_invalid_budget_denied_before_effect(self):
        h=Harness();self.addCleanup(h.close)
        with patch('subprocess.Popen',side_effect=AssertionError('spawn')):
            with self.assertRaises(Exception):self.invoke(h)
        self.assertEqual(h.ledger._db.execute('SELECT count(*) FROM receipts').fetchone()[0],0)
        h=self.harness()
        with patch('subprocess.Popen',side_effect=AssertionError('spawn')):
            with self.assertRaises(Exception):a.run_foreground_review(h.issuer,h.ledger,budget=object())
        with self.assertRaises(Exception):self.invoke(h) # invalid attempt is not reset

    def test_original_deadline_survives_delayed_run_review_handoff(self):
        import time
        h=self.harness();original=r.run_review;real=time.monotonic;late=[False]
        budget=replace(h.fake.budget,wall_clock_ms=1000)
        def delayed(*args):
            late[0]=True
            return original(*args)
        with patch.object(r,'run_review',side_effect=delayed), \
                patch('time.monotonic',side_effect=lambda:real()+(2 if late[0] else 0)), \
                patch('subprocess.Popen',side_effect=AssertionError('late spawn')) as spawn:
            result=self.invoke(h,budget)
            spawn.assert_not_called()
        self.assertEqual(result['code'],'REVIEW_TIMEOUT',result)
        self.assertFalse(h.argv_file.exists())
        self.assertEqual(result['provider_calls'],1) # consume already committed, never reset
        services=h.issuer.foreground_services
        receipt=next(iter(services.authority.receipts.values()))
        self.assertEqual(h.ledger.status(receipt.nonce),'EFFECT_UNKNOWN')

    def test_new_issuer_replay_stays_in_existing_ledger(self):
        h=self.harness();self.invoke(h)
        other=a.ProductionReviewIssuer(h.pin,h.clock,h.observer,h.context)
        with patch('subprocess.Popen',side_effect=AssertionError('replay')) as spawn:
            result=a.run_foreground_review(other,h.ledger,budget=h.fake.budget)
            spawn.assert_not_called()
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['provider_calls'],0)
        self.assertEqual(h.ledger._db.execute('SELECT count(*) FROM receipts').fetchone()[0],1)

    def test_finalize_failure_and_unknown_owned_child_stay_inspectable(self):
        import signal,subprocess
        h=self.harness()
        with patch.object(a.ProductionReviewAuthority,'finalize',side_effect=RuntimeError('private')):
            result=self.invoke(h)
        self.assertEqual(result['code'],'REVIEW_FINALIZE_UNKNOWN')
        self.assertEqual(result['provider_calls'],1)
        self.assertIsNotNone(h.issuer.foreground_services)
        # Same live-child requirement as the retained-child case in
        # WorkerJobIntegrationTests: the fixture must still own a running child
        # when the bounded reap fails.
        h=self.harness(mode='timeout')
        with patch.object(subprocess.Popen,'wait',side_effect=subprocess.TimeoutExpired('fake',1)):
            result=self.invoke(h)
        pending=h.issuer.pending_job
        self.assertIsNotNone(pending)
        try:
            self.assertFalse(pending.cleanup_confirmed)
            self.assertEqual(result['status'],'BLOCKED')
            with patch('subprocess.Popen',side_effect=AssertionError('resend')):
                with self.assertRaises(Exception):self.invoke(h)
            self.assertIs(h.issuer.pending_job,pending)
        finally:
            pending.process.wait(timeout=2) # bounded test-owned cleanup only
        self.assertEqual(pending.process.returncode,-signal.SIGKILL)

    def test_concurrent_claim_rejects_without_waiting_or_replacing_holder(self):
        import threading
        h=self.harness();entered=threading.Event();release=threading.Event();errors=[]
        def delayed(*args,**kwargs):
            entered.set()
            if not release.wait(timeout=2):raise AssertionError('fixture wait')
            raise ValueError('synthetic compose failure')
        def first():
            try:self.invoke(h)
            except BaseException as exc:errors.append(exc)
        with patch.object(a,'compose_host_services',side_effect=delayed) as compose:
            worker=threading.Thread(target=first);worker.start()
            try:
                self.assertTrue(entered.wait(timeout=1))
                with self.assertRaises(Exception):self.invoke(h)
            finally:release.set();worker.join(timeout=2)
            self.assertFalse(worker.is_alive());compose.assert_called_once()
        self.assertEqual(len(errors),1)
        self.assertIsNone(h.issuer.foreground_services)

    def test_dual_verdicts_remain_data_and_ledger_is_borrowed(self):
        for mode,code,decision in (('success','REVIEW_APPROVED','APPROVE'),
                ('changes','REVIEW_CHANGES_REQUIRED','CHANGES_REQUIRED'),
                ('blocked','REVIEW_BLOCKED','BLOCKED')):
            with self.subTest(mode=mode):
                h=self.harness(mode=mode,timeout=LOAD_TOLERANT_TIMEOUT_SECONDS);result=self.invoke(h)
                self.assertEqual(result['code'],code,result)
                self.assertEqual((result['security'],result['functional']),('APPROVE',decision))
                self.assertFalse(result['authorized'])
                self.assertEqual(result['execution_disposition'],'STOP_NO_EXECUTION_AUTHORITY')
                self.assertFalse(result['handoff']['triggered'])
                self.assertEqual(h.ledger._db.execute('SELECT count(*) FROM receipts').fetchone()[0],1)

    def test_invalid_source_runtime_window_and_budget_cannot_spawn(self):
        for case in ('source','runtime','window','host','calls','repairs','overlong'):
            with self.subTest(case=case):
                h=self.harness();budget=h.fake.budget
                if case=='source':h.observer.baseline_check=None
                if case=='runtime':h.exe.write_text(h.exe.read_text()+'\n# drift')
                if case=='window':h.fake.utc='2026-10-14T00:10:00Z'
                if case=='host':h.hostpath.write_bytes(b'{}')
                if case=='calls':budget=replace(budget,max_provider_calls=2)
                if case=='repairs':budget=replace(budget,max_repair_attempts=1)
                if case=='overlong':budget=replace(budget,wall_clock_ms=180001)
                with patch('subprocess.Popen',side_effect=AssertionError('spawn')) as spawn:
                    try:result=self.invoke(h,budget)
                    except Exception as exc:self.assertIsNotNone(getattr(exc,'review_code',None))
                    else:self.assertEqual(result['status'],'BLOCKED',result)
                    spawn.assert_not_called()
                self.assertFalse(h.argv_file.exists())

    def test_exhausted_prologue_and_final_acceptance_use_original_deadline(self):
        import time
        for stage in ('compose','return'):
            with self.subTest(stage=stage):
                h=self.harness();late=[False];real=time.monotonic
                compose=a.compose_host_services;run=r.run_review
                def late_compose(*args,**kwargs):
                    services=compose(*args,**kwargs)
                    if stage=='compose':late[0]=True
                    return services
                def late_return(*args):
                    result=run(*args);late[0]=True;return result
                with patch.object(a,'compose_host_services',side_effect=late_compose), \
                        patch.object(r,'run_review',side_effect=late_return), \
                        patch('time.monotonic',side_effect=lambda:real()+(181 if late[0] else 0)):
                    if stage=='compose':
                        with patch('subprocess.Popen',side_effect=AssertionError('spawn')):
                            with self.assertRaises(Exception) as caught:self.invoke(h)
                        self.assertEqual(caught.exception.review_code,'REVIEW_TIMEOUT')
                    else:
                        result=self.invoke(h)
                        self.assertEqual(result['code'],'REVIEW_TIMEOUT',result)
                        self.assertEqual(result['provider_calls'],1)
                        self.assertNotIn('verdict',result)
                self.assertIsNotNone(h.issuer.foreground_services)

    def test_real_fake_timeout_pipe_and_invalid_reply_fail_closed(self):
        import time
        from llmtool.providers import cli_lifecycle
        for mode in ('timeout','descendant','stderr','invalid-inner','private','spoof','exit'):
            with self.subTest(mode=mode):
                timeout=1 if mode in ('timeout','descendant') else LOAD_TOLERANT_TIMEOUT_SECONDS
                h=self.harness(mode=mode,timeout=timeout);started=time.monotonic()
                observations=[]
                actual_absent=cli_lifecycle._group_absent
                def observe(pgid):
                    absent=actual_absent(pgid);observations.append(absent);return absent
                with patch('llmtool.providers.cli_lifecycle._group_absent',side_effect=observe):
                    result=self.invoke(h)
                self.assertEqual(result['status'],'BLOCKED',result)
                self.assertEqual(result['provider_calls'],1)
                self.assertFalse(result['authorized'])
                self.assertNotIn('SECRET_FAKE',json.dumps(result))
                self.assertLess(time.monotonic()-started,LOAD_TOLERANT_TIMEOUT_SECONDS+10)
                pending=h.issuer.pending_job
                if pending is None:
                    self.assertTrue(observations[-1])
                else:
                    self.assertFalse(pending.cleanup_confirmed)
                    self.assertTrue(pending.process._llmtool_cleanup_pending)
                    self.assertTrue(pending.process._llmtool_group_unconfirmed is True or
                                    (observations and observations[-1] is False))

    def test_cancellation_and_baseexception_retain_actual_services(self):
        import signal
        from llmtool.providers.cli_lifecycle import CLIProcessLifecycle
        original=CLIProcessLifecycle.check
        for sig in (signal.SIGINT,signal.SIGTERM):
            with self.subTest(sig=sig):
                h=self.harness();before=signal.getsignal(sig)
                def cancel(lifecycle):
                    if lifecycle.deadline is not None and lifecycle.process is not None:
                        lifecycle._record(sig,None)
                    original(lifecycle)
                with patch.object(CLIProcessLifecycle,'check',cancel):
                    if sig==signal.SIGINT:self.assertEqual(self.invoke(h)['code'],'REVIEW_CANCELLED')
                    else:
                        with self.assertRaises(SystemExit):self.invoke(h)
                self.assertEqual(signal.getsignal(sig),before)
                self.assertIsNotNone(h.issuer.foreground_services)
                self.assertIsNone(h.issuer.pending_job)
                receipt=next(iter(h.issuer.foreground_services.authority.receipts.values()))
                self.assertEqual(h.ledger.status(receipt.nonce),'EFFECT_UNKNOWN')
        # A BaseException must not replace/lose the retained launcher handle.
        h=self.harness();marker=object()
        class HostAbort(BaseException):pass
        def abort(*args):
            h.issuer.foreground_services.launcher.pending_job=marker
            raise HostAbort()
        with patch.object(r,'run_review',side_effect=abort):
            with self.assertRaises(HostAbort):self.invoke(h)
        self.assertIs(h.issuer.pending_job,marker)


if __name__ == "__main__":
    unittest.main()
