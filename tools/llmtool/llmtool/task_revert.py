"""Admission of a confirmed main CI incident revert (#1706 R5a, #1019 Row 40).

Pure and read-only: ``admit`` reads only its two arguments and returns a new
dict. It never changes an input and never reads or writes files, Git, the
network or the TaskStore. The result is advice for the PM; nothing here starts
a revert.
"""

import copy

from . import main_incident as mi
from . import main_incident_store as store

MAX_SCOPE_PRS = 5
ADMITTED = 'REVERT_ADMITTED'
REVERT_INCIDENT_INVALID = 'REVERT_INCIDENT_INVALID'
REVERT_INCIDENT_NOT_OPEN = 'REVERT_INCIDENT_NOT_OPEN'
REVERT_NOT_CONFIRMED = 'REVERT_NOT_CONFIRMED'
REVERT_CONFIRMATION_STALE = 'REVERT_CONFIRMATION_STALE'
REVERT_CLASSIFICATION_NOT_CODE = 'REVERT_CLASSIFICATION_NOT_CODE'
REVERT_CLASSIFICATION_UNKNOWN = 'REVERT_CLASSIFICATION_UNKNOWN'
REVERT_SCOPE_TOO_LARGE = 'REVERT_SCOPE_TOO_LARGE'
# The dependency refusal codes are owned by main_incident.
REVERT_DEPENDENCY_UNKNOWN = mi.DEPENDENCY_UNKNOWN
REVERT_DEPENDENT_LATER_MERGE = mi.REVERT_DEPENDENT


def _refuse(code):
    return {'code': code, 'refused': True}


def _valid_record(record):
    """The record shape the later checks rely on; every other doubt is invalid."""
    if not isinstance(record, dict):
        return False
    if not (isinstance(record.get('incident_id'), str) and record['incident_id']):
        return False
    if not isinstance(record.get('outcome'), str):
        return False
    observations = record.get('observations')
    if (not isinstance(observations, list) or not observations or
            not all(isinstance(item, dict) for item in observations)):
        return False
    return isinstance(record.get('confirmations'), list)


def _valid_scope(scope):
    """A non-empty list of exact ints >= 1, already sorted and unique."""
    return (isinstance(scope, list) and bool(scope) and
            all(type(pr) is int and pr >= 1 for pr in scope) and
            scope == sorted(set(scope)))


def admit(record, dependency):
    """Decide whether a confirmed incident may start a revert; return a new dict.

    The checks run in order and the first failure decides:

    1. a malformed record is REVERT_INCIDENT_INVALID;
    2. any outcome but OPEN is REVERT_INCIDENT_NOT_OPEN;
    3. no confirmation is REVERT_NOT_CONFIRMED;
    4. a confirmation that is not a dict, has a non-int (including bool)
       observation index, does not name the last observation, or is not bound
       to that observation's digest is REVERT_CONFIRMATION_STALE; a last
       observation that cannot be digested is REVERT_INCIDENT_INVALID;
    5. a last observation that is ENV or FLAKY is
       REVERT_CLASSIFICATION_NOT_CODE and any other class but CODE is
       REVERT_CLASSIFICATION_UNKNOWN;
    6. a malformed scope is REVERT_INCIDENT_INVALID and more than
       MAX_SCOPE_PRS PRs is REVERT_SCOPE_TOO_LARGE;
    7. a missing or unknown dependency result is REVERT_DEPENDENCY_UNKNOWN,
       a later dependent merge is REVERT_DEPENDENT_LATER_MERGE and no
       dependent merge admits the revert.
    """
    if not _valid_record(record):
        return _refuse(REVERT_INCIDENT_INVALID)
    if record['outcome'] != store.OPEN:
        return _refuse(REVERT_INCIDENT_NOT_OPEN)
    confirmations, observations = record['confirmations'], record['observations']
    if not confirmations:
        return _refuse(REVERT_NOT_CONFIRMED)
    confirmation, last = confirmations[-1], observations[-1]
    index = confirmation.get('observation_index') if isinstance(confirmation, dict) else None
    try:
        digest = store.observation_digest(last)
    except (TypeError, ValueError, RecursionError):
        return _refuse(REVERT_INCIDENT_INVALID)
    if (not isinstance(confirmation, dict) or type(index) is not int or
            index != len(observations) - 1 or
            confirmation.get('evidence_sha256') != digest):
        return _refuse(REVERT_CONFIRMATION_STALE)
    cls = last.get('class')
    if cls != mi.CODE:
        if cls in (mi.ENV, mi.FLAKY):
            return _refuse(REVERT_CLASSIFICATION_NOT_CODE)
        return _refuse(REVERT_CLASSIFICATION_UNKNOWN)
    scope = confirmation.get('scope')
    if not _valid_scope(scope):
        return _refuse(REVERT_INCIDENT_INVALID)
    if len(scope) > MAX_SCOPE_PRS:
        return _refuse(REVERT_SCOPE_TOO_LARGE)
    if not isinstance(dependency, dict):
        return _refuse(REVERT_DEPENDENCY_UNKNOWN)
    code = dependency.get('code')
    if code == mi.REVERT_DEPENDENT:
        dependents = dependency.get('dependents')
        return {'code': REVERT_DEPENDENT_LATER_MERGE, 'refused': True,
                'dependents': copy.deepcopy(dependents if isinstance(dependents, list) else [])}
    if code != mi.NO_DEPENDENT:
        return _refuse(REVERT_DEPENDENCY_UNKNOWN)
    return {'code': ADMITTED, 'refused': False, 'incident_id': record['incident_id'],
            'scope': list(scope), 'observation_index': index,
            'evidence_sha256': confirmation['evidence_sha256']}
