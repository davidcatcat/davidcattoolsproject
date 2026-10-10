"""Public immutable contract API: strict data, exact binding, no authority."""

import copy
import hashlib
import json
from dataclasses import FrozenInstanceError, fields, replace
import unittest
from unittest.mock import patch

from llmtool import action_contract as c
from llmtool.errors import LLMToolError


def make_action(kind=c.ActionKind.FILE_WRITE):
    task = c.TaskBinding('task-1', '1'*32, 1, '2'*64)
    repo = c.RepositoryBinding('3'*64, '/synthetic/repository', 10, 20, '4'*40)
    metadata = c.FileMetadata(10, 21, 0o644, 1000, 1000, 3, 100, 1)
    state = c.FileState(True, c.FileType.REGULAR, '5'*64, metadata)
    target = c.FileExpectation('file.txt', state)
    if kind is c.ActionKind.FILE_CREATE:
        target = c.FileExpectation('file.txt', c.FileState(False, c.FileType.ABSENT, None, None))
    if kind in (c.ActionKind.FILE_CREATE, c.ActionKind.FILE_WRITE):
        payload = c.FileChangePayload(target, '6'*64, 4, 0o600)
    elif kind is c.ActionKind.FILE_DELETE:
        payload = c.FileDeletePayload(target)
    else:
        payload = c.GitAddPayload(c.GitOperation.ADD, [target], '7'*64)
    return c.Action(1, 'action-1', 100, kind, task, repo, ['file.txt', 'other.txt'], payload)


def make_approval(action=None):
    action = action or make_action()
    return c.Approval(1, 'USER', action.digest, action.kind, action.task,
        action.repository, action.scope, action.expected_state.digest, 100, 200, '8'*64)


def validate(action, approval, **overrides):
    return c.validate_approval(action, approval, **{'current_state': action.expected_state,
        'expected_nonce': '8'*64, 'now': 150, **overrides})


def leaves(value, path=()):
    if type(value) is dict:
        for key, item in value.items():
            yield from leaves(item, path+(key,))
    elif type(value) is list:
        for index, item in enumerate(value):
            yield from leaves(item, path+(index,))
    else:
        yield path, value


def changed_leaf(value, path, new_value):
    result = copy.deepcopy(value)
    target = result
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = new_value
    return result


def alternative(value):
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value+1
    if value is None:
        return 'UNSUPPORTED'
    if len(value) in (32, 40, 64) and set(value) <= set('0123456789abcdef'):
        return ('a' if value[0] != 'a' else 'b')+value[1:]
    if value.startswith('/'):
        return value+'-changed'
    return value+'-changed'


class ContractAPITests(unittest.TestCase):
    def test_action_and_approval_schema_version_exact_key_and_strict_integer(self):
        for model, parser, value in ((c.Action, c.parse_action, make_action()),
                                    (c.Approval, c.parse_approval, make_approval())):
            with self.subTest(model=model):
                raw = value.to_dict()
                self.assertEqual(raw['schema_version'], 1)
                self.assertNotIn('version', raw)
                self.assertEqual(parser(json.dumps(raw)), value)
                self.assertEqual(replace(value, schema_version=1), value)
                if model is c.Action:
                    self.assertEqual(value.expected_state.version, 1)
                for variant in ('legacy', 'both', 'missing', True, '1', 1.0, 2):
                    invalid = copy.deepcopy(raw)
                    if variant == 'legacy':
                        invalid['version'] = invalid.pop('schema_version')
                    elif variant == 'both':
                        invalid['version'] = 1
                    elif variant == 'missing':
                        del invalid['schema_version']
                    else:
                        invalid['schema_version'] = variant
                    with self.subTest(variant=variant), self.assertRaises(LLMToolError):
                        parser(json.dumps(invalid))
                with self.assertRaises(LLMToolError):
                    parser(value.to_json()[:-1] + ',"schema_version":1}')
                with self.assertRaises(TypeError):
                    replace(value, version=1)

    def test_all_supported_kinds_roundtrip_with_exact_state(self):
        for kind in c.ActionKind:
            with self.subTest(kind=kind):
                action = make_action(kind)
                self.assertEqual(c.parse_action(action.to_json()), action)
                state = action.expected_state
                self.assertEqual(c.parse_repository_state(state.to_json()), state)
                approval = make_approval(action)
                self.assertEqual(c.parse_approval(approval.to_json()), approval)
                result = validate(action, approval)
                self.assertTrue(result.bindings_valid)
                self.assertFalse(result.trusted_user_source)
                self.assertFalse(result.authorized)
                self.assertEqual(result.code, c.ApprovalCode.NOT_AUTHORIZED)

    def test_exact_domain_separated_utf8_canonical_bytes(self):
        action = replace(make_action(), repository=replace(make_action().repository,
                                                        canonical_root='/synthetic/資料'))
        for domain, value in (('llmtool.action', action),
                ('llmtool.action-state', action.expected_state),
                ('llmtool.approval', make_approval(action))):
            expected = json.dumps({'domain': domain, 'version': 1, 'value': value.to_dict()},
                sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
            self.assertEqual(value.digest, hashlib.sha256(expected).hexdigest())
        reordered = dict(reversed(list(action.to_dict().items())))
        self.assertEqual(c.parse_action(json.dumps(reordered)).digest, action.digest)
        self.assertEqual(c.parse_action(' \n'+action.to_json()+'\t ').digest, action.digest)
        self.assertNotEqual(action.digest, action.expected_state.digest)

    def test_detached_nested_immutability(self):
        action = make_action(c.ActionKind.GIT_OPERATION)
        paths = list(action.scope)
        files = list(action.payload.files)
        detached = replace(action, scope=paths, payload=replace(action.payload, files=files))
        paths.append('third.txt')
        files.clear()
        self.assertEqual(detached, action)
        output = detached.to_dict()
        output['payload']['files'][0]['state']['metadata']['mode'] = 0
        self.assertEqual(detached.payload.files[0].state.metadata.mode, 0o644)
        for value in (action, action.payload, action.payload.files[0],
                      action.payload.files[0].state, action.payload.files[0].state.metadata,
                      action.repository, action.task, make_approval(action)):
            with self.subTest(type=type(value)):
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, fields(value)[0].name, None)
        self.assertIs(type(detached.payload.files), tuple)
        self.assertIs(type(detached.scope), tuple)

    def test_every_action_field_mutation_rejects_or_changes_digest_and_invalidates_old_receipt(self):
        # Each leaf is enumerated rather than selecting a few convenient fields.
        # Fixed enum/version/existence combinations reject; every accepted change
        # must hash differently and invalidate the original receipt.
        tested = set()
        for kind in c.ActionKind:
            action, approval = make_action(kind), make_approval(make_action(kind))
            for path, value in leaves(action.to_dict()):
                with self.subTest(kind=kind, field=path):
                    tested.add((kind, path))
                    raw = changed_leaf(action.to_dict(), path, alternative(value))
                    try:
                        changed = c.parse_action(json.dumps(raw))
                    except LLMToolError:
                        continue
                    self.assertNotEqual(changed.digest, action.digest)
                    self.assertFalse(validate(changed, approval).bindings_valid)
        self.assertEqual(len(tested), 109)

    def test_fixed_schema_enumeration_and_each_git_path_precondition(self):
        common = {'schema_version','action_id','created_at','kind','task.task_id','task.run_id',
            'task.contract_version','task.contract_digest','repository.repository_id',
            'repository.canonical_root','repository.root_device','repository.root_inode',
            'repository.expected_head','scope.0','scope.1'}
        metadata = {'device','inode','mode','uid','gid','size','mtime_ns','nlink'}
        for kind in c.ActionKind:
            action = make_action(kind)
            target = 'payload.files.0' if kind is c.ActionKind.GIT_OPERATION else 'payload.target'
            expected = common | {target+'.path',target+'.state.exists',
                                 target+'.state.file_type',target+'.state.content_sha256'}
            if kind is c.ActionKind.FILE_CREATE:
                expected.add(target+'.state.metadata')
            else:
                expected |= {target+'.state.metadata.'+name for name in metadata}
            if kind in (c.ActionKind.FILE_CREATE,c.ActionKind.FILE_WRITE):
                expected |= {'payload.new_content_sha256','payload.new_size','payload.new_mode'}
            if kind is c.ActionKind.GIT_OPERATION:
                expected |= {'payload.operation','payload.expected_index_sha256'}
            actual = {'.'.join(str(part) for part in path) for path,_ in leaves(action.to_dict())}
            self.assertEqual(actual,expected)
        action = make_action(c.ActionKind.GIT_OPERATION)
        second = replace(action.payload.files[0],path='other.txt')
        action = replace(action,payload=replace(action.payload,files=[*action.payload.files,second]))
        receipt = make_approval(action)
        self.assertTrue(validate(action,receipt).bindings_valid)
        for index in (0,1):
            for attribute in ('content_sha256','metadata'):
                target = action.payload.files[index]
                state = (replace(target.state,content_sha256='9'*64) if attribute=='content_sha256'
                         else replace(target.state,metadata=replace(target.state.metadata,inode=99)))
                changed = list(action.payload.files);changed[index]=replace(target,state=state)
                modified = replace(action,payload=replace(action.payload,files=changed))
                self.assertNotEqual(modified.digest,action.digest)
                self.assertFalse(validate(modified,receipt).bindings_valid)
            raw = action.to_dict();del raw['payload']['files'][index]['state']['metadata']
            with self.assertRaises(LLMToolError):
                c.parse_action(json.dumps(raw))

    def test_valid_kind_path_and_existence_changes_invalidate(self):
        old = make_action(c.ActionKind.FILE_WRITE)
        receipt = make_approval(old)
        for new in (make_action(c.ActionKind.FILE_CREATE), make_action(c.ActionKind.FILE_DELETE),
                    make_action(c.ActionKind.GIT_OPERATION)):
            self.assertNotEqual(new.digest, old.digest)
            self.assertFalse(validate(new, receipt).bindings_valid)
        target = replace(old.payload.target, path='other.txt')
        changed = replace(old, payload=replace(old.payload, target=target))
        self.assertNotEqual(changed.digest, old.digest)
        self.assertFalse(validate(changed, receipt).bindings_valid)

    def test_every_receipt_field_mutation_rejects_or_fails_binding_or_trusted_attestation(self):
        action = make_action()
        receipt = make_approval(action)
        context = c.TrustedApprovalContext(receipt.digest)
        for path, value in leaves(receipt.to_dict()):
            with self.subTest(field=path):
                raw = changed_leaf(receipt.to_dict(), path, alternative(value))
                try:
                    changed = c.parse_approval(json.dumps(raw))
                except LLMToolError:
                    continue
                self.assertNotEqual(changed.digest, receipt.digest)
                self.assertFalse(validate(action, changed, trusted_context=context).bindings_valid)

    def test_every_supplied_state_field_mutation_rejects_or_fails(self):
        for kind in c.ActionKind:
            action = make_action(kind)
            for path, value in leaves(action.expected_state.to_dict()):
                with self.subTest(kind=kind, field=path):
                    raw = changed_leaf(action.expected_state.to_dict(), path, alternative(value))
                    try:
                        changed = c.parse_repository_state(json.dumps(raw))
                    except LLMToolError:
                        continue
                    self.assertNotEqual(changed.digest, action.expected_state.digest)
                    self.assertEqual(validate(action, make_approval(action), current_state=changed).code,
                                     c.ApprovalCode.STATE_MISMATCH)

    def test_expiry_nonce_issue_order_and_synthetic_consumption(self):
        action, receipt = make_action(), make_approval()
        cases = ({'now': 99}, {'now': 200}, {'now': 201},
                 {'expected_nonce': '9'*64}, {'consumed_nonces': ['8'*64]})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                result = validate(action, receipt, **kwargs)
                self.assertFalse(result.bindings_valid)
                self.assertFalse(result.authorized)
        self.assertTrue(validate(action, receipt, now=100).bindings_valid)
        self.assertTrue(validate(action, receipt, now=199).bindings_valid)
        early = replace(receipt, issued_at=99)
        self.assertEqual(validate(action, early).code, c.ApprovalCode.BINDING_MISMATCH)
        # No ledger: identical calls validate again when caller supplies no consumed set.
        self.assertEqual(validate(action, receipt), validate(action, receipt))
        for kwargs in ({'now': True}, {'expected_nonce': None}, {'consumed_nonces': ['8'*64]*2},
                       {'consumed_nonces': ['a'*64]*129}):
            with self.assertRaises(LLMToolError):
                validate(action, receipt, **kwargs)

    def test_user_json_claim_cannot_attest_or_authorize(self):
        action, receipt = make_action(), c.parse_approval(make_approval().to_json())
        result = validate(action, receipt)
        self.assertFalse(result.trusted_user_source)
        self.assertFalse(result.authorized)
        self.assertEqual(c.approval_audit(action, receipt, result).event_type, c.AuditType.PROPOSED)
        context = c.TrustedApprovalContext(receipt.digest)
        attested = validate(action, receipt, trusted_context=context)
        self.assertTrue(attested.trusted_user_source)
        self.assertFalse(attested.authorized)
        self.assertEqual(c.approval_audit(action, receipt, attested).event_type, c.AuditType.GRANTED)
        changed_context = c.TrustedApprovalContext('9'*64)
        self.assertFalse(validate(action, receipt, trusted_context=changed_context).bindings_valid)
        with self.assertRaises(LLMToolError):
            validate(action, receipt, trusted_context={'approval_digest': receipt.digest})
        for source in ('MODEL', 'approved', 'user', True, None):
            data = receipt.to_dict(); data['source'] = source
            with self.assertRaises(LLMToolError):
                c.parse_approval(json.dumps(data))
        data = receipt.to_dict(); data['trusted_context'] = context.to_dict()
        with self.assertRaises(LLMToolError):
            c.parse_approval(json.dumps(data))

    def test_safe_audit_types_no_raw_text_and_no_execution(self):
        action, receipt = make_action(), make_approval()
        for kwargs, event_type in (({}, c.AuditType.PROPOSED),
            ({'now': 200}, c.AuditType.EXPIRED), ({'expected_nonce': '9'*64}, c.AuditType.REJECTED)):
            audit = c.approval_audit(action, receipt, validate(action, receipt, **kwargs))
            self.assertEqual(audit.event_type, event_type)
            self.assertLess(len(audit.to_json()), 1024)
            self.assertNotIn(action.repository.canonical_root, audit.to_json())
            self.assertNotIn(receipt.nonce, audit.to_json())
        audit = c.AuditEvent(1, c.AuditType.INVALID_CONTRACT, None, None, None, None)
        self.assertEqual(audit.event_type, c.AuditType.INVALID_CONTRACT)
        for event in (c.AuditType.PROPOSED,c.AuditType.GRANTED,c.AuditType.REJECTED,c.AuditType.EXPIRED):
            with self.assertRaises(LLMToolError):
                c.AuditEvent(1,event,None,None,None,c.ApprovalCode.USER_ATTESTED)
        for invalid in ('EXECUTED', 'sk-synthetic-secret-marker', {'raw_stderr': 'SECRET'}):
            with self.assertRaises(LLMToolError):
                c.AuditEvent(1, invalid, None, None, None, None)

    def test_pure_api_never_reads_or_runs_anything(self):
        action, receipt = make_action(), make_approval()
        from contextlib import ExitStack
        with ExitStack() as stack:
            for target in ('builtins.open', 'os.open', 'os.stat', 'subprocess.Popen',
                           'os.system', 'socket.socket'):
                stack.enter_context(patch(target, side_effect=AssertionError('tripwire')))
            self.assertEqual(c.parse_action(action.to_json()), action)
            self.assertTrue(validate(action, receipt).bindings_valid)
            self.assertFalse(validate(action, receipt).authorized)


class StrictContractTests(unittest.TestCase):
    def reject(self, value, parser=c.parse_action):
        with self.assertRaises(LLMToolError) as result:
            parser(value if type(value) is str else json.dumps(value))
        self.assertNotIn('PRIVATE', str(result.exception))

    def test_unknown_missing_duplicate_keys_every_nested_object(self):
        action = make_action().to_dict()
        for path in ((), ('task',), ('repository',), ('payload',), ('payload','target'),
                     ('payload','target','state'), ('payload','target','state','metadata')):
            current = action
            for key in path:
                current = current[key]
            for key in current:
                raw = copy.deepcopy(action); target = raw
                for part in path:
                    target = target[part]
                del target[key]
                self.reject(raw)
            raw = copy.deepcopy(action); target = raw
            for part in path:
                target = target[part]
            target['PRIVATE_unknown'] = None
            self.reject(raw)
        self.reject('{"schema_version":1,"schema_version":1}')
        self.reject(make_action().to_json().replace('"mode":420', '"mode":420,"mode":420'))
        for parser, value in ((c.parse_approval, make_approval().to_dict()),
                              (c.parse_repository_state, make_action().expected_state.to_dict())):
            for key in value:
                raw = copy.deepcopy(value); del raw[key]
                self.reject(raw, parser)
            self.reject({**value, 'PRIVATE_unknown': True}, parser)

    def test_strict_types_no_coercion_or_permissive_future_version(self):
        action = make_action().to_dict()
        for field, values in (('schema_version', (True, 0, 2, '1', 1.0, None)),
                ('created_at', (True, '100', 100.0, -1, c.MAX_EPOCH+1)),
                ('action_id', (True, 7, '', 'approved;touch PRIVATE')),
                ('kind', (True, 'COMMAND', 'FILE_WRITE ', 'file_write', 'EXECUTE')),
                ('scope', ('file.txt', [], ['file.txt']*2, ['other.txt','file.txt']))):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.reject({**action, field: value})
        for path in (('task','contract_version'), ('repository','root_device'),
                     ('payload','new_size'), ('payload','target','state','metadata','mode')):
            for value in (True, 1.0, '1', -1):
                self.reject(changed_leaf(action, path, value))
        for value in ('1'*39, 'a'*41, 'A'*40, '', None):
            self.reject(changed_leaf(action, ('repository','expected_head'), value))

    def test_json_bombs_constants_controls_utf8_bounds(self):
        for value in ('', '[]', '{}', 'null', 'PRIVATE', '```json\n{}\n```',
                '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{"x":1e999}',
                '['*500+']'*500, ' '* (c.JSON_LIMIT+1), '\ud800'):
            self.reject(value)
        for text in ('PRIVATE\x00', 'PRIVATE\x1b', 'PRIVATE\x7f', 'PRIVATE\x85',
                     'PRIVATE\u202e', 'PRIVATE\u200b', 'PRIVATE\n', 'PRIVATE\t'):
            self.reject({**make_action().to_dict(), 'action_id': text})
        raw = make_action().to_json()
        self.assertEqual(c.parse_action(raw+' '*(c.JSON_LIMIT-len(raw))), make_action())
        self.reject(raw+' '*(c.JSON_LIMIT-len(raw)+1))
        # UTF-8 bytes, not just character count.
        self.reject('"'+'資料'*(c.JSON_LIMIT//3)+'"')
        self.reject({'x': [None]*1024})

    def test_paths_scope_literal_and_sensitive_exclusion(self):
        invalid = ('/absolute', '../file', 'dir/../file', './file', 'dir//file', 'dir/',
            '.git/config', '.Git/config', '.aws/credentials', '.env', '.env.local',
            '.config/llmtool/config.yaml', 'key.pem', 'credentials.json', '-option',
            'dir/-option', '~/.file', '$HOME/file', 'file;touch', 'a|b', 'a&&b', 'a||b',
            'a>b', 'a<b', '$(touch)', '`touch`', 'a\\b', 'a*b', 'a b', 'a\nPRIVATE',
            'a\u200b', 'x/'*32+'file', 'x'*1025)
        action = make_action().to_dict()
        for path in invalid:
            with self.subTest(path=path):
                raw = changed_leaf(action, ('payload','target','path'), path)
                raw['scope'] = [path]
                self.reject(raw)
        raw = changed_leaf(action, ('payload','target','path'), 'unlisted.txt')
        self.reject(raw)
        for root in ('relative', '/', '/repo/../x', '/repo//x', '/repo/link/./x', '/repo/$HOME'):
            self.reject(changed_leaf(action, ('repository','canonical_root'), root))
        self.reject({**action, 'scope': ['file.txt']+[f'x{i}' for i in range(32)]})

    def test_absent_regular_metadata_content_and_size_fail_closed(self):
        action = make_action().to_dict()
        for path, values in (
                (('payload','target','state','exists'), (False, 1, 'true')),
                (('payload','target','state','file_type'), ('SYMLINK', 'DIRECTORY', 'ABSENT')),
                (('payload','target','state','metadata'), (None, {})),
                (('payload','target','state','metadata','nlink'), (0, 2, True)),
                (('payload','target','state','metadata','size'), (c.MAX_CONTENT_BYTES+1,)),
                (('payload','target','state','metadata','mode'), (0o4755,)),
                (('payload','new_mode'), (0o4755, True)),
                (('payload','new_size'), (c.MAX_CONTENT_BYTES+1, -1)),
                (('payload','new_content_sha256'), (None, 'x'*64))):
            for value in values:
                self.reject(changed_leaf(action, path, value))
        create = make_action(c.ActionKind.FILE_CREATE).to_dict()
        self.reject(changed_leaf(create, ('payload','target','state','metadata'),
                                 make_action().payload.target.state.metadata.to_dict()))
        self.reject(changed_leaf(create, ('kind',), 'FILE_WRITE'))

    def test_git_add_only_complete_preconditions_and_no_arbitrary_payload(self):
        action = make_action(c.ActionKind.GIT_OPERATION).to_dict()
        for operation in ('COMMIT', 'PUSH', 'RESET', 'CLEAN', 'CHECKOUT', 'CONFIG', 'add', 'ADD;touch'):
            self.reject(changed_leaf(action, ('payload','operation'), operation))
        for key, value in (('argv', []), ('executable', ''), ('shell', ''), ('env', {}),
                           ('hooks', []), ('subcommand', 'push'), ('command', 'approved')):
            raw = copy.deepcopy(action); raw['payload'][key] = value
            self.reject(raw)
        self.reject(changed_leaf(action, ('payload','files'), []))
        self.reject(changed_leaf(action, ('payload','files'), action['payload']['files']*2))
        self.reject(changed_leaf(action, ('payload','files',0,'state','metadata'), None))
        self.reject(changed_leaf(action, ('payload','expected_index_sha256'), None))
        metadata = make_action().payload.target.state.metadata
        target = make_action().payload.target
        with self.assertRaises(LLMToolError):
            c.GitAddPayload(c.GitOperation.ADD,
                [replace(target, path='a', state=replace(target.state, metadata=replace(metadata, size=c.MAX_CONTENT_BYTES))),
                 replace(target, path='b', state=replace(target.state, metadata=replace(metadata, size=1)))], '7'*64)

    def test_approval_bounds_source_and_no_authority_extension(self):
        receipt = make_approval().to_dict()
        for field, values in (('issued_at', (True, 100.0, '100', -1)),
            ('expires_at', (100, 99, 3701, True, c.MAX_EPOCH+1)),
            ('nonce', ('', 'A'*64, '8'*63, 'approved', None)),
            ('source', ('MODEL', 'SYSTEM', 'user', True))):
            for value in values:
                self.reject({**receipt, field: value}, c.parse_approval)
        for key in ('approved', 'authorized', 'trusted_context', 'signature', 'reasoning', 'raw_stderr'):
            self.reject({**receipt, key: 'PRIVATE'}, c.parse_approval)
        self.assertEqual(c.parse_approval(json.dumps({**receipt,'expires_at':3700})).expires_at, 3700)

    def test_constructors_cannot_bypass_typed_fields(self):
        action = make_action()
        for kwargs in ({'kind':'FILE_WRITE'}, {'payload':action.payload.to_dict()},
                       {'task':action.task.to_dict()}, {'repository':action.repository.to_dict()},
                       {'scope':('file.txt', object())}, {'schema_version':True}):
            with self.assertRaises(LLMToolError):
                replace(action, **kwargs)
        with self.assertRaises(LLMToolError):
            replace(action.payload.target.state, file_type='REGULAR')


# --- A11b.3 D1 end-to-end journeys (Issue #97): legacy contract -> decide ->
# ledger issue -> redeem -> fake sink. Helpers are imported lazily because
# test_authorization_policy itself imports this module.
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

_CANONICAL_REPO = Path(__file__).resolve().parents[3]


def _d1():
    from llmtool import authorization_ledger as ledger
    from llmtool import authorization_policy as policy
    import test_authorization_policy as helpers
    return policy, ledger, helpers


class _JourneyClock:
    def __init__(self, now=1000):
        self.now = now

    def __call__(self):
        return self.now


def _journey_sink_class():
    policy, ledger, _ = _d1()

    class RecordingSink(ledger.EffectSink):
        """Synthetic sink: records bindings in memory, touches no file."""

        def __init__(self, error=None):
            self.calls, self.error = [], error

        @property
        def effect_kind(self):
            return policy.EffectKind.FAKE_FILE_CREATE

        def apply(self, binding):
            self.calls.append(binding)
            if self.error is not None:
                raise self.error
    return RecordingSink


class D1AuthorizationJourneyTests(unittest.TestCase):
    """User journeys across the legacy contract, D1 policy and one-use ledger."""

    def setUp(self):
        self.p, self.l, self.h = _d1()
        self.Sink = _journey_sink_class()
        tmp = tempfile.TemporaryDirectory(prefix='d1-journey-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ledger_dir = self.root / 'ledger'
        self.ledger_dir.mkdir(mode=0o700)
        os.chmod(self.ledger_dir, 0o700)
        self.ledger_path = self.ledger_dir / 'receipts.sqlite3'
        self.clock = _JourneyClock()
        self.ledger = self.l.ReceiptLedger.create(self.ledger_path, clock=self.clock)
        self.addCleanup(lambda: self.ledger.close())

    # -- helpers ---------------------------------------------------------
    def decide(self, action, policy=None, **changes):
        policy = policy or self.h.make_policy(action)
        values = dict(issuer=self.h.FakeHostIssuer(policy), observed=self.h.make_observed(action),
                      redeem_run_id=self.h.REDEEM_RUN)
        values.update(changes)
        return self.p.decide(action, policy, **values)

    def redeem(self, receipt, action=None, ledger=None, sink=None, **observed_changes):
        sink = sink if sink is not None else self.Sink()
        observed = self.h.make_observed(action or self.h.make_create(), **observed_changes)
        result = (ledger or self.ledger).redeem(receipt, redeem_run_id=self.h.REDEEM_RUN,
                                                observed=observed, sink=sink)
        return result, sink

    def receipt_rows(self):
        db = sqlite3.connect('file:%s?mode=ro' % self.ledger_path, uri=True)
        try:
            return db.execute('SELECT status FROM receipts ORDER BY rowid').fetchall()
        finally:
            db.close()

    def assertNoAuthority(self, decision, sink=None):
        """No ALLOW, no ledger receipt and no sink call can follow this decision."""
        self.assertIs(type(decision), self.p.PolicyDecision)
        self.assertIsNot(decision.decision, self.p.Decision.ALLOW_WITH_TRUSTED_RECEIPT)
        self.assertIsNone(decision.binding)
        with self.assertRaises(self.l.LedgerError) as caught:
            self.ledger.issue(decision, ttl_seconds=60)
        self.assertIs(caught.exception.code, self.l.LedgerCode.NOT_ALLOWED)
        if sink is not None:
            self.assertEqual(sink.calls, [])

    # -- (a) forged approval ----------------------------------------------
    def test_user_attested_legacy_approval_never_yields_allow_or_receipt(self):
        action = self.h.make_create()
        approval = make_approval(action)
        context = c.TrustedApprovalContext(approval.digest)
        validation = c.validate_approval(action, approval, current_state=action.expected_state,
                                         expected_nonce=approval.nonce, now=150,
                                         trusted_context=context)
        self.assertIs(validation.code, c.ApprovalCode.USER_ATTESTED)
        self.assertTrue(validation.trusted_user_source)
        self.assertFalse(validation.authorized)
        parsed = c.parse_approval(approval.to_json())
        forged_issuers = (approval, parsed, approval.to_dict(), approval.to_json(), validation,
                          validation.to_dict(), context, {'source': 'USER', 'trusted': True},
                          {'receipt_id': approval.nonce}, 'USER', True)
        sink = self.Sink()
        for issuer in forged_issuers:
            with self.subTest(issuer=type(issuer).__name__):
                decision = self.decide(action, issuer=issuer)
                self.assertIs(decision.decision, self.p.Decision.DENY)
                self.assertIn(decision.code, (self.p.DecisionCode.NO_ISSUER,
                                              self.p.DecisionCode.UNTRUSTED_ISSUER))
                self.assertNoAuthority(decision, sink)
        # The attested approval/validation themselves are not ledger input either.
        for value in (approval, validation, context, approval.to_dict()):
            with self.subTest(issue=type(value).__name__):
                with self.assertRaises(self.l.LedgerError) as caught:
                    self.ledger.issue(value, ttl_seconds=60)
                self.assertIs(caught.exception.code, self.l.LedgerCode.NOT_ALLOWED)
        # A receipt handle built around the approval nonce is unknown to the ledger.
        forged = self.l.Receipt(self.h.allow(action).binding, 1000, 1060, approval.nonce)
        result, sink = self.redeem(forged, action)
        self.assertIs(result.code, self.l.RedeemCode.RECEIPT_UNKNOWN)
        self.assertFalse(result.effect_done)
        self.assertEqual(sink.calls, [])
        self.assertEqual(self.receipt_rows(), [])

    # -- (b) action / contract mismatch -----------------------------------
    def test_action_contract_mismatch_is_denied_before_and_after_issue(self):
        action = self.h.make_create()
        policy = self.h.make_policy(action)
        other_task = replace(action.task, contract_digest='c'*64)
        cases = {
            'kind outside contract': (make_action(c.ActionKind.FILE_WRITE), policy,
                                      self.p.DecisionCode.UNSUPPORTED_ACTION),
            'path outside contract': (action, self.h.make_policy(action, scope=('file.txt',)),
                                      self.p.DecisionCode.OUT_OF_CONTRACT),
            'different contract digest': (replace(action, task=other_task), policy,
                                          self.p.DecisionCode.POLICY_BINDING_MISMATCH),
        }
        sink = self.Sink()
        for label, (candidate, candidate_policy, code) in cases.items():
            with self.subTest(label=label):
                decision = self.decide(candidate, candidate_policy,
                                       issuer=self.h.FakeHostIssuer(candidate_policy),
                                       observed=self.h.make_observed(action))
                self.assertIs(decision.decision, self.p.Decision.DENY)
                self.assertIs(decision.code, code)
                self.assertNoAuthority(decision, sink)
        # A legacy approval for action A does not bind a different action B.
        other = replace(action, action_id='action-2')
        self.assertEqual(c.validate_approval(other, make_approval(action),
                         current_state=other.expected_state, expected_nonce='8'*64,
                         now=150).code, c.ApprovalCode.BINDING_MISMATCH)
        # A receipt issued for action A cannot be retargeted to action B's digest.
        receipt = self.ledger.issue(self.decide(action), ttl_seconds=60)
        retargeted = replace(receipt, binding=replace(receipt.binding, action_digest=other.digest))
        result, sink = self.redeem(retargeted, action)
        self.assertIs(result.code, self.l.RedeemCode.RECEIPT_TAMPERED)
        self.assertEqual(sink.calls, [])
        self.assertEqual(self.redeem(receipt, action)[0].code, self.l.RedeemCode.REPLAY)
        self.assertEqual(self.ledger.status(receipt.nonce), 'DENIED')

    # -- (c) HEAD / index / worktree drift between issue and redeem --------
    def test_repository_state_change_between_issue_and_redeem_denies_without_effect(self):
        action = self.h.make_create()
        drift = {'HEAD': dict(head='c'*40), 'index': dict(index_sha256='c'*64),
                 'worktree': dict(worktree_sha256='c'*64), 'parent': dict(parent_inode=31),
                 'target created': dict(target_absent=False)}
        for index, (label, change) in enumerate(drift.items()):
            with self.subTest(label=label):
                # One receipt per decision binding: each drift case gets its own action.
                action = replace(action, action_id='drift-%d' % index)
                receipt = self.ledger.issue(self.decide(action), ttl_seconds=60)
                result, sink = self.redeem(receipt, action, **change)
                self.assertIs(result.code, self.l.RedeemCode.BINDING_MISMATCH)
                self.assertFalse(result.effect_done)
                self.assertEqual(sink.calls, [])
                self.assertEqual(self.ledger.status(receipt.nonce), 'DENIED')
                # Restoring the old state later does not revive the receipt.
                again, sink = self.redeem(receipt, action)
                self.assertIs(again.code, self.l.RedeemCode.REPLAY)
                self.assertEqual(sink.calls, [])

    # -- (d) policy tampering ----------------------------------------------
    def test_recreated_policy_with_same_id_but_other_scope_has_no_authority(self):
        action = self.h.make_create()
        reviewed = self.h.make_policy(action, scope=('file.txt',))
        issuer = self.h.FakeHostIssuer(reviewed)
        tampered = (self.h.make_policy(action),  # same id/version, widened scope
                    self.h.make_policy(action, scope=('file.txt', 'other.txt', 'third.txt')))
        sink = self.Sink()
        for policy in tampered:
            with self.subTest(scope=policy.scope):
                self.assertEqual((policy.policy_id, policy.version),
                                 (reviewed.policy_id, reviewed.version))
                self.assertNotEqual(policy.digest, reviewed.digest)
                decision = self.decide(action, policy, issuer=issuer)
                self.assertIs(decision.code, self.p.DecisionCode.UNTRUSTED_POLICY)
                self.assertNoAuthority(decision, sink)
        # Swapping the policy digest inside an issued receipt is detected.
        policy = self.h.make_policy(action)
        receipt = self.ledger.issue(self.decide(action, policy), ttl_seconds=60)
        swapped = replace(receipt, binding=replace(receipt.binding, policy_digest=reviewed.digest))
        result, sink = self.redeem(swapped, action)
        self.assertIs(result.code, self.l.RedeemCode.RECEIPT_TAMPERED)
        self.assertEqual(sink.calls, [])

    # -- (e) missing authority ----------------------------------------------
    def test_missing_or_wrong_authority_never_reaches_ledger_or_sink(self):
        action = self.h.make_create()
        policy = self.h.make_policy(action)
        cases = {
            'no issuer': (None, self.p.DecisionCode.NO_ISSUER),
            'issuer vouches nothing': (self.h.FakeHostIssuer(), self.p.DecisionCode.UNTRUSTED_POLICY),
            'owner P0 issuer': (self.h.FakeHostIssuer(policy, issuer_class=self.p.IssuerClass.OWNER_P0),
                                self.p.DecisionCode.UNTRUSTED_ISSUER),
        }
        sink = self.Sink()
        for label, (issuer, code) in cases.items():
            with self.subTest(label=label):
                decision = self.decide(action, policy, issuer=issuer)
                self.assertIs(decision.code, code)
                self.assertNoAuthority(decision, sink)
        never_issued = self.l.Receipt(self.h.allow(action).binding, 1000, 1060, 'e'*64)
        result, sink = self.redeem(never_issued, action)
        self.assertIs(result.code, self.l.RedeemCode.RECEIPT_UNKNOWN)
        self.assertEqual(sink.calls, [])
        self.assertEqual(self.receipt_rows(), [])

    # -- (f) non-TTY owner P0 -----------------------------------------------
    def test_non_tty_owner_p0_waits_and_piped_yes_cannot_approve(self):
        action = self.h.make_create()
        sink = self.Sink()
        piped = io.StringIO('yes\ny\nAPPROVE\n')
        with patch('sys.stdin', piped), \
                patch('builtins.input', side_effect=AssertionError('decide must not prompt')):
            self.assertFalse(sys.stdin.isatty())
            for requirement in sorted(self.p.OWNER_P0, key=lambda r: r.value):
                for interactive in (False, True):
                    with self.subTest(requirement=requirement.value, interactive=interactive):
                        decision = self.decide(action, requirements=frozenset((requirement,)),
                                               interactive=interactive)
                        self.assertIs(decision.decision, self.p.Decision.WAITING_APPROVAL)
                        self.assertIs(decision.code, self.p.DecisionCode.OWNER_P0_REQUIRED
                                      if interactive else self.p.DecisionCode.OWNER_P0_NON_INTERACTIVE)
                        self.assertEqual(decision.status, 'NEEDS_APPROVAL')
                        self.assertNoAuthority(decision, sink)
        self.assertEqual(piped.read(), 'yes\ny\nAPPROVE\n')  # stdin was never consumed
        for requirement in sorted(self.p.SEPARATE_AUTHORITY, key=lambda r: r.value):
            with self.subTest(separate=requirement.value):
                decision = self.decide(action, requirements=frozenset((requirement,)))
                self.assertIs(decision.code, self.p.DecisionCode.SEPARATE_AUTHORIZATION_REQUIRED)
                self.assertNoAuthority(decision, sink)
        self.assertEqual(self.receipt_rows(), [])

    # -- (g) replay after restart -------------------------------------------
    def test_replay_after_restart_and_after_crash_never_repeats_effect(self):
        action = self.h.make_create()
        receipt = self.ledger.issue(self.decide(action), ttl_seconds=60)
        result, sink = self.redeem(receipt, action)
        self.assertIs(result.code, self.l.RedeemCode.FAKE_EFFECT_DONE)
        self.assertEqual(sink.calls, [receipt.binding])
        # The same decision cannot mint a second receipt (one per binding).
        with self.assertRaises(self.l.LedgerError) as caught:
            self.ledger.issue(self.decide(action), ttl_seconds=60)
        self.assertIs(caught.exception.code, self.l.LedgerCode.NOT_ALLOWED)
        crash_action = replace(action, action_id='action-crash')
        crashed = self.ledger.issue(self.decide(crash_action), ttl_seconds=60)
        with self.assertRaises(SystemExit):  # in-process stand-in for a crash after consume
            self.redeem(crashed, crash_action, sink=self.Sink(error=SystemExit(17)))
        self.ledger.close()
        raw = json.loads(receipt.binding.to_json())
        raw['action_paths'] = tuple(raw['action_paths'])
        # The handle a caller persisted before the restart, rebuilt from its data.
        rebuilt = self.l.Receipt(self.p.ReceiptBinding(**raw), receipt.issued_at,
                                 receipt.expires_at, receipt.nonce)
        with self.l.ReceiptLedger.open(self.ledger_path, clock=self.clock) as restarted:
            # The crash may already be recorded as unknown; recover() is idempotent.
            self.assertIn(restarted.recover(), (0, 1))
            self.assertEqual(restarted.recover(), 0)
            self.assertEqual(restarted.status(receipt.nonce), 'EFFECT_DONE')
            self.assertEqual(restarted.status(crashed.nonce), 'EFFECT_UNKNOWN')
            for handle, bound in ((receipt, action), (crashed, crash_action), (rebuilt, action)):
                again, sink = self.redeem(handle, bound, ledger=restarted)
                self.assertIs(again.code, self.l.RedeemCode.REPLAY)
                self.assertFalse(again.effect_done)
                self.assertEqual(sink.calls, [])
        self.assertEqual(self.receipt_rows(), [('EFFECT_DONE',), ('EFFECT_UNKNOWN',)])

    # -- (h) zero real repository mutation ----------------------------------
    @staticmethod
    def _snapshot(base):
        state = {}
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for name in sorted(dirnames + filenames):
                path = os.path.join(dirpath, name)
                st = os.lstat(path)
                content = None
                if os.path.isfile(path) and not os.path.islink(path):
                    with open(path, 'rb') as handle:
                        content = handle.read()
                state[os.path.relpath(path, base)] = (st.st_mode, st.st_ino, st.st_size,
                                                      st.st_mtime_ns, content)
        st = os.lstat(base)
        state['.'] = (st.st_mode, st.st_ino, st.st_mtime_ns)
        return state

    @staticmethod
    def _git_status():
        env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
        try:
            done = subprocess.run(['git', '-C', str(_CANONICAL_REPO), 'status', '--porcelain=v1',
                                   '--untracked-files=all'], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    def test_full_allowed_journey_mutates_no_repository_and_only_writes_ledger(self):
        git_before = self._git_status()
        if git_before is None:
            self.skipTest('canonical repository git status unavailable')
        repo = self.root / 'repo'
        (repo / 'src' / 'pkg').mkdir(parents=True)
        (repo / '.git').mkdir()
        files = {'README.md': b'synthetic\n', 'src/pkg/module.py': b'X = 1\n',
                 'other.txt': b'keep me\n', '.git/HEAD': b'ref: refs/heads/main\n'}
        for name, data in files.items():
            (repo / name).write_bytes(data)
        os.utime(repo / 'README.md', ns=(1_000_000_000, 1_000_000_000))
        canonical = os.path.realpath(str(repo))
        st = os.stat(canonical)
        base = self.h.make_create()
        binding = c.RepositoryBinding('3'*64, canonical, st.st_dev, st.st_ino, '4'*40)
        action = replace(base, repository=binding)
        policy = self.h.make_policy(action)
        observed = self.h.make_observed(action, parent_device=st.st_dev, parent_inode=st.st_ino)
        self.assertFalse((repo / action.payload.target.path).exists())
        repo_before = self._snapshot(canonical)
        root_before = sorted(os.listdir(self.root))
        tripwire = AssertionError('D1 journey must not spawn processes or open sockets')
        with patch('subprocess.Popen', side_effect=tripwire), \
                patch('socket.socket', side_effect=tripwire), \
                patch('os.system', side_effect=tripwire):
            decision = self.p.decide(action, policy, issuer=self.h.FakeHostIssuer(policy),
                                     observed=observed, redeem_run_id=self.h.REDEEM_RUN)
            self.assertIs(decision.decision, self.p.Decision.ALLOW_WITH_TRUSTED_RECEIPT)
            self.assertEqual(decision.status, 'FAKE_POLICY_ALLOWED')
            receipt = self.ledger.issue(decision, ttl_seconds=60)
            sink = self.Sink()
            result = self.ledger.redeem(receipt, redeem_run_id=self.h.REDEEM_RUN,
                                        observed=observed, sink=sink)
        self.assertIs(result.code, self.l.RedeemCode.FAKE_EFFECT_DONE)
        self.assertEqual(sink.calls, [decision.binding])
        self.assertEqual(sink.calls[0].canonical_root, canonical)
        self.assertEqual(self._snapshot(canonical), repo_before)
        self.assertFalse((repo / action.payload.target.path).exists())
        self.assertEqual(sorted(os.listdir(self.root)), root_before)
        self.assertEqual(sorted(os.listdir(self.ledger_dir)), ['receipts.sqlite3'])
        ledger_stat = os.lstat(self.ledger_path)
        self.assertEqual(ledger_stat.st_mode & 0o777, 0o600)
        self.assertEqual(os.lstat(self.ledger_dir).st_mode & 0o777, 0o700)
        self.assertFalse(str(self.ledger_path).startswith(canonical + os.sep))
        self.assertEqual(self._git_status(), git_before)
