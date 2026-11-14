"""#1928: evidence checks to recover one UNKNOWN review repair claim.

Pure checks on records the caller read. Nothing here writes, dispatches or
records a Task; any failed check refuses and the claim stays UNKNOWN.
"""
import re

from .errors import LLMToolError
from .review_repair import EC2_FAMILY_SUFFIX

_HEX32 = re.compile(r'[0-9a-f]{32}')
_HEX40 = re.compile(r'[0-9a-f]{40}')


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
    claim = found[0] if len(found) == 1 else {}
    family, head = claim.get('family'), claim.get('head_sha')
    if (claim.get('state') != 'UNKNOWN' or claim.get('executor_task') != executor_task or
            claim.get('pr') != pr_number or type(head) is not str or
            not _HEX40.fullmatch(head) or
            any(type(claim.get(key)) is not int for key in ('attempt', 'version', 'comment_id')) or
            type(family) is not str or family.endswith(EC2_FAMILY_SUFFIX)):
        refuse('REPAIR_RECOVERY_CLAIM_INVALID')
    return dict(claim)
