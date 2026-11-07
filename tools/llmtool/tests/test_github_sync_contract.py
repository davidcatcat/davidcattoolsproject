"""Tests for the pure GitHubSync message contract (#1144 WP5)."""

import copy
import dataclasses
from datetime import date, datetime, timedelta, timezone
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
OTHER_TASK_ID = 'fedcba9876543210fedcba9876543210'
REQUEST_ID = 'wp5-sync-0001'
OWNER_REF = ('https://github.com/davidcatcat/davidcattoolsproject/issues/1144'
             '#issuecomment-6700000001')
NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
EXPIRES = '2026-11-08T00:00:00Z'
EXPIRES_AT = datetime(2026, 11, 8, 0, 0, 0, tzinfo=timezone.utc)
_MISSING = object()


def envelope(kind='DISCUSS', **overrides):
    value = {'schema_version': 1, 'kind': kind, 'request_id': REQUEST_ID,
             'revision': 1, 'title': 'Sync title', 'summary': 'Short summary.'}
    if kind == 'EXECUTE_REQUEST':
        value['scope'] = ['tools/llmtool/llmtool/github_sync_contract.py']
        value['operation'] = 'DEVELOP'
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


def authorization(**overrides):
    """Return a PM-built Owner authorization record that covers ``envelope``."""
    value = {'owner_ref': OWNER_REF, 'issue_number': ISSUE, 'task_id': None,
             'operations': ['DEVELOP'], 'scope': ['tools/llmtool/llmtool/'],
             'expires_at': EXPIRES, 'revoked': False}
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
        self.assertEqual(message.operation, 'DEVELOP')
        self.assertIsNone(message.task_id)

    def test_execute_request_operations(self):
        self.assertEqual(sync.OPERATIONS, frozenset({'DEVELOP', 'DOCS', 'TEST_ONLY'}))
        for operation in sorted(sync.OPERATIONS):
            with self.subTest(operation=operation):
                message = parse(envelope('EXECUTE_REQUEST', operation=operation))
                self.assertEqual(message.operation, operation)

    def test_execute_request_with_task_id(self):
        message = parse(envelope('EXECUTE_REQUEST', task_id=TASK_ID))
        self.assertEqual(message.task_id, TASK_ID)

    def test_non_execute_kinds_have_no_operation(self):
        for kind in ('DISCUSS', 'RECORD', 'STOP_REQUEST'):
            with self.subTest(kind=kind):
                self.assertIsNone(parse(envelope(kind)).operation)

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
        payload = envelope()
        del payload['kind']
        self.assertNotIn('kind', payload)
        self.assertRefused(parse(payload), sync.SYNC_FIELD_MISSING)

    def test_field_unknown(self):
        cases = (
            envelope(approved=True),
            envelope(authority='OWNER'),
            envelope('DISCUSS', scope=['a.py']),
            envelope('RECORD', task_id=TASK_ID),
            envelope('STOP_REQUEST', scope=['a.py']),
            envelope('DISCUSS', operation='DEVELOP'),
            envelope('RECORD', operation='DEVELOP'),
            envelope('STOP_REQUEST', operation='DEVELOP'),
            envelope('EXECUTE_REQUEST', operations=['DEVELOP']),
            envelope('EXECUTE_REQUEST', owner_ref=OWNER_REF),
            envelope(status='DONE'),
            ack_envelope(summary='Free text.'),
            ack_envelope(title='Free text.'),
            ack_envelope(operation='DEVELOP'),
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
            envelope('EXECUTE_REQUEST', operation=_MISSING),
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
            envelope('EXECUTE_REQUEST', operation='develop'),
            envelope('EXECUTE_REQUEST', operation='EXECUTE'),
            envelope('EXECUTE_REQUEST', operation='DEVELOP '),
            envelope('EXECUTE_REQUEST', operation=''),
            envelope('EXECUTE_REQUEST', operation=None),
            envelope('EXECUTE_REQUEST', operation=5),
            envelope('EXECUTE_REQUEST', operation=True),
            envelope('EXECUTE_REQUEST', operation=['DEVELOP']),
            envelope('EXECUTE_REQUEST', task_id=TASK_ID.upper()),
            envelope('EXECUTE_REQUEST', task_id=TASK_ID[:-1]),
            envelope('EXECUTE_REQUEST', task_id=None),
            envelope('EXECUTE_REQUEST', task_id=7),
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

    def test_sensitive_summary_is_refused(self):
        # Benign text stands in for credential material; the patched screen
        # flags only this summary, so no secret-shaped value is ever built.
        summary = 'Summary marked for the screen test.'

        def flag_summary(value, limit):
            if value == summary:
                raise LLMToolError('recognizable credential material')
            return value

        for kind in sorted(sync.INBOUND_KINDS):
            with self.subTest(kind=kind):
                with mock.patch.object(sync, '_screen_text',
                                       side_effect=flag_summary) as screen:
                    result = parse(envelope(kind, summary=summary))
                self.assertRefused(result, sync.SYNC_SENSITIVE_CONTENT)
                self.assertNotIn(summary, repr(result))
                self.assertNotIn(summary, str(result))
                screen.assert_any_call(summary, mock.ANY)

    def test_benign_execute_request_reaches_the_real_screen(self):
        scope = ['tools/llmtool/llmtool/github_sync_contract.py', 'docs/b.md']
        payload = envelope('EXECUTE_REQUEST', scope=scope)
        with mock.patch.object(sync, '_screen_text',
                               wraps=task_submission_cli._screen_text) as screen:
            message = parse(payload)
        self.assertIsInstance(message, sync.SyncMessage)
        self.assertEqual(message.scope, tuple(scope))
        screened = [call.args[0] for call in screen.call_args_list]
        self.assertEqual(screened, [REQUEST_ID, payload['title'], payload['summary'], *scope])

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
        screen = mock.Mock(side_effect=AssertionError('screen must not be called'))
        with mock.patch.object(sync, '_screen_text', screen):
            result = parse(envelope(title='a\nb', summary='Benign summary text.'))
        self.assertRefused(result, sync.SYNC_FIELD_INVALID)
        screen.assert_not_called()

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


def decide(message, auth=None, ledger_status='NEW', now=NOW):
    return sync.authority(message, auth, ledger_status=ledger_status, now=now)


def execute(login=OWNER, **overrides):
    return parse(envelope('EXECUTE_REQUEST', **overrides), login=login)


class AuthorityTest(unittest.TestCase):
    """Non-execute kinds, the result shape and caller arguments."""

    def assertDecision(self, decision, decision_class, reason_code):
        self.assertIsInstance(decision, sync.AuthorityDecision)
        self.assertEqual(decision.decision_class, decision_class)
        self.assertEqual(decision.reason_code, reason_code)
        if decision_class != sync.DISPATCH_NORMAL_FLOW:
            self.assertIsNone(decision.owner_ref)

    def test_classes_and_reasons_are_fixed(self):
        self.assertEqual(sync.DECISION_CLASSES, frozenset({
            'NO_ACTION', 'RECORD_ONLY', 'STOP_REQUEST_FOR_PM', 'NEVER_REDISPATCH',
            'WAITING_OWNER', 'REFUSED_OUT_OF_SCOPE', 'DISPATCH_NORMAL_FLOW'}))
        self.assertEqual(sync.AUTHORITY_REASONS, frozenset({
            'NOT_A_SYNC_MESSAGE', 'MESSAGE_INVALID', 'DISCUSS', 'RECORD', 'ACK',
            'STOP_REQUEST', 'DUPLICATE', 'NO_AUTHORIZATION', 'AUTHORIZATION_INVALID',
            'REVOKED', 'EXPIRED', 'ISSUE_MISMATCH', 'OPERATION_OUT_OF_SCOPE',
            'TASK_OUT_OF_SCOPE', 'PATH_OUT_OF_SCOPE', 'AUTHORIZED'}))
        self.assertEqual(sync.LEDGER_STATUSES, frozenset({'NEW', 'DUPLICATE'}))

    def test_non_execute_kinds(self):
        for login in (OWNER, AI):
            with self.subTest(login=login):
                discuss_message = parse(envelope('DISCUSS'), login=login)
                discuss = decide(discuss_message)
                self.assertDecision(discuss, sync.NO_ACTION, 'DISCUSS')
                self.assertEqual(discuss.request_key, discuss_message.request_key)
                self.assertEqual(discuss.delivery_id, discuss_message.delivery_id)
                self.assertDecision(decide(parse(envelope('RECORD'), login=login)),
                                    sync.RECORD_ONLY, 'RECORD')
                stop_message = parse(envelope('STOP_REQUEST'), login=login)
                stop = decide(stop_message)
                self.assertDecision(stop, sync.STOP_REQUEST_FOR_PM, 'STOP_REQUEST')
                self.assertEqual(stop.task_id, TASK_ID)
                self.assertEqual(stop.request_key, stop_message.request_key)
                self.assertEqual(stop.delivery_id, stop_message.delivery_id)
                ack = sync.parse_comment(comment(
                    sync.render_ack(REQUEST_ID, 1, 'DONE', task_id=TASK_ID), login=login), ISSUE)
                self.assertDecision(decide(ack), sync.NO_ACTION, 'ACK')

    def test_default_arguments(self):
        self.assertDecision(sync.authority(parse(envelope('RECORD'))),
                            sync.RECORD_ONLY, 'RECORD')
        self.assertDecision(sync.authority(execute()), sync.WAITING_OWNER,
                            'NO_AUTHORIZATION')

    def test_discuss_record_stop_ack_never_dispatch_with_any_authorization(self):
        broad = authorization(operations=sorted(sync.OPERATIONS), scope=['tools', 'docs', 'a'],
                              task_id=TASK_ID)
        ack = sync.parse_comment(comment(sync.render_ack(REQUEST_ID, 1, 'LINKED',
                                                         task_id=TASK_ID)), ISSUE)
        messages = [parse(envelope(kind), login=login)
                    for kind in ('DISCUSS', 'RECORD', 'STOP_REQUEST') for login in (OWNER, AI)]
        messages.append(ack)
        auths = (None, authorization(), broad, authorization(revoked=True), 'not a dict')
        for message in messages:
            for auth in auths:
                for ledger in ('NEW', 'DUPLICATE'):
                    with self.subTest(kind=message.kind, auth=auth, ledger=ledger):
                        decision = decide(message, auth, ledger)
                        self.assertNotEqual(decision.decision_class, sync.DISPATCH_NORMAL_FLOW)
                        self.assertIsNone(decision.owner_ref)
                        expected = {'DISCUSS': sync.NO_ACTION, 'RECORD': sync.RECORD_ONLY,
                                    'STOP_REQUEST': sync.STOP_REQUEST_FOR_PM,
                                    'ACK': sync.NO_ACTION}[message.kind]
                        self.assertEqual(decision.decision_class, expected)

    def test_non_messages_get_no_action(self):
        items = (None, {}, 'EXECUTE', envelope('EXECUTE_REQUEST'),
                 comment(block(envelope('EXECUTE_REQUEST'))),
                 sync.SyncRefusal(sync.NOT_A_SYNC_MESSAGE),
                 sync.SyncRefusal(sync.SYNC_FIELD_INVALID))
        for item in items:
            with self.subTest(item=item):
                self.assertDecision(decide(item, authorization()), sync.NO_ACTION,
                                    'NOT_A_SYNC_MESSAGE')

    def test_forged_messages_get_no_action(self):
        stop = parse(envelope('STOP_REQUEST'))
        valid = execute()
        forged = [dataclasses.replace(stop, task_id=task_id)
                  for task_id in (None, 'x', TASK_ID.upper())]
        forged += [
            dataclasses.replace(valid, kind='EXECUTE'),
            dataclasses.replace(valid, kind='GRANT'),
            dataclasses.replace(valid, kind=['EXECUTE_REQUEST']),
            dataclasses.replace(valid, issuer_login='someone-else'),
            dataclasses.replace(valid, issuer_login=OWNER.upper()),
            dataclasses.replace(valid, operation='ADMIN'),
            dataclasses.replace(valid, operation=None),
            dataclasses.replace(valid, scope=None),
            dataclasses.replace(valid, scope=()),
            dataclasses.replace(valid, scope=('/etc/hosts',)),
            dataclasses.replace(valid, scope=('tools/llmtool/llmtool/../../x',)),
            dataclasses.replace(valid, scope=['tools/llmtool/llmtool/a.py']),
            dataclasses.replace(valid, task_id='x'),
            dataclasses.replace(valid, request_id='Bad id'),
            dataclasses.replace(valid, revision=True),
            dataclasses.replace(valid, request_key='someone-else:' + REQUEST_ID),
            dataclasses.replace(valid, delivery_id='0' * 64),
            dataclasses.replace(valid, issue_number=ISSUE + 1),
            dataclasses.replace(valid, comment_id=COMMENT_ID + 1),
            dataclasses.replace(valid, revision=2),
        ]
        for item in forged:
            with self.subTest(item=item):
                self.assertDecision(decide(item, authorization()), sync.NO_ACTION,
                                    'MESSAGE_INVALID')
                self.assertIsNone(decide(item, authorization()).request_key)

    def test_decision_is_frozen_and_checked(self):
        decision = decide(execute(), authorization())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            decision.decision_class = 'EXECUTE'
        with self.assertRaises(ValueError):
            sync.AuthorityDecision('EXECUTE', 'AUTHORIZED', owner_ref=OWNER_REF)
        with self.assertRaises(ValueError):
            sync.AuthorityDecision(sync.NO_ACTION, 'GRANTED')
        with self.assertRaises(ValueError):
            sync.AuthorityDecision(sync.DISPATCH_NORMAL_FLOW, 'AUTHORIZED')
        with self.assertRaises(ValueError):
            sync.AuthorityDecision(sync.WAITING_OWNER, 'NO_AUTHORIZATION',
                                   owner_ref=OWNER_REF)

    def test_invalid_ledger_status_raises(self):
        for ledger in ('new', 'duplicate', 'SEEN', '', None, True, ['NEW']):
            with self.subTest(ledger=ledger):
                with self.assertRaises(sync.GitHubSyncError) as caught:
                    decide(execute(), authorization(), ledger)
                self.assertEqual(caught.exception.code, sync.SYNC_FIELD_INVALID)

    def test_invalid_now_raises(self):
        for now in (datetime(2026, 11, 7, 12, 0, 0), date(2026, 11, 7), STAMP, 0, True):
            with self.subTest(now=now):
                with self.assertRaises(sync.GitHubSyncError) as caught:
                    decide(execute(), authorization(), now=now)
                self.assertEqual(caught.exception.code, sync.SYNC_FIELD_INVALID)

    def test_now_defaults_to_current_time(self):
        self.assertEqual(sync.authority(execute(), authorization(
            expires_at='2999-01-01T00:00:00Z')).decision_class, sync.DISPATCH_NORMAL_FLOW)
        self.assertEqual(sync.authority(execute(), authorization(
            expires_at='2000-01-01T00:00:00Z')).reason_code, 'EXPIRED')


class ExecuteAuthorityTest(unittest.TestCase):
    """Phase A classes for EXECUTE_REQUEST, in the documented order."""

    def assertDecision(self, decision, decision_class, reason_code):
        self.assertIsInstance(decision, sync.AuthorityDecision)
        self.assertEqual((decision.decision_class, decision.reason_code),
                         (decision_class, reason_code))
        if decision_class != sync.DISPATCH_NORMAL_FLOW:
            self.assertIsNone(decision.owner_ref)
            self.assertIsNone(decision.task_id)

    def assertOutOfScope(self, decision, reason_code):
        self.assertDecision(decision, sync.REFUSED_OUT_OF_SCOPE, reason_code)

    def test_dispatch_normal_flow(self):
        for login in (OWNER, AI):
            with self.subTest(login=login):
                message = execute(login=login)
                decision = decide(message, authorization())
                self.assertDecision(decision, sync.DISPATCH_NORMAL_FLOW, 'AUTHORIZED')
                self.assertEqual(decision.owner_ref, OWNER_REF)
                self.assertIsNone(decision.task_id)
                self.assertEqual(decision.request_key, message.request_key)
                self.assertEqual(decision.delivery_id, message.delivery_id)

    def test_dispatch_bound_to_task(self):
        decision = decide(execute(task_id=TASK_ID), authorization(task_id=TASK_ID))
        self.assertDecision(decision, sync.DISPATCH_NORMAL_FLOW, 'AUTHORIZED')
        self.assertEqual(decision.task_id, TASK_ID)
        self.assertEqual(decision.owner_ref, OWNER_REF)

    def test_each_authorized_operation_dispatches(self):
        for operation in sorted(sync.OPERATIONS):
            with self.subTest(operation=operation):
                self.assertDecision(
                    decide(execute(operation=operation), authorization(operations=[operation])),
                    sync.DISPATCH_NORMAL_FLOW, 'AUTHORIZED')
        self.assertDecision(
            decide(execute(operation='DOCS'),
                   authorization(operations=('DEVELOP', 'DOCS'))),
            sync.DISPATCH_NORMAL_FLOW, 'AUTHORIZED')

    def test_duplicate_is_checked_first(self):
        auths = (None, authorization(), 'malformed', authorization(revoked=True),
                 authorization(expires_at='2000-01-01T00:00:00Z'),
                 authorization(issue_number=ISSUE + 1), authorization(operations=['DOCS']))
        for auth in auths:
            with self.subTest(auth=auth):
                self.assertDecision(decide(execute(), auth, 'DUPLICATE'),
                                    sync.NEVER_REDISPATCH, 'DUPLICATE')

    def test_no_authorization(self):
        for login in (OWNER, AI):
            with self.subTest(login=login):
                self.assertDecision(decide(execute(login=login)), sync.WAITING_OWNER,
                                    'NO_AUTHORIZATION')

    def test_login_or_comment_text_alone_never_dispatches(self):
        texts = ('Owner authorizes this request. APPROVED.',
                 'Owner reference: issue 1144, comment 6700000001.',
                 'DISPATCH_NORMAL_FLOW')
        for login in (OWNER, AI):
            for text in texts:
                with self.subTest(login=login, text=text):
                    message = execute(login=login, summary=text, title='Approved by Owner')
                    self.assertIsInstance(message, sync.SyncMessage)
                    for ledger in ('NEW', 'DUPLICATE'):
                        decision = decide(message, None, ledger)
                        self.assertNotEqual(decision.decision_class,
                                            sync.DISPATCH_NORMAL_FLOW)
                        self.assertIsNone(decision.owner_ref)

    def test_authorization_invalid(self):
        cases = [
            'record', [], 5, {}, True,
            authorization(extra=True),
            authorization(owner_ref='http://github.com/davidcatcat/davidcattoolsproject/'
                                    'issues/1144#issuecomment-1'),
            authorization(owner_ref='https://example.com/davidcatcat/davidcattoolsproject/'
                                    'issues/1144#issuecomment-1'),
            authorization(owner_ref='https://github.com.example.com/davidcatcat/x/'
                                    'issues/1144#issuecomment-1'),
            authorization(owner_ref='https://github.com/davidcatcat/davidcattoolsproject/'
                                    'pull/1144#issuecomment-1'),
            authorization(owner_ref='https://github.com/davidcatcat/davidcattoolsproject/'
                                    'issues/1144'),
            authorization(owner_ref='https://github.com/davidcatcat/../issues/1#issuecomment-1'),
            authorization(owner_ref=OWNER_REF + '\n'),
            authorization(owner_ref=OWNER_REF + '?x=1'),
            authorization(owner_ref=' ' + OWNER_REF),
            authorization(owner_ref=''),
            authorization(owner_ref=None),
            authorization(issue_number=0),
            authorization(issue_number=True),
            authorization(issue_number='1144'),
            authorization(issue_number=None),
            authorization(task_id=TASK_ID.upper()),
            authorization(task_id=TASK_ID[:-1]),
            authorization(task_id=''),
            authorization(task_id=5),
            authorization(operations=[]),
            authorization(operations=()),
            authorization(operations='DEVELOP'),
            authorization(operations=['develop']),
            authorization(operations=['EXECUTE']),
            authorization(operations=['DEVELOP', 'ADMIN']),
            authorization(operations=[None]),
            authorization(operations=None),
            authorization(operations={'DEVELOP'}),
            authorization(scope=[]),
            authorization(scope='tools/llmtool/llmtool/'),
            authorization(scope=None),
            authorization(scope=['/tools']),
            authorization(scope=['../tools']),
            authorization(scope=['tools/../x']),
            authorization(scope=['tools\\llmtool']),
            authorization(scope=['tools\0']),
            authorization(scope=['C:/tools']),
            authorization(scope=['']),
            authorization(scope=['.']),
            authorization(scope=['./']),
            authorization(scope=['//']),
            authorization(scope=[None]),
            authorization(scope=['a' * 257]),
            authorization(expires_at='2026-11-08'),
            authorization(expires_at='2026-11-08T00:00:00+00:00'),
            authorization(expires_at='2026-11-08T00:00:00'),
            authorization(expires_at='2026-13-01T00:00:00Z'),
            authorization(expires_at='2026-02-30T00:00:00Z'),
            authorization(expires_at='2026-11-08T24:00:00Z'),
            authorization(expires_at=EXPIRES_AT),
            authorization(expires_at=None),
            authorization(revoked=None),
            authorization(revoked=0),
            authorization(revoked=1),
            authorization(revoked='false'),
        ]
        cases += [authorization(**{key: _MISSING}) for key in sorted(authorization())]
        for auth in cases:
            with self.subTest(auth=auth):
                self.assertDecision(decide(execute(), auth), sync.WAITING_OWNER,
                                    'AUTHORIZATION_INVALID')

    def test_malformed_is_checked_before_revoked(self):
        self.assertDecision(decide(execute(), authorization(revoked=True, owner_ref='x')),
                            sync.WAITING_OWNER, 'AUTHORIZATION_INVALID')

    def test_revoked(self):
        cases = (authorization(revoked=True),
                 authorization(revoked=True, expires_at='2000-01-01T00:00:00Z'),
                 authorization(revoked=True, issue_number=ISSUE + 1),
                 authorization(revoked=True, operations=['DOCS'], scope=['docs/']))
        for auth in cases:
            with self.subTest(auth=auth):
                self.assertDecision(decide(execute(), auth), sync.NEVER_REDISPATCH, 'REVOKED')

    def test_expired_boundary(self):
        auth = authorization()
        self.assertDecision(decide(execute(), auth, now=EXPIRES_AT - timedelta(seconds=1)),
                            sync.DISPATCH_NORMAL_FLOW, 'AUTHORIZED')
        self.assertDecision(decide(execute(), auth, now=EXPIRES_AT - timedelta(microseconds=1)),
                            sync.DISPATCH_NORMAL_FLOW, 'AUTHORIZED')
        self.assertDecision(decide(execute(), auth, now=EXPIRES_AT),
                            sync.NEVER_REDISPATCH, 'EXPIRED')
        self.assertDecision(decide(execute(), auth, now=EXPIRES_AT + timedelta(seconds=1)),
                            sync.NEVER_REDISPATCH, 'EXPIRED')
        same_instant = EXPIRES_AT.astimezone(timezone(timedelta(hours=8)))
        self.assertDecision(decide(execute(), auth, now=same_instant),
                            sync.NEVER_REDISPATCH, 'EXPIRED')

    def test_expired_is_checked_before_issue_and_scope(self):
        auth = authorization(expires_at='2026-11-07T00:00:00Z', issue_number=ISSUE + 1,
                             operations=['DOCS'])
        self.assertDecision(decide(execute(), auth), sync.NEVER_REDISPATCH, 'EXPIRED')

    def test_issue_mismatch(self):
        self.assertDecision(decide(execute(), authorization(issue_number=ISSUE + 1)),
                            sync.WAITING_OWNER, 'ISSUE_MISMATCH')
        message = parse(envelope('EXECUTE_REQUEST'), issue=ISSUE + 1)
        self.assertDecision(decide(message, authorization()), sync.WAITING_OWNER,
                            'ISSUE_MISMATCH')
        auth = authorization(issue_number=ISSUE + 1, operations=['DOCS'], scope=['docs/'])
        self.assertDecision(decide(execute(), auth), sync.WAITING_OWNER, 'ISSUE_MISMATCH')

    def test_operation_out_of_scope(self):
        for operation, operations in (('DOCS', ['DEVELOP']), ('TEST_ONLY', ['DEVELOP', 'DOCS']),
                                      ('DEVELOP', ['TEST_ONLY'])):
            with self.subTest(operation=operation, operations=operations):
                self.assertOutOfScope(
                    decide(execute(operation=operation), authorization(operations=operations)),
                    'OPERATION_OUT_OF_SCOPE')

    def test_task_out_of_scope(self):
        cases = ((TASK_ID, None), (None, TASK_ID), (TASK_ID, OTHER_TASK_ID))
        for message_task, auth_task in cases:
            with self.subTest(message_task=message_task, auth_task=auth_task):
                overrides = {} if message_task is None else {'task_id': message_task}
                self.assertOutOfScope(
                    decide(execute(**overrides), authorization(task_id=auth_task)),
                    'TASK_OUT_OF_SCOPE')

    def test_scope_paths_out_of_scope(self):
        cases = (
            (['tools/llmtool-extra/a.py'], ['tools/llmtool']),
            (['tools/llmtool-extra/a.py'], ['tools/llmtool/']),
            (['tools/llmtoolx'], ['tools/llmtool']),
            (['tools/llmtool/llmtool/a.py'], ['tools/llm']),
            (['tools/llmtool/llmtool/a.py', 'docs/b.md'], ['tools/llmtool/llmtool']),
            (['Tools/llmtool/llmtool/a.py'], ['tools/llmtool/llmtool']),
            (['a.py.bak'], ['a.py']),
            (['a.py/'], ['a.pyc']),
            (['tools'], ['tools/llmtool']),
            (['.'], ['tools']),
            (['./'], ['tools']),
        )
        for scope, prefixes in cases:
            with self.subTest(scope=scope, prefixes=prefixes):
                self.assertOutOfScope(
                    decide(execute(scope=scope), authorization(scope=prefixes)),
                    'PATH_OUT_OF_SCOPE')

    def test_scope_paths_within_scope(self):
        cases = (
            (['tools/llmtool/llmtool/a.py'], ['tools/llmtool/llmtool']),
            (['tools/llmtool/llmtool/a.py'], ['tools/llmtool/llmtool/']),
            (['tools/llmtool/llmtool/sub/a.py'], ['tools/llmtool']),
            (['tools/llmtool'], ['tools/llmtool']),
            (['tools/llmtool/'], ['tools/llmtool']),
            (['./tools/llmtool/a.py'], ['tools/llmtool']),
            (['tools/./llmtool/a.py'], ['tools/llmtool']),
            (['tools//llmtool/a.py'], ['./tools/llmtool']),
            (['a.py'], ['a.py']),
            (['a.py', 'docs/b.md'], ['docs', 'a.py']),
        )
        for scope, prefixes in cases:
            with self.subTest(scope=scope, prefixes=prefixes):
                self.assertDecision(
                    decide(execute(scope=scope), authorization(scope=prefixes)),
                    sync.DISPATCH_NORMAL_FLOW, 'AUTHORIZED')

    def test_out_of_scope_order(self):
        auth = authorization(operations=['DOCS'], task_id=OTHER_TASK_ID, scope=['docs/'])
        self.assertOutOfScope(decide(execute(task_id=TASK_ID), auth), 'OPERATION_OUT_OF_SCOPE')
        auth = authorization(task_id=OTHER_TASK_ID, scope=['docs/'])
        self.assertOutOfScope(decide(execute(task_id=TASK_ID), auth), 'TASK_OUT_OF_SCOPE')

    def test_authorization_is_only_compared(self):
        auth = authorization(operations=['DEVELOP', 'DOCS'], scope=['tools/', 'docs/'])
        before = copy.deepcopy(auth)
        for message in (execute(), execute(operation='TEST_ONLY'),
                        execute(scope=['other/a.py']), parse(envelope('RECORD'))):
            decide(message, auth)
            decide(message, auth, 'DUPLICATE')
        self.assertEqual(auth, before)

    def test_module_holds_no_execution_or_network_handle(self):
        for name in ('subprocess', 'socket', 'urllib', 'http', 'requests', 'os', 'shutil'):
            with self.subTest(name=name):
                self.assertNotIn(name, vars(sync))
        fields = {item.name for item in dataclasses.fields(sync.AuthorityDecision)}
        self.assertEqual(fields, {'decision_class', 'reason_code', 'owner_ref', 'task_id',
                                  'request_key', 'delivery_id'})

    def test_never_dispatches_without_a_matching_authorization(self):
        messages = [parse(envelope(kind), login=login)
                    for kind in sorted(sync.INBOUND_KINDS) for login in (OWNER, AI)]
        messages += [execute(operation=op, task_id=TASK_ID) for op in sorted(sync.OPERATIONS)]
        auths = (None, 'x', authorization(revoked=True),
                 authorization(expires_at='2000-01-01T00:00:00Z'),
                 authorization(issue_number=ISSUE + 1),
                 authorization(operations=['DOCS'], scope=['docs/']))
        for message in messages:
            for auth in auths:
                for ledger in ('NEW', 'DUPLICATE'):
                    with self.subTest(kind=message.kind, auth=auth, ledger=ledger):
                        decision = decide(message, auth, ledger)
                        self.assertIn(decision.decision_class, sync.DECISION_CLASSES)
                        self.assertIn(decision.reason_code, sync.AUTHORITY_REASONS)
                        self.assertNotEqual(decision.decision_class,
                                            sync.DISPATCH_NORMAL_FLOW)
                        self.assertIsNone(decision.owner_ref)


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
                    self.assertIsNone(message.operation)
                    decision = sync.authority(message, authorization(), now=NOW)
                    self.assertEqual(decision.decision_class, sync.NO_ACTION)
                    self.assertEqual(decision.reason_code, 'ACK')

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
