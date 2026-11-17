"""#2285 Phase B part 1: the Owner ask Stop hook (llmtool.owner_ask_stop_hook).

Every test uses fake stdin, an injected fake GitHub fetcher, temp dirs and a
temp log path. No network and no real gh call (gh_fetch is tested with a
patched subprocess.run). No credential-shaped data.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from llmtool import owner_ask_stop_hook as hook

MARKER = hook._marker
AUDIT = hook._audit
NOW = datetime(2026, 11, 17, 12, 0, 0, tzinfo=timezone.utc).timestamp()
TURN_START = NOW - 600
SLUG = 'example/widget'
ISSUE = 7
BASE = f'repos/{SLUG}/issues/{ISSUE}'
ASK_ID = 5001
URL = f'https://github.com/{SLUG}/issues/{ISSUE}#issuecomment-{ASK_ID}'
ASK = f'Owner decision needed under R-SCOPE: approve the scope change. Ask record: {URL}'
QUESTION = 'Approve the synthetic scope change?'
LOG_KEYS = {'time', 'session', 'issue', 'outcome', 'reply_sha256', 'mode', 'action'}
DEEP = b'[' * 100000 + b']' * 100000


def stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def tstamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def ask_body(row='R-SCOPE'):
    return '\n'.join((MARKER.MARKER_ASK, MARKER.OWNER_ASK_LINE.format(row=row), '', QUESTION))


def comment(cid, created, body, issue=ISSUE, association='MEMBER', slug=SLUG):
    return {'id': cid, 'issue_url': f'https://api.github.com/repos/{slug}/issues/{issue}',
            'created_at': stamp(created), 'body': body, 'author_association': association}


def filler(count, start_id=100, created=NOW - 86400):
    return [comment(start_id + i, created + i, 'Progress note') for i in range(count)]


def ask_comment(**changes):
    value = comment(ASK_ID, NOW - 3600, ask_body())
    value.update(changes)
    return value


def answer_comment(created=NOW - 1800, body=None, cid=6001):
    return comment(cid, created, body or (MARKER.MARKER_ANSWER + '\n\nOwner words relayed.'))


def serve(comments, count=None, base=BASE, number=ISSUE):
    """Fake gh responses: the Issue (with its comment count) and every page."""
    responses = {base: json.dumps({'number': number, 'comments':
                                   len(comments) if count is None else count}).encode()}
    chunks = [comments[i:i + 100] for i in range(0, len(comments), 100)] or [[]]
    if len(chunks[-1]) == 100:
        chunks.append([])
    for page, chunk in enumerate(chunks, 1):
        responses[f'{base}/comments?per_page=100&page={page}'] = json.dumps(chunk).encode()
    return responses


def page(number, base=BASE):
    return f'{base}/comments?per_page=100&page={number}'


class FakeGitHub:
    """Injected fetcher: unknown endpoints fail like a gh error."""

    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls = []

    def __call__(self, endpoint, timeout):
        self.calls.append((endpoint, timeout))
        value = self.responses.get(endpoint)
        if value is None:
            raise hook.FetchError(hook.FETCH_FAILED)
        if isinstance(value, BaseException):
            raise value
        return value

    @property
    def endpoints(self):
        return [endpoint for endpoint, _ in self.calls]


def transcript_records(turn_start=TURN_START):
    return [
        {'type': 'user', 'timestamp': tstamp(NOW - 7200),
         'message': {'role': 'user', 'content': 'Earlier prompt'}},
        {'type': 'assistant', 'timestamp': tstamp(NOW - 7190),
         'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Earlier'}]}},
        {'type': 'user', 'timestamp': tstamp(turn_start - 10),
         'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'Current prompt'}]}},
        {'type': 'assistant', 'timestamp': tstamp(turn_start),
         'message': {'role': 'assistant', 'content': [
             {'type': 'tool_use', 'id': 'tool-1', 'name': 'Read', 'input': {}}]}},
        {'type': 'user', 'timestamp': tstamp(turn_start + 5),
         'message': {'role': 'user', 'content': [
             {'type': 'tool_result', 'tool_use_id': 'tool-1', 'content': 'done'}]}},
        {'type': 'assistant', 'timestamp': tstamp(turn_start + 10),
         'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Final'}]}},
    ]


class HookCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNone(hook._LOAD_ERROR)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.transcript = self.dir / 'session.jsonl'
        self.log = self.dir / 'state' / 'owner-ask-gate.jsonl'
        self.write_transcript(transcript_records())
        self.comments = filler(3) + [ask_comment()]

    def write_transcript(self, records, prefix=b'', suffix=b''):
        body = b''.join(json.dumps(record).encode() + b'\n' for record in records)
        self.transcript.write_bytes(prefix + body + suffix)

    def payload(self, message=ASK, active=False, session='session-a'):
        return {'session_id': session, 'transcript_path': str(self.transcript),
                'cwd': str(self.dir), 'hook_event_name': 'Stop',
                'stop_hook_active': active, 'last_assistant_message': message}

    def evaluate(self, message=ASK, responses=None, fake=None):
        fake = fake or FakeGitHub(serve(self.comments) if responses is None else responses)
        result = hook.evaluate(self.payload(message), message, fetch=fake, now=NOW)
        return result, fake

    def assertOutcome(self, expected, message=ASK, responses=None, fake=None):
        result, fake = self.evaluate(message, responses, fake)
        self.assertEqual(result[0], expected)
        return fake

    def run_hook(self, message=ASK, *, mode=None, active=False, session='session-a',
                 fake=None, stdin=None):
        fake = fake or FakeGitHub(serve(self.comments))
        environ = {} if mode is None else {hook.MODE_ENV: mode}
        data = stdin if stdin is not None else json.dumps(
            self.payload(message, active, session)).encode()
        out = hook.run(data, environ=environ, fetch=fake, now=NOW, log_path=self.log)
        return out, fake

    def log_records(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding='ascii').splitlines()]


class DetectionTests(HookCase):
    def test_screen_inputs(self):
        cases = (
            (AUDIT.CHAT_ASK + ' approve the release', True),
            ('Status line\n' + AUDIT.CHAT_ASK + ' A or B', True),
            (AUDIT.CHAT_ASK + AUDIT.CHAT_NOTHING, False),
            ('Owner, please choose:\nA. ship now\nB. wait a day', True),
            ('Owner choice:\n- **(A)** ship\n- **(B)** wait', True),
            ('Choose:\nA. ship now\nB. wait a day', False),
            ('Owner, only one option:\nA. ship now', False),
            ('This needs R-RELEASE approval.', True),
            ('reserved_row=R-OTHER', True),
            ('XR-SCOPE and R-SCOPED are not row terms', False),
            ('Owner update: the build is green, e.g. all tests ran.', False),
            ('Plain progress note.', False),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertIs(hook.asks_owner(message), expected)

    def test_every_reserved_row_is_a_screen_term(self):
        for row in MARKER.RESERVED_ROWS:
            with self.subTest(row=row):
                self.assertTrue(hook.asks_owner(f'Row {row} applies.'))

    def test_not_an_ask_is_not_checked_logged_or_blocked(self):
        message = f'Progress only; earlier record {URL}'
        out, fake = self.run_hook(message, mode='enforce')
        self.assertEqual(out, '')
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.log_records(), [])

    def test_detection_never_decides_the_outcome(self):
        # Any screen leads to the same verification; a screen alone never passes.
        for message in (f'R-SCOPE ask: {URL}', AUDIT.CHAT_ASK + ' approve ' + URL,
                        f'Owner, choose:\nA. yes\nB. no\n{URL}'):
            with self.subTest(message=message):
                self.assertOutcome(hook.PASS, message)
        for message in ('R-SCOPE ask without a link', AUDIT.CHAT_ASK + ' approve'):
            with self.subTest(message=message):
                fake = self.assertOutcome(hook.NO_CITATION, message)
                self.assertEqual(fake.calls, [])


class PassRuleTests(HookCase):
    def test_valid_cited_ask_passes_and_is_not_logged(self):
        result, fake = self.evaluate()
        self.assertEqual(result, (hook.PASS, str(ISSUE)))
        self.assertEqual(fake.endpoints, [BASE, page(1)])
        for _, timeout in fake.calls:
            self.assertTrue(0 < timeout <= AUDIT.PAGE_TIMEOUT)
        out, _ = self.run_hook(mode='enforce')
        self.assertEqual(out, '')
        self.assertEqual(self.log_records(), [])

    def test_no_cited_comment_url(self):
        for message in ('Owner decision needed under R-SCOPE.',
                        f'R-SCOPE ask: https://github.com/{SLUG}/issues/{ISSUE}',
                        f'R-SCOPE ask: https://github.com/{SLUG}/pull/{ISSUE}#issuecomment-1'):
            with self.subTest(message=message):
                fake = self.assertOutcome(hook.NO_CITATION, message)
                self.assertEqual(fake.calls, [])

    def test_cited_comment_missing_from_the_complete_list(self):
        message = ASK.replace(str(ASK_ID), '9999')
        self.assertOutcome(hook.COMMENT_NOT_FOUND, message)

    def test_line_one_must_be_the_ask_marker(self):
        row_line = MARKER.OWNER_ASK_LINE.format(row='R-SCOPE')
        for first in (MARKER.MARKER_ANSWER, ' ' + MARKER.MARKER_ASK,
                      MARKER.MARKER_ASK.replace('status=WAITING', 'status=BLOCKED'),
                      'Question first'):
            with self.subTest(first=first):
                self.comments[-1] = ask_comment(body=f'{first}\n{row_line}\n\n{QUESTION}')
                self.assertOutcome(hook.MARKER_MISSING)
        self.comments[-1] = ask_comment(body='')
        self.assertOutcome(hook.MARKER_MISSING)

    def test_line_two_must_name_a_reserved_row(self):
        for second in (None, '', '<!-- owner-ask: v=2 reserved_row=R-SCOPE -->',
                       MARKER.OWNER_ASK_LINE.format(row='R-NOPE'),
                       MARKER.OWNER_ASK_LINE.format(row='R-SCOPE') + ' extra'):
            with self.subTest(second=second):
                lines = [MARKER.MARKER_ASK] + ([] if second is None else [second, '', QUESTION])
                self.comments[-1] = ask_comment(body='\n'.join(lines))
                self.assertOutcome(hook.ROW_MISSING)

    def test_comment_must_belong_to_the_cited_issue(self):
        for changes in ({'issue_url': f'https://api.github.com/repos/{SLUG}/issues/8'},
                        {'issue_url': 'https://api.github.com/repos/example/other/issues/7'}):
            with self.subTest(changes=changes):
                self.comments[-1] = ask_comment(**changes)
                self.assertOutcome(hook.ISSUE_MISMATCH)

    def test_author_association_must_be_trusted(self):
        for association in ('CONTRIBUTOR', 'NONE', 'member', None, 1):
            with self.subTest(association=association):
                self.comments[-1] = ask_comment(author_association=association)
                self.assertOutcome(hook.UNTRUSTED_AUTHOR)
        self.comments[-1] = ask_comment()
        del self.comments[-1]['author_association']
        self.assertOutcome(hook.UNTRUSTED_AUTHOR)
        for association in sorted(MARKER.TRUSTED_ASSOCIATION):
            with self.subTest(association=association):
                self.comments[-1] = ask_comment(author_association=association)
                self.assertOutcome(hook.PASS)

    def test_ask_must_be_younger_than_the_wait_horizon(self):
        self.comments[-1] = ask_comment(created_at=stamp(NOW - AUDIT.WAIT_HORIZON))
        self.assertOutcome(hook.ASK_EXPIRED)
        self.comments[-1] = ask_comment(created_at=stamp(NOW - AUDIT.WAIT_HORIZON + 60))
        self.assertOutcome(hook.PASS)

    def test_later_settling_comment_fails(self):
        settling = (
            MARKER.MARKER_ANSWER,
            '<!-- activity: type=pm_progress actor=PM target=Owner status=WITHDRAWN -->',
            '<!-- activity: type=pm_progress actor=PM target=Owner status=DONE -->',
            '<!-- activity: type=owner_reply actor=Owner target=PM status=ANSWERED -->',
        )
        for first in settling:
            with self.subTest(first=first):
                self.comments = filler(3) + [ask_comment(), answer_comment(body=first)]
                self.assertOutcome(hook.ASK_SETTLED)
        # Same second, higher id: still later.
        self.comments = filler(3) + [ask_comment(), answer_comment(created=NOW - 3600)]
        self.assertOutcome(hook.ASK_SETTLED)

    def test_earlier_or_unrelated_comments_do_not_settle(self):
        cases = (
            [answer_comment(created=NOW - 7000), ask_comment()],
            [ask_comment(), answer_comment(created=NOW - 3600, cid=ASK_ID - 1)],
            [ask_comment(), answer_comment(
                body='<!-- activity: type=pm_progress actor=PM target=PM status=DONE -->')],
            [ask_comment(), answer_comment(body='Thanks. ' + MARKER.MARKER_ANSWER)],
        )
        for extra in cases:
            with self.subTest(ids=[item['id'] for item in extra]):
                self.comments = filler(3) + extra
                self.assertOutcome(hook.PASS)

    def test_ask_must_exist_before_this_turns_first_assistant_entry(self):
        for created in (TURN_START, TURN_START + 60, NOW):
            with self.subTest(created=created - TURN_START):
                self.comments[-1] = ask_comment(created_at=stamp(created))
                self.assertOutcome(hook.ASK_NOT_BEFORE_TURN)
        self.comments[-1] = ask_comment(created_at=stamp(TURN_START - 1))
        self.assertOutcome(hook.PASS)

    def test_turn_starts_at_the_last_human_prompt(self):
        # The ask predates the current turn but not an earlier one; tool results
        # and meta or sidechain user records do not start a turn.
        records = transcript_records()
        records.append({'type': 'user', 'isMeta': True, 'timestamp': tstamp(TURN_START + 20),
                        'message': {'role': 'user', 'content': 'meta note'}})
        records.append({'type': 'user', 'isSidechain': True,
                        'timestamp': tstamp(TURN_START + 25),
                        'message': {'role': 'user', 'content': 'side prompt'}})
        records.append({'type': 'assistant', 'timestamp': tstamp(TURN_START + 30),
                        'message': {'role': 'assistant', 'content': []}})
        self.write_transcript(records)
        self.assertEqual(hook.turn_start(str(self.transcript)), TURN_START)
        self.comments[-1] = ask_comment(created_at=stamp(NOW - 7100))
        self.assertOutcome(hook.PASS)

    def test_a_second_valid_citation_can_pass(self):
        message = f'R-SCOPE ask. Old: {URL.replace(str(ASK_ID), "9999")} New: {URL}'
        result, fake = self.evaluate(message)
        self.assertEqual(result, (hook.PASS, str(ISSUE)))
        # One complete read of the Issue serves both citations.
        self.assertEqual(fake.endpoints, [BASE, page(1)])

    def test_unverifiable_citation_outranks_a_definitive_failure(self):
        other_base = f'repos/{SLUG}/issues/8'
        message = (f'R-SCOPE ask: {URL.replace(str(ASK_ID), "9999")} and '
                   f'https://github.com/{SLUG}/issues/8#issuecomment-7001')
        responses = serve(self.comments)
        responses[other_base] = hook.FetchError(hook.FETCH_FAILED)
        self.assertEqual(self.evaluate(message, responses)[0], (hook.FETCH_FAILED, '8'))


class TranscriptFailClosedTests(HookCase):
    def assertOrderUnknown(self):
        fake = self.assertOutcome(hook.ORDER_UNKNOWN)
        self.assertEqual(fake.calls, [])

    def test_missing_unreadable_or_relative_transcript(self):
        self.transcript.unlink()
        self.assertOrderUnknown()
        self.transcript.mkdir()
        self.assertOrderUnknown()
        for path in (None, 'session.jsonl', '', 7):
            with self.subTest(path=path):
                self.assertIsNone(hook.turn_start(path))

    def test_malformed_record_before_a_valid_one_is_never_skipped(self):
        for prefix in (b'{not json\n', b'[]\n', b'"text"\n', b'{"no_type": 1}\n',
                       b'{"type": 7}\n', b'\xff\xfe\n', DEEP + b'\n'):
            with self.subTest(prefix=prefix[:12]):
                self.write_transcript(transcript_records(), prefix=prefix)
                self.assertOrderUnknown()

    def test_malformed_record_after_the_turn_start(self):
        self.write_transcript(transcript_records(), suffix=b'{"type": "assistant", ')
        self.assertOrderUnknown()

    def test_recursion_error_record(self):
        with self.assertRaises(RecursionError):
            json.loads(DEEP)
        self.write_transcript(transcript_records(), suffix=DEEP + b'\n')
        self.assertOrderUnknown()

    def test_missing_or_unparsable_timestamp(self):
        for index in (1, 2, 3, 5):
            for value in (None, 'yesterday', '2026-11-17T11:50:00', 1794916800, ''):
                with self.subTest(index=index, value=value):
                    records = transcript_records()
                    if value is None:
                        del records[index]['timestamp']
                    else:
                        records[index]['timestamp'] = value
                    self.write_transcript(records)
                    self.assertOrderUnknown()

    def test_user_or_assistant_record_without_message(self):
        for index, message in ((2, None), (3, 'text'), (2, {'content': None}),
                               (2, {'content': 7})):
            with self.subTest(index=index, message=message):
                records = transcript_records()
                records[index]['message'] = message
                self.write_transcript(records)
                self.assertOrderUnknown()

    def test_no_prompt_or_no_assistant_entry_after_it(self):
        self.write_transcript(transcript_records()[:3])
        self.assertOrderUnknown()
        self.write_transcript([record for record in transcript_records()
                               if record['type'] == 'assistant'])
        self.assertOrderUnknown()
        self.write_transcript([])
        self.assertOrderUnknown()

    def test_other_record_types_need_no_timestamp(self):
        records = [{'type': 'summary', 'summary': 'earlier work'}] + transcript_records()
        self.write_transcript(records)
        self.assertOutcome(hook.PASS)

    def test_oversized_transcript(self):
        with mock.patch.object(AUDIT, 'MAX_TRANSCRIPT_BYTES', 64):
            self.assertOrderUnknown()


class FetchFailClosedTests(HookCase):
    def test_issue_or_any_page_error(self):
        self.comments = filler(150) + [ask_comment()]
        responses = serve(self.comments)
        self.assertOutcome(hook.PASS, responses=responses)
        for endpoint in (BASE, page(1), page(2)):
            with self.subTest(endpoint=endpoint):
                broken = dict(responses)
                broken[endpoint] = hook.FetchError(hook.FETCH_FAILED)
                self.assertOutcome(hook.FETCH_FAILED, responses=broken)
                broken[endpoint] = '[]'  # not bytes
                self.assertOutcome(hook.FETCH_FAILED, responses=broken)

    def test_later_page_is_always_read(self):
        self.comments = filler(99) + [ask_comment()]
        fake = self.assertOutcome(hook.PASS)
        self.assertEqual(fake.endpoints, [BASE, page(1), page(2)])

    def test_page_limit_overrun(self):
        with mock.patch.object(hook, 'MAX_COMMENT_PAGES', 2):
            self.comments = filler(199) + [ask_comment()]
            fake = self.assertOutcome(hook.FETCH_PAGE_LIMIT)
            self.assertEqual(fake.endpoints, [BASE, page(1), page(2)])
            self.comments = filler(198) + [ask_comment()]
            self.assertOutcome(hook.PASS)

    def test_incomplete_list(self):
        self.assertOutcome(hook.FETCH_INCOMPLETE, responses=serve(self.comments, count=5))
        self.assertOutcome(hook.FETCH_INCOMPLETE, responses=serve(self.comments, count=3))
        for issue in ({'number': ISSUE}, {'comments': True}, {'comments': -1},
                      {'comments': '4'}):
            with self.subTest(issue=issue):
                responses = serve(self.comments)
                responses[BASE] = json.dumps(issue).encode()
                self.assertOutcome(hook.FETCH_INCOMPLETE, responses=responses)
        oversized = serve(self.comments)
        oversized[page(1)] = json.dumps(filler(101) + [ask_comment()]).encode()
        self.assertOutcome(hook.FETCH_INCOMPLETE, responses=oversized)
        repeated = filler(100) + filler(1) + [ask_comment()]
        self.assertOutcome(hook.FETCH_INCOMPLETE, responses=serve(repeated))

    def test_budget_exhaustion_is_a_fetch_failure(self):
        ticks = iter((0.0, 0.0, hook.HOOK_BUDGET + 1.0))
        fake = FakeGitHub(serve(self.comments))
        result = hook.evaluate(self.payload(), ASK, fetch=fake, now=NOW,
                               clock=lambda: next(ticks))
        self.assertEqual(result, (hook.FETCH_FAILED, str(ISSUE)))
        self.assertEqual(fake.endpoints, [BASE])


class DecodeAndEntryTests(HookCase):
    def test_any_decode_error_including_recursion(self):
        with self.assertRaises(RecursionError):
            json.loads(DEEP)
        for endpoint in (BASE, page(1)):
            for raw in (b'{not json', b'\xff\xfe[]', DEEP, b''):
                with self.subTest(endpoint=endpoint, raw=raw[:12]):
                    responses = serve(self.comments)
                    responses[endpoint] = raw
                    self.assertOutcome(hook.DECODE_FAILED, responses=responses)

    def test_recursion_error_on_a_later_page(self):
        self.comments = filler(99) + [ask_comment(), answer_comment()]
        responses = serve(self.comments)
        responses[page(2)] = DEEP
        self.assertOutcome(hook.DECODE_FAILED, responses=responses)

    def test_unexpected_container_shapes(self):
        for endpoint, value in ((BASE, []), (page(1), {'items': []}), (page(1), None)):
            with self.subTest(endpoint=endpoint, value=value):
                responses = serve(self.comments)
                responses[endpoint] = json.dumps(value).encode()
                self.assertOutcome(hook.DECODE_FAILED, responses=responses)

    def test_every_malformed_entry_is_refused_not_dropped(self):
        good = filler(1, start_id=900)[0]
        variants = ['not an object', None, [good]]
        for key, values in (('id', (True, 0, -1, '901', 1.5, None)),
                            ('issue_url', (None, 'not a url', 7,
                                           f'https://api.github.com/repos/{SLUG}/issues/x')),
                            ('created_at', (None, '2026-11-17', 'yesterday', 1794916800)),
                            ('body', (None, 7, ['text']))):
            for value in values:
                variant = dict(good)
                variant[key] = value
                variants.append(variant)
            missing = dict(good)
            del missing[key]
            variants.append(missing)
        for variant in variants:
            with self.subTest(variant=variant):
                self.comments = filler(3) + [variant, ask_comment()]
                self.assertOutcome(hook.ENTRY_INVALID)

    def test_malformed_entry_on_a_later_page(self):
        self.comments = filler(99) + [ask_comment(), dict(filler(1, 900)[0], id=True)]
        self.assertOutcome(hook.ENTRY_INVALID)

    def test_answer_on_a_later_page_settles(self):
        self.comments = filler(99) + [ask_comment(), answer_comment()]
        fake = self.assertOutcome(hook.ASK_SETTLED)
        self.assertEqual(fake.endpoints, [BASE, page(1), page(2)])


class ModeAndLogTests(HookCase):
    def test_observe_is_the_default_and_never_blocks(self):
        for mode in (None, 'observe', 'ENFORCE', 'enforce ', '1', ''):
            with self.subTest(mode=mode):
                out, _ = self.run_hook('Owner decision needed under R-SCOPE.', mode=mode)
                self.assertEqual(out, '')
                record = self.log_records()[-1]
                self.assertEqual((record['outcome'], record['mode'], record['action']),
                                 (hook.NO_CITATION, 'observe', 'LOG'))

    def test_enforce_blocks_a_definitive_failure(self):
        self.comments[-1] = ask_comment(author_association='CONTRIBUTOR')
        out, _ = self.run_hook(mode='enforce')
        decision = json.loads(out)
        self.assertEqual(set(decision), {'decision', 'reason'})
        self.assertEqual(decision['decision'], 'block')
        self.assertIn(hook.UNTRUSTED_AUTHOR, decision['reason'])
        self.assertNotIn('unavailable', decision['reason'])
        self.assertEqual(self.log_records(), [{
            'time': stamp(NOW), 'session': 'session-a', 'issue': str(ISSUE),
            'outcome': hook.UNTRUSTED_AUTHOR,
            'reply_sha256': hashlib.sha256(ASK.encode()).hexdigest(),
            'mode': 'enforce', 'action': 'BLOCK'}])

    def test_enforce_blocks_once_when_verification_is_unavailable(self):
        fake = FakeGitHub({BASE: hook.FetchError(hook.FETCH_FAILED)})
        out, _ = self.run_hook(mode='enforce', fake=fake)
        reason = json.loads(out)['reason']
        self.assertIn('verification was unavailable', reason)
        self.assertIn(hook.FETCH_FAILED, reason)
        out, _ = self.run_hook(mode='enforce', fake=fake, active=True)
        self.assertEqual(out, '')
        self.assertEqual([r['action'] for r in self.log_records()], ['BLOCK', 'LOOP_GUARD'])

    def test_unverifiable_in_observe_only_logs(self):
        self.transcript.unlink()
        out, fake = self.run_hook()
        self.assertEqual(out, '')
        self.assertEqual(fake.calls, [])
        record = self.log_records()[-1]
        self.assertEqual((record['outcome'], record['action'], record['issue']),
                         (hook.ORDER_UNKNOWN, 'LOG', str(ISSUE)))

    def test_unexpected_exception_is_unverifiable(self):
        fake = FakeGitHub({BASE: RuntimeError('synthetic fetch fault')})
        out, _ = self.run_hook(fake=fake)
        self.assertEqual(out, '')
        out, _ = self.run_hook(mode='enforce', fake=fake)
        self.assertIn('verification was unavailable', json.loads(out)['reason'])
        out, _ = self.run_hook(mode='enforce', fake=fake)
        self.assertEqual(out, '')
        self.assertEqual([(r['outcome'], r['action']) for r in self.log_records()],
                         [(hook.HOOK_ERROR, 'LOG'), (hook.HOOK_ERROR, 'BLOCK'),
                          (hook.HOOK_ERROR, 'LOOP_GUARD')])

    def test_contracts_unavailable_never_pass(self):
        with mock.patch.object(hook, '_LOAD_ERROR', 'ImportError'):
            out, fake = self.run_hook(mode='enforce')
        self.assertIn(hook.HOOK_ERROR, json.loads(out)['reason'])
        self.assertEqual(fake.calls, [])

    def test_loop_guard_on_stop_hook_active(self):
        for active in (True, 'true', None, 1):
            with self.subTest(active=active):
                out, _ = self.run_hook('Owner decision needed under R-SCOPE.',
                                       mode='enforce', active=active)
                self.assertEqual(out, '')
                self.assertEqual(self.log_records()[-1]['action'], 'LOOP_GUARD')

    def test_loop_guard_on_a_reply_hash_already_blocked(self):
        message = 'Owner decision needed under R-SCOPE.'
        first, _ = self.run_hook(message, mode='enforce')
        self.assertEqual(json.loads(first)['decision'], 'block')
        again, _ = self.run_hook(message, mode='enforce')
        self.assertEqual(again, '')
        other_reply, _ = self.run_hook(message + ' Second wording.', mode='enforce')
        self.assertEqual(json.loads(other_reply)['decision'], 'block')
        other_session, _ = self.run_hook(message, mode='enforce', session='session-b')
        self.assertEqual(json.loads(other_session)['decision'], 'block')
        self.assertEqual([r['action'] for r in self.log_records()],
                         ['BLOCK', 'LOOP_GUARD', 'BLOCK', 'BLOCK'])

    def test_log_holds_hashes_and_codes_only(self):
        self.comments[-1] = ask_comment(author_association='NONE')
        self.run_hook(mode='enforce')
        self.run_hook('Owner decision needed under R-SCOPE.')
        text = self.log.read_text(encoding='ascii')
        records = self.log_records()
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(set(record), LOG_KEYS)
            self.assertRegex(record['reply_sha256'], r'\A[0-9a-f]{64}\Z')
            # Only the hash field may hold hex; no other value carries a comment id.
            others = [value for key, value in record.items() if key != 'reply_sha256']
            self.assertNotIn(str(ASK_ID), ' '.join(others))
        for content in (ASK, URL, QUESTION, 'Owner decision', 'issuecomment',
                        MARKER.MARKER_ASK, 'Progress note', str(self.transcript)):
            self.assertNotIn(content, text)
        self.assertEqual(stat.S_IMODE(os.stat(self.log).st_mode), 0o600)

    def test_log_write_failure_does_not_break_the_hook(self):
        self.log = self.dir / 'blocker' / 'owner-ask-gate.jsonl'
        (self.dir / 'blocker').write_text('a file, not a directory', encoding='ascii')
        out, _ = self.run_hook('Owner decision needed under R-SCOPE.', mode='enforce')
        self.assertEqual(json.loads(out)['decision'], 'block')

    def test_invalid_input_is_logged_and_never_blocks(self):
        for stdin in (b'', b'{not json', b'[]', b'\xff', DEEP,
                      json.dumps({'session_id': 'session-a'}).encode(),
                      json.dumps({'session_id': 'bad id/x', 'last_assistant_message': 7}).encode()):
            with self.subTest(stdin=stdin[:16]):
                out, fake = self.run_hook(mode='enforce', stdin=stdin)
                self.assertEqual(out, '')
                self.assertEqual(fake.calls, [])
                record = self.log_records()[-1]
                self.assertEqual((record['outcome'], record['action'], record['issue'],
                                  record['reply_sha256']),
                                 (hook.INPUT_INVALID, 'LOG', 'NONE', 'NONE'))
                self.assertIn(record['session'], ('session-a', 'UNKNOWN'))

    def test_default_log_path(self):
        self.assertEqual(hook.default_log_path(),
                         Path.home() / '.local' / 'state' / 'llmtool' / 'owner-ask-gate.jsonl')


class EntryPointTests(HookCase):
    def call_main(self, payload, environ):
        stdin = io.TextIOWrapper(io.BytesIO(payload))
        stdout = io.StringIO()
        with mock.patch('sys.stdin', stdin), mock.patch('sys.stdout', stdout), \
                mock.patch('sys.stderr', io.StringIO()), \
                mock.patch.dict(os.environ, environ), \
                mock.patch.object(hook, 'default_log_path', return_value=self.log), \
                mock.patch.object(hook, 'gh_fetch', side_effect=AssertionError('no network')):
            code = hook.main([])
        return code, stdout.getvalue()

    def test_main_exits_zero_and_prints_only_a_block(self):
        message = 'Owner decision needed under R-SCOPE.'
        code, out = self.call_main(json.dumps(self.payload(message)).encode(),
                                   {hook.MODE_ENV: 'enforce'})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['decision'], 'block')
        code, out = self.call_main(json.dumps(self.payload('Plain note.')).encode(),
                                   {hook.MODE_ENV: 'enforce'})
        self.assertEqual((code, out), (0, ''))
        code, out = self.call_main(b'{broken', {hook.MODE_ENV: 'enforce'})
        self.assertEqual((code, out), (0, ''))

    def test_main_survives_a_run_failure(self):
        with mock.patch.object(hook, 'run', side_effect=RuntimeError('synthetic')):
            code, out = self.call_main(b'{}', {})
        self.assertEqual((code, out), (0, ''))


class GhFetchTests(unittest.TestCase):
    ENDPOINT = f'{BASE}/comments?per_page=100&page=1'

    def test_read_only_argv_without_shell_and_with_timeout(self):
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout=b'[]', stderr=b'')
        with mock.patch.object(hook.subprocess, 'run', return_value=done) as run:
            self.assertEqual(hook.gh_fetch(self.ENDPOINT, 12), b'[]')
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['gh', 'api', '--method', 'GET', self.ENDPOINT])
        self.assertIs(kwargs['shell'], False)
        self.assertEqual(kwargs['timeout'], 12)
        self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
        self.assertIs(kwargs['check'], False)
        self.assertEqual(kwargs['env']['GH_PROMPT_DISABLED'], '1')

    def test_failures_raise_fetch_error(self):
        failed = subprocess.CompletedProcess(args=[], returncode=1, stdout=b'', stderr=b'x')
        for effect in (dict(return_value=failed),
                       dict(side_effect=subprocess.TimeoutExpired(['gh'], 1)),
                       dict(side_effect=OSError('missing'))):
            with self.subTest(effect=sorted(effect)):
                with mock.patch.object(hook.subprocess, 'run', **effect):
                    with self.assertRaises(hook.FetchError):
                        hook.gh_fetch(self.ENDPOINT, 1)


class ReuseTests(unittest.TestCase):
    def test_contracts_come_from_the_owner_scripts(self):
        scripts = Path(__file__).resolve().parent.parent / 'scripts'
        self.assertEqual(Path(MARKER.__file__).resolve(), scripts / 'pm_owner_marker.py')
        self.assertEqual(Path(AUDIT.__file__).resolve(), scripts / 'owner_ask_audit.py')
        self.assertEqual(AUDIT.WAIT_HORIZON, 7 * 86400)

    def test_no_shell_string_or_settings_surface(self):
        source = Path(hook.__file__).read_text(encoding='utf-8')
        for forbidden in ('shell=True', 'os.system', 'settings.json', '.claude/'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
