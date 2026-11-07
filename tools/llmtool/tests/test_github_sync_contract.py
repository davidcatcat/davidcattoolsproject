"""Tests for the pure GitHubSync message contract (#1144 WP5)."""

import dataclasses
import hashlib
import json
import unittest
from unittest import mock

from llmtool import github_identity
from llmtool import github_sync_contract as sync
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError


OWNER = github_identity.owner_login()
AI = github_identity.ai_login()
ISSUE = 1144
COMMENT_ID = 4242
STAMP = '2026-11-07T01:02:03Z'
LATER = '2026-11-07T01:09:00Z'
TASK_ID = '0123456789abcdef0123456789abcdef'
REQUEST_ID = 'wp5-sync-0001'
_MISSING = object()


def envelope(kind='DISCUSS', **overrides):
    value = {'schema_version': 1, 'kind': kind, 'request_id': REQUEST_ID,
             'revision': 1, 'title': 'Sync title', 'summary': 'Short summary.'}
    if kind == 'EXECUTE_REQUEST':
        value['scope'] = ['tools/llmtool/llmtool/github_sync_contract.py']
    if kind == 'STOP_REQUEST':
        value['task_id'] = TASK_ID
    for key, item in overrides.items():
        if item is _MISSING:
            value.pop(key, None)
        else:
            value[key] = item
    return value


def ack_envelope(**overrides):
    value = {'schema_version': 1, 'kind': 'ACK', 'request_id': REQUEST_ID,
             'revision': 1, 'status': 'RECEIVED'}
    for key, item in overrides.items():
        if item is _MISSING:
            value.pop(key, None)
        else:
            value[key] = item
    return value


def block(payload, info='llmtool-sync', fence='```'):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return f'{fence}{info}\n{text}\n{fence}'


def comment(body, login=OWNER, comment_id=COMMENT_ID, created=STAMP, updated=_MISSING):
    return {
        'id': comment_id,
        'body': body,
        'user': {'login': login},
        'author_association': 'OWNER',
        'created_at': created,
        'updated_at': created if updated is _MISSING else updated,
        'html_url': ('https://github.com/davidcatcat/davidcattoolsproject/issues/'
                     f'{ISSUE}#issuecomment-{COMMENT_ID}'),
    }


def parse(payload, issue=ISSUE, **kwargs):
    body = 'Sync message.\n\n' + block(payload) + '\n'
    return sync.parse_comment(comment(body, **kwargs), issue)


def sensitive_sample():
    """Return a value the real record-pm screen flags, built at run time."""
    candidates = (
        'gh' + 'p_' + ''.join(('Zq7Xw2', 'Lm9Rt4', 'Vb6Np3', 'Hs8Kd1', 'Jf5Gc0', 'Ty2Ue7')),
        'AK' + 'IA' + ''.join(('QX7Z', 'W2LM', '9RT4', 'VB6N')),
        '-----BEGIN ' + 'PRIVATE KEY-----',
        'sk' + '-' + ''.join(('Zq7Xw2Lm9R', 't4Vb6Np3Hs', '8Kd1Jf5Gc0', 'Ty2Ue7Pa')),
    )
    for candidate in candidates:
        try:
            task_submission_cli._screen_text('Summary ' + candidate, 16 * 1024)
        except LLMToolError:
            return candidate
    raise AssertionError('no sample is recognised by the record-pm screen')


def expected_delivery_id(issue, comment_id, request_id, revision):
    text = '\0'.join(('llmtool-github-sync-delivery-v1', str(issue), str(comment_id),
                      request_id, str(revision)))
    return hashlib.sha256(text.encode('ascii')).hexdigest()


class ParseKindsTest(unittest.TestCase):

    def test_discuss(self):
        message = parse(envelope('DISCUSS'))
        self.assertIsInstance(message, sync.SyncMessage)
        self.assertEqual(message.kind, 'DISCUSS')
        self.assertEqual(message.request_id, REQUEST_ID)
        self.assertEqual(message.revision, 1)
        self.assertEqual(message.issuer_login, OWNER)
        self.assertEqual(message.issue_number, ISSUE)
        self.assertEqual(message.comment_id, COMMENT_ID)
        self.assertEqual(message.title, 'Sync title')
        self.assertEqual(message.summary, 'Short summary.')
        self.assertIsNone(message.scope)
        self.assertIsNone(message.task_id)
        self.assertIsNone(message.status)
        self.assertIsNone(message.reason_code)

    def test_record(self):
        message = parse(envelope('RECORD'))
        self.assertEqual(message.kind, 'RECORD')
        self.assertIsNone(message.scope)
        self.assertIsNone(message.task_id)

    def test_execute_request(self):
        message = parse(envelope('EXECUTE_REQUEST', scope=['a.py', 'docs/b.md']))
        self.assertEqual(message.kind, 'EXECUTE_REQUEST')
        self.assertEqual(message.scope, ('a.py', 'docs/b.md'))
        self.assertIsNone(message.task_id)

    def test_stop_request(self):
        message = parse(envelope('STOP_REQUEST'))
        self.assertEqual(message.kind, 'STOP_REQUEST')
        self.assertEqual(message.task_id, TASK_ID)
        self.assertIsNone(message.scope)

    def test_both_trusted_logins(self):
        for login in (OWNER, AI):
            with self.subTest(login=login):
                message = parse(envelope(), login=login)
                self.assertIsInstance(message, sync.SyncMessage)
                self.assertEqual(message.issuer_login, login)
                self.assertEqual(message.request_key, f'{login}:{REQUEST_ID}')

    def test_delivery_id(self):
        self.assertEqual(sync.DELIVERY_DOMAIN, 'llmtool-github-sync-delivery-v1')
        message = parse(envelope(revision=7))
        self.assertEqual(message.delivery_id,
                         expected_delivery_id(ISSUE, COMMENT_ID, REQUEST_ID, 7))
        others = {
            parse(envelope(revision=8)).delivery_id,
            parse(envelope(revision=7), comment_id=COMMENT_ID + 1).delivery_id,
            parse(envelope(revision=7), issue=ISSUE + 1).delivery_id,
            parse(envelope(revision=7, request_id='wp5-sync-0002')).delivery_id,
        }
        self.assertEqual(len(others), 4)
        self.assertNotIn(message.delivery_id, others)

    def test_request_key_is_independent_of_revision_and_comment(self):
        first = parse(envelope(revision=1))
        second = parse(envelope(revision=2), comment_id=COMMENT_ID + 1)
        self.assertEqual(first.request_key, second.request_key)
        self.assertNotEqual(first.delivery_id, second.delivery_id)

    def test_result_is_frozen(self):
        message = parse(envelope())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            message.kind = 'EXECUTE_REQUEST'

    def test_boundary_values_accepted(self):
        cases = (
            envelope(revision=1000),
            envelope(request_id='wp5-' + 'x' * 60),
            envelope(request_id='0' * 8),
            envelope(title='T' * 200),
            envelope(summary='s' * 4000),
            envelope(summary='Line one.\n\tLine two.\r\nLine three.'),
            envelope('EXECUTE_REQUEST', scope=[f'dir/file{n}.py' for n in range(50)]),
            envelope('EXECUTE_REQUEST', scope=['a/' + 'b' * 254]),
            envelope('EXECUTE_REQUEST', scope=['./a.py', 'a/./b', 'a.b/c.d']),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertIsInstance(parse(payload), sync.SyncMessage)

    def test_tilde_fence_and_crlf(self):
        body = 'Intro\n\n' + block(envelope(), fence='~~~') + '\n'
        self.assertIsInstance(sync.parse_comment(comment(body), ISSUE), sync.SyncMessage)
        crlf = ('Intro\n\n' + block(envelope()) + '\n').replace('\n', '\r\n')
        self.assertIsInstance(sync.parse_comment(comment(crlf), ISSUE), sync.SyncMessage)

    def test_indented_fence_and_longer_close(self):
        body = '  ```llmtool-sync\n' + json.dumps(envelope()) + '\n  `````\n'
        self.assertIsInstance(sync.parse_comment(comment(body), ISSUE), sync.SyncMessage)

    def test_other_code_blocks_are_allowed(self):
        body = ('```python\nprint("```llmtool-sync")\n```\n\n' + block(envelope()) +
                '\n\n```json\n{}\n```\n')
        message = sync.parse_comment(comment(body), ISSUE)
        self.assertIsInstance(message, sync.SyncMessage)


class IgnoredTest(unittest.TestCase):

    def assertIgnored(self, result):
        self.assertEqual(result, sync.SyncRefusal(sync.NOT_A_SYNC_MESSAGE))
        self.assertTrue(result.ignored)

    def test_non_sync_comments_are_ignored(self):
        payload = json.dumps(envelope())
        bodies = (
            '',
            'LGTM',
            payload,
            block(envelope(), info='json'),
            block(envelope(), info=''),
            block(envelope(), info='llmtool-sync extra'),
            block(envelope(), info='LLMTOOL-SYNC'),
            block(envelope(), info='llmtool-syncs'),
            '> ```llmtool-sync\n> ' + payload + '\n> ```',
            '    ```llmtool-sync\n    ' + payload + '\n    ```',
            '````markdown\n' + block(envelope()) + '\n````',
            '``llmtool-sync\n' + payload + '\n``',
            '```llmtool-sync`\n' + payload + '\n```',
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIgnored(sync.parse_comment(comment(body), ISSUE))

    def test_non_string_body_is_ignored(self):
        for body in (None, 5, ['x']):
            with self.subTest(body=body):
                self.assertIgnored(sync.parse_comment(comment(body), ISSUE))

    def test_untrusted_non_sync_comment_is_ignored_not_refused(self):
        result = sync.parse_comment(comment('Hello', login='someone-else'), ISSUE)
        self.assertIgnored(result)


class RefusalTest(unittest.TestCase):

    def assertRefused(self, result, code):
        self.assertEqual(result, sync.SyncRefusal(code))
        self.assertFalse(result.ignored)

    def test_refusal_codes_are_fixed(self):
        self.assertEqual(sync.REFUSAL_CODES, frozenset({
            'SYNC_MULTIPLE_ENVELOPES', 'SYNC_ENVELOPE_TOO_LARGE', 'SYNC_JSON_INVALID',
            'SYNC_SCHEMA_UNSUPPORTED', 'SYNC_KIND_UNKNOWN', 'SYNC_FIELD_MISSING',
            'SYNC_FIELD_UNKNOWN', 'SYNC_FIELD_INVALID', 'SYNC_SENSITIVE_CONTENT',
            'SYNC_ISSUER_UNTRUSTED', 'SYNC_EDITED'}))
        self.assertEqual(sync.NOT_A_SYNC_MESSAGE, 'NOT_A_SYNC_MESSAGE')
        with self.assertRaises(ValueError):
            sync.SyncRefusal('SOMETHING_ELSE')

    def test_refusal_carries_only_the_code(self):
        self.assertEqual([field.name for field in dataclasses.fields(sync.SyncRefusal)],
                         ['code'])
        marker = 'echo-marker-7f3a9c'
        results = (
            parse(envelope(title=marker + '\n')),
            parse(envelope(request_id=marker.upper())),
            parse(envelope(**{marker: True})),
            parse(envelope(kind=marker)),
            parse(envelope(), login=marker),
        )
        for result in results:
            with self.subTest(result=result):
                self.assertIsInstance(result, sync.SyncRefusal)
                self.assertNotIn(marker, repr(result).lower())
                self.assertNotIn(marker, str(result).lower())

    def test_multiple_envelopes(self):
        bodies = (
            block(envelope()) + '\n\n' + block(envelope(request_id='wp5-sync-0002')),
            block(envelope()) + '\n' + block(envelope()),
            block(envelope()) + '\n\n```llmtool-sync\n{}',
            block(envelope()) + '\n\n' + block(envelope(), fence='~~~'),
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertRefused(sync.parse_comment(comment(body), ISSUE),
                                   sync.SYNC_MULTIPLE_ENVELOPES)

    def test_envelope_too_large(self):
        payload = json.dumps(envelope())
        exact = payload + ' ' * (sync.MAX_ENVELOPE_BYTES - len(payload.encode('utf-8')))
        self.assertEqual(len(exact.encode('utf-8')), 8192)
        self.assertIsInstance(sync.parse_comment(comment(block(exact)), ISSUE),
                              sync.SyncMessage)
        self.assertRefused(sync.parse_comment(comment(block(exact + ' ')), ISSUE),
                           sync.SYNC_ENVELOPE_TOO_LARGE)
        wide = json.dumps(envelope(summary='\u4e2d' * 3000), ensure_ascii=False)
        self.assertGreater(len(wide.encode('utf-8')), sync.MAX_ENVELOPE_BYTES)
        self.assertRefused(sync.parse_comment(comment(block(wide)), ISSUE),
                           sync.SYNC_ENVELOPE_TOO_LARGE)
        unclosed = '```llmtool-sync\n' + 'x' * 9000
        self.assertRefused(sync.parse_comment(comment(unclosed), ISSUE),
                           sync.SYNC_ENVELOPE_TOO_LARGE)

    def test_json_invalid(self):
        valid = json.dumps(envelope())
        texts = (
            '',
            'not json',
            '[]',
            '"text"',
            '1',
            'null',
            valid[:-1],
            valid + ' {}',
            '{"schema_version": 1, "schema_version": 1, "kind": "DISCUSS"}',
            '{"schema_version": NaN}',
            '{"schema_version": Infinity}',
            '{"schema_version": -Infinity}',
            '{"a": {"b": 1, "b": 2}}',
            '[' * 4000 + ']' * 4000,
        )
        for text in texts:
            with self.subTest(text=text[:40]):
                self.assertRefused(sync.parse_comment(comment(block(text)), ISSUE),
                                   sync.SYNC_JSON_INVALID)

    def test_unclosed_block_is_json_invalid(self):
        body = 'Intro\n\n```llmtool-sync\n' + json.dumps(envelope())
        self.assertRefused(sync.parse_comment(comment(body), ISSUE), sync.SYNC_JSON_INVALID)

    def test_lone_surrogate_is_json_invalid(self):
        body = '```llmtool-sync\n{"schema_version": 1, "title": "\ud800"}\n```'
        self.assertRefused(sync.parse_comment(comment(body), ISSUE), sync.SYNC_JSON_INVALID)

    def test_schema_unsupported(self):
        for version in (2, 0, -1, '1', True, 1.0, None, [1]):
            with self.subTest(version=version):
                self.assertRefused(parse(envelope(schema_version=version)),
                                   sync.SYNC_SCHEMA_UNSUPPORTED)

    def test_schema_missing(self):
        self.assertRefused(parse(envelope(schema_version=_MISSING)), sync.SYNC_FIELD_MISSING)

    def test_kind_unknown(self):
        for kind in ('EXECUTE', 'discuss', 'APPROVE', 'GRANT', '', 5, None, [], {}):
            with self.subTest(kind=kind):
                self.assertRefused(parse(envelope(kind=kind)), sync.SYNC_KIND_UNKNOWN)

    def test_kind_missing(self):
        self.assertRefused(parse(envelope(kind=_MISSING)), sync.SYNC_FIELD_MISSING)

    def test_field_unknown(self):
        cases = (
            envelope(approved=True),
            envelope(authority='OWNER'),
            envelope('DISCUSS', scope=['a.py']),
            envelope('RECORD', task_id=TASK_ID),
            envelope('STOP_REQUEST', scope=['a.py']),
            envelope('EXECUTE_REQUEST', task_id=TASK_ID),
            envelope(status='DONE'),
            ack_envelope(summary='Free text.'),
            ack_envelope(title='Free text.'),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertRefused(parse(payload), sync.SYNC_FIELD_UNKNOWN)

    def test_field_missing(self):
        cases = (
            envelope(request_id=_MISSING),
            envelope(revision=_MISSING),
            envelope(title=_MISSING),
            envelope(summary=_MISSING),
            envelope('EXECUTE_REQUEST', scope=_MISSING),
            envelope('STOP_REQUEST', task_id=_MISSING),
            ack_envelope(status=_MISSING),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertRefused(parse(payload), sync.SYNC_FIELD_MISSING)

    def test_field_invalid(self):
        cases = (
            envelope(request_id='short'),
            envelope(request_id='a' * 7),
            envelope(request_id='a' * 65),
            envelope(request_id='Wp5-sync-0001'),
            envelope(request_id='-wp5-sync-001'),
            envelope(request_id='wp5_sync_0001'),
            envelope(request_id='wp5-sync-0001\n'),
            envelope(request_id=12345678),
            envelope(request_id=None),
            envelope(revision=0),
            envelope(revision=1001),
            envelope(revision=-1),
            envelope(revision=True),
            envelope(revision=False),
            envelope(revision='1'),
            envelope(revision=1.0),
            envelope(revision=None),
            envelope(title=''),
            envelope(title='   '),
            envelope(title='T' * 201),
            envelope(title='a\nb'),
            envelope(title='a\tb'),
            envelope(title='a\u2028b'),
            envelope(title='a\u200bb'),
            envelope(title=5),
            envelope(title=None),
            envelope(summary=''),
            envelope(summary='s' * 4001),
            envelope(summary='a\x07b'),
            envelope(summary='a\x00b'),
            envelope(summary='a\x85b'),
            envelope(summary='a\u202eb'),
            envelope(summary=['x']),
            envelope(summary=None),
            envelope('STOP_REQUEST', task_id=TASK_ID.upper()),
            envelope('STOP_REQUEST', task_id=TASK_ID[:-1]),
            envelope('STOP_REQUEST', task_id=TASK_ID + '0'),
            envelope('STOP_REQUEST', task_id='g' * 32),
            envelope('STOP_REQUEST', task_id=123),
            envelope('STOP_REQUEST', task_id=None),
            ack_envelope(status='ACCEPTED'),
            ack_envelope(status='done'),
            ack_envelope(status=None),
            ack_envelope(task_id='x'),
            ack_envelope(task_id=None),
            ack_envelope(reason_code='free text'),
            ack_envelope(reason_code=None),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertRefused(parse(payload), sync.SYNC_FIELD_INVALID)

    def test_scope_invalid(self):
        scopes = (
            [],
            [f'f{n}.py' for n in range(51)],
            'a.py',
            None,
            {'a.py': True},
            [1],
            [None],
            [['a.py']],
            ['/etc/hosts'],
            ['/'],
            [''],
            ['   '],
            ['..'],
            ['../a.py'],
            ['a/../b'],
            ['a/..'],
            ['a..b'],
            ['a\\b.py'],
            ['C:/x.py'],
            ['c:x.py'],
            ['a\0b'],
            ['a\nb'],
            ['a\u200bb'],
            ['a' * 257],
            ['\u00e9' * 129],
            ['ok.py', '/abs.py'],
        )
        for scope in scopes:
            with self.subTest(scope=scope):
                self.assertRefused(parse(envelope('EXECUTE_REQUEST', scope=scope)),
                                   sync.SYNC_FIELD_INVALID)

    def test_sensitive_summary_with_real_screen(self):
        sample = sensitive_sample()
        for kind in sorted(sync.INBOUND_KINDS):
            with self.subTest(kind=kind):
                result = parse(envelope(kind, summary='Summary ' + sample))
                self.assertRefused(result, sync.SYNC_SENSITIVE_CONTENT)
                self.assertNotIn(sample, repr(result))

    def test_sensitive_fields_are_screened(self):
        def fake_screen(value, limit):
            if 'flagged' in value:
                raise LLMToolError('flagged')
            return value

        cases = (
            envelope(title='Title flagged'),
            envelope(summary='Summary flagged'),
            envelope(request_id='flagged-0001'),
            envelope('EXECUTE_REQUEST', scope=['ok.py', 'docs/flagged.md']),
        )
        with mock.patch.object(sync, '_screen_text', fake_screen):
            self.assertIsInstance(parse(envelope()), sync.SyncMessage)
            for payload in cases:
                with self.subTest(payload=payload):
                    self.assertRefused(parse(payload), sync.SYNC_SENSITIVE_CONTENT)

    def test_screen_failure_fails_closed(self):
        def broken_screen(value, limit):
            raise RuntimeError('screen unavailable')

        with mock.patch.object(sync, '_screen_text', broken_screen):
            self.assertRefused(parse(envelope()), sync.SYNC_SENSITIVE_CONTENT)

    def test_structural_errors_win_over_sensitive(self):
        sample = sensitive_sample()
        self.assertRefused(parse(envelope(title='a\nb', summary=sample)),
                           sync.SYNC_FIELD_INVALID)

    def test_untrusted_issuer(self):
        logins = ('someone-else', OWNER.upper(), AI.upper(), OWNER + '[bot]',
                  ' ' + OWNER, '', None, 5)
        for login in logins:
            with self.subTest(login=login):
                self.assertRefused(parse(envelope(), login=login),
                                   sync.SYNC_ISSUER_UNTRUSTED)

    def test_missing_user_is_untrusted(self):
        for user in (_MISSING, None, 'davidcatcat', {}):
            with self.subTest(user=user):
                value = comment(block(envelope()))
                if user is _MISSING:
                    del value['user']
                else:
                    value['user'] = user
                self.assertRefused(sync.parse_comment(value, ISSUE),
                                   sync.SYNC_ISSUER_UNTRUSTED)

    def test_untrusted_content_is_not_parsed(self):
        self.assertRefused(sync.parse_comment(
            comment(block('not json'), login='someone-else'), ISSUE),
            sync.SYNC_ISSUER_UNTRUSTED)

    def test_edited_comment(self):
        for login in (OWNER, AI):
            with self.subTest(login=login):
                self.assertRefused(parse(envelope(), login=login, updated=LATER),
                                   sync.SYNC_EDITED)
        self.assertRefused(parse(envelope(revision=2), created=LATER, updated=STAMP),
                           sync.SYNC_EDITED)

    def test_edited_is_checked_before_content(self):
        body = block(envelope()) + '\n' + block(envelope())
        self.assertRefused(sync.parse_comment(comment(body, updated=LATER), ISSUE),
                           sync.SYNC_EDITED)

    def test_comment_metadata_invalid(self):
        for comment_id in (0, -1, True, '4242', None, 2 ** 63, 1.0):
            with self.subTest(comment_id=comment_id):
                self.assertRefused(parse(envelope(), comment_id=comment_id),
                                   sync.SYNC_FIELD_INVALID)
        for stamp in (None, '', 'yesterday', '2026-11-07 01:02:03Z', STAMP + '\n',
                      '\u0662026-11-07T01:02:03Z', 1):
            with self.subTest(stamp=stamp):
                self.assertRefused(parse(envelope(), created=stamp, updated=stamp),
                                   sync.SYNC_FIELD_INVALID)
        for key in ('created_at', 'updated_at', 'id'):
            with self.subTest(key=key):
                value = comment(block(envelope()))
                del value[key]
                self.assertRefused(sync.parse_comment(value, ISSUE),
                                   sync.SYNC_FIELD_INVALID)


class CallerArgumentsTest(unittest.TestCase):

    def test_invalid_issue_number_raises(self):
        for issue in (0, -1, True, '1144', 10 ** 7, None, 1144.0):
            with self.subTest(issue=issue):
                with self.assertRaises(sync.GitHubSyncError) as caught:
                    sync.parse_comment(comment(block(envelope())), issue)
                self.assertEqual(caught.exception.code, sync.SYNC_FIELD_INVALID)
                self.assertIsInstance(caught.exception, LLMToolError)

    def test_non_dict_comment_raises(self):
        for value in (None, [], 'body', 5):
            with self.subTest(value=value):
                with self.assertRaises(sync.GitHubSyncError) as caught:
                    sync.parse_comment(value, ISSUE)
                self.assertEqual(caught.exception.code, sync.SYNC_FIELD_INVALID)


class AuthorityTest(unittest.TestCase):

    def test_decisions_are_fixed(self):
        self.assertEqual(sync.AUTHORITY_DECISIONS, frozenset({
            'NO_ACTION', 'RECORD_ONLY', 'REQUEST_FOR_PM', 'STOP_REQUEST_FOR_PM'}))

    def test_each_kind(self):
        for login in (OWNER, AI):
            with self.subTest(login=login):
                discuss = sync.authority(parse(envelope('DISCUSS'), login=login))
                self.assertEqual(discuss.decision, sync.NO_ACTION)
                record = sync.authority(parse(envelope('RECORD'), login=login))
                self.assertEqual(record.decision, sync.RECORD_ONLY)
                execute_message = parse(envelope('EXECUTE_REQUEST'), login=login)
                execute = sync.authority(execute_message)
                self.assertEqual(execute.decision, sync.REQUEST_FOR_PM)
                self.assertEqual(execute.reason, 'EXECUTE_AUTHORITY_NOT_ESTABLISHED')
                self.assertEqual(execute.request_key, execute_message.request_key)
                self.assertEqual(execute.delivery_id, execute_message.delivery_id)
                self.assertIsNone(execute.task_id)
                stop = sync.authority(parse(envelope('STOP_REQUEST'), login=login))
                self.assertEqual(stop.decision, sync.STOP_REQUEST_FOR_PM)
                self.assertEqual(stop.task_id, TASK_ID)

    def test_decision_is_frozen(self):
        decision = sync.authority(parse(envelope('EXECUTE_REQUEST')))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            decision.decision = 'EXECUTE'

    def test_never_grants_execution(self):
        messages = [parse(envelope(kind), login=login)
                    for kind in sorted(sync.INBOUND_KINDS) for login in (OWNER, AI)]
        messages.append(sync.parse_comment(
            comment(sync.render_ack(REQUEST_ID, 1, 'DONE', task_id=TASK_ID), login=AI), ISSUE))
        execute = parse(envelope('EXECUTE_REQUEST'))
        inputs = messages + [
            sync.SyncRefusal(sync.NOT_A_SYNC_MESSAGE),
            sync.SyncRefusal(sync.SYNC_EDITED),
            None, {}, 'EXECUTE', envelope('EXECUTE_REQUEST'),
            dataclasses.replace(execute, kind='EXECUTE'),
            dataclasses.replace(execute, kind='GRANT'),
            dataclasses.replace(execute, issuer_login='davidcatcat'),
        ]
        for item in inputs:
            with self.subTest(item=item):
                decision = sync.authority(item)
                self.assertIsInstance(decision, sync.AuthorityDecision)
                self.assertIn(decision.decision, sync.AUTHORITY_DECISIONS)
                self.assertNotIn('GRANT', decision.decision)
                self.assertNotEqual(decision.decision, 'EXECUTE')
                if getattr(item, 'kind', None) == 'EXECUTE_REQUEST':
                    self.assertEqual(decision.decision, sync.REQUEST_FOR_PM)
                    self.assertEqual(decision.reason, 'EXECUTE_AUTHORITY_NOT_ESTABLISHED')

    def test_non_messages_get_no_action(self):
        for item in (None, {}, envelope('EXECUTE_REQUEST'),
                     sync.SyncRefusal(sync.SYNC_FIELD_INVALID)):
            with self.subTest(item=item):
                self.assertEqual(sync.authority(item).decision, sync.NO_ACTION)

    def test_forged_stop_without_valid_task_id_gets_no_action(self):
        stop = parse(envelope('STOP_REQUEST'))
        for task_id in (None, 'x', TASK_ID.upper()):
            with self.subTest(task_id=task_id):
                forged = dataclasses.replace(stop, task_id=task_id)
                self.assertEqual(sync.authority(forged).decision, sync.NO_ACTION)


class RenderAckTest(unittest.TestCase):

    def test_statuses_are_fixed(self):
        self.assertEqual(sync.ACK_STATUSES, frozenset({
            'RECEIVED', 'REFUSED', 'LINKED', 'WAITING_OWNER', 'BLOCKED', 'DONE'}))

    def test_round_trip_every_status(self):
        for status in sorted(sync.ACK_STATUSES):
            for login in (AI, OWNER):
                with self.subTest(status=status, login=login):
                    body = sync.render_ack(REQUEST_ID, 3, status, task_id=TASK_ID,
                                           reason_code='SYNC_EDITED')
                    message = sync.parse_comment(comment(body, login=login), ISSUE)
                    self.assertIsInstance(message, sync.SyncMessage)
                    self.assertEqual(message.kind, 'ACK')
                    self.assertEqual(message.request_id, REQUEST_ID)
                    self.assertEqual(message.revision, 3)
                    self.assertEqual(message.status, status)
                    self.assertEqual(message.task_id, TASK_ID)
                    self.assertEqual(message.reason_code, 'SYNC_EDITED')
                    self.assertIsNone(message.title)
                    self.assertIsNone(message.summary)
                    self.assertIsNone(message.scope)
                    self.assertEqual(sync.authority(message).decision, sync.NO_ACTION)

    def test_round_trip_without_optional_fields(self):
        body = sync.render_ack(REQUEST_ID, 1, 'RECEIVED')
        message = sync.parse_comment(comment(body, login=AI), ISSUE)
        self.assertEqual(message.kind, 'ACK')
        self.assertEqual(message.status, 'RECEIVED')
        self.assertIsNone(message.task_id)
        self.assertIsNone(message.reason_code)

    def test_body_holds_one_block_with_fixed_fields(self):
        body = sync.render_ack(REQUEST_ID, 2, 'REFUSED', reason_code='SYNC_FIELD_INVALID')
        lines = body.splitlines()
        self.assertEqual(lines.count('```llmtool-sync'), 1)
        start = lines.index('```llmtool-sync')
        end = lines.index('```', start + 1)
        payload = json.loads('\n'.join(lines[start + 1:end]))
        self.assertEqual(payload, {'schema_version': 1, 'kind': 'ACK',
                                   'request_id': REQUEST_ID, 'revision': 2,
                                   'status': 'REFUSED',
                                   'reason_code': 'SYNC_FIELD_INVALID'})
        self.assertEqual(sync.render_ack(REQUEST_ID, 2, 'REFUSED',
                                         reason_code='SYNC_FIELD_INVALID'), body)

    def test_invalid_arguments_are_refused(self):
        cases = (
            ((REQUEST_ID, 1, 'ACCEPTED'), {}),
            ((REQUEST_ID, 1, 'received'), {}),
            ((REQUEST_ID, 1, None), {}),
            ((REQUEST_ID, 1, ['DONE']), {}),
            (('Bad request id', 1, 'DONE'), {}),
            (('short', 1, 'DONE'), {}),
            ((None, 1, 'DONE'), {}),
            ((REQUEST_ID, 0, 'DONE'), {}),
            ((REQUEST_ID, 1001, 'DONE'), {}),
            ((REQUEST_ID, True, 'DONE'), {}),
            ((REQUEST_ID, '1', 'DONE'), {}),
            ((REQUEST_ID, 1.0, 'DONE'), {}),
            ((REQUEST_ID, 1, 'LINKED'), {'task_id': TASK_ID.upper()}),
            ((REQUEST_ID, 1, 'LINKED'), {'task_id': ''}),
            ((REQUEST_ID, 1, 'LINKED'), {'task_id': 5}),
            ((REQUEST_ID, 1, 'REFUSED'), {'reason_code': 'free text'}),
            ((REQUEST_ID, 1, 'REFUSED'), {'reason_code': 'sync_edited'}),
            ((REQUEST_ID, 1, 'REFUSED'), {'reason_code': 'A' * 65}),
            ((REQUEST_ID, 1, 'REFUSED'), {'reason_code': ''}),
            ((REQUEST_ID, 1, 'REFUSED'), {'reason_code': 'SYNC_EDITED\n'}),
        )
        for args, kwargs in cases:
            with self.subTest(args=args, kwargs=kwargs):
                with self.assertRaises(sync.GitHubSyncError) as caught:
                    sync.render_ack(*args, **kwargs)
                self.assertEqual(caught.exception.code, sync.SYNC_FIELD_INVALID)

    def test_sensitive_request_id_is_refused(self):
        def fake_screen(value, limit):
            if 'flagged' in value:
                raise LLMToolError('flagged')
            return value

        with mock.patch.object(sync, '_screen_text', fake_screen):
            with self.assertRaises(sync.GitHubSyncError) as caught:
                sync.render_ack('flagged-0001', 1, 'DONE')
        self.assertEqual(caught.exception.code, sync.SYNC_SENSITIVE_CONTENT)


if __name__ == '__main__':
    unittest.main()
