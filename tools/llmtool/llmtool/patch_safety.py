"""A11b.4 P1: structural offline preflight, never patch execution or rollback.

All observations and pins are caller supplied, not authenticated filesystem/Git
facts. A matching bounded recovery image is evidence only, not safe recovery.
No parser, issuer, I/O, clock, apply or undo operation is provided.
"""

import hashlib
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from . import action_contract as c
from .errors import LLMToolError

# Conservative P1 subset: no executable or shared-writable file proposals.
SUPPORTED_MODES = frozenset((0o600, 0o644))


class PatchStatus(str, Enum):
    VERIFIED_STRUCTURE = 'VERIFIED_STRUCTURE'
    REJECTED = 'REJECTED'
    NOT_VERIFIED = 'NOT_VERIFIED'


class PatchCode(str, Enum):
    STRUCTURE_MATCHES = 'STRUCTURE_MATCHES'
    INVALID_INPUT = 'INVALID_INPUT'
    UNSUPPORTED_KIND = 'UNSUPPORTED_KIND'
    ACTION_MISMATCH = 'ACTION_MISMATCH'
    TASK_MISMATCH = 'TASK_MISMATCH'
    REPOSITORY_MISMATCH = 'REPOSITORY_MISMATCH'
    STATE_MISMATCH = 'STATE_MISMATCH'
    SCOPE_MISMATCH = 'SCOPE_MISMATCH'
    PATH_COLLISION = 'PATH_COLLISION'
    UNSUPPORTED_MODE = 'UNSUPPORTED_MODE'
    MISSING_PREIMAGE = 'MISSING_PREIMAGE'
    PREIMAGE_MISMATCH = 'PREIMAGE_MISMATCH'
    MISSING_POSTIMAGE = 'MISSING_POSTIMAGE'
    POSTIMAGE_MISMATCH = 'POSTIMAGE_MISMATCH'
    MISSING_RECOVERY_IMAGE = 'MISSING_RECOVERY_IMAGE'
    RECOVERY_IMAGE_MISMATCH = 'RECOVERY_IMAGE_MISMATCH'
    UNEXPECTED_CONTENT = 'UNEXPECTED_CONTENT'


class UnverifiedFact(str, Enum):
    LIVE_FILESYSTEM = 'LIVE_FILESYSTEM'
    CLEAN_GIT_STATE = 'CLEAN_GIT_STATE'
    PATH_RACE_RESISTANCE = 'PATH_RACE_RESISTANCE'
    HOST_AUTHORITY = 'HOST_AUTHORITY'
    SAFE_ROLLBACK = 'SAFE_ROLLBACK'
    INDEX_BINDING = 'INDEX_BINDING'


@dataclass(frozen=True)
class Postimage(c.ContractValue):
    """Expected content/mode only: no invented inode, ownership or timestamp."""
    file_type: c.FileType
    content_sha256: Optional[str]
    size: Optional[int]
    mode: Optional[int]

    def __post_init__(self):
        c._typed(self.file_type, c.FileType)
        if self.file_type is c.FileType.ABSENT:
            if any(v is not None for v in (self.content_sha256, self.size, self.mode)):
                raise LLMToolError('patch safety invalid; details omitted')
        else:
            c._hex(self.content_sha256)
            c._integer(self.size, c.MAX_CONTENT_BYTES)
            if type(self.mode) is not int or self.mode not in SUPPORTED_MODES:
                raise LLMToolError('patch safety invalid; details omitted')

    @property
    def digest(self):
        return c._digest('llmtool.patch-postimage', self)


@dataclass(frozen=True)
class RecoveryEvidence(c.ContractValue):
    """Commitment to supplied before state and bytes, not a stored backup."""
    before_state_digest: str
    file_state_digest: str
    image_sha256: Optional[str]

    def __post_init__(self):
        c._hex(self.before_state_digest)
        c._hex(self.file_state_digest)
        if self.image_sha256 is not None:
            c._hex(self.image_sha256)


@dataclass(frozen=True)
class PatchResult(c.ContractValue):
    status: PatchStatus
    code: PatchCode
    action_digest: Optional[str] = None
    before_state_digest: Optional[str] = None
    affected_paths: tuple = ()
    postimage: Optional[Postimage] = None
    recovery: Optional[RecoveryEvidence] = None
    unverified: tuple = tuple(UnverifiedFact)

    def __post_init__(self):
        c._typed(self.status, PatchStatus)
        c._typed(self.code, PatchCode)
        for digest in (self.action_digest, self.before_state_digest):
            if digest is not None:
                c._hex(digest)
        facts = c._collection(self.unverified, UnverifiedFact, len(UnverifiedFact))
        if facts not in (tuple(UnverifiedFact), tuple(UnverifiedFact)[:-1]):
            raise LLMToolError('patch safety invalid; details omitted')
        object.__setattr__(self, 'unverified', facts)
        if self.status is PatchStatus.VERIFIED_STRUCTURE:
            if self.code is not PatchCode.STRUCTURE_MATCHES:
                raise LLMToolError('patch safety invalid; details omitted')
            c._hex(self.action_digest)
            c._hex(self.before_state_digest)
            paths = c._scope(self.affected_paths)
            if len(paths) != 1:
                raise LLMToolError('patch safety invalid; details omitted')
            c._typed(self.postimage, Postimage)
            c._typed(self.recovery, RecoveryEvidence)
            if self.recovery.before_state_digest != self.before_state_digest:
                raise LLMToolError('patch safety invalid; details omitted')
            object.__setattr__(self, 'affected_paths', paths)
        elif (self.code is PatchCode.STRUCTURE_MATCHES or self.affected_paths != () or
              self.postimage is not None or self.recovery is not None):
            raise LLMToolError('patch safety invalid; details omitted')

    @property
    def ready_to_execute(self):
        return False

    @property
    def rollback_authorized(self):
        return False

    @property
    def postimage_digest(self):
        return None if self.postimage is None else self.postimage.digest

    def to_dict(self):
        return {**super().to_dict(), 'postimage_digest': self.postimage_digest,
                'ready_to_execute': False, 'rollback_authorized': False}


def _collision(paths):
    # Conservatively refuse Unicode/case aliases and file/ancestor collisions.
    # This does not infer actual filesystem case/normalization semantics.
    folded = [unicodedata.normalize('NFC', path).casefold() for path in paths]
    return (any(path != unicodedata.normalize('NFC', path) for path in paths) or
            len(set(folded)) != len(folded) or
            any(a != b and b.startswith(a + '/') for a in folded for b in folded))


def _matches_bytes(value, digest, size):
    return len(value) == size and hashlib.sha256(value).hexdigest() == digest


def preflight(action, *, expected_action_digest, expected_task, authorized_scope,
              expected_state, current_state, preimage=None, new_content=None,
              recovery_image=None):
    """Check a single typed file action against separately pinned supplied inputs.

    expected_state.files must equal the action's exact before-file expectation;
    its optional index digest is a separate caller pin (file Actions omit index).
    current_state must match that full expected state, including any index pin.
    WRITE/DELETE need both preimage and recovery_image bytes; both are checked
    against the bound before digest/size. CREATE records absence, not an image.
    Only bytes <= 1 MiB are accepted, including empty bytes. Failure results
    contain fixed codes/digests, never raw paths or content. Pins are not authority.
    """
    try:
        c._typed(action, c.Action)
        c._typed(expected_task, c.TaskBinding)
        c._typed(expected_state, c.RepositoryState)
        c._typed(current_state, c.RepositoryState)
        c._hex(expected_action_digest)
        scope = c._scope(authorized_scope)
        # Reuse the exact typed validators, including nested state/mode/path checks.
        # Reconstructing guards against internally altered frozen objects; it is
        # not protection against hostile Python running in this process.
        c.parse_action(action.to_json())
        c.TaskBinding(**expected_task.to_dict())
        c.parse_repository_state(expected_state.to_json())
        c.parse_repository_state(current_state.to_json())
        for value in (preimage, new_content, recovery_image):
            if value is not None and (type(value) is not bytes or
                                      len(value) > c.MAX_CONTENT_BYTES):
                return PatchResult(PatchStatus.REJECTED, PatchCode.INVALID_INPUT)
    except LLMToolError:
        return PatchResult(PatchStatus.REJECTED, PatchCode.INVALID_INPUT)

    action_digest, before_digest = action.digest, expected_state.digest
    facts = tuple(UnverifiedFact)
    if expected_state.index_sha256 is not None:
        facts = facts[:-1]

    def stop(code, missing=False):
        return PatchResult(PatchStatus.NOT_VERIFIED if missing else PatchStatus.REJECTED,
                           code, action_digest, before_digest, unverified=facts)

    if action.kind is c.ActionKind.GIT_OPERATION:
        return stop(PatchCode.UNSUPPORTED_KIND)
    if action_digest != expected_action_digest:
        return stop(PatchCode.ACTION_MISMATCH)
    if action.task != expected_task:
        return stop(PatchCode.TASK_MISMATCH)
    if (action.repository != expected_state.repository or
            current_state.repository != expected_state.repository):
        return stop(PatchCode.REPOSITORY_MISMATCH)
    if (expected_state.files != action.expected_state.files or current_state != expected_state):
        return stop(PatchCode.STATE_MISMATCH)
    if any(path not in scope for path in action.scope):
        return stop(PatchCode.SCOPE_MISMATCH)
    if (_collision(scope) or _collision(action.scope) or
            _collision(tuple(f.path for f in current_state.files))):
        return stop(PatchCode.PATH_COLLISION)

    target = action.payload.target
    before = target.state
    if before.exists and before.metadata.mode not in SUPPORTED_MODES:
        return stop(PatchCode.UNSUPPORTED_MODE)
    if action.kind is c.ActionKind.FILE_CREATE:
        if preimage is not None or recovery_image is not None:
            return stop(PatchCode.UNEXPECTED_CONTENT)
    else:
        if preimage is None:
            return stop(PatchCode.MISSING_PREIMAGE, missing=True)
        if not _matches_bytes(preimage, before.content_sha256, before.metadata.size):
            return stop(PatchCode.PREIMAGE_MISMATCH)
        if recovery_image is None:
            return stop(PatchCode.MISSING_RECOVERY_IMAGE, missing=True)
        if not _matches_bytes(recovery_image, before.content_sha256, before.metadata.size):
            return stop(PatchCode.RECOVERY_IMAGE_MISMATCH)

    if action.kind is c.ActionKind.FILE_DELETE:
        if new_content is not None:
            return stop(PatchCode.UNEXPECTED_CONTENT)
        post = Postimage(c.FileType.ABSENT, None, None, None)
    else:
        if action.payload.new_mode not in SUPPORTED_MODES:
            return stop(PatchCode.UNSUPPORTED_MODE)
        if new_content is None:
            return stop(PatchCode.MISSING_POSTIMAGE, missing=True)
        if not _matches_bytes(new_content, action.payload.new_content_sha256,
                              action.payload.new_size):
            return stop(PatchCode.POSTIMAGE_MISMATCH)
        post = Postimage(c.FileType.REGULAR, action.payload.new_content_sha256,
                         action.payload.new_size, action.payload.new_mode)
    recovery = RecoveryEvidence(before_digest, c._digest('llmtool.patch-before-file', before),
                                before.content_sha256)
    return PatchResult(PatchStatus.VERIFIED_STRUCTURE, PatchCode.STRUCTURE_MATCHES,
                       action_digest, before_digest, (target.path,), post, recovery, facts)
