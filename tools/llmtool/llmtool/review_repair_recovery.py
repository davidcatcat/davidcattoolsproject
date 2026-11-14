"""#1928: evidence checks to recover one UNKNOWN review repair claim.

Pure checks on records the caller read. Nothing here writes, dispatches or
records a Task; any failed check refuses and the claim stays UNKNOWN.
"""
import re

from .errors import LLMToolError
from .review_repair import EC2_FAMILY_SUFFIX

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
