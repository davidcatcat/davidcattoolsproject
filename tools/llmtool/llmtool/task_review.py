"""S1b A: bounded review data and fake-only integration, no production issuer.

The private fixture seam is deliberately not a production host API. No provider
factory/configuration is imported. B must independently design actual attestation,
issuer/replay and route wiring; CLI input can never select this fixture seam.
"""
import hashlib
import json
import re
import time
import sys
import math
from dataclasses import dataclass
from typing import Protocol, TYPE_CHECKING, Mapping
from types import MappingProxyType
from datetime import datetime, timezone

from .action_contract import TaskBinding, _hex, _identifier, _path, _integer, _pairs
from .diagnostics import (safe_diagnostic_text, _contains_credential_value,
                          contains_unsafe_controls)
from .errors import coded_error, LLMToolError
from .task_lifecycle import inspect_task, State, utc_time
from .task_verification import CriterionKind, EvidenceSource, EvidenceOutcome, parse_evidence_record
from .run_budget import (RunBudget, UsageLedger, CallPhase, BudgetCode,
                         admit_next, usage_from_provider)
from .repair_policy import review_source_limit_bytes

from .task_review_part1 import (
    PACKET_LIMIT, SOURCE_LIMIT, EVIDENCE_LIMIT, VERDICT_LIMIT, REVIEWER_ROLES,
    HOST_INSTRUCTIONS, reject, canonical, digest, load_json, screen, safe_id,
    scope_path, ReviewCallBinding, Admission, ConsumeToken, CallOutcome,
    LaunchResult, ReviewAuthority, ReviewerLauncher, AuthorshipObserver, Clock,
    UnavailableReviewAuthority)
from .task_review_part2 import (
    HostServices, PairApproval, clock_utc, ReviewPacket, make_packet, envelope,
    _clocks_agree)


def review_authority_factory():
    # Only B's separately accepted task may replace this single factory binding.
    return UnavailableReviewAuthority()


def _parse_verdict(text, packet):
    value = load_json(text)
    screen(text)
    p = packet.to_dict()
    bound = ('ready_task', 'acceptance_task', 'source_sha', 'epoch', 'sequence',
             'observation_digest', 'reviewer_binding_digest')
    keys = {'schema_version', 'review_id', *bound, 'packet_digest', 'security',
            'functional', 'findings', 'criterion_evidence'}
    if (type(value) is not dict or set(value) != keys or type(value['schema_version']) is not int or
            value['schema_version'] != 1 or value['packet_digest'] != packet.digest or
            any(type(value[k]) is not type(p[k]) or value[k] != p[k] for k in bound)):
        reject('REVIEW_VERDICT_INVALID')
    safe_id(value['review_id'])
    # Reuse strict existing nested binding validators; bool != version integer.
    for key in ('ready_task','acceptance_task'):
        TaskBinding(**value[key])
    decisions = ('APPROVE', 'CHANGES_REQUIRED', 'BLOCKED')
    if value['security'] not in decisions or value['functional'] not in decisions:
        reject('REVIEW_VERDICT_INVALID')
    findings = value['findings']
    if type(findings) is not list or len(findings) > 32:
        reject('REVIEW_VERDICT_INVALID')
    paths = {s['path'] for s in p['sources']}
    criteria = {c['criterion_id'] for c in p['acceptance_contract']['criteria']}
    refs = {e['origin_ref'] for e in p['evidence']} | {p['ci']['origin_ref']}
    ids = set()
    for f in findings:
        if type(f) is not dict or set(f) != {'finding_id','category','severity','priority','release_blocker',
                                           'summary','reproducer','path','criterion_id','evidence_refs'}:
            reject('REVIEW_VERDICT_INVALID')
        safe_id(f['finding_id'])
        if (f['finding_id'] in ids or f['category'] not in ('Security','Functional') or
                f['severity'] not in ('SEV-0','SEV-1','SEV-2','SEV-3','SEV-4') or
                f['priority'] not in ('P0','P1','P2','P3') or type(f['release_blocker']) is not bool or
                f['path'] not in paths or f['criterion_id'] not in criteria or
                type(f['evidence_refs']) is not list or not 1 <= len(f['evidence_refs']) <= 32 or
                not set(f['evidence_refs']) <= refs):
            reject('REVIEW_VERDICT_INVALID')
        ids.add(f['finding_id'])
        for field in ('summary', 'reproducer'):
            safe_diagnostic_text(f[field], 1024)
        category = f['category'].lower()
        if ((f['release_blocker'] or f['severity'] in ('SEV-0','SEV-1','SEV-2')) and
                value[category] == 'APPROVE'):
            reject('REVIEW_VERDICT_INVALID')
    entries = value['criterion_evidence']
    if type(entries) is not list or len(entries) > 32:
        reject('REVIEW_VERDICT_INVALID')
    selected = set()
    for entry in entries:
        if (type(entry) is not dict or set(entry) != {'criterion_id', 'evidence_refs'} or
                entry['criterion_id'] not in criteria or entry['criterion_id'] in selected or
                type(entry['evidence_refs']) is not list or not 1 <= len(entry['evidence_refs']) <= 32 or
                not set(entry['evidence_refs']) <= refs):
            reject('REVIEW_VERDICT_INVALID')
        selected.add(entry['criterion_id'])
    if selected != criteria:
        reject('REVIEW_VERDICT_INVALID')
    return value


def parse_verdict(text, packet):
    try:
        return _parse_verdict(text, packet)
    except (LLMToolError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        if getattr(exc,'review_code',None) == 'REVIEW_PRIVACY_BLOCKED':
            raise
        reject('REVIEW_VERDICT_INVALID')


def run_review(ready, final, expect, events, fixture):
    """Four trusted host seams; shipped CLI factory is unavailable.

    Only the factory/test host supplies services and pinned context. CLI/env/JSON
    cannot inject them. B must authenticate its implementations separately.
    """
    snapshot = None
    calls = 0
    invocation = 'NOT_CALLED'
    token = binding = None
    call_complete = False
    consumed = False
    result = final_result = None
    fake = getattr(fixture.authority,'test_only',False) is True
    try:
        real_start = time.monotonic()
        real_deadline = real_start + min(180, fixture.budget.wall_clock_ms / 1000)
        start = fixture.clock.monotonic()
        now = clock_utc(fixture.clock)
        snapshot = inspect_task(ready, expect, events, now)
        if (snapshot.structural_validation != 'SUCCESS' or snapshot.reported_state is not State.WAITING_REVIEW or
                snapshot.handoff_preview is None):
            reject('REVIEW_NOT_READY')
        if type(fixture.approval) is not PairApproval:
            reject('REVIEW_PAIR_NOT_APPROVED')
        approval = fixture.approval
        approval.validate(ready, final, expect, now)
        # Sample the clock only AFTER observe(): an observation stamps its own time
        # last, so a clock read taken earlier is older than it and looks negative.
        before = fixture.observer.observe()
        before.validate(expect, events, clock_utc(fixture.clock), fixture.clock.monotonic())
        packet = make_packet(ready, final, expect, snapshot, approval, before, load_json(fixture.packet_input.decode(),1024*1024),
                             expected_route='FAKE_TEST_ONLY' if fake and type(fixture) is not HostServices else fixture.route['id'])
        # Packet construction must not leave time/state unchecked before effect.
        submit = fixture.observer.observe()
        submit_now = clock_utc(fixture.clock)
        submit_mono = fixture.clock.monotonic()
        submit.validate(expect, events, submit_now, submit_mono)
        # The exact observation pinned into the packet/binding must remain fresh
        # too; a new equivalent sample cannot renew an old observation digest.
        before.validate(expect, events, submit_now, submit_mono)
        _clocks_agree(before, submit)
        if submit.state_digest != before.state_digest:
            reject('REVIEW_SOURCE_DRIFT')
        if fixture.approval != approval:
            reject('REVIEW_PAIR_NOT_APPROVED')
        approval.validate(ready, final, expect, submit_now)
        if inspect_task(ready, expect, submit.events, submit_now).structural_validation != 'SUCCESS':
            reject('REVIEW_NOT_READY')
        budget = fixture.budget
        if (type(budget) is not RunBudget or budget.max_provider_calls != 1 or budget.max_repair_attempts != 0 or
                budget.wall_clock_ms > 180000 or fixture.hard_cost_cap or not fixture.unknown_cost_permitted):
            reject('REVIEW_BUDGET_BLOCKED')
        ledger = UsageLedger((),0,int((submit_mono-start)*1000))
        if admit_next(budget,ledger,CallPhase.REVIEW) is not BudgetCode.WITHIN_BUDGET:
            reject('REVIEW_BUDGET_BLOCKED')
        packet.messages()  # Validate the complete fixed-instruction/data rendering before consume.
        route=fixture.route
        binding=ReviewCallBinding(approval.ready_task.task_id,approval.ready_task,approval.acceptance_task,
            approval.criterion_map,approval.ready_task.run_id,expect.epoch,snapshot.last_sequence,
            expect.source_sha,packet.digest,packet.reviewed_object_digest,before.digest,
            tuple(sorted({a.agent_id for a in expect.authored_by})),
            fixture.reviewer_role if type(fixture) is HostServices else REVIEWER_ROLES[0],fixture.reviewer_launch_nonce,
            route['id'],route['version'],route['digest'],'claude-cli',route['profile'],route['model'],
            digest({'data_classes':('SOURCE','DIFF','REVIEW_METADATA'),
                    'path_list_digest':digest([s['path'] for s in packet.to_dict()['sources']],
                                            'llmtool.review-path-list.v1')}),
            'FAKE_TEST_ONLY' if fake and type(fixture) is not HostServices else 'PRODUCTION_REVIEW',fixture.issuer_capability_id,
            fixture.redeem_run_id,approval.expires_at)
        admission=fixture.authority.admit(binding)
        if (type(admission) is not Admission or admission.admitted is not True or
                admission.code != 'REVIEW_ADMITTED' or
                admission.binding != binding or admission.test_only is not fake):
            reject('REVIEW_AUTHORITY_UNAVAILABLE' if isinstance(fixture.authority,UnavailableReviewAuthority)
                   else 'REVIEW_GRANT_NOT_AUTHORIZED')
        safe_id(admission.grant_ref)
        token=fixture.authority.consume(admission)
        if (type(token) is not ConsumeToken or token.committed is not True or token.test_only is not fake or
                token.binding_digest != binding.digest or token.reviewer_launch_nonce != binding.reviewer_launch_nonce or
                token.grant_ref != admission.grant_ref):
            reject('REVIEW_GRANT_NOT_AUTHORIZED')
        consumed = True
        calls = 1
        invocation = 'FAKE_ATTEMPTED' if fake else 'HOST_ATTEMPTED'
        call_start = fixture.clock.monotonic()
        worker = binding.reviewer_role == REVIEWER_ROLES[1]
        real_deadline = real_start + min(180, budget.wall_clock_ms / 1000)
        reply = (fixture.launcher.launch(packet.data,token,deadline=real_deadline) if worker else
                 fixture.launcher.launch(packet.data,token))
        if (type(reply) is not LaunchResult or reply.complete is not True or reply.test_only is not fake or
                reply.reviewer_launch_nonce != binding.reviewer_launch_nonce):
            reject('REVIEW_LAUNCH_NOT_ATTESTED')
        _hex(reply.provenance_digest)
        if worker:
            job_clock = load_json(reply.job_evidence_json.decode(),4096)
            effective = job_clock.get('deadline')
            if type(effective) not in (int,float) or not math.isfinite(effective):
                reject('REVIEW_LAUNCH_NOT_ATTESTED')
            real_deadline = min(real_deadline,effective)
            if time.monotonic() >= real_deadline: reject('REVIEW_TIMEOUT')
        call_complete=True
        end = fixture.clock.monotonic()
        if end < call_start or end-call_start > 120 or end-start > min(180,budget.wall_clock_ms/1000):
            reject('REVIEW_TIMEOUT')
        invocation = 'FAKE_RETURNED' if fake else 'HOST_RETURNED'
        after = fixture.observer.observe()
        after_now = clock_utc(fixture.clock)
        after.validate(expect, events, after_now, fixture.clock.monotonic())
        _clocks_agree(submit, after)
        if fixture.approval != approval:
            reject('REVIEW_PAIR_NOT_APPROVED')
        approval.validate(ready,final,expect,after_now)
        if after.state_digest != submit.state_digest:
            reject('REVIEW_SOURCE_DRIFT')
        if inspect_task(ready,expect,after.events,after_now).structural_validation != 'SUCCESS':
            reject('REVIEW_NOT_READY')
        if any(value in reply.content for a in (*expect.agents,*(expect.authored_by or ()))
               for value in (a.session_id,a.root,a.common_dir)):
            reject('REVIEW_PRIVACY_BLOCKED')
        verdict = parse_verdict(reply.content,packet)
        metadata = load_json(reply.metadata_json.decode(),4096)
        if (binding.reviewer_role == REVIEWER_ROLES[1] and
                type(metadata.get('input_tokens')) is int and type(metadata.get('output_tokens')) is int):
            metadata = dict(prompt_tokens=metadata['input_tokens'],
                            completion_tokens=metadata['output_tokens'],
                            total_tokens=metadata['input_tokens'] + metadata['output_tokens'])
        usage = usage_from_provider(CallPhase.REVIEW,metadata)
        job_evidence = load_json(reply.job_evidence_json.decode(),4096)
        if worker:
            # Host-only launcher evidence is separate from the model's verdict.
            # It cannot rename the original TaskContract/EXPECT reviewer.
            if (job_evidence.get('launch_nonce') != binding.reviewer_launch_nonce or
                    job_evidence.get('runtime_pin') != fixture.worker_runtime_digest or
                    job_evidence.get('identity_source') != 'CLI_REPORTED_RUNTIME_ID' or
                    any(job_evidence.get(k) is not True for k in
                        ('reaped','pipes_drained','pipes_closed','runtime_closed')) or
                    time.monotonic() >= real_deadline):
                reject('REVIEW_LAUNCH_NOT_ATTESTED')
        # Post-call usage is recorded telemetry only: it is reported as KNOWN or
        # UNKNOWN and never re-decides an otherwise valid, already-authorized
        # verdict. Pre-call admission above remains the only budget gate.
        approve = verdict['security'] == verdict['functional'] == 'APPROVE'
        result = envelope(('REVIEW_FAKE_APPROVED' if fake else 'REVIEW_APPROVED') if approve else 'REVIEW_CHANGES_REQUIRED' if
            'CHANGES_REQUIRED' in (verdict['security'],verdict['functional']) else 'REVIEW_BLOCKED',
            snapshot=snapshot,fake=fake,calls=calls,status='APPROVE' if approve else 'BLOCKED',
            collector_status='FAKE_VALIDATED' if fake else 'HOST_VALIDATED',invocation_status=invocation,verdict_validation='SUCCESS',
            security=verdict['security'],functional=verdict['functional'],packet_digest=packet.digest,
            post_observation_digest=after.digest,verdict=verdict,
            outstanding_final_criteria=[approval.security_criterion,approval.functional_criterion],
            handoff={'triggered':False,'source_sha':expect.source_sha,'findings':verdict['findings']},
            input_tokens=usage.input_tokens,output_tokens=usage.output_tokens,
            execution_disposition='STOP_NO_EXECUTION_AUTHORITY',
            reviewer_role=binding.reviewer_role,job_evidence=job_evidence,
            review_authority='WORKER_DATA_ONLY' if worker else 'REVIEW_DATA_ONLY')
    except (LLMToolError,ValueError,TypeError,AttributeError,KeyError,UnicodeError,RecursionError,TimeoutError) as exc:
        result = envelope(getattr(exc,'review_code','REVIEW_PROVIDER_UNAVAILABLE' if calls else 'REVIEW_INPUT_INVALID'),
                        snapshot=snapshot,fake=fake,calls=calls,invocation_status=invocation,
                        limit_metadata=getattr(exc,'review_metadata',{}))
    except Exception:
        # Consume already happened: unexpected transport errors must retain the
        # attempted call and UNKNOWN effect rather than reset CLI accounting.
        result = envelope('REVIEW_PROVIDER_UNAVAILABLE' if calls else 'REVIEW_INPUT_INVALID',
                        snapshot=snapshot,fake=fake,calls=calls,invocation_status=invocation)
    except KeyboardInterrupt:
        result = envelope('REVIEW_CANCELLED',snapshot=snapshot,fake=fake,calls=calls,
                        invocation_status=invocation)

    finally:
        if consumed and binding is not None:
            unwinding = sys.exc_info()[0] is not None
            try:
                fixture.authority.finalize(token,CallOutcome(binding.digest,
                    'EFFECT_DONE' if call_complete else 'EFFECT_UNKNOWN',invocation))
            except Exception:
                # Preserve original SystemExit/BaseException on unwind.
                if not unwinding:
                    final_result = envelope('REVIEW_FINALIZE_UNKNOWN',snapshot=snapshot,fake=fake,calls=calls,invocation_status=invocation)
            else:
                try:
                    if not unwinding and binding.reviewer_role == REVIEWER_ROLES[1] and call_complete:
                        fixture.launcher.validate_completion(token)
                        if time.monotonic() >= real_deadline: reject('REVIEW_TIMEOUT')
                except Exception as exc:
                    final_result = envelope(getattr(exc,'review_code','REVIEW_FINALIZE_UNKNOWN'),snapshot=snapshot,
                                            fake=fake,calls=calls,invocation_status=invocation)
    return final_result or result


def _run_fake_review(ready,final,expect,events,fixture):
    """Test convenience wrapper; no CLI/env/JSON path reaches fake services."""
    if getattr(fixture.authority,'test_only',False) is not True:
        return envelope('REVIEW_GRANT_NOT_AUTHORIZED',fake=True)
    return run_review(ready,final,expect,events,fixture)
