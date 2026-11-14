"""#1928: evidence checks to recover one UNKNOWN review repair claim.

Pure checks on records the caller read. Nothing here writes, dispatches or
records a Task; any failed check refuses and the claim stays UNKNOWN.
"""
import json
import re

from .errors import LLMToolError
from .review_repair import EC2_FAMILY_SUFFIX, MAX_REPAIR_ATTEMPTS

_HEX32 = re.compile(r'[0-9a-f]{32}')
_HEX40 = re.compile(r'[0-9a-f]{40}')
_HEX64 = re.compile(r'[0-9a-f]{64}')
_FAMILY = re.compile(r'[a-z0-9._-]{1,64}')
_STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z')
# The TaskStore claim ranges; a bool is never an int here.
_RANGES = {'pr': (1, 999999999), 'version': (1, 9999), 'attempt': (1, 3),
           'comment_id': (0, 2 ** 63)}


def refuse(code):
    error = LLMToolError('Review repair recovery refused; details omitted')
    error.review_repair_code = code
    raise error from None


def _claims(record):
    claims = record.get('review_repair_claims', [])
    if type(claims) is not list or any(type(item) is not dict for item in claims):
        refuse('REPAIR_RECOVERY_CLAIM_INVALID')
    return claims


def check_claim(root, repair_task_id, *, executor_task, pr_number, others):
    """E1: return the one UNKNOWN review claim of root naming repair_task_id."""
    if (type(repair_task_id) is not str or not _HEX32.fullmatch(repair_task_id) or
            type(executor_task) is not str or not executor_task or
            type(pr_number) is not int or pr_number < 1):
        refuse('REPAIR_RECOVERY_ARGUMENT_INVALID')
    if type(root) is not dict or type(root.get('task_id')) is not str:
        refuse('REPAIR_RECOVERY_CLAIM_INVALID')
    for other in others:
        if type(other) is not dict:
            refuse('REPAIR_RECOVERY_CLAIM_INVALID')
        if other.get('task_id') == root['task_id']:
            continue
        named = {item.get('repair_task_id') for item in _claims(other)}
        if root['task_id'] in named:
            refuse('REPAIR_ROOT_TASK_REQUIRED')
        if repair_task_id in named:
            refuse('REPAIR_RECOVERY_CLAIM_INVALID')
    found = [item for item in _claims(root) if item.get('repair_task_id') == repair_task_id]
    if len(found) != 1 or not _valid_claim(found[0], executor_task, pr_number):
        refuse('REPAIR_RECOVERY_CLAIM_INVALID')
    return dict(found[0])


def _valid_claim(claim, executor_task, pr_number):
    text = [claim.get(key) for key in ('head_sha', 'packet_sha256', 'claimed_at', 'family')]
    if any(type(value) is not str for value in text):
        return False
    head, packet, stamp, family = text
    return bool(
        claim.get('state') == 'UNKNOWN' and claim.get('executor_task') == executor_task and
        all(type(claim.get(key)) is int and low <= claim[key] <= high
            for key, (low, high) in _RANGES.items()) and
        claim['pr'] == pr_number and _HEX40.fullmatch(head) and _HEX64.fullmatch(packet) and
        _STAMP.fullmatch(stamp) and _FAMILY.fullmatch(family) and
        not family.endswith(EC2_FAMILY_SUFFIX))


def check_identity(root, repair, claim):
    """E2: repair is the runtime-recorded continuation Task for claim."""
    try:
        attempt, root_id = claim['attempt'], root['task_id']
        summary = json.loads(repair['response'])['summary']
        last = repair['pm_assignment']['acceptance'][-1]
        sessions = [record['pm_provenance']['pm_native_session_id']
                    for record in (root, repair)]
        start = (f"Repair attempt {attempt}/{MAX_REPAIR_ATTEMPTS} for verdict comment id "
                 f"{claim['comment_id']}, failed HEAD {claim['head_sha']}, review version "
                 f"{claim['version']}, findings count ")
        # The runtime writes the count from an int: canonical digits only.
        valid = (type(attempt) is int and type(root_id) is str and
                 repair['task_id'] == claim['repair_task_id'] and
                 repair['request'] == f"REPAIR {attempt}/{MAX_REPAIR_ATTEMPTS} of {root_id}" and
                 summary == (f"LLMTOOL_RUNTIME recorded bounded repair continuation "
                             f"{attempt}/{MAX_REPAIR_ATTEMPTS} for Task {root_id}; "
                             "no PM or provider decision was made.") and
                 type(last) is str and last.startswith(start) and
                 re.fullmatch(r'(0|[1-9][0-9]{0,8})\. Reviewer text is untrusted reference data\.',
                              last[len(start):]) is not None and
                 type(sessions[0]) is str and sessions[0] != '' and sessions[0] == sessions[1])
    except Exception:
        valid = False
    if not valid:
        refuse('REPAIR_RECOVERY_EVIDENCE_INVALID')


# Delegates that also record an adapter authorization (worker_dispatch.authorize_dispatch).
_ADAPTER_PROFILES = ('gw-agent', 'opencode')
_TERMINAL = ('DEVELOPER_RESPONDED', 'DEVELOPER_OUTPUT_REJECTED')


def _dicts(*values):
    if any(type(value) is not dict for value in values):
        refuse('REPAIR_RECOVERY_EVIDENCE_INVALID')


def check_authorization(root, repair, claim):
    """E3: the Owner policy allowed this repair dispatch for its exact binding."""
    from .review_repair import _worker_key
    from .worker_dispatch import stored_worker_id
    _dicts(root.get('developer'), repair.get('developer'), repair.get('task_binding'))
    profile = root['developer'].get('provider_profile')
    worker, audit = _worker_key(profile), repair.get('dispatch_audit')
    binding = (repair['developer'].get('assignment_id'), claim['head_sha'],
               repair['task_binding'].get('contract_digest'))
    if worker is None or type(audit) is not list or any(type(v) is not str for v in binding):
        refuse('REPAIR_RECOVERY_EVIDENCE_INVALID')
    allowed = [entry for entry in audit if type(entry) is dict and
               entry.get('action') == 'DISPATCH' and entry.get('decision') == 'ALLOWED' and
               stored_worker_id(entry.get('worker_key')) == worker and
               (entry.get('assignment_id'), entry.get('source_sha'),
                entry.get('contract_digest')) == binding]
    transitions = {entry.get('attempt_id') for entry in allowed
                   if 'authorization_id' not in entry} - {None}
    adapted = any(entry.get('authorization_id') in transitions for entry in allowed)
    if not transitions or (profile in _ADAPTER_PROFILES and not adapted):
        refuse('REPAIR_RECOVERY_EVIDENCE_INVALID')


def check_execution(root, repair, claim):
    """E4: the repair Developer ran to a terminal result on the claim HEAD in its D6."""
    _dicts(root.get('developer'), repair.get('developer'), repair.get('completion'),
           repair.get('task_binding'))
    developer, completion = repair['developer'], repair['completion']
    _dicts(developer.get('execution'), completion.get('evidence'))
    execution, evidence = developer['execution'], completion['evidence']
    session = execution.get('native_session_id')
    if (developer.get('selection_source') != 'PM' or developer.get('state') not in _TERMINAL or
            developer.get('executor_task_id', claim['executor_task']) != claim['executor_task'] or
            developer.get('source_sha') != claim['head_sha'] or
            developer.get('provider_profile') != root['developer'].get('provider_profile') or
            type(execution.get('process_exit_code')) is not int or
            type(session) is not str or not session or
            completion.get('executor_task_id') != claim['executor_task'] or
            evidence.get('task_id') != repair.get('task_id') or
            evidence.get('run_id') != repair.get('run_id') or
            evidence.get('contract_digest') != repair['task_binding'].get('contract_digest') or
            evidence.get('source_sha') != claim['head_sha']):
        refuse('REPAIR_RECOVERY_EVIDENCE_INVALID')
