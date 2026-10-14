"""Private synchronous S1b fake handoff seam; no live transport or store.

Trusted fixture composition is cooperative Python, not host authentication or
OS isolation. Deadlines bound simulated callbacks, not arbitrary blocking code.
Production notification authority/composition is deliberately unavailable.
"""
import math
from dataclasses import dataclass
from typing import Protocol

from .action_contract import _hex, _integer
from .errors import LLMToolError
from .task_lifecycle import inspect_task, State, Role, utc_time, MAX_EVENTS
from .task_review import (PairApproval, canonical, digest, load_json, screen,
                          make_packet, parse_verdict, reject, clock_utc)
from .task_review_collector import Observation

CUE_LIMIT = 64 * 1024


@dataclass(frozen=True)
class HostSample:
    observation: Observation
    base_sha: str
    tree_sha: str
    reviewer_runtime: tuple
    cli_versions: tuple
    generations: tuple
    route_digest: str
    preflight_digest: str
    grant_ref: str
    active: bool = True

    @property
    def pin(self):
        return digest({'state': self.observation.state_digest,
                       'base': self.base_sha, 'tree': self.tree_sha,
                       'runtime': self.reviewer_runtime,
                       'versions': self.cli_versions, 'generations': self.generations,
                       'route': self.route_digest, 'preflight': self.preflight_digest,
                       'grant': self.grant_ref}, 'llmtool.fake-handoff-host.v1')


@dataclass(frozen=True)
class InvocationSample:
    source: str
    counter: int
    generation: str


@dataclass(frozen=True)
class FakeGrant:
    approval: PairApproval
    host_pin: str
    packet_digest: str
    event_id: str
    source_ref: str
    actor_ref: str
    invocation_source: str
    invocation_generation: str
    not_before: str
    expires_at: str
    armed_generations: tuple
    rearm_generations: tuple = ()
    wait_seconds: int = 600
    poll_seconds: int = 60
    read_seconds: int = 5
    reviewer_seconds: int = 120


@dataclass(frozen=True)
class Delivery:
    message_id: str
    session_id: str
    packet_digest: str
    delivered: bool


@dataclass(frozen=True)
class ReviewResult:
    message_id: str
    session_id: str
    packet_digest: str
    started: bool
    complete: bool
    verdict: str


class FakeHost(Protocol):
    test_only: bool
    def observe(self) -> HostSample: ...
    def invocations(self) -> InvocationSample: ...


class FakeClaims(Protocol):
    test_only: bool
    def claim(self, key: str, reviewed_object: str) -> str: ...
    def finish(self, key: str, status: str) -> None: ...


class FakeTransport(Protocol):
    test_only: bool
    def read(self): ...
    def notify(self, reference: str, packet) -> Delivery: ...
    def review(self, delivery: Delivery, packet) -> ReviewResult: ...
    def receipt(self, delivery: Delivery, verdict_digest: str): ...
    def clean(self) -> bool: ...


@dataclass(frozen=True)
class FakeServices:
    host: FakeHost
    transport: FakeTransport
    claims: FakeClaims
    clock: object
    grant: FakeGrant
    packet_input: bytes


def handoff_services_factory():
    """No shipped host registration, live CLI or notification authority."""
    return None


def _result(code, status='BLOCKED', zero='NOT_VERIFIED', **extra):
    return dict(code=code, status=status, test_only=True, authorized=False,
                runtime_evidence_status='NOT_VERIFIED', evidence_class='FAKE_FIXTURE',
                zero_model_calls=zero, token_usage='UNKNOWN', **extra)


def _run_fake_handoff(ready, final, expect, services):
    """Finite fixture-only journey; DATA cannot select host, grant or effect.

    FakeClaims lives in fixture ownership and retains at-most-once state across
    adapter instances. It is NOT ReceiptLedger notification support or a durable
    production store. Every consumed ambiguous effect remains EFFECT_UNKNOWN.
    """
    key = None
    done = False
    first_counter = None
    last_utc = last_mono = None
    zero = 'NOT_VERIFIED'
    outcome = _result('HANDOFF_INPUT_INVALID')
    try:
        if type(services) is not FakeServices or type(services.grant) is not FakeGrant:
            reject('HANDOFF_HOST_UNAVAILABLE')
        s, g = services, services.grant
        if any(getattr(x, 'test_only', None) is not True
               for x in (s.host, s.transport, s.claims)):
            reject('HANDOFF_HOST_UNAVAILABLE')
        for value, maximum in ((g.wait_seconds, 600), (g.poll_seconds, 60),
                               (g.read_seconds, 5), (g.reviewer_seconds, 120)):
            _integer(value, minimum=1, maximum=maximum)
        if type(g.approval) is not PairApproval or type(s.packet_input) is not bytes:
            reject('HANDOFF_INPUT_INVALID')
        for value in (g.host_pin, g.packet_digest):
            _hex(value)

        def clocks():
            nonlocal last_utc, last_mono
            now = clock_utc(s.clock)
            mono = s.clock.monotonic()
            if type(mono) not in (int, float) or not math.isfinite(mono):
                reject('HANDOFF_CLOCK_INVALID')
            stamp = utc_time(now).timestamp()
            if last_utc is not None and (stamp < last_utc or mono < last_mono or
                                        abs((stamp-last_utc)-(mono-last_mono)) > 5):
                reject('HANDOFF_CLOCK_INVALID')
            last_utc, last_mono = stamp, mono
            return now, mono

        def counter():
            sample = s.host.invocations()
            if (type(sample) is not InvocationSample or
                    sample.source != g.invocation_source or not sample.source or
                    sample.generation != g.invocation_generation or not sample.generation or
                    type(sample.counter) is not int or sample.counter < 0):
                reject('HANDOFF_INVOCATION_UNVERIFIED')
            return sample

        now, start = clocks()
        first_counter = counter()
        deadline = start + g.wait_seconds
        expires = min(utc_time(g.expires_at), utc_time(g.approval.expires_at))
        if utc_time(g.not_before) >= expires:
            reject('HANDOFF_GRANT_INVALID')

        def observe():
            now, mono = clocks()
            if not utc_time(g.not_before) <= utc_time(now) < expires or mono >= deadline:
                reject('HANDOFF_EXPIRED')
            g.approval.validate(ready, final, expect, now)
            h = s.host.observe()
            if type(h) is not HostSample or h.active is not True:
                reject('HANDOFF_HOST_UNAVAILABLE')
            h.observation.validate(expect, h.observation.events, now, mono)
            if h.pin != g.host_pin:
                reject('HANDOFF_IDENTITY_DRIFT')
            for field in (h.base_sha, h.tree_sha):
                _hex(field, (40, 64))
            _hex(h.route_digest); _hex(h.preflight_digest)
            if (type(h.cli_versions) is not tuple or len(h.cli_versions) != 2 or
                    any(type(v) is not str or not v for v in h.cli_versions) or
                    type(h.generations) is not tuple or len(h.generations) != 2 or
                    any(type(v) is not int or v < 0 for v in h.generations)):
                reject('HANDOFF_SESSION_INVALID')
            if h.generations != g.armed_generations and h.generations != g.rearm_generations:
                reject('HANDOFF_REARM_REQUIRED')
            reviewers = [a for a in expect.agents if a.role is Role.REVIEWER]
            if len(reviewers) != 1:
                reject('HANDOFF_REVIEWER_INVALID')
            reviewer = reviewers[0]
            if any(a.agent_id == reviewer.agent_id or a.session_id == reviewer.session_id
                   for a in (expect.authored_by or ())):
                reject('HANDOFF_REVIEWER_INVALID')
            if (type(h.reviewer_runtime) is not tuple or len(h.reviewer_runtime) != 3 or
                    any(type(p) is not str or not p.startswith(reviewer.root+'/')
                        for p in h.reviewer_runtime)):
                reject('HANDOFF_RUNTIME_INVALID')
            if counter() != first_counter:
                reject('HANDOFF_MODEL_CALL_DETECTED')
            # Host callbacks can consume time. Never authorize an effect using
            # the clocks captured before the callbacks returned.
            fresh_now, fresh_mono = clocks()
            if not utc_time(g.not_before) <= utc_time(fresh_now) < expires or fresh_mono >= deadline:
                reject('HANDOFF_EXPIRED')
            g.approval.validate(ready, final, expect, fresh_now)
            h.observation.validate(expect, h.observation.events, fresh_now, fresh_mono)
            return h, reviewer

        for _ in range(MAX_EVENTS):
            observe()
            _, read_start = clocks()
            raw = s.transport.read()
            now, read_end = clocks()
            if read_end-read_start > g.read_seconds:
                reject('HANDOFF_READ_TIMEOUT')
            if read_end >= deadline:
                reject('HANDOFF_EXPIRED')
            if raw is None:
                s.clock.wait(min(g.poll_seconds, deadline-read_end))
                continue
            if type(raw) is not bytes or len(raw) > CUE_LIMIT:
                reject('HANDOFF_CUE_INVALID')
            text = raw.decode('utf-8')
            cue = load_json(text, CUE_LIMIT)
            screen(text)
            h, reviewer = observe()
            events = h.observation.events
            snapshot = inspect_task(ready, expect, events, now)
            if (snapshot.structural_validation != 'SUCCESS' or
                    snapshot.reported_state is not State.WAITING_REVIEW or
                    snapshot.handoff_preview is None or
                    snapshot.handoff_preview.to_agent != reviewer):
                reject('HANDOFF_NOT_READY')
            expected = {'kind': 'READY_FOR_REVIEW', 'event_id': g.event_id,
                        'source_ref': g.source_ref, 'actor_ref': g.actor_ref,
                        'source_sha': expect.source_sha, 'epoch': expect.epoch,
                        'sequence': snapshot.last_sequence, 'packet_digest': g.packet_digest}
            if type(cue) is not dict or set(cue) != set(expected) or any(
                    type(cue[k]) is not type(v) or cue[k] != v for k, v in expected.items()):
                reject('HANDOFF_CUE_BINDING_INVALID')
            if g.event_id != events[-1].event_id:
                reject('HANDOFF_CUE_BINDING_INVALID')
            packet = make_packet(ready, final, expect, snapshot, g.approval,
                                 h.observation, load_json(s.packet_input.decode(), 1024*1024))
            if packet.digest != g.packet_digest or packet.to_dict()['base_sha'] != h.base_sha:
                reject('HANDOFF_PACKET_DRIFT')
            packet.messages()  # Existing fixed instruction / untrusted DATA boundary.
            # A fresh host observation after packet preparation and before claim/effect.
            observe()
            reviewed = digest({'task': ready.task_id, 'final': final.task_id,
                               'contracts': [ready.digest, final.digest], 'sha': expect.source_sha,
                               'reviewer': reviewer.to_dict(), 'scope': list(ready.authorized_scope)},
                              'llmtool.fake-handoff-reviewed.v1')
            proposed = digest({'object': reviewed, 'run': expect.task.run_id, 'epoch': expect.epoch,
                               'pair': g.approval.record_ref, 'transport': 'FAKE_TEST_ONLY',
                               'event': g.event_id}, 'llmtool.fake-handoff-claim.v1')
            claim = s.claims.claim(proposed, reviewed)
            if claim == 'EFFECT_DONE':
                outcome = _result('HANDOFF_ALREADY_REVIEWED', 'SKIP')
                break
            if claim != 'CONSUMED':
                reject('HANDOFF_EFFECT_UNKNOWN')
            key = proposed
            observe()  # Claim latency or host revocation cannot authorize notification.
            delivery = s.transport.notify(g.source_ref, packet)
            if (type(delivery) is not Delivery or delivery.delivered is not True or
                    not delivery.message_id or delivery.session_id != reviewer.session_id or
                    delivery.packet_digest != packet.digest):
                reject('HANDOFF_DELIVERY_UNKNOWN')
            observe()  # Consumer admission is independent of queue acknowledgement.
            _, turn_start = clocks()
            turn_deadline = min(deadline, turn_start+g.reviewer_seconds)
            reply = s.transport.review(delivery, packet)
            now, turn_end = clocks()
            if turn_end >= turn_deadline or utc_time(now) >= expires:
                outcome = _result('HANDOFF_LATE_RESULT', historical_result=True)
                break
            observe()
            if (type(reply) is not ReviewResult or reply.started is not True or reply.complete is not True or
                    reply.message_id != delivery.message_id or reply.session_id != reviewer.session_id or
                    reply.packet_digest != packet.digest):
                reject('HANDOFF_RESULT_UNVERIFIED')
            verdict = parse_verdict(reply.verdict, packet)
            status = ('PASS' if verdict['security'] == verdict['functional'] == 'APPROVE' else
                      'CHANGES_REQUIRED' if 'BLOCKED' not in (verdict['security'], verdict['functional']) else 'BLOCKED')
            observe()
            receipt = s.transport.receipt(delivery, digest(verdict))
            observe()
            author = snapshot.handoff_preview.from_agent
            if receipt != (author.session_id, delivery.message_id, packet.digest, digest(verdict)):
                reject('HANDOFF_RECEIPT_UNKNOWN')
            s.claims.finish(key, 'EFFECT_DONE')
            done = True
            outcome = _result('HANDOFF_FAKE_RESULT', status)
            break
        else:
            reject('HANDOFF_EVENT_LIMIT')
    except KeyboardInterrupt:
        outcome = _result('HANDOFF_INTERRUPTED')
    except (Exception,) as exc:
        code = getattr(exc, 'review_code', 'HANDOFF_INPUT_INVALID')
        outcome = _result(code if isinstance(exc, LLMToolError) else 'HANDOFF_INPUT_INVALID')
    finally:
        if key is not None and not done:
            try:
                services.claims.finish(key, 'EFFECT_UNKNOWN')
            except Exception:
                outcome = _result('HANDOFF_CLAIM_FINALIZE_UNKNOWN')
        if type(services) is FakeServices:
            try:
                if services.transport.clean() is not True:
                    outcome = _result('HANDOFF_CLEANUP_UNKNOWN')
                sample = services.host.invocations()
                if (first_counter is None or type(sample) is not InvocationSample or sample != first_counter):
                    outcome = _result('HANDOFF_INVOCATION_UNVERIFIED')
                else:
                    zero = 'FAKE_HOST_VERIFIED'
            except Exception:
                outcome = _result('HANDOFF_INVOCATION_UNVERIFIED')
    outcome['zero_model_calls'] = zero
    return outcome
