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
        common = {'version','action_id','created_at','kind','task.task_id','task.run_id',
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
        self.reject('{"version":1,"version":1}')
        self.reject(make_action().to_json().replace('"mode":420', '"mode":420,"mode":420'))
        for parser, value in ((c.parse_approval, make_approval().to_dict()),
                              (c.parse_repository_state, make_action().expected_state.to_dict())):
            for key in value:
                raw = copy.deepcopy(value); del raw[key]
                self.reject(raw, parser)
            self.reject({**value, 'PRIVATE_unknown': True}, parser)

    def test_strict_types_no_coercion_or_permissive_future_version(self):
        action = make_action().to_dict()
        for field, values in (('version', (True, 0, 2, '1', 1.0, None)),
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
                       {'scope':('file.txt', object())}, {'version':True}):
            with self.assertRaises(LLMToolError):
                replace(action, **kwargs)
        with self.assertRaises(LLMToolError):
            replace(action.payload.target.state, file_type='REGULAR')
