"""B1 pickup source acceptance: fake RPC/child and isolated receipt fixtures."""
import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from llmtool import codex_handoff_transport as c, authorization_policy as p, authorization_ledger as l
from llmtool import task_review as r
from test_task_handoff import Journey


class NotificationIssuer(p.HostIssuer):
    issuer_class = p.IssuerClass.NOTIFICATION
    capability_id = 'notification-test-host'
    def __init__(self, binding): self.binding = binding; self.revoked=False
    def vouches_for(self, digest): return not self.revoked and digest == self.binding.digest


class Host:
    def __init__(self, trial):
        self.trial=trial; self.calls=0; self.counter_calls=0
        self.change=lambda f,n:f
        self.counter_change=lambda x:x
    def invocations(self):
        self.counter_calls+=1
        return self.counter_change(self.trial.j.host.invocations())
    def observe(self):
        self.calls+=1;t=self.trial
        frame=c.PickupObservation(t.j.host.observe(),t.session,not t.rpc.queued,not t.rpc.queued,
                                 True,True,True,True,tuple(t.events))
        return self.change(frame,self.calls)


class RPC:
    def __init__(self, trial):
        self.t=trial;self.queued=False;self.calls=[];self.mode='complete';self.withdraw_ok=True
        self.cancel_ok=True;self.author_ok=True;self.child_exit=True;self.cleanup=True
        self.after_queue=lambda:None;self.verdict_mutate=lambda x:x
    def read(self):
        self.calls.append('read');b=self.t.binding
        return r.canonical(dict(kind='READY_FOR_REVIEW',event_id=b.event_id,source_ref=b.source_ref,
            actor_ref=b.actor_ref,source_sha=b.source_sha,epoch=b.epoch,sequence=b.sequence,packet_digest=b.packet_digest))
    def queue(self, request):
        self.calls.append('queue');self.request=request
        record=self.t.ledger.notification_record(self.t.binding.object_key)
        assert record['status']=='CONSUMED' and record['pending']==1
        self.queued=True
        if self.mode!='queued':
            self.t.event('DELIVERY');self.t.event('TURN_STARTED','turn-1')
            if self.mode!='running':self.t.event('TURN_COMPLETED','turn-1')
        self.after_queue()
        return c.QueueAck('message-1',self.t.session.session_id,self.t.binding.packet_digest,
                          self.t.session.server_digest,True,self.child_exit)
    def verdict(self, turn):
        self.calls.append('verdict');packet=self.t.j.packet;v=packet.to_dict()
        answer={k:v[k] for k in ('ready_task','acceptance_task','source_sha','epoch','sequence',
                                'observation_digest','reviewer_binding_digest')}
        answer.update(schema_version=1,review_id='pickup-review',packet_digest=packet.digest,
            security='BLOCKED' if self.mode=='blocked' else 'APPROVE',functional='APPROVE',findings=[],
            criterion_evidence=[dict(criterion_id=x['criterion_id'],evidence_refs=['actual-test-artifact','exact-ci'])
                                for x in v['acceptance_contract']['criteria']])
        return json.dumps(self.verdict_mutate(answer))
    def author_receipt(self, message, verdict):
        self.calls.append('author_receipt')
        if self.author_ok:self.t.event('AUTHOR_RECEIPT','turn-1',result=verdict)
        self.queued=False
    def withdraw(self, message):
        self.calls.append(('withdraw',message))
        if self.withdraw_ok:
            self.queued=False;self.t.event('WITHDRAWN')
        return True  # Reply is deliberately NOT the evidence oracle.
    def cancel(self, message, turn):
        self.calls.append(('cancel',message,turn))
        if self.cancel_ok:
            self.queued=False;self.t.event('TURN_CANCELLED',turn)
        return True
    def clean(self):self.calls.append('clean');return self.cleanup


class Trial:
    def __init__(self):
        self.j=Journey();self.tmp=tempfile.TemporaryDirectory(prefix='notification-fixture-')
        self.path=str(Path(self.tmp.name)/'notification.sqlite')
        self.ledger=l.ReceiptLedger.create(self.path,clock=self.j.clock.utc,kind='NOTIFICATION')
        reviewer=self.j.expect.agents[1]
        self.session=c.SessionPin(reviewer.session_id,reviewer.root,reviewer.common_dir,reviewer.branch,
            '8'*40,self.j.host.sample.reviewer_runtime,'1'*64,'2'*64,1,'unix:///fake/owned.sock',
            '3'*64,'HOST_ROLLOUT','4'*64,'host-lifecycle','counter-1')
        sample=self.j.host.observe()
        now=self.j.clock.utc()
        self.binding=p.NotificationBinding(self.j.expect.task,self.j.review_fixture.approval.acceptance_task,
            self.j.expect.source_sha,'2'*40,'3'*40,self.j.packet.digest,
            r.digest((sample.pin,self.session.digest)),sample.route_digest,r.digest(list(self.j.ready.authorized_scope)),
            self.j.review_fixture.approval.authors_digest,r.digest(reviewer.to_dict()),'writer-session',reviewer.session_id,
            7,4,'event-4','source-reference','approved-actor','a'*64,'notification-test-host','a'*32,
            self.ledger.creation_nonce,now-1,now+240)
        self.grant=c.PickupGrant(self.binding,self.j.review_fixture.approval,self.session,self.path,1)
        self.events=[];self.rpc=RPC(self);self.host=Host(self);self.issuer=NotificationIssuer(self.binding)
    def event(self, kind, turn='', result=''):
        b=self.binding
        self.events.append(c.HostEvent(kind,self.session.event_source,self.session.event_source_digest,
            b.author_session if kind=='AUTHOR_RECEIPT' else b.reviewer_session,
            'message-1',turn,b.packet_digest,self.j.clock.utc(),result))
    def run(self):
        services=c._fixture_services(self.grant,self.host,self.rpc,self.issuer,self.ledger,self.j.clock,
                                    r.canonical(self.j.review_fixture.data),
                                    admission=p.decide_notification(self.binding,issuer=self.issuer,observed=
                                        p.NotificationObserved(self.binding.digest,self.binding.host_pin,True,True,True,True,True,True,True)))
        return c._run_fixture_pickup(self.j.ready,self.j.final,self.j.expect,services)
    def close(self):self.ledger.close();self.tmp.cleanup()


class PickupTests(unittest.TestCase):
    def setUp(self):self.t=Trial()
    def tearDown(self):self.t.close()
    def blocked(self, result, queues=0):
        self.assertEqual(result['status'],'BLOCKED',result)
        self.assertFalse(result['authorized']);self.assertEqual(result['runtime_evidence_status'],'NOT_VERIFIED')
        self.assertEqual(self.t.rpc.calls.count('queue'),queues,result)
    def test_positive_full_correlated_journey_no_external_effects(self):
        with patch('subprocess.Popen',side_effect=AssertionError('real process')),\
             patch('socket.socket',side_effect=AssertionError('real socket')):
            result=self.t.run()
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(set(result['dimensions'].values()),{'PASS'})
        self.assertFalse(result['authorized']);self.assertEqual(result['evidence_class'],'FAKE_FIXTURE')
        self.assertEqual(self.t.rpc.request['expires_at'],self.t.binding.expires_at)
        self.assertIn('EXPIRED',self.t.rpc.request['instructions'])
        self.assertEqual(self.t.ledger.notification_record(self.t.binding.object_key)['review_status'],'APPROVE')
        self.assertEqual(self.t.run()['status'],'SKIP');self.assertEqual(self.t.rpc.calls.count('queue'),1)
    def test_public_default_never_calls_rejected_components(self):
        self.assertIsNone(c.pickup_services_factory())
        self.blocked(c.run_pickup(host=self.t.host,transport=self.t.rpc,grant=self.t.grant))
        self.assertEqual(self.t.host.calls,0);self.assertEqual(self.t.rpc.calls,[])
        self.t.grant=replace(self.t.grant,hard_cost_cap=True)
        self.blocked(self.t.run());self.assertEqual(self.t.host.calls,0);self.assertEqual(self.t.host.counter_calls,0)
        self.assertEqual(self.t.rpc.calls,[])
    def test_m1_idle_and_empty_must_be_same_sample(self):
        for field in ('idle','queue_empty','exclusive','no_tools','withdrawal_supported','cancel_supported'):
            with self.subTest(field=field):
                self.t.host.change=lambda x,n:replace(x,**{field:False})
                self.blocked(self.t.run())
    def test_m1_undelivered_expiry_verified_withdrawal(self):
        self.t.rpc.mode='queued';self.t.grant=replace(self.t.grant,wait_seconds=10,poll_seconds=5)
        result=self.t.run();self.blocked(result,queues=1)
        self.assertEqual(result['dimensions']['QUEUE_ACCEPTED'],'PASS')
        self.assertEqual(result['dimensions']['TURN_STARTED'],'NOT_VERIFIED')
        self.assertIn(('withdraw','message-1'),self.t.rpc.calls)
        record=self.t.ledger.notification_record(self.t.binding.object_key)
        self.assertEqual(record['status'],'WITHDRAWN');self.assertFalse(record['pending'])
        self.assertLess(self.t.rpc.request['expires_at'],self.t.binding.expires_at)
        self.assertNotIn('author_receipt',self.t.rpc.calls)
    def test_m1_withdrawal_ack_without_host_proof_is_unknown_pending_no_resend(self):
        self.t.rpc.mode='queued';self.t.rpc.withdraw_ok=False
        self.t.grant=replace(self.t.grant,wait_seconds=10,poll_seconds=5)
        result=self.t.run();self.blocked(result,queues=1)
        record=self.t.ledger.notification_record(self.t.binding.object_key)
        self.assertEqual(record['status'],'EFFECT_UNKNOWN');self.assertTrue(record['pending'])
        self.blocked(self.t.run(),queues=1)
        self.assertEqual(self.t.rpc.calls.count('queue'),1)
    def test_turn_timeout_cancels_only_original_turn(self):
        self.t.rpc.mode='running';self.t.grant=replace(self.t.grant,turn_seconds=5,poll_seconds=5)
        self.blocked(self.t.run(),queues=1)
        self.assertIn(('cancel','message-1','turn-1'),self.t.rpc.calls)
        self.assertEqual(self.t.ledger.notification_record(self.t.binding.object_key)['status'],'CANCELLED')
    def test_missing_cancellation_proof_retains_pending(self):
        self.t.rpc.mode='running';self.t.rpc.cancel_ok=False
        self.t.grant=replace(self.t.grant,turn_seconds=5,poll_seconds=5)
        self.blocked(self.t.run(),queues=1)
        self.assertTrue(self.t.ledger.notification_pending(self.t.binding.reviewer_session))
    def test_h2_pre_dispatch_expiry_is_known_zero_effect(self):
        def delay(frame,n):
            if n==5:self.t.j.clock.wait(250)
            return frame
        self.t.host.change=delay
        result=self.t.run();self.blocked(result)
        record=self.t.ledger.notification_record(self.t.binding.object_key)
        self.assertEqual(record['status'],'ZERO_EFFECT');self.assertFalse(record['pending'])
    def test_h3_blocked_is_not_reviewed_or_automatic_retry(self):
        self.t.rpc.mode='blocked';result=self.t.run();self.blocked(result,queues=1)
        self.assertEqual(result['dimensions']['REVIEW_COMPLETED'],'NOT_VERIFIED')
        record=self.t.ledger.notification_record(self.t.binding.object_key)
        self.assertEqual((record['status'],record['review_status']),('EFFECT_DONE','BLOCKED'))
        self.blocked(self.t.run(),queues=1)
    def test_h4_session_change_and_generation_require_new_external_pins(self):
        self.t.grant=replace(self.t.grant,session=replace(self.t.session,session_id='new-session'))
        self.blocked(self.t.run());self.assertEqual(self.t.host.calls,0)
        self.t.grant=replace(self.t.grant,session=replace(self.t.session,generation=2))
        self.blocked(self.t.run());self.assertEqual(self.t.host.calls,0)
    def test_l1_self_report_or_wrong_event_source_never_proves_delivery(self):
        def corrupt():
            self.t.events=[replace(e,source='REVIEWER_SELF_REPORT') for e in self.t.events]
        self.t.rpc.after_queue=corrupt
        result=self.t.run();self.blocked(result,queues=1)
        self.assertEqual(result['dimensions']['SESSION_DELIVERED'],'NOT_VERIFIED')
    def test_l1_wrong_message_turn_and_tool_effect(self):
        for mutate in (lambda e:replace(e,message_id='wrong'),
                       lambda e:replace(e,turn_id='wrong') if e.kind=='TURN_COMPLETED' else e,
                       lambda e:replace(e,kind='TOOL') if e.kind=='TURN_COMPLETED' else e):
            with self.subTest(mutate=mutate):
                old=self.t
                if old.rpc.calls:self.t=Trial();old.close()
                self.t.grant=replace(self.t.grant,wait_seconds=10,poll_seconds=5)
                self.t.rpc.after_queue=lambda:setattr(self.t,'events',[mutate(e) for e in self.t.events])
                self.blocked(self.t.run(),queues=1)
    def test_l1_author_receipt_must_be_original_session_and_exact_verdict(self):
        self.t.rpc.author_ok=False
        self.blocked(self.t.run(),queues=1)
        self.assertEqual(self.t.ledger.notification_record(self.t.binding.object_key)['status'],'EFFECT_UNKNOWN')
    def test_source_packet_epoch_route_and_runtime_drift(self):
        for field,value in [('source_sha','0'*40),('base_sha','0'*40),('tree_sha','0'*40),
                            ('packet_digest','0'*64),('route_digest','0'*64),('epoch',8)]:
            with self.subTest(field=field):
                self.t.binding=replace(self.t.grant.binding,**{field:value})
                original=self.t.grant;self.t.grant=replace(original,binding=self.t.binding)
                self.blocked(self.t.run());self.t.grant=original;self.t.binding=original.binding
    def test_cue_injection_and_limits(self):
        for raw in (b'[]',b'x'*65537,b'['*17+b']'*17,b'{"expect":{"trusted":true}}'):
            with self.subTest(raw=raw[:12]):
                self.t.rpc.read=lambda:raw;self.blocked(self.t.run())
    def test_unknown_ack_and_interruption_do_not_resend(self):
        self.t.rpc.after_queue=lambda:(_ for _ in ()).throw(KeyboardInterrupt())
        self.blocked(self.t.run(),queues=1)
        self.assertTrue(self.t.ledger.notification_pending(self.t.binding.reviewer_session))
        self.blocked(self.t.run(),queues=1)
    def test_callback_time_or_invocation_change_blocks_pre_effect(self):
        self.t.host.change=lambda f,n:(self.t.j.clock.wait(250) or f)
        self.blocked(self.t.run())
        self.t.host.change=lambda f,n:f
        self.t.host.counter_change=lambda x:replace(x,source='untrusted')
        self.blocked(self.t.run())
    def test_expired_grant_has_zero_rejected_component_callbacks(self):
        self.t.j.clock.wait(250)
        self.blocked(self.t.run())
        self.assertEqual(self.t.host.calls,0)
        self.assertEqual(self.t.host.counter_calls,0)
        self.assertEqual(self.t.rpc.calls,[])
    def test_late_host_callback_cannot_overrun_turn_budget(self):
        self.t.grant=replace(self.t.grant,turn_seconds=2)
        delayed=False
        def delay(frame,n):
            nonlocal delayed
            if 'verdict' in self.t.rpc.calls and 'author_receipt' not in self.t.rpc.calls and not delayed:
                delayed=True;self.t.j.clock.wait(3)
            return frame
        self.t.host.change=delay
        self.blocked(self.t.run(),queues=1)
        self.assertNotIn('author_receipt',self.t.rpc.calls)
    def test_tool_events_during_result_or_receipt_are_not_ignored(self):
        for stage in ('verdict','author_receipt'):
            with self.subTest(stage=stage):
                if self.t.rpc.calls:
                    self.t.close();self.t=Trial()
                original=getattr(self.t.rpc,stage)
                def tool(*args):
                    result=original(*args);self.t.event('TOOL','turn-1');return result
                setattr(self.t.rpc,stage,tool)
                self.blocked(self.t.run(),queues=1)
    def test_exception_review_code_is_never_echoed(self):
        class Bad(Exception):review_code='SensitiveValue123'
        self.t.rpc.read=lambda:(_ for _ in ()).throw(Bad('PRIVATE_SECRET'))
        result=self.t.run();self.blocked(result)
        self.assertNotIn('SensitiveValue123',json.dumps(result));self.assertNotIn('PRIVATE_SECRET',json.dumps(result))
    def test_fake_child_bounds_and_owned_cleanup(self):
        clock=self.t.j.clock
        class Child:
            def __init__(self,mode):self.mode=mode;self.stopped=False
            def read(self):
                if self.mode=='blocked':clock.wait(5)
                return 'stdout',b'x'*65537 if self.mode=='large' else b'ok',self.mode=='ok'
            def stop_owned(self):self.stopped=True
            def wait(self):return 0 if self.stopped or self.mode=='ok' else None
        ok=Child('ok');self.assertEqual(c._collect_fixture_child(ok,clock)['stdout'],b'ok');self.assertTrue(ok.stopped)
        for mode in ('large','blocked'):
            child=Child(mode)
            with self.assertRaises(Exception):c._collect_fixture_child(child,clock)
            self.assertTrue(child.stopped)


if __name__=='__main__':unittest.main()


class DeltaHost(Host):
    def observe(self):
        self.calls+=1;t=self.trial
        frame=c.PickupObservation(t.j.host.observe(),t.session,not t.rpc.queued,not t.rpc.queued,
            True,False,True,True,tuple(t.events),True,t.binding.coverage_digest,
            r.digest([e.__dict__ for e in t.events if e.kind != "AUTHOR_RECEIPT"]),t.rpc.child_exit)
        return self.change(frame,self.calls)
    def domains(self):
        from llmtool.codex_pickup_native import DomainSample,DOMAINS
        t=self.trial
        sample=(DomainSample(DOMAINS[0],t.session.counter_source,'owned-worker',t.binding.task.run_id,t.session.counter_generation,0,('dispatcher',)),
                DomainSample(DOMAINS[1],'reviewer-host',t.binding.reviewer_session,t.binding.task.run_id,'turn-generation',int('queue' in t.rpc.calls),('original-turn',)),
                DomainSample(DOMAINS[2],'author-host',t.binding.author_session,t.binding.task.run_id,'author-generation',int(any(e.kind=='AUTHOR_RECEIPT' for e in t.events)),('receipt-turn',)))
        return self.domain_change(sample)
    def __init__(self, trial):super().__init__(trial);self.domain_change=lambda x:x


class DeltaTrial(Trial):
    def __init__(self):
        super().__init__();self.ledger.close()
        self.path=str(Path(self.tmp.name)/'delta-v2.sqlite')
        self.ledger=l.ReceiptLedger.create(self.path,clock=self.j.clock.utc,kind='NOTIFICATION',notification_layout=2)
        self.binding=replace(self.binding,ledger_nonce=self.ledger.creation_nonce,tool_mode='AUDIT_ONLY_TRIAL',
            risk_acceptance_digest='d'*64,coverage_digest='e'*64)
        self.issuer.binding=self.binding;self.grant=replace(self.grant,binding=self.binding,ledger_path=self.path)
        self.host=DeltaHost(self)
    def run(self):
        obs=p.NotificationObserved(self.binding.digest,self.binding.host_pin,True,True,True,True,False,True,True,True,self.binding.risk_acceptance_digest)
        services=c._fixture_services(self.grant,self.host,self.rpc,self.issuer,self.ledger,self.j.clock,
            r.canonical(self.j.review_fixture.data),admission=p.decide_notification(self.binding,issuer=self.issuer,observed=obs))
        return c._run_fixture_pickup(self.j.ready,self.j.final,self.j.expect,services)


class NativeDeltaJourneyTests(unittest.TestCase):
    def setUp(self):self.t=DeltaTrial()
    def tearDown(self):self.t.close()
    def test_complete_audit_only_fake_journey_not_native_authority(self):
        result=self.t.run();self.assertEqual(result['status'],'PASS',result)
        self.assertFalse(result['authorized']);self.assertEqual(result['runtime_evidence_status'],'NOT_VERIFIED')
        row=self.t.ledger._db.execute('SELECT terminal_status,author_receipt_status FROM receipts').fetchone()
        self.assertEqual(row,('Completed','VERIFIED'))
        self.assertEqual(self.t.run()['status'],'SKIP');self.assertEqual(self.t.rpc.calls.count('queue'),1)
    def test_async_author_receipt_waits_without_repeating_notification(self):
        self.t.rpc.author_ok=False
        old=self.t.rpc.author_receipt
        notified=False
        def observe(frame,count):
            nonlocal notified
            if 'author_receipt' in self.t.rpc.calls and count>10 and not notified:
                notified=True
                verdict=r.parse_verdict(self.t.rpc.verdict('turn-1'),self.t.j.packet)
                self.t.event('AUTHOR_RECEIPT','turn-1',result=r.digest(verdict))
                return replace(frame,events=tuple(self.t.events))
            return frame
        self.t.host.change=observe
        result=self.t.run();self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(self.t.rpc.calls.count('author_receipt'),1);self.assertEqual(self.t.rpc.calls.count('queue'),1)
    def test_missing_receipt_is_durable_partial_not_reviewer_unknown(self):
        self.t.rpc.author_ok=False;self.t.grant=replace(self.t.grant,receipt_seconds=10)
        result=self.t.run();self.assertEqual(result['status'],'PARTIAL',result)
        self.assertEqual(result['dimensions']['REVIEW_COMPLETED'],'PASS')
        self.assertEqual(result['dimensions']['AUTHOR_RECEIPTED'],'NOT_VERIFIED')
        self.assertFalse(self.t.ledger.notification_pending(self.t.binding.reviewer_session))
        self.assertEqual(self.t.ledger.notification_record(self.t.binding.object_key)['status'],'EFFECT_DONE')
        self.assertEqual(self.t.run()['status'],'SKIP');self.assertEqual(self.t.rpc.calls.count('queue'),1)
    def test_p1_turn_only_tool_before_mapping_and_after_verdict(self):
        for stage in ('queue','verdict','author_receipt'):
            with self.subTest(stage=stage):
                if self.t.rpc.calls:self.t.close();self.t=DeltaTrial()
                original=getattr(self.t.rpc,stage)
                def tool(*args):
                    value=original(*args);self.t.event('TOOL','turn-1');self.t.events[-1]=replace(self.t.events[-1],message_id='')
                    return value
                setattr(self.t.rpc,stage,tool)
                result=self.t.run();self.assertEqual(result['status'],'BLOCKED',result)
                self.assertIn('PICKUP_TOOL_EFFECT',result['failure_codes'])
    def test_coverage_or_child_exit_missing_blocks_before_queue(self):
        for field in ('coverage_complete','child_exited'):
            self.t.host.change=lambda f,n:replace(f,**{field:False})
            result=self.t.run();self.assertEqual(result['status'],'BLOCKED',result)
            self.assertNotIn('queue',self.t.rpc.calls)
    def test_n3_failed_and_interrupted_terminal_not_review(self):
        for status in ('Failed','Interrupted'):
            if self.t.rpc.calls:self.t.close();self.t=DeltaTrial()
            self.t.rpc.after_queue=lambda:self.t.events.__setitem__(-1,replace(self.t.events[-1],terminal_status=status))
            result=self.t.run();self.assertEqual(result['status'],'BLOCKED',result)
            self.assertEqual(result['dimensions']['REVIEW_COMPLETED'],'NOT_VERIFIED')
            self.assertNotIn('verdict',self.t.rpc.calls)
            self.assertFalse(self.t.ledger.notification_pending(self.t.binding.reviewer_session))
    def test_n3_in_progress_is_not_terminal(self):
        self.t.rpc.after_queue=lambda:self.t.events.__setitem__(-1,replace(self.t.events[-1],terminal_status='InProgress'))
        self.t.rpc.cancel_ok=False
        result=self.t.run();self.assertEqual(result['status'],'BLOCKED',result)
        self.assertTrue(self.t.ledger.notification_pending(self.t.binding.reviewer_session))
    def test_worker_counter_increment_is_not_reviewer_inference(self):
        self.t.host.domain_change=lambda samples:(replace(samples[0],counter=int('queue' in self.t.rpc.calls)),*samples[1:])
        result=self.t.run();self.assertEqual(result['status'],'BLOCKED',result)
        self.assertIn('PICKUP_COUNTER_DOMAIN',result['failure_codes'])
    def test_late_author_receipt_is_partial_with_original_review_preserved(self):
        old=self.t.rpc.author_receipt
        def late(*args):
            old(*args);self.t.j.clock.wait(230)
        self.t.rpc.author_receipt=late
        result=self.t.run();self.assertEqual(result['status'],'PARTIAL',result)
        self.assertFalse(self.t.ledger.notification_pending(self.t.binding.reviewer_session))


class AdvisoryDeltaRegressions(unittest.TestCase):
    def setUp(self):self.t=DeltaTrial()
    def tearDown(self):self.t.close()
    def test_author_only_timeout_preserves_proven_review_as_partial(self):
        old=self.t.rpc.author_receipt
        def delay(*args):old(*args);self.t.j.clock.wait(6)
        self.t.rpc.author_receipt=delay
        result=self.t.run();self.assertEqual(result['status'],'PARTIAL',result)
        self.assertEqual(result['dimensions']['AUTHOR_RECEIPTED'],'NOT_VERIFIED')
        self.assertFalse(self.t.ledger.notification_pending(self.t.binding.reviewer_session))
    def test_terminal_contradiction_after_verdict_never_passes(self):
        original=self.t.rpc.verdict
        def drift(*args):
            result=original(*args);self.t.events[-1]=replace(self.t.events[-1],terminal_status='Failed');return result
        self.t.rpc.verdict=drift
        result=self.t.run();self.assertEqual(result['status'],'BLOCKED',result)
        self.assertTrue(self.t.ledger.notification_pending(self.t.binding.reviewer_session))
    def test_cleanup_provider_domain_change_is_not_zero(self):
        def clean():
            self.t.host.domain_change=lambda samples:(replace(samples[0],counter=1),*samples[1:]);return True
        self.t.rpc.clean=clean
        result=self.t.run();self.assertEqual(result['status'],'BLOCKED',result)
        self.assertIn('PICKUP_COUNTER_DOMAIN',result['failure_codes'])
    def test_domain_callback_return_time_is_checked(self):
        old=self.t.host.domains
        def delayed():
            result=old();self.t.j.clock.wait(230);return result
        self.t.host.domains=delayed
        result=self.t.run();self.assertEqual(result['status'],'BLOCKED',result)
        self.assertNotIn('queue',self.t.rpc.calls)


class CleanupCoverageRegression(unittest.TestCase):
    def test_cleanup_late_turn_only_tool_is_not_hidden(self):
        t=DeltaTrial();self.addCleanup(t.close)
        def clean():
            t.event('TOOL','turn-1');t.events[-1]=replace(t.events[-1],message_id='');return True
        t.rpc.clean=clean
        result=t.run();self.assertEqual(result['status'],'BLOCKED',result)
        self.assertIn('PICKUP_TOOL_EFFECT',result['failure_codes'])
        self.assertTrue(t.ledger.notification_pending(t.binding.reviewer_session))


class TerminalCleanupRegression(unittest.TestCase):
    def test_failed_and_interrupted_late_tool_restore_unknown_pending(self):
        for status in ('Failed','Interrupted'):
            with self.subTest(status=status):
                t=DeltaTrial()
                try:
                    t.rpc.after_queue=lambda:t.events.__setitem__(-1,replace(t.events[-1],terminal_status=status))
                    def clean():
                        t.event('TOOL','turn-1');t.events[-1]=replace(t.events[-1],message_id='');return True
                    t.rpc.clean=clean
                    result=t.run();self.assertEqual(result['status'],'BLOCKED',result)
                    self.assertTrue(t.ledger.notification_pending(t.binding.reviewer_session))
                    self.assertEqual(t.ledger.notification_record(t.binding.object_key)['status'],'EFFECT_UNKNOWN')
                finally:t.close()
