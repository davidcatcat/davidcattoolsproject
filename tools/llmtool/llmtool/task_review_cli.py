"""Public S1b A review command: local reads only, real invocation BLOCKED by B."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .errors import LLMToolError
from .task_lifecycle import parse_expectations, parse_events, inspect_task, State
from .task_lifecycle_cli import read_task_file
from .task_verification import parse_task_contract
from .task_review import (load_json, reject, envelope, VERDICT_LIMIT,
                          UnavailableReviewAuthority, run_review)
from .task_review import review_authority_factory  # B's only future factory binding.
from .task_review_collector import collect_local_facts


def add_review_parser(sub):
    review = sub.add_parser('review',help='Validate review inputs; production host authority still required')
    for name in ('contract','acceptance-contract','expect','events'):
        review.add_argument('--'+name,required=True)
    review.add_argument('--json',action='store_true',required=True)


def run_task_review(args):
    snapshot = None
    try:
        files = []
        for name in ('expect','contract','acceptance_contract','events'):
            raw,inode = read_task_file(getattr(args,name))
            files.append((raw,inode))
        if len({inode for raw,inode in files}) != 4:
            reject('REVIEW_INPUT_ALIAS')
        expect_raw,ready_raw,final_raw,event_raw = [raw for raw,_ in files]
        load_json(expect_raw,1024*1024)
        load_json(ready_raw,VERDICT_LIMIT); load_json(final_raw,VERDICT_LIMIT)
        for line in event_raw.splitlines():
            load_json(line,16*1024)
        expect = parse_expectations(expect_raw)
        ready = parse_task_contract(ready_raw)
        final = parse_task_contract(final_raw)
        events = parse_events(event_raw)
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        snapshot = inspect_task(ready,expect,events,now)
        if (snapshot.structural_validation != 'SUCCESS' or
                snapshot.reported_state is not State.WAITING_REVIEW or not snapshot.handoff_preview):
            result = envelope('REVIEW_NOT_READY',snapshot=snapshot)
        else:
            facts = collect_local_facts(Path.cwd())
            if not facts.clean_tracked or facts.source_sha != expect.source_sha:
                reject('REVIEW_SOURCE_DRIFT')
            developer = snapshot.handoff_preview.from_agent
            if (facts.root != developer.root or facts.branch != developer.branch or
                    facts.common_dir != developer.common_dir):
                reject('REVIEW_IDENTITY_NOT_ATTESTED')
            services = review_authority_factory()
            if isinstance(services,UnavailableReviewAuthority):
                result = envelope(services.admit(None).code,snapshot=snapshot,
                    collector_status='LOCAL_FACTS_ONLY',local_facts_digest=facts.digest,
                    missing_gates=['HOST_SESSION_ATTESTATION','TRUSTED_PAIR_APPROVAL',
                                   'PRODUCTION_GRANT_REPLAY','APPROVED_PROVIDER_ROUTE'])
            else:
                # Only accepted B host code may supply these services, never CLI input.
                result = run_review(ready,final,expect,events,services)
        exit_code = 0 if result['status'] == 'APPROVE' else 1
    except (LLMToolError,OSError,ValueError,TypeError,UnicodeError,RecursionError) as exc:
        code = getattr(exc,'review_code',getattr(exc,'lifecycle_code','REVIEW_INPUT_INVALID'))
        result = envelope(code,snapshot=snapshot)
        exit_code = 2 if snapshot is None else 1
    except Exception:
        result = envelope('REVIEW_INTERNAL_ERROR',snapshot=snapshot)
        exit_code = 2
    try:
        print(json.dumps(result,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False))
        sys.stdout.flush()
    except BrokenPipeError:
        # Preserve non-PASS, without writing /dev/null or an implicit artifact.
        sys.stdout = None
    return exit_code
