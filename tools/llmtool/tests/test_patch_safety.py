"""P1 public API: supplied state consistency, no effects or recovery authority."""

import ast
import hashlib
import json
import socket
import subprocess
import time
import unicodedata
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import unittest
from unittest.mock import patch

from llmtool import action_contract as c, patch_safety as p
from llmtool.errors import LLMToolError
from test_action_contract import make_action

OLD = b'before'
NEW = b'after'
INDEX = 'd' * 64


def sha(value):
    return hashlib.sha256(value).hexdigest()


def proposal(kind=c.ActionKind.FILE_WRITE, old=OLD, new=NEW, mode=0o600):
    action = make_action(kind)
    target = action.payload.target if kind is not c.ActionKind.GIT_OPERATION else action.payload.files[0]
    if target.state.exists:
        target = replace(target, state=replace(target.state, content_sha256=sha(old),
                         metadata=replace(target.state.metadata, size=len(old))))
    if kind in (c.ActionKind.FILE_CREATE, c.ActionKind.FILE_WRITE):
        payload = replace(action.payload, target=target, new_content_sha256=sha(new),
                          new_size=len(new), new_mode=mode)
    elif kind is c.ActionKind.FILE_DELETE:
        payload = replace(action.payload, target=target)
    else:
        payload = replace(action.payload, files=(target,))
    return replace(action, payload=payload)


def inspect(action=None, **changes):
    action = action or proposal()
    state = replace(action.expected_state, index_sha256=INDEX)
    create = action.kind is c.ActionKind.FILE_CREATE
    values = dict(expected_action_digest=action.digest, expected_task=action.task,
                  authorized_scope=action.scope, expected_state=state, current_state=state,
                  preimage=None if create else OLD, recovery_image=None if create else OLD,
                  new_content=None if action.kind is c.ActionKind.FILE_DELETE else NEW)
    candidate = changes.pop('candidate', action)
    values.update(changes)
    return p.preflight(candidate, **values)


class PatchSafetyTests(unittest.TestCase):
    def assertStopped(self, result, code, missing=False):
        self.assertIs(result.code, code)
        self.assertIs(result.status, p.PatchStatus.NOT_VERIFIED if missing else p.PatchStatus.REJECTED)
        self.assertEqual(result.affected_paths, ())
        self.assertIsNone(result.postimage)
        self.assertIsNone(result.recovery)
        self.assertNoAuthority(result)

    def assertNoAuthority(self, result):
        self.assertIs(result.ready_to_execute, False)
        self.assertIs(result.rollback_authorized, False)
        data = json.loads(result.to_json())
        self.assertIs(data['ready_to_execute'], False)
        self.assertIs(data['rollback_authorized'], False)
        self.assertIn(p.UnverifiedFact.SAFE_ROLLBACK, result.unverified)
        self.assertIn(p.UnverifiedFact.LIVE_FILESYSTEM, result.unverified)

    def test_create_write_delete_matching_structure(self):
        for kind in (c.ActionKind.FILE_CREATE, c.ActionKind.FILE_WRITE, c.ActionKind.FILE_DELETE):
            with self.subTest(kind=kind):
                action = proposal(kind)
                result = inspect(action)
                self.assertIs(result.status, p.PatchStatus.VERIFIED_STRUCTURE)
                self.assertIs(result.code, p.PatchCode.STRUCTURE_MATCHES)
                self.assertEqual(result.action_digest, action.digest)
                self.assertEqual(result.before_state_digest,
                                 replace(action.expected_state, index_sha256=INDEX).digest)
                self.assertEqual(result.affected_paths, ('file.txt',))
                self.assertEqual(result.recovery.image_sha256,
                                 None if kind is c.ActionKind.FILE_CREATE else sha(OLD))
                self.assertNoAuthority(result)

    def test_postimage_is_content_metadata_commitment_not_live_file_state(self):
        action = proposal()
        result = inspect(action)
        self.assertEqual(result.postimage, p.Postimage(c.FileType.REGULAR, sha(NEW), len(NEW), 0o600))
        self.assertEqual(result.postimage_digest, result.postimage.digest)
        self.assertEqual(result.to_dict()['postimage_digest'], result.postimage.digest)
        self.assertNotIn('inode', result.postimage.to_dict())
        absent = inspect(proposal(c.ActionKind.FILE_DELETE)).postimage
        self.assertEqual(absent, p.Postimage(c.FileType.ABSENT, None, None, None))
        self.assertNotEqual(absent.digest, result.postimage_digest)

    def test_action_digest_and_task_pins_are_independent(self):
        action = proposal()
        self.assertStopped(inspect(action, expected_action_digest='f' * 64), p.PatchCode.ACTION_MISMATCH)
        for change in (dict(task_id='task-2'), dict(run_id='a' * 32),
                       dict(contract_version=2), dict(contract_digest='f' * 64)):
            with self.subTest(change=change):
                self.assertStopped(inspect(action, expected_task=replace(action.task, **change)),
                                   p.PatchCode.TASK_MISMATCH)
        candidate = replace(action, action_id='action-2')
        self.assertStopped(inspect(action, candidate=candidate), p.PatchCode.ACTION_MISMATCH)

    def test_repository_root_device_inode_and_head_must_match(self):
        action = proposal()
        state = replace(action.expected_state, index_sha256=INDEX)
        for change in (dict(repository_id='e' * 64), dict(canonical_root='/other/repository'),
                       dict(root_device=11), dict(root_inode=30), dict(expected_head='a' * 40)):
            with self.subTest(change=change):
                changed = replace(state, repository=replace(state.repository, **change))
                self.assertStopped(inspect(action, current_state=changed), p.PatchCode.REPOSITORY_MISMATCH)
                self.assertStopped(inspect(action, expected_state=changed, current_state=changed),
                                   p.PatchCode.REPOSITORY_MISMATCH)

    def test_stale_or_missing_current_index_rejects(self):
        action = proposal()
        expected = replace(action.expected_state, index_sha256=INDEX)
        for index in (None, 'e' * 64):
            with self.subTest(index=index):
                self.assertStopped(inspect(action, current_state=replace(expected, index_sha256=index)),
                                   p.PatchCode.STATE_MISMATCH)

    def test_unbound_index_is_explicitly_not_verified(self):
        action = proposal()
        result = inspect(action, expected_state=action.expected_state, current_state=action.expected_state)
        self.assertIs(result.status, p.PatchStatus.VERIFIED_STRUCTURE)
        self.assertIn(p.UnverifiedFact.INDEX_BINDING, result.unverified)
        self.assertNotIn(p.UnverifiedFact.INDEX_BINDING, inspect(action).unverified)
        self.assertIn(p.UnverifiedFact.CLEAN_GIT_STATE, inspect(action).unverified)
        self.assertNoAuthority(result)

    def test_every_before_metadata_field_is_compared(self):
        action = proposal()
        state = replace(action.expected_state, index_sha256=INDEX)
        target = state.files[0]
        changes = dict(device=11, inode=22, mode=0o600, uid=1001, gid=1001, size=7, mtime_ns=101)
        for field, value in changes.items():
            with self.subTest(field=field):
                other = replace(target, state=replace(target.state,
                                metadata=replace(target.state.metadata, **{field: value})))
                self.assertStopped(inspect(action, current_state=replace(state, files=(other,))),
                                   p.PatchCode.STATE_MISMATCH)
        other = replace(target, state=replace(target.state, content_sha256='e' * 64))
        self.assertStopped(inspect(action, current_state=replace(state, files=(other,))),
                           p.PatchCode.STATE_MISMATCH)

    def test_expected_before_cannot_disagree_with_action_even_if_current_matches(self):
        action = proposal()
        target = replace(action.payload.target,
                         state=replace(action.payload.target.state, content_sha256='f' * 64))
        wrong = replace(action.expected_state, files=(target,), index_sha256=INDEX)
        self.assertStopped(inspect(action, expected_state=wrong, current_state=wrong),
                           p.PatchCode.STATE_MISMATCH)

    def test_create_collision_and_write_missing_target_reject(self):
        create = proposal(c.ActionKind.FILE_CREATE)
        present = proposal().payload.target
        state = replace(create.expected_state, files=(present,), index_sha256=INDEX)
        self.assertStopped(inspect(create, current_state=state), p.PatchCode.STATE_MISMATCH)
        write = proposal()
        absent = create.payload.target
        state = replace(write.expected_state, files=(absent,), index_sha256=INDEX)
        self.assertStopped(inspect(write, current_state=state), p.PatchCode.STATE_MISMATCH)

    def test_extra_or_undeclared_current_paths_reject(self):
        action = proposal()
        extra = replace(action.payload.target, path='other.txt')
        state = replace(action.expected_state, files=(action.payload.target, extra), index_sha256=INDEX)
        self.assertStopped(inspect(action, current_state=state), p.PatchCode.STATE_MISMATCH)
        self.assertStopped(inspect(action, expected_state=state, current_state=state),
                           p.PatchCode.STATE_MISMATCH)

    def test_scope_bypass_rejects(self):
        self.assertStopped(inspect(authorized_scope=('other.txt',)), p.PatchCode.SCOPE_MISMATCH)
        self.assertStopped(inspect(authorized_scope=('file.txt',)), p.PatchCode.SCOPE_MISMATCH)

    def test_case_unicode_and_ancestor_collisions_reject(self):
        for scope in (('FILE.txt', 'file.txt', 'other.txt'),
                      ('file.txt', 'other.txt', 'src', 'src/child'),
                      ('SS', 'file.txt', 'other.txt', 'ß')):
            with self.subTest(scope=scope):
                self.assertStopped(inspect(authorized_scope=scope), p.PatchCode.PATH_COLLISION)
        action = proposal()
        changed = replace(action, scope=('FILE.txt', 'file.txt', 'other.txt'))
        self.assertStopped(inspect(changed), p.PatchCode.PATH_COLLISION)

    def test_ordinary_unicode_literal_path_is_supported(self):
        action = proposal()
        target = replace(action.payload.target, path='資料.txt')
        action = replace(action, scope=('資料.txt',), payload=replace(action.payload, target=target))
        self.assertIs(inspect(action).status, p.PatchStatus.VERIFIED_STRUCTURE)

    def test_nfc_alias_accepted_by_contract_path_rejects_as_collision(self):
        # R108-F1: these pass action_contract._path (unlike 'e\u0301.txt'), so only
        # the NFC check in patch_safety._collision can stop them.
        for alias, canonical in (('\u212b.txt', '\u00c5.txt'), ('\uf900.txt', '\u8c48.txt')):
            for kind in (c.ActionKind.FILE_CREATE, c.ActionKind.FILE_WRITE, c.ActionKind.FILE_DELETE):
                with self.subTest(alias=alias, kind=kind):
                    self.assertEqual(unicodedata.normalize('NFC', alias), canonical)
                    self.assertEqual(c._path(alias), alias)
                    action = proposal(kind)
                    target = replace(action.payload.target, path=alias)
                    aliased = replace(action, scope=(alias,), payload=replace(action.payload, target=target))
                    self.assertStopped(inspect(aliased), p.PatchCode.PATH_COLLISION)
                    self.assertStopped(inspect(action, authorized_scope=tuple(sorted(action.scope + (alias,)))),
                                       p.PatchCode.PATH_COLLISION)
                    # Control: the NFC spelling of the same name is an ordinary path.
                    target = replace(target, path=canonical)
                    plain = replace(action, scope=(canonical,), payload=replace(action.payload, target=target))
                    self.assertIs(inspect(plain).status, p.PatchStatus.VERIFIED_STRUCTURE)

    def test_credentials_traversal_control_and_symlink_types_reuse_contract_validation(self):
        for path in ('../x', '/absolute', 'src/../x', '.git/config', '.env', 'src/id_rsa',
                     'src/key.pem', '.config/gh/hosts', 'a\nb', 'a\u202eb', 'e\u0301.txt'):
            with self.subTest(path=path), self.assertRaises(LLMToolError):
                replace(proposal().payload.target, path=path)
        for file_type in ('SYMLINK', 'DIRECTORY', 'REGULAR'):
            with self.subTest(file_type=file_type), self.assertRaises(LLMToolError):
                replace(proposal().payload.target.state, file_type=file_type)
        with self.assertRaises(LLMToolError):
            replace(proposal().payload.target.state.metadata, nlink=2)

    def test_unsupported_kind_has_no_effect(self):
        self.assertStopped(inspect(proposal(c.ActionKind.GIT_OPERATION)), p.PatchCode.UNSUPPORTED_KIND)

    def test_modes_are_a_conservative_explicit_subset(self):
        for mode in (0, 0o444, 0o666, 0o755, 0o777):
            with self.subTest(new_mode=mode):
                self.assertStopped(inspect(proposal(mode=mode)), p.PatchCode.UNSUPPORTED_MODE)
                action = proposal()
                before = action.payload.target.state
                target = replace(action.payload.target, state=replace(before,
                                 metadata=replace(before.metadata, mode=mode)))
                self.assertStopped(inspect(replace(action, payload=replace(action.payload, target=target))),
                                   p.PatchCode.UNSUPPORTED_MODE)
        for mode in (0o600, 0o644):
            self.assertIs(inspect(proposal(mode=mode)).status, p.PatchStatus.VERIFIED_STRUCTURE)

    def test_missing_preimage_new_content_and_recovery_stop(self):
        for key, code in (('preimage', p.PatchCode.MISSING_PREIMAGE),
                          ('new_content', p.PatchCode.MISSING_POSTIMAGE),
                          ('recovery_image', p.PatchCode.MISSING_RECOVERY_IMAGE)):
            with self.subTest(key=key):
                self.assertStopped(inspect(**{key: None}), code, missing=True)
        self.assertStopped(inspect(proposal(c.ActionKind.FILE_DELETE), recovery_image=None),
                           p.PatchCode.MISSING_RECOVERY_IMAGE, missing=True)

    def test_wrong_digest_or_size_of_every_image_rejects(self):
        for key, code, good in (('preimage', p.PatchCode.PREIMAGE_MISMATCH, OLD),
                                ('new_content', p.PatchCode.POSTIMAGE_MISMATCH, NEW),
                                ('recovery_image', p.PatchCode.RECOVERY_IMAGE_MISMATCH, OLD)):
            for bad in (b'X' * len(good), good + b'X', good[:-1]):
                with self.subTest(key=key, size=len(bad)):
                    self.assertStopped(inspect(**{key: bad}), code)

    def test_contradictory_content_for_create_or_delete_rejects(self):
        create = proposal(c.ActionKind.FILE_CREATE)
        for key in ('preimage', 'recovery_image'):
            self.assertStopped(inspect(create, **{key: b''}), p.PatchCode.UNEXPECTED_CONTENT)
        self.assertStopped(inspect(proposal(c.ActionKind.FILE_DELETE), new_content=b''),
                           p.PatchCode.UNEXPECTED_CONTENT)

    def test_empty_and_maximum_byte_images(self):
        for size in (0, c.MAX_CONTENT_BYTES):
            old, new = b'x' * size, b'y' * size
            for kind in (c.ActionKind.FILE_CREATE, c.ActionKind.FILE_WRITE, c.ActionKind.FILE_DELETE):
                with self.subTest(size=size, kind=kind):
                    action = proposal(kind, old=old, new=new)
                    create = kind is c.ActionKind.FILE_CREATE
                    result = inspect(action, preimage=None if create else old,
                                     recovery_image=None if create else old,
                                     new_content=None if kind is c.ActionKind.FILE_DELETE else new)
                    self.assertIs(result.status, p.PatchStatus.VERIFIED_STRUCTURE)
                    self.assertNoAuthority(result)

    def test_overlimit_mutable_and_untyped_images_reject(self):
        class BytesSubclass(bytes):
            pass
        for key in ('preimage', 'new_content', 'recovery_image'):
            for bad in (b'x' * (c.MAX_CONTENT_BYTES + 1), bytearray(OLD), memoryview(OLD),
                        'raw-secret-token', {}, True, BytesSubclass(OLD)):
                with self.subTest(key=key, kind=type(bad).__name__):
                    self.assertStopped(inspect(**{key: bad}), p.PatchCode.INVALID_INPUT)

    def test_untyped_approved_json_and_invalid_pins_reject(self):
        action = proposal()
        for candidate in (dict(action.to_dict(), approved=True), action.to_json(), None, True):
            self.assertStopped(inspect(action, candidate=candidate), p.PatchCode.INVALID_INPUT)
        for changes in (dict(expected_task=action.task.to_dict()), dict(expected_state={}),
                        dict(current_state=None), dict(expected_action_digest='bad'),
                        dict(authorized_scope=('file.txt', 'file.txt'))):
            self.assertStopped(inspect(action, **changes), p.PatchCode.INVALID_INPUT)

    def test_tampered_nested_contract_is_revalidated_without_echo(self):
        action = proposal()
        object.__setattr__(action.payload.target, 'path', '../private-secret')
        result = p.preflight(action, expected_action_digest='a' * 64, expected_task=action.task,
                             authorized_scope=('file.txt',), expected_state=proposal().expected_state,
                             current_state=proposal().expected_state)
        self.assertStopped(result, p.PatchCode.INVALID_INPUT)
        self.assertNotIn('private-secret', result.to_json() + repr(result))

    def test_failure_results_never_contain_paths_bytes_or_synthetic_secrets(self):
        secret = b'token=synthetic-secret-value'
        action = proposal()
        target = replace(action.payload.target, path='private-name.txt')
        action = replace(action, scope=('private-name.txt',),
                         payload=replace(action.payload, target=target))
        for key in ('preimage', 'new_content', 'recovery_image'):
            result = inspect(action, **{key: secret})
            rendered = result.to_json() + repr(result) + str(result.to_dict())
            self.assertNotIn(secret.decode(), rendered)
            self.assertNotIn('private-name.txt', rendered)
            self.assertNoAuthority(result)

    def test_success_never_serializes_raw_images(self):
        secret = b'token=synthetic-only-image'
        action = proposal(old=secret, new=secret)
        result = inspect(action, preimage=secret, recovery_image=secret, new_content=secret)
        self.assertIs(result.status, p.PatchStatus.VERIFIED_STRUCTURE)
        self.assertNotIn(secret.decode(), result.to_json() + repr(result))

    def test_repeat_determinism_input_and_output_immutability(self):
        action = proposal()
        before = action.to_json()
        first = inspect(action)
        for _ in range(5):
            self.assertEqual(inspect(action).to_json(), first.to_json())
        self.assertEqual(action.to_json(), before)
        with self.assertRaises(FrozenInstanceError):
            first.status = p.PatchStatus.REJECTED
        with self.assertRaises(FrozenInstanceError):
            first.postimage.mode = 0o777
        with self.assertRaises(LLMToolError):
            replace(first, code=p.PatchCode.STATE_MISMATCH)
        with self.assertRaises(LLMToolError):
            replace(first, status=p.PatchStatus.REJECTED)
        with self.assertRaises(LLMToolError):
            replace(first, recovery=replace(first.recovery, before_state_digest='f' * 64))

    def test_recovery_commitment_binds_all_before_metadata_without_claiming_backup(self):
        action = proposal()
        first = inspect(action)
        target = action.payload.target
        target = replace(target, state=replace(target.state,
                         metadata=replace(target.state.metadata, mtime_ns=999)))
        changed = replace(action, payload=replace(action.payload, target=target))
        second = inspect(changed)
        self.assertNotEqual(first.recovery.file_state_digest, second.recovery.file_state_digest)
        self.assertNotEqual(first.recovery.before_state_digest, second.recovery.before_state_digest)
        self.assertEqual(first.recovery.image_sha256, second.recovery.image_sha256)
        self.assertNoAuthority(first)
        self.assertNoAuthority(second)

    def test_pure_api_has_zero_io_on_positive_and_negative_paths(self):
        import os
        actions = [proposal(kind) for kind in (c.ActionKind.FILE_CREATE, c.ActionKind.FILE_WRITE,
                                              c.ActionKind.FILE_DELETE, c.ActionKind.GIT_OPERATION)]
        def boom(*args, **kwargs):
            raise AssertionError('P1 attempted an effect')
        with patch('builtins.open', boom), patch.object(os, 'open', boom), \
                patch.object(os, 'stat', boom), patch.object(os, 'system', boom), \
                patch.object(subprocess, 'Popen', boom), patch.object(socket, 'socket', boom), \
                patch.object(time, 'time', boom), patch.object(time, 'monotonic', boom):
            for action in actions:
                inspect(action).to_json()
            self.assertStopped(inspect(actions[1], recovery_image=None),
                               p.PatchCode.MISSING_RECOVERY_IMAGE, missing=True)
            self.assertStopped(inspect(actions[1], new_content=b'bad'), p.PatchCode.POSTIMAGE_MISMATCH)

    def test_module_import_surface_excludes_execution_and_authority(self):
        source = Path(p.__file__).read_text(encoding='utf-8')
        tree = ast.parse(source)
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names}
        self.assertEqual(imports, {'hashlib', 'unicodedata'})
        froms = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertEqual(froms, {'dataclasses', 'enum', 'typing', None, 'errors'})
        for forbidden in ('subprocess', 'authorization_policy', 'authorization_ledger',
                          'os.open', 'git apply', 'argparse'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
