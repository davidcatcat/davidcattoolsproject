"""Fake-only handoff user journey and adversarial host/authority boundaries."""
import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from llmtool import task_handoff as h, task_review as r, task_lifecycle as t
from llmtool.errors import LLMToolError
from test_task_review import fixture, FakeHost as ReviewFixture


class Clock:
    def __init__(self): self.seconds = 10; self.mono = 10.0
    def utc(self): return int(t.utc_time('2026-10-13T00:00:00Z').timestamp()) + self.seconds
    def monotonic(self): return self.mono
    def wait(self, amount): self.seconds += amount; self.mono += amount


class Claims:
    test_only = True
    def __init__(self): self.rows = {}; self.objects = {}; self.fail_finish = False
    def claim(self, key, obj):
        if obj in self.objects:
            status = self.rows[self.objects[obj]]
            return 'EFFECT_DONE' if status == 'EFFECT_DONE' else 'EFFECT_UNKNOWN'
        self.objects[obj] = key; self.rows[key] = 'CONSUMED'
        return 'CONSUMED'
    def finish(self, key, status):
        if self.fail_finish: raise RuntimeError('PRIVATE_SECRET')
        self.rows[key] = status


class Host:
    test_only = True
    def __init__(self, clock, fixture):
        self.clock, self.fixture = clock, fixture
        self.calls = 0; self.invoked = 0
        self.change = lambda sample, n: sample
        self.counter_change = lambda sample: sample
        self.sample = h.HostSample(fixture.observation, '2'*40, '3'*40,
            ('/repo/claude/.venv/bin/python', '/repo/claude/.venv', '/repo/claude/src/app.py'),
            ('claude-1', 'codex-1'), (1, 1), '4'*64, '5'*64, 'fake-grant')
    def observe(self):
        self.calls += 1
        obs = replace(self.sample.observation, observed_at=r.clock_utc(self.clock),
                      monotonic_at=self.clock.mono)
        return self.change(replace(self.sample, observation=obs), self.calls)
    def invocations(self):
        return self.counter_change(h.InvocationSample('host-lifecycle', self.invoked, 'counter-1'))


class Transport:
    test_only = True
    def __init__(self, journey):
        self.journey = journey; self.notifications = []; self.receipts = []
        self.raw = None; self.no_events = False; self.cleanup = True
        self.on_read = lambda: None; self.on_notify = lambda: None
        self.on_review = lambda: None; self.on_receipt = lambda: None
        self.delivery_change = lambda d: d; self.reply_change = lambda d: d
        self.verdict_change = lambda v: v
    def read(self):
        self.on_read()
        if self.no_events: return None
        return self.raw if self.raw is not None else r.canonical(self.journey.cue())
    def notify(self, reference, packet):
        assert self.journey.claims.rows and all(v != 'ISSUED' for v in self.journey.claims.rows.values())
        self.notifications.append(reference); self.on_notify()
        return self.delivery_change(h.Delivery('message-1', 'reviewer-session', packet.digest, True))
    def review(self, delivery, packet):
        self.on_review()
        p = packet.to_dict()
        verdict = {k: p[k] for k in ('ready_task', 'acceptance_task', 'source_sha', 'epoch',
                    'sequence', 'observation_digest', 'reviewer_binding_digest')}
        verdict.update(schema_version=1, review_id='review-1', packet_digest=packet.digest,
            security='APPROVE', functional='APPROVE', findings=[],
            criterion_evidence=[{'criterion_id':c['criterion_id'], 'evidence_refs':['actual-test-artifact','exact-ci']}
                                for c in p['acceptance_contract']['criteria']])
        return self.reply_change(h.ReviewResult(delivery.message_id, delivery.session_id,
                                  packet.digest, True, True, json.dumps(self.verdict_change(verdict))))
    def receipt(self, delivery, verdict_digest):
        self.on_receipt(); self.receipts.append(delivery.message_id)
        return ('writer-session', delivery.message_id, delivery.packet_digest, verdict_digest)
    def clean(self): return self.cleanup


class Journey:
    def __init__(self):
        self.ready, self.final, self.expect, self.events = fixture()
        self.clock = Clock(); self.review_fixture = ReviewFixture(self.ready, self.final, self.expect, self.events)
        self.host = Host(self.clock, self.review_fixture); self.claims = Claims(); self.transport = Transport(self)
        sample = self.host.observe()
        self.packet = r.make_packet(self.ready, self.final, self.expect,
            t.inspect_task(self.ready, self.expect, self.events, r.clock_utc(self.clock)),
            self.review_fixture.approval, sample.observation, copy.deepcopy(self.review_fixture.data))
        self.grant = h.FakeGrant(self.review_fixture.approval, sample.pin, self.packet.digest,
            'event-4', 'approved-source-reference', 'approved-actor', 'host-lifecycle', 'counter-1',
            '2026-10-13T00:00:00Z', '2026-10-13T00:05:00Z', (1, 1))
    def cue(self):
        return dict(kind='READY_FOR_REVIEW', event_id=self.grant.event_id,
            source_ref=self.grant.source_ref, actor_ref=self.grant.actor_ref,
            source_sha=self.expect.source_sha, epoch=self.expect.epoch, sequence=len(self.events),
            packet_digest=self.grant.packet_digest)
    def run(self):
        services = h.FakeServices(self.host, self.transport, self.claims, self.clock, self.grant,
                                  r.canonical(self.review_fixture.data))
        return h._run_fake_handoff(self.ready, self.final, self.expect, services)


class HandoffTests(unittest.TestCase):
    def setUp(self): self.j = Journey()
    def blocked(self, result, notify=0):
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertFalse(result['authorized']); self.assertEqual(result['runtime_evidence_status'], 'NOT_VERIFIED')
        self.assertEqual(len(self.j.transport.notifications), notify)
    def test_complete_fake_journey_has_no_external_effects(self):
        with patch('subprocess.Popen', side_effect=AssertionError('subprocess forbidden')), \
             patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             patch('builtins.open', side_effect=AssertionError('writes forbidden')):
            result = self.j.run()
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['zero_model_calls'], 'FAKE_HOST_VERIFIED')
        self.assertEqual(result['token_usage'], 'UNKNOWN'); self.assertEqual(result['evidence_class'], 'FAKE_FIXTURE')
        self.assertFalse(result['authorized']); self.assertEqual(result['runtime_evidence_status'], 'NOT_VERIFIED')
        self.assertEqual(len(self.j.transport.notifications), 1); self.assertEqual(len(self.j.transport.receipts), 1)
        self.assertEqual(list(self.j.claims.rows.values()), ['EFFECT_DONE'])
        self.assertIsNone(h.handoff_services_factory())
    def test_no_event_finite_timeout_zero_calls(self):
        self.j.transport.no_events = True; self.j.grant = replace(self.j.grant, wait_seconds=180)
        result = self.j.run(); self.blocked(result)
        self.assertEqual(result['code'], 'HANDOFF_EXPIRED'); self.assertEqual(self.j.clock.mono, 190)
        self.assertFalse(self.j.claims.rows); self.assertEqual(self.j.host.invoked, 0)
    def test_duplicate_restart_and_new_comment_are_skipped(self):
        self.assertEqual(self.j.run()['status'], 'PASS')
        self.assertEqual(self.j.run()['status'], 'SKIP')
        self.j.grant = replace(self.j.grant, source_ref='new-approved-comment')
        self.assertEqual(self.j.run()['status'], 'SKIP')
        self.assertEqual(len(self.j.transport.notifications), 1)
    def test_cross_run_reviewed_object_skip(self):
        self.assertEqual(self.j.run()['status'], 'PASS')
        other = Journey(); other.claims = self.j.claims
        other.expect = replace(other.expect, task=other.ready.binding('9'*32))
        other.events = tuple(replace(e,task=other.expect.task,
            evidence=tuple(replace(v,task=other.expect.task) for v in e.evidence),
            handoff=replace(e.handoff,task=other.expect.task) if e.handoff else None) for e in other.events)
        other.review_fixture.approval = replace(other.review_fixture.approval,ready_task=other.expect.task)
        other.host.sample = replace(other.host.sample,observation=replace(other.host.sample.observation,
            task=other.expect.task,events=other.events))
        other.review_fixture.data['evidence'][0]['record']=other.events[2].evidence[0].to_dict()
        sample=other.host.observe()
        packet=r.make_packet(other.ready,other.final,other.expect,
            t.inspect_task(other.ready,other.expect,other.events,r.clock_utc(other.clock)),
            other.review_fixture.approval,sample.observation,other.review_fixture.data)
        other.grant=replace(other.grant,approval=other.review_fixture.approval,host_pin=sample.pin,packet_digest=packet.digest)
        self.assertEqual(other.run()['status'],'SKIP')
        self.assertFalse(other.transport.notifications)
    def test_untrusted_cue_cannot_set_bindings_or_grant(self):
        for field, value in [('kind','IGNORE_POLICY'),('source_sha','0'*40),('actor_ref','attacker'),
                             ('epoch',True),('sequence',99),('packet_digest','0'*64),('expect',{}),
                             ('source_ref','https://unapproved.example'),('event_id','repeat')]:
            with self.subTest(field=field):
                self.j=Journey(); cue=self.j.cue(); cue[field]=value
                self.j.transport.raw=r.canonical(cue); self.blocked(self.j.run())
    def test_malformed_large_deep_and_sensitive_cues(self):
        for raw in (b'not-json', b'\xff', b'x'*65537, b'['*17+b']'*17,
                    b'{"token":"sk-abcdefghijklmnopqrstuvwxyz123456789"}'):
            with self.subTest(raw=raw[:20]):
                self.j=Journey(); self.j.transport.raw=raw; self.blocked(self.j.run())
    def test_readiness_conjunction_is_required(self):
        for events in (self.j.events[:3], tuple(replace(e,handoff=None) for e in self.j.events),
                       (*self.j.events[:3],replace(self.j.events[-1],event_id='event-3')),
                       (*self.j.events[:3],replace(self.j.events[-1],sequence=5))):
            with self.subTest(events=events[-1].state):
                self.j=Journey(); self.j.host.sample=replace(self.j.host.sample,
                    observation=replace(self.j.host.sample.observation,events=events))
                self.j.grant=replace(self.j.grant,host_pin=self.j.host.sample.pin)
                self.blocked(self.j.run())
    def test_all_identity_dimensions_drift_block_both_phases(self):
        for phase in (2,5,6):
            for field,value in [('source_sha','0'*40),('epoch',8),('repository','wrong/repo'),
                                ('runtime_paths',('/wrong/python','/wrong','/wrong/import'))]:
                with self.subTest(phase=phase,field=field):
                    self.j=Journey()
                    self.j.host.change=lambda s,n: replace(s,observation=replace(s.observation,**{field:value})) if n>=phase else s
                    self.blocked(self.j.run(),notify=1 if phase==6 else 0)
    def test_session_id_change_rejects_old_grant(self):
        old=self.j.expect.agents[1]; changed=replace(old,session_id='new-session')
        self.j.host.change=lambda s,n: replace(s,observation=replace(s.observation,agents=(self.j.expect.agents[0],changed)))
        self.blocked(self.j.run())
    def test_cli_restart_requires_explicit_same_id_rearm(self):
        self.j.host.sample=replace(self.j.host.sample,generations=(2,1),cli_versions=('claude-2','codex-1'))
        self.j.grant=replace(self.j.grant,host_pin=self.j.host.sample.pin)
        result=self.j.run();self.blocked(result);self.assertEqual(result['code'],'HANDOFF_REARM_REQUIRED')
        self.j.grant=replace(self.j.grant,rearm_generations=(2,1))
        self.assertEqual(self.j.run()['status'],'PASS')
    def test_historical_author_self_review_blocked_even_with_repinned_host(self):
        author=replace(self.j.expect.agents[0],session_id='reviewer-session')
        with self.assertRaises(LLMToolError):
            replace(self.j.expect,authored_by=(author,))
        self.j.host.sample=replace(self.j.host.sample,observation=replace(
            self.j.host.sample.observation,authored_by=(author,)))
        self.j.grant=replace(self.j.grant,host_pin=self.j.host.sample.pin)
        self.blocked(self.j.run())
    def test_base_tree_route_and_venv_drift(self):
        for field,value in [('base_sha','0'*40),('tree_sha','0'*40),('route_digest','0'*64),
                            ('reviewer_runtime',('/wrong/python','/wrong','/wrong/import')),
                            ('preflight_digest','0'*64),('active',False)]:
            with self.subTest(field=field):
                self.j=Journey();self.j.host.change=lambda s,n:replace(s,**{field:value});self.blocked(self.j.run())
    def test_expired_and_revoked_grants(self):
        self.j.grant=replace(self.j.grant,expires_at='2026-10-13T00:00:10Z');self.blocked(self.j.run())
        self.j=Journey();self.j.host.change=lambda s,n:replace(s,active=False) if n>=5 else s
        self.blocked(self.j.run())
    def test_host_callback_expiry_never_notifies_after_claim(self):
        def delay(sample, n):
            if n == 5: self.j.clock.wait(300)
            return sample
        self.j.host.change=delay
        result=self.j.run();self.blocked(result)
        self.assertEqual(list(self.j.claims.rows.values()),['EFFECT_UNKNOWN'])
    def test_counter_callback_expiry_never_notifies_after_claim(self):
        def delay(sample):
            if self.j.host.calls == 5: self.j.clock.wait(300)
            return sample
        self.j.host.counter_change=delay
        result=self.j.run();self.blocked(result)
        self.assertEqual(list(self.j.claims.rows.values()),['EFFECT_UNKNOWN'])
    def test_packet_input_drift_and_privacy_blocked(self):
        for change in (lambda d:d.update(base_sha='0'*40),
                       lambda d:d['sources'][0].update(content='writer-session'),
                       lambda d:d.update(route='PRODUCTION')):
            self.j=Journey();change(self.j.review_fixture.data);self.blocked(self.j.run())
    def test_ack_loss_interrupt_and_restart_never_resend(self):
        for action in (lambda: (_ for _ in ()).throw(RuntimeError('PRIVATE_SECRET')),
                       lambda: (_ for _ in ()).throw(KeyboardInterrupt())):
            self.j=Journey();self.j.transport.on_notify=action;result=self.j.run();self.blocked(result,notify=1)
            self.assertEqual(list(self.j.claims.rows.values()),['EFFECT_UNKNOWN'])
            self.j.transport.on_notify=lambda:None;self.blocked(self.j.run(),notify=1)
    def test_crash_consumed_claim_not_replayed(self):
        self.j.claims.claim('old-consumed',r.digest({'task':self.j.ready.task_id,'final':self.j.final.task_id,
            'contracts':[self.j.ready.digest,self.j.final.digest],'sha':self.j.expect.source_sha,
            'reviewer':self.j.expect.agents[1].to_dict(),'scope':list(self.j.ready.authorized_scope)},
            'llmtool.fake-handoff-reviewed.v1'))
        self.blocked(self.j.run());self.assertEqual(self.j.run()['code'],'HANDOFF_EFFECT_UNKNOWN')
    def test_late_result_has_no_author_receipt_or_pass(self):
        for change in (lambda:self.j.clock.wait(120),lambda:self.j.clock.wait(290)):
            self.j=Journey();self.j.transport.on_review=change;result=self.j.run();self.blocked(result,notify=1)
            self.assertEqual(result['code'],'HANDOFF_LATE_RESULT');self.assertTrue(result['historical_result'])
            self.assertFalse(self.j.transport.receipts);self.assertEqual(list(self.j.claims.rows.values()),['EFFECT_UNKNOWN'])
    def test_both_verdict_categories_required(self):
        for security,functional,status in [('APPROVE','CHANGES_REQUIRED','CHANGES_REQUIRED'),
                ('BLOCKED','APPROVE','BLOCKED'),('CHANGES_REQUIRED','APPROVE','CHANGES_REQUIRED')]:
            self.j=Journey();self.j.transport.verdict_change=lambda v:dict(v,security=security,functional=functional)
            self.assertEqual(self.j.run()['status'],status)
    def test_partial_malformed_unbound_and_exit_zero_results(self):
        for mutate in (lambda v:dict(v,source_sha='0'*40),lambda v:dict(v,security='PASS'),
                       lambda v:dict(v,criterion_evidence=[]),lambda v:dict(v,exit_code=0)):
            self.j=Journey();self.j.transport.verdict_change=mutate;self.blocked(self.j.run(),notify=1)
        for mutate in (lambda d:replace(d,started=False),lambda d:replace(d,complete=False),
                       lambda d:replace(d,session_id='other'),lambda d:replace(d,message_id='other')):
            self.j=Journey();self.j.transport.reply_change=mutate;self.blocked(self.j.run(),notify=1)
    def test_delivery_receipt_cleanup_and_finalization_fail_closed(self):
        self.j.transport.delivery_change=lambda d:replace(d,delivered=False);self.blocked(self.j.run(),notify=1)
        self.j=Journey();self.j.transport.receipt=lambda d,v:('copied-result',);self.blocked(self.j.run(),notify=1)
        self.j=Journey();self.j.transport.cleanup=False;self.blocked(self.j.run(),notify=1)
        self.j=Journey();self.j.claims.fail_finish=True;self.blocked(self.j.run(),notify=1)
    def test_trusted_host_counter_missing_tampered_or_changed(self):
        for transform in (lambda s:None,lambda s:replace(s,source='worker-self-report'),
                          lambda s:replace(s,generation='other'),lambda s:replace(s,counter=True)):
            self.j=Journey();self.j.host.counter_change=transform;self.blocked(self.j.run())
            self.assertEqual(self.j.run()['zero_model_calls'],'NOT_VERIFIED')
        self.j=Journey();self.j.transport.on_review=lambda:setattr(self.j.host,'invoked',1)
        result=self.j.run();self.blocked(result,notify=1);self.assertEqual(result['zero_model_calls'],'NOT_VERIFIED')
    def test_timeout_clock_rollback_no_progress_and_limits(self):
        self.j.transport.on_read=lambda:self.j.clock.wait(6);self.blocked(self.j.run())
        self.j=Journey();self.j.transport.on_read=lambda:setattr(self.j.clock,'mono',0);self.blocked(self.j.run())
        self.j=Journey();self.j.transport.no_events=True;self.j.clock.wait=lambda x:None
        result=self.j.run();self.blocked(result);self.assertEqual(result['code'],'HANDOFF_EVENT_LIMIT')
        for field,value in [('wait_seconds',601),('poll_seconds',61),('read_seconds',6),('reviewer_seconds',121)]:
            self.j=Journey();self.j.grant=replace(self.j.grant,**{field:value});self.blocked(self.j.run())
    def test_live_or_caller_composition_unavailable(self):
        self.j.host.test_only=False;self.blocked(self.j.run())
        self.assertEqual(h._run_fake_handoff(self.j.ready,self.j.final,self.j.expect,{})['status'],'BLOCKED')


if __name__ == '__main__': unittest.main()
