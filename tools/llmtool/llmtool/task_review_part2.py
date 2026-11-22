"""Review packet construction, host services and envelope helpers.

#2281: extracted from task_review without behaviour changes so every module
stays inside one bounded read.
"""
import hashlib
from dataclasses import dataclass
from typing import Mapping
from types import MappingProxyType
from datetime import datetime, timezone

from .action_contract import TaskBinding, _hex, _integer
from .diagnostics import safe_diagnostic_text
from .task_lifecycle import utc_time
from .task_verification import (CriterionKind, EvidenceSource, EvidenceOutcome,
                                parse_evidence_record)
from .run_budget import RunBudget
from .task_review_part1 import (
    PACKET_LIMIT, VERDICT_LIMIT, SOURCE_LIMIT, EVIDENCE_LIMIT, REVIEWER_ROLES,
    HOST_INSTRUCTIONS, reject, canonical, digest, load_json, screen, safe_id,
    scope_path, ReviewAuthority, ReviewerLauncher, AuthorshipObserver, Clock)


def clock_utc(clock):
    value=clock.utc()
    _integer(value,maximum=253402300799)
    return datetime.fromtimestamp(value,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


@dataclass(frozen=True)
class PairApproval:
    """Immutable host pair pin; its constructor never authenticates approval."""
    ready_task: TaskBinding
    acceptance_task: TaskBinding
    source_sha: str
    epoch: int
    criterion_map: tuple
    security_criterion: str
    functional_criterion: str
    authors_digest: str
    record_ref: str
    expires_at: str

    def validate(self, ready, final, expect, now):
        safe_id(self.record_ref)
        _integer(self.epoch, minimum=1)
        _hex(self.source_sha, (40,64))
        _hex(self.authors_digest)
        if (self.ready_task != expect.task or self.ready_task != ready.binding(self.ready_task.run_id) or
                self.acceptance_task != final.binding(self.acceptance_task.run_id) or
                self.ready_task.task_id == self.acceptance_task.task_id or
                self.source_sha != expect.source_sha or self.epoch != expect.epoch or
                self.authors_digest != digest([a.to_dict() for a in (expect.authored_by or ())]) or
                utc_time(self.expires_at) <= utc_time(now)):
            reject('REVIEW_PAIR_NOT_APPROVED')
        if (ready.authorized_scope != final.authorized_scope or
                ready.forbidden_scope != final.forbidden_scope or
                any(c.kind is CriterionKind.REVIEW for c in ready.criteria)):
            reject('REVIEW_PAIR_NOT_APPROVED')
        rc = {c.criterion_id: c for c in ready.criteria}
        fc = {c.criterion_id: c for c in final.criteria}
        if type(self.criterion_map) is not tuple or len(self.criterion_map) != len(rc):
            reject('REVIEW_PAIR_NOT_APPROVED')
        try:
            mapping = dict(self.criterion_map)
        except (ValueError, TypeError):
            reject('REVIEW_PAIR_NOT_APPROVED')
        reviews = {self.security_criterion, self.functional_criterion}
        if (len(mapping) != len(rc) or set(mapping) != set(rc) or
                len(set(mapping.values())) != len(rc) or len(reviews) != 2 or
                set(fc) != set(mapping.values()) | reviews):
            reject('REVIEW_PAIR_NOT_APPROVED')
        for a, b in mapping.items():
            if b not in fc or (rc[a].kind, rc[a].description) != (fc[b].kind, fc[b].description):
                reject('REVIEW_PAIR_NOT_APPROVED')
        if any(fc[c].kind is not CriterionKind.REVIEW for c in reviews):
            reject('REVIEW_PAIR_NOT_APPROVED')


@dataclass(frozen=True)
class HostServices:
    """Trusted host composition, never a JSON/CLI authorization endpoint."""
    authority: ReviewAuthority
    launcher: ReviewerLauncher
    observer: AuthorshipObserver
    clock: Clock
    approval: 'PairApproval'
    packet_input: bytes
    route: Mapping
    reviewer_launch_nonce: str
    issuer_capability_id: str
    redeem_run_id: str
    budget: RunBudget
    hard_cost_cap: bool
    unknown_cost_permitted: bool
    reviewer_role: str = REVIEWER_ROLES[0]
    worker_runtime_digest: str = None

    def __post_init__(self):
        if type(self.packet_input) is not bytes or type(self.approval) is not PairApproval:
            reject('REVIEW_HOST_SERVICES_INVALID')
        if self.reviewer_role not in REVIEWER_ROLES:
            reject('REVIEW_HOST_SERVICES_INVALID')
        if self.reviewer_role == REVIEWER_ROLES[1]:
            _hex(self.worker_runtime_digest)
        elif self.worker_runtime_digest is not None:
            reject('REVIEW_HOST_SERVICES_INVALID')
        route = dict(self.route)
        if set(route) != {'id','version','digest','profile','model'}:
            reject('REVIEW_HOST_SERVICES_INVALID')
        safe_id(route['id']); _integer(route['version'],minimum=1); _hex(route['digest'])
        for key in ('profile','model'): safe_diagnostic_text(route[key],1024)
        object.__setattr__(self,'route',MappingProxyType(route))
        _hex(self.reviewer_launch_nonce,(32,)); _hex(self.redeem_run_id,(32,))
        safe_id(self.issuer_capability_id)
        if type(self.budget) is not RunBudget or type(self.hard_cost_cap) is not bool or type(self.unknown_cost_permitted) is not bool:
            reject('REVIEW_HOST_SERVICES_INVALID')


@dataclass(frozen=True)
class ReviewPacket:
    data: bytes

    def __post_init__(self):
        if type(self.data) is not bytes or len(self.data) > PACKET_LIMIT:
            reject('REVIEW_PACKET_TOO_LARGE',component='total',limit=PACKET_LIMIT)
        screen(self.data.decode('utf-8'))
        load_json(self.data.decode('utf-8'),PACKET_LIMIT)

    @property
    def digest(self):
        return hashlib.sha256(b'llmtool.review-packet.v1\0' + self.data).hexdigest()

    @property
    def reviewed_object_digest(self):
        payload = self.to_dict()
        payload.pop('observation_digest', None)
        payload.pop('sequence', None)
        return digest(payload, 'llmtool.reviewed-object.v1')

    def to_dict(self):
        return load_json(self.data.decode('utf-8'), PACKET_LIMIT)

    def messages(self):
        p = self.to_dict()
        binding = {k:p[k] for k in ('ready_task','acceptance_task','source_sha','epoch','sequence',
                                   'observation_digest','reviewer_binding_digest')}
        binding['packet_digest'] = self.digest
        user = canonical({'review_packet_data':p,'verdict_binding':binding}).decode('utf-8')
        screen(user)
        return [{'role': 'system', 'content': HOST_INSTRUCTIONS},
                {'role': 'user', 'content': user}]


def make_packet(ready, final, expect, snapshot, approval, observation, bundle, *, expected_route='FAKE_TEST_ONLY'):
    """Only fixture-observed selected originals, never model-supplied references."""
    if len(ready.to_json().encode()) > VERDICT_LIMIT or len(final.to_json().encode()) > VERDICT_LIMIT:
        reject('REVIEW_PACKET_TOO_LARGE', component='contract', limit=VERDICT_LIMIT)
    required = {'base_sha', 'source_sha', 'diff', 'sources', 'evidence', 'ci', 'route', 'grant_ref'}
    if type(bundle) is not dict or set(bundle) != required or bundle['source_sha'] != expect.source_sha:
        reject('REVIEW_PACKET_BINDING_INVALID')
    _hex(bundle['base_sha'], (40, 64))
    if type(bundle['sources']) is not list or not 1 <= len(bundle['sources']) <= 32:
        reject('REVIEW_PACKET_BINDING_INVALID')
    sources = []
    seen = set()
    for item in bundle['sources']:
        if type(item) is not dict or set(item) != {'path', 'content', 'sha256'}:
            reject('REVIEW_PACKET_BINDING_INVALID')
        scope_path(item['path'], ready.authorized_scope)
        if (item['path'] in seen or any(item['path'] == p or item['path'].startswith(p + '/')
                                     for p in ready.forbidden_scope)):
            reject('REVIEW_EXPORT_SCOPE_BLOCKED')
        seen.add(item['path'])
        screen(item['content'])
        raw = item['content'].encode('utf-8')
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            reject('REVIEW_PACKET_BINDING_INVALID')
        sources.append({**item, 'byte_length': len(raw)})
    if type(bundle['diff']) is not dict or set(bundle['diff']) != {'content', 'sha256', 'changed_paths', 'complete'}:
        reject('REVIEW_PACKET_BINDING_INVALID')
    diff = bundle['diff']
    screen(diff['content'])
    if (diff['complete'] is not True or type(diff['changed_paths']) is not list or
            set(diff['changed_paths']) != seen or len(diff['changed_paths']) != len(seen) or
            hashlib.sha256(diff['content'].encode()).hexdigest() != diff['sha256'] or
            '\x00' in diff['content'] or 'GIT binary patch' in diff['content']):
        reject('REVIEW_PACKET_BINDING_INVALID')
    size = len(diff['content'].encode()) + sum(s['byte_length'] for s in sources)
    if size > SOURCE_LIMIT:
        reject('REVIEW_PACKET_TOO_LARGE', component='diff_source', limit=SOURCE_LIMIT)
    evidence = bundle['evidence']
    if type(evidence) is not list or not 1 <= len(evidence) <= 128:
        reject('REVIEW_EVIDENCE_NOT_VERIFIED')
    records = {r.evidence_id: r for e in observation.events for r in e.evidence}
    represented = set()
    for item in evidence:
        if type(item) is not dict or set(item) != {'record', 'original_digest', 'origin_ref', 'verified'}:
            reject('REVIEW_EVIDENCE_NOT_VERIFIED')
        safe_id(item['origin_ref'])
        _hex(item['original_digest'])
        record = item['record']
        if type(record) is not dict or record.get('evidence_id') not in records:
            reject('REVIEW_EVIDENCE_NOT_VERIFIED')
        actual = records[record['evidence_id']]
        parsed = parse_evidence_record(canonical(record).decode())
        if (parsed != actual or item['verified'] is not True or
                actual.source is EvidenceSource.MODEL_CLAIM or actual.outcome is not EvidenceOutcome.PASSED or
                not actual.complete or record['evidence_id'] in represented or
                item['original_digest'] not in actual.artifact_digests):
            reject('REVIEW_EVIDENCE_NOT_VERIFIED')
        represented.add(record['evidence_id'])
    if represented != set(records):
        reject('REVIEW_EVIDENCE_NOT_VERIFIED')
    ci = bundle['ci']
    if (type(ci) is not dict or set(ci) != {'repo', 'run_id', 'head_sha', 'jobs', 'origin_ref'} or
            ci['head_sha'] != expect.source_sha or ci['repo'] != observation.repository or
            type(ci['jobs']) is not dict or set(ci['jobs']) != {'3.9', '3.14'} or
            any(v != 'SUCCESS' for v in ci['jobs'].values())):
        reject('REVIEW_EVIDENCE_NOT_VERIFIED')
    safe_id(ci['run_id']); safe_id(ci['origin_ref'])
    if len(canonical({'evidence': evidence, 'ci': ci})) > EVIDENCE_LIMIT:
        reject('REVIEW_PACKET_TOO_LARGE', component='test_ci', limit=EVIDENCE_LIMIT)
    if bundle['route'] != expected_route:
        reject('REVIEW_ROUTE_NOT_AUTHORIZED')
    safe_id(bundle['grant_ref'])
    h = snapshot.handoff_preview
    payload = {'schema_version': 1, 'ready_contract': ready.to_dict(), 'acceptance_contract': final.to_dict(),
        'ready_task': approval.ready_task.to_dict(), 'acceptance_task': approval.acceptance_task.to_dict(),
        'criterion_map': [list(p) for p in approval.criterion_map],
        'security_criterion': approval.security_criterion, 'functional_criterion': approval.functional_criterion,
        'pair_approval_ref': approval.record_ref, 'base_sha': bundle['base_sha'], 'source_sha': expect.source_sha,
        'epoch': expect.epoch, 'sequence': snapshot.last_sequence, 'observation_digest': observation.digest,
        'authors_digest': approval.authors_digest, 'reviewer_binding_digest': digest(h.to_agent.to_dict()),
        'handoff_digest': digest(h.to_dict()), 'check_digest': expect.check_digest,
        'artifact_digests': list(expect.artifact_digests), 'diff': {**diff, 'byte_length': len(diff['content'].encode())},
        'sources': sources, 'evidence': evidence, 'ci': ci, 'route': bundle['route'], 'grant_ref': bundle['grant_ref']}
    raw = canonical(payload)
    if len(raw) > PACKET_LIMIT:
        reject('REVIEW_PACKET_TOO_LARGE', component='total', limit=PACKET_LIMIT)
    screen(raw.decode())
    # Logical Session/root strings never leave the host, even in selected source.
    private = [value for a in (*expect.agents,*(expect.authored_by or ()))
               for value in (a.session_id, a.root, a.common_dir)]
    if any(value in raw.decode() for value in private):
        reject('REVIEW_PRIVACY_BLOCKED')
    load_json(raw.decode(), PACKET_LIMIT)
    return ReviewPacket(raw)


def envelope(code, *, snapshot=None, fake=False, calls=0, **extra):
    result = {'schema_version':1, 'code':code, 'status':'BLOCKED', 'authorized':False,
        'runtime_evidence_status':'NOT_VERIFIED', 'test_only':fake,
        'production_handoff':'NOT_DELIVERED', 'collector_status':'NOT_ATTESTED',
        'invocation_status':'NOT_CALLED', 'verdict_validation':'NOT_VERIFIED',
        'reported_state': snapshot.reported_state.value if snapshot and snapshot.reported_state else None,
        'structural_validation': snapshot.structural_validation if snapshot else 'INVALID',
        'provider_calls':calls, 'input_tokens':None, 'output_tokens':None, 'cost':None}
    result.update(extra)
    return result


def _clocks_agree(previous, current):
    utc_delta = (utc_time(current.observed_at)-utc_time(previous.observed_at)).total_seconds()
    mono_delta = current.monotonic_at-previous.monotonic_at
    if utc_delta < 0 or mono_delta < 0 or abs(utc_delta-mono_delta) > 2:
        reject('REVIEW_CLOCK_INVALID')
