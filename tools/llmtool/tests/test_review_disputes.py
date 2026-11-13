"""#1502 WP-D D13 (section 4 items 1-12, E10): PM disputes of formal review findings.

Covers the optional review_disputes record, ``task dispute open|defer|rule``,
the pure arbiter chooser and the review-repair and High Debug gates. Every
store write in these tests goes through the real task_store dispute
validators. No test data here is credential-shaped (Owner #763 A); the
screen test injects the real screen function's decision.
"""

import argparse
import hashlib
import inspect
import io
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llmtool import (cli, high_debug, review_repair, task_completion, task_store,
                     task_submission_cli)
from llmtool.errors import LLMToolError

import review_trust_fixture
import test_review_repair as trr
import worker_policy_fixture

TASK, HEAD, PR, EXECUTOR = trr.TASK, trr.HEAD, trr.PR, trr.EXECUTOR
NEW_HEAD = 'd' * 40
# The trusted repository slug the dispute commands read from the trust source
# (patched in _Case); production code holds no owner or repository literal.
REPOSITORY = 'davidcatcat/davidcattoolsproject'
OTHER_REPOSITORY = 'example/other'
REF = f'https://github.com/{REPOSITORY}/pull/42#issuecomment-1001'
RULING_REF = f'https://github.com/{REPOSITORY}/pull/42#issuecomment-1002'
ISSUE_REF = f'https://github.com/{REPOSITORY}/issues/1502#issuecomment-1003'
FINDINGS = ('Finding: F1 [High] a.py:1 the guard is missing\n'
            'Finding: F2 [Low] a.py:2 naming\n'
            'Finding: F3 [Medium] a.py:3 edge case\n'
            'Finding: F4 [Advisory] a.py:4 note\n'
            'Finding: F5 [Critical] a.py:5 data loss\n'
            'Finding: F6 [Blocking] a.py:6 wrong result\n')
RULING_ID = 1002
REVIEW_LOGIN = 'review-account'
COMMON_DIR = '/tmp/git'   # trr._record() completion evidence


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


_ACTORS = {'codex-reviewer': 'Codex', 'claude-reviewer': 'Claude'}
_ACCOUNT_MODES = {'Codex': 'CODEX_CLI', 'Claude': 'EXPLICIT_CLAUDE_CONFIG_DIR'}


def _ruling_body(finding_id='F1', outcome='WITHDRAWN', *, head=HEAD, actor='Codex', pr=PR,
                 binding=True, extra=''):
    """The arbiter's ruling text: its review claim's review_text and its comment body."""
    lines = [f'<!-- activity: type=dispute_ruling actor={actor} target=PM status={outcome} -->',
             f'Ruling: {finding_id} {outcome} | PR: #{pr} | HEAD: {head}']
    if binding:
        lines.append(task_completion._review_binding_line(TASK, trr.RUN, trr.CONTRACT, head))
    lines.append('The disputed finding was re-read against the posted evidence.')
    return '\n'.join(lines) + '\n' + extra


def _ruling_comment(finding_id='F1', outcome='WITHDRAWN', *, head=HEAD, actor='Codex',
                    pr=PR, issue=PR, comment_id=RULING_ID, login=REVIEW_LOGIN,
                    created=None, edited=False, binding=True, extra='', body=None,
                    repository=REPOSITORY):
    """The arbiter's ruling comment as GitHub returns it (security v91 F1)."""
    created = _now() if created is None else created
    if body is None:
        body = _ruling_body(finding_id, outcome, head=head, actor=actor, pr=pr,
                            binding=binding, extra=extra)
    return {'id': comment_id, 'body': body,
            'issue_url': f'https://api.github.com/repos/{repository}/issues/{issue}',
            'user': {'login': login}, 'created_at': created,
            'updated_at': '2099-01-01T00:00:00Z' if edited else created}


def _arbiter_claim(finding_id='F1', outcome='WITHDRAWN', *, head=HEAD, actor='Codex',
                   reviewer_task='arbiter-d6', session='arbiter-session-1', version=2,
                   account_mode=None, executor=EXECUTOR, state='COMPLETED', pr=PR,
                   text=None, **result):
    """The arbiter run's COMPLETED review-dispatch claim (High Debug #1775).

    Its review_text is, by default, exactly the ruling comment body.
    """
    review_text = _ruling_body(finding_id, outcome, head=head, actor=actor) if text is None else text
    review_result = {'task_id': TASK, 'run_id': trr.RUN, 'contract_digest': trr.CONTRACT,
                     'head': head, 'verdict': 'BLOCKED', 'review_text': review_text}
    review_result.update(result)
    return {'executor_task': executor, 'pr': pr, 'head_sha': head, 'version': version,
            'state': state, 'reviewer_task': reviewer_task, 'native_session_id': session,
            'account_mode': _ACCOUNT_MODES[actor] if account_mode is None else account_mode,
            'claimed_at': '2026-11-13T00:30:00Z', 'updated_at': '2026-11-13T00:40:00Z',
            'review_result': review_result}


def _registration(*, task='arbiter-d6', agent='codex-reviewer', session='arbiter-session-1',
                  head=HEAD, login=REVIEW_LOGIN):
    """A registered D6 Reviewer record as task_completion.read_d6_registration returns it."""
    return SimpleNamespace(task_id=task, trusted_agent_id=agent, native_session_id=session,
                           result_sha=head, root='/registered/' + task, common_dir=COMMON_DIR,
                           branch=task + '-branch', github_login=login)


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _verdict(*, actor='Claude', status='FAIL', head=HEAD, version=1, findings=FINDINGS):
    marker = 'FIX' if status == 'FAIL' else status
    return (f'<!-- activity: type=review_verdict actor={actor} target=PM status={marker} -->\n'
            f'Verdict: {status} | Version: v{version} | HEAD: {head}\n' + findings)


def _review_claim(*, head=HEAD, version=1, actor='Claude', status='FAIL',
                  account_mode='DEFAULT_SECOND_ACCOUNT', session='review-session',
                  findings=FINDINGS, result_head=None):
    claim = dict(trr._record()['review_dispatch_claim'][0])
    claim.update(head_sha=head, version=version, state='COMPLETED',
                 account_mode=account_mode, native_session_id=session,
                 review_result={'verdict': status,
                                'head': head if result_head is None else result_head,
                                'review_text': _verdict(actor=actor, status=status, head=head,
                                                        version=version, findings=findings)})
    return claim


def _task(*, developer=None, claims=None, disputes=None, published_head=None):
    record = trr._record()
    if developer is not None:
        record['developer'] = dict(record['developer'], **developer)
    record['review_dispatch_claim'] = [_review_claim()] if claims is None else claims
    if disputes is not None:
        record['review_disputes'] = disputes
    if published_head is not None:
        record['published_pr'] = {'pr': PR, 'head_sha': published_head}
    record['task_binding'] = {'contract_digest': trr.CONTRACT}
    record['record_sha256'] = hashlib.sha256(json.dumps(
        {key: value for key, value in record.items() if key != 'record_sha256'},
        sort_keys=True).encode()).hexdigest()
    return record


def _entry(finding_id='F1', *, head=HEAD, severity='High', state='OPEN', ruling=None,
           pr=PR, evidence_ref=REF):
    entry = {'finding_id': finding_id, 'pr': pr, 'head_sha': head, 'review_version': 1,
             'severity': severity, 'state': state, 'evidence_ref': evidence_ref,
             'opened_at': '2026-11-13T00:00:00Z'}
    if ruling is not None:
        entry['ruling'] = {'outcome': ruling, 'head_sha': head, 'reviewer': 'codex-reviewer',
                           'reviewer_task': 'arbiter-d6', 'reviewer_session': 'arbiter-session-1',
                           'evidence_ref': RULING_REF, 'ruled_at': '2026-11-13T01:00:00Z'}
    return entry


class DisputeStore(trr.TempTaskStore):
    """The repair-test store, with the real task_store dispute transition and shape rules."""

    def compare_and_update(self, task_id, *, expected, **changes):
        if 'review_disputes' in changes:
            current = self.get(task_id)
            task_store._check_review_disputes_transition(current.get('review_disputes'),
                                                         changes['review_disputes'])
            task_store._validate_review_disputes({'review_disputes': changes['review_disputes']})
        return super().compare_and_update(task_id, expected=expected, **changes)


def _head(value):
    return lambda _record, _pr: value


class _Case(unittest.TestCase):
    """Fakes only the outside world: GitHub reads and the D6 registry and worktree.

    The live PR read, the D6 Reviewer record check and the ruling comment
    check run through the real default code paths of task_submission_cli.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        worker_policy_fixture.install(self)
        self.github = {f'pulls/{PR}': {'number': PR, 'state': 'open', 'head': {'sha': HEAD}},
                       f'issues/comments/{RULING_ID}': lambda: _ruling_comment()}
        self.d6 = {'arbiter-d6': _registration()}
        self.worktree_heads = {}
        self.trusted = REPOSITORY
        for name, fake in (('_github_get', self._github_get),
                           ('read_d6_registration', self._read_d6),
                           ('collect_git_snapshot', self._snapshot),
                           ('_trusted_slug', self._trusted_slug)):
            patcher = patch.object(task_completion, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _trusted_slug(self, *_args, **_kwargs):
        # The trust source's repository slug; an exception stands for an unreadable source.
        if isinstance(self.trusted, Exception):
            raise self.trusted
        return self.trusted

    def _github_get(self, path):
        value = self.github.get(path)
        if value is None:
            raise LLMToolError('GitHub read failed')
        return value() if callable(value) else value

    def _read_d6(self, task_id, common_dir, **_kwargs):
        if common_dir != COMMON_DIR or task_id not in self.d6:
            raise LLMToolError('no registered D6 record')
        return self.d6[task_id]

    def _snapshot(self, root):
        registration = next(item for item in self.d6.values() if item.root == root)
        return SimpleNamespace(head=self.worktree_heads.get(root, registration.result_sha),
                               root=registration.root, common_dir=registration.common_dir,
                               branch=registration.branch)

    def live(self, **pull):
        self.github[f'pulls/{PR}'] = dict({'number': PR, 'state': 'open',
                                           'head': {'sha': HEAD}}, **pull)

    def use(self, record):
        self.store = DisputeStore(self.temp.name, record)
        claims = record.get('review_dispatch_claim') if type(record) is dict else None
        self.base_claims = list(claims) if type(claims) is list else []
        return self.store

    def arm(self, arbiter_claims=None, **kwargs):
        """Record the arbiter run's review claims: by default one matching the ruling.

        The claim stands for the arbiter run that llmtool dispatched and
        collected; it replaces any claim an earlier arm() recorded.
        """
        if arbiter_claims is None:
            versions = [item['version'] for item in self.base_claims
                        if item.get('head_sha') == kwargs.get('head', HEAD)]
            arbiter_claims = [_arbiter_claim(version=max(versions, default=0) + 1, **kwargs)]
        self.store.compare_and_update(TASK, expected={},
                                      review_dispatch_claim=self.base_claims + arbiter_claims)

    def refused(self, code, call, *args, **kwargs):
        if call == self.rule:
            # Record the arbiter's claim first: the snapshot covers only the ruling call.
            call, args, kwargs = self.prepare_rule(*args, **kwargs), (), {}
        before = self.store.get(TASK)
        with self.assertRaises(LLMToolError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.dispute_code, code)
        # A refusal writes nothing.
        self.assertEqual(self.store.get(TASK), before)

    def open(self, finding_id='F1', *, head=HEAD, evidence_ref=REF, live=False):
        return task_submission_cli.dispute_open(self.store, TASK, pr=PR, finding_id=finding_id,
                                                evidence_ref=evidence_ref,
                                                current_head=None if live else _head(head))

    def defer(self, finding_id, *, head=HEAD, live=False):
        return task_submission_cli.dispute_defer(self.store, TASK, pr=PR, finding_id=finding_id,
                                                 evidence_ref=REF,
                                                 current_head=None if live else _head(head))

    def prepare_rule(self, finding_id='F1', *, outcome='WITHDRAWN', reviewer='codex-reviewer',
                     session='arbiter-session-1', head=HEAD, evidence_ref=RULING_REF,
                     reviewer_task='arbiter-d6', comment=None, live=False,
                     arbiter_claims=None):
        """Record the arbiter run's claim and comment; return the ruling call.

        By default the arbiter run's one claim and its comment rule exactly as
        this call records (High Debug #1775).
        """
        actor = _ACTORS.get(reviewer, 'Codex')
        self.arm(arbiter_claims, finding_id=finding_id, outcome=outcome, head=head,
                 actor=actor, reviewer_task=reviewer_task, session=session)
        self.github[f'issues/comments/{RULING_ID}'] = (
            comment if comment is not None
            else lambda: _ruling_comment(finding_id, outcome, head=head, actor=actor))
        return lambda: task_submission_cli.dispute_rule(
            self.store, TASK, pr=PR, finding_id=finding_id, outcome=outcome,
            reviewer=reviewer, reviewer_task=reviewer_task, reviewer_session=session,
            evidence_ref=evidence_ref, current_head=None if live else _head(head))

    def rule(self, *args, **kwargs):
        return self.prepare_rule(*args, **kwargs)()

    def disputes(self):
        return self.store.get(TASK).get('review_disputes', [])


class DisputeOpenTests(_Case):
    def test_open_records_one_open_entry_bound_to_the_recorded_fail(self):
        self.use(_task())
        result = self.open()
        [entry] = self.disputes()
        self.assertEqual(set(entry), {'finding_id', 'pr', 'head_sha', 'review_version',
                                      'severity', 'state', 'evidence_ref', 'opened_at'})
        self.assertEqual((entry['finding_id'], entry['pr'], entry['head_sha'],
                          entry['review_version'], entry['severity'], entry['state'],
                          entry['evidence_ref']),
                         ('F1', PR, HEAD, 1, 'High', 'OPEN', REF))
        self.assertTrue(task_store._valid_utc_seconds(entry['opened_at']))
        self.assertEqual(result['state'], 'OPEN')
        # An AGY (Gemini) Task first reviewed by Claude: llmtool names Codex.
        self.assertEqual(result['arbiter'], {'reviewer': 'codex-reviewer', 'family': 'codex',
                                             'fresh_session': False})
        self.assertIs(result['review_launched'], False)

    def test_open_accepts_an_issue_comment_and_reads_the_live_pr_head(self):
        # The recorded published_pr lags behind the PR; the live read decides (v91 F3).
        self.use(_task(published_head=NEW_HEAD))
        task_submission_cli.dispute_open(self.store, TASK, pr=PR, finding_id='F3',
                                         evidence_ref=ISSUE_REF)
        self.assertEqual(self.disputes()[0]['evidence_ref'], ISSUE_REF)
        self.assertEqual(self.disputes()[0]['head_sha'], HEAD)
        task_completion._github_get.assert_any_call(f'pulls/{PR}')

    def test_open_needs_a_recorded_fail_verdict_of_the_pr_head_naming_the_finding(self):
        dispatched = dict(_review_claim(), state='DISPATCHED')
        cases = (
            ('DISPUTE_NO_FAIL_VERDICT', [], HEAD, 'F1'),
            ('DISPUTE_NO_FAIL_VERDICT', [dispatched], HEAD, 'F1'),
            ('DISPUTE_NO_FAIL_VERDICT', [_review_claim(status='PASS')], HEAD, 'F1'),
            ('DISPUTE_NO_FAIL_VERDICT', [_review_claim(result_head=NEW_HEAD)], HEAD, 'F1'),
            # The latest version of the head decides: a later PASS leaves nothing to dispute.
            ('DISPUTE_NO_FAIL_VERDICT',
             [_review_claim(), _review_claim(version=2, status='PASS')], HEAD, 'F1'),
            # The verdict was for another HEAD than the PR's current one.
            ('DISPUTE_NO_FAIL_VERDICT', [_review_claim()], NEW_HEAD, 'F1'),
            ('DISPUTE_FINDING_NOT_FOUND', [_review_claim()], HEAD, 'F9'),
            ('DISPUTE_FINDING_NOT_FOUND',
             [_review_claim(findings='Finding: address the reported issue\n')], HEAD, 'F1'),
            ('DISPUTE_FINDINGS_INVALID',
             [_review_claim(findings='Finding: F1 [High] a\nFinding: F1 [Low] b\n')],
             HEAD, 'F1'),
            ('DISPUTE_FINDINGS_INVALID',
             [_review_claim(findings='Finding: F1 [Severe] a.py:1 text\n')], HEAD, 'F1'),
        )
        for code, claims, head, finding_id in cases:
            with self.subTest(code=code, claims=claims, head=head, finding=finding_id):
                self.use(_task(claims=claims))
                self.refused(code, self.open, finding_id, head=head)

    def test_open_refuses_bad_arguments_and_an_unknown_head(self):
        self.use(_task())
        for finding_id in ('X1', 'F0', 'f1', ''):
            with self.subTest(finding=finding_id):
                self.refused('DISPUTE_ARGUMENT_INVALID', self.open, finding_id)
        for ref in ('https://github.com/example/other/pull/42#issuecomment-1001',
                    REF.replace('https', 'http'), REF.split('#')[0], 'see the PR'):
            with self.subTest(ref=ref):
                self.refused('DISPUTE_EVIDENCE_REF_INVALID', self.open, evidence_ref=ref)
        del self.github[f'pulls/{PR}']   # the live PR read fails
        self.refused('DISPUTE_HEAD_UNKNOWN', task_submission_cli.dispute_open, self.store,
                     TASK, pr=PR, finding_id='F1', evidence_ref=REF)
        self.refused('DISPUTE_HEAD_UNKNOWN', self.open, head='not-a-sha')

    def test_evidence_text_is_screened_like_other_task_text(self):
        # The real screen decides; its decision is injected, so no credential-shaped text.
        self.use(_task())
        real_screen = task_store._emitted_sensitive
        with patch.object(task_store, '_emitted_sensitive',
                          side_effect=lambda value: value == REF or real_screen(value)):
            self.refused('DISPUTE_EVIDENCE_REF_INVALID', self.open)
        self.open()
        flagged = 'arbiter-session-flagged'
        with patch.object(task_store, '_emitted_sensitive',
                          side_effect=lambda value: value == flagged or real_screen(value)):
            self.refused('DISPUTE_ARGUMENT_INVALID', self.rule, session=flagged)

    def test_a_second_open_dispute_of_one_finding_is_refused(self):
        self.use(_task())
        self.open()
        self.refused('DISPUTE_ALREADY_OPEN', self.open)
        self.refused('DISPUTE_ALREADY_OPEN', self.open, evidence_ref=ISSUE_REF)
        # Another finding of the same verdict is its own dispute.
        self.open('F3')
        self.assertEqual([item['finding_id'] for item in self.disputes()], ['F1', 'F3'])

    def test_a_ruled_finding_is_never_disputed_or_ruled_again(self):
        self.use(_task(claims=[_review_claim(),
                               _review_claim(head=NEW_HEAD, version=2,
                                             session='review-session-2')]))
        self.open()
        self.rule(outcome='UPHELD')
        self.refused('DISPUTE_ALREADY_RULED', self.open)
        self.refused('DISPUTE_ALREADY_RULED', self.rule, outcome='WITHDRAWN',
                     session='arbiter-session-2')
        # Not even after a new HEAD whose FAIL names the same finding again.
        self.refused('DISPUTE_ALREADY_RULED', self.open, head=NEW_HEAD)
        self.refused('DISPUTE_ALREADY_RULED', self.rule, head=NEW_HEAD,
                     session='arbiter-session-2')
        self.assertEqual([item['state'] for item in self.disputes()], ['RULED'])

    def test_an_abandoned_or_unknown_task_is_refused(self):
        self.use(dict(_task(), abandonment={'reason': 'dropped'}))
        self.refused('DISPUTE_TASK_ABANDONED', self.open)
        self.use(_task())
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.dispute_open(self.store, 'not-a-task', pr=PR, finding_id='F1',
                                             evidence_ref=REF, current_head=_head(HEAD))
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_ARGUMENT_INVALID')
        self.use(_task(disputes='broken'))
        self.refused('DISPUTE_RECORD_INVALID', self.open)


class DisputeDeferTests(_Case):
    def test_only_low_or_advisory_findings_may_be_deferred(self):
        self.use(_task())
        for finding_id in ('F1', 'F3', 'F5', 'F6'):   # High, Medium, Critical, Blocking
            with self.subTest(finding=finding_id):
                self.refused('DEFER_BLOCKING_REFUSED', self.defer, finding_id)
        self.assertEqual(self.defer('F2')['state'], 'DEFERRED')
        self.assertEqual(self.defer('F4')['severity'], 'Advisory')
        self.assertEqual([(item['finding_id'], item['state'], item['severity'])
                          for item in self.disputes()],
                         [('F2', 'DEFERRED', 'Low'), ('F4', 'DEFERRED', 'Advisory')])

    def test_blocker_severity_is_never_deferred(self):
        self.use(_task(claims=[_review_claim(findings='Finding: F1 [Blocker] a.py:1 text\n')]))
        self.refused('DEFER_BLOCKING_REFUSED', self.defer, 'F1')

    def test_a_deferred_finding_is_not_deferred_or_opened_twice_on_one_head(self):
        self.use(_task())
        self.defer('F2')
        self.refused('DISPUTE_ALREADY_DEFERRED', self.defer, 'F2')
        self.refused('DISPUTE_ALREADY_DEFERRED', self.open, 'F2')

    def test_the_store_refuses_a_deferred_blocking_entry_written_directly(self):
        for severity in ('Blocker', 'Blocking', 'Critical', 'High', 'Medium'):
            with self.subTest(severity=severity):
                self.use(_task())
                with self.assertRaises(LLMToolError) as caught:
                    self.store.compare_and_update(
                        TASK, expected={},
                        review_disputes=[_entry(severity=severity, state='DEFERRED')])
                self.assertEqual(caught.exception.task_store_error_code,
                                 'REVIEW_DISPUTE_INVALID')


class DisputeRuleTests(_Case):
    def test_the_ruling_binds_head_run_and_evidence_and_never_replaces_the_pass(self):
        self.use(_task())
        self.open()
        ruling_call = self.prepare_rule()   # records the arbiter run's claim
        before = self.store.get(TASK)
        result = ruling_call()
        after = self.store.get(TASK)
        [entry] = after['review_disputes']
        self.assertEqual(entry['state'], 'RULED')
        ruling = entry['ruling']
        self.assertEqual({key: ruling[key] for key in ruling if key != 'ruled_at'},
                         {'outcome': 'WITHDRAWN', 'head_sha': HEAD,
                          'reviewer': 'codex-reviewer', 'reviewer_task': 'arbiter-d6',
                          'reviewer_session': 'arbiter-session-1', 'evidence_ref': RULING_REF})
        self.assertGreaterEqual(ruling['ruled_at'], entry['opened_at'])
        # The disputed entry keeps its fields; nothing else in the Task moves.
        self.assertEqual({key: entry[key] for key in entry if key not in ('state', 'ruling')},
                         {key: value for key, value in before['review_disputes'][0].items()
                          if key != 'state'})
        self.assertEqual({key: value for key, value in after.items()
                          if key not in ('review_disputes', 'record_sha256')},
                         {key: value for key, value in before.items()
                          if key not in ('review_disputes', 'record_sha256')})
        self.assertIs(result['formal_pass_required'], True)
        self.assertEqual(result['outcome'], 'WITHDRAWN')

    def test_upheld_is_recorded_the_same_way(self):
        self.use(_task())
        self.open('F3')
        self.assertEqual(self.rule('F3', outcome='UPHELD')['outcome'], 'UPHELD')
        self.assertEqual(self.disputes()[0]['ruling']['outcome'], 'UPHELD')

    def test_rule_refuses_without_an_open_dispute_of_the_current_head(self):
        self.use(_task())
        self.refused('DISPUTE_NOT_OPEN', self.rule)
        self.defer('F2')
        self.refused('DISPUTE_NOT_OPEN', self.rule, 'F2')

    def test_a_head_change_makes_an_open_dispute_stale(self):
        self.use(_task())
        self.open()
        self.refused('DISPUTE_STALE', self.rule, head=NEW_HEAD)
        self.assertEqual(self.disputes()[0]['state'], 'OPEN')

    def test_rule_refuses_bad_arguments(self):
        self.use(_task())
        self.open()
        for changes in ({'outcome': 'DENIED'}, {'reviewer': 'pm'},
                        {'reviewer': 'claude-developer'}, {'session': ''},
                        {'session': 'has space'}):
            with self.subTest(changes=changes):
                self.refused('DISPUTE_ARGUMENT_INVALID', self.rule, **changes)
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.dispute_rule(
                self.store, TASK, pr=PR, finding_id='F1', outcome='WITHDRAWN',
                reviewer='codex-reviewer', reviewer_task='Not A D6', reviewer_session='s-1',
                evidence_ref=RULING_REF, current_head=_head(HEAD))
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_ARGUMENT_INVALID')
        # The ruling's evidence is its own comment, not the dispute's.
        self.refused('DISPUTE_EVIDENCE_REF_INVALID', self.rule, evidence_ref=REF)

    def test_only_the_arbiter_llmtool_chose_may_rule(self):
        # AGY (Gemini) Task, first reviewed by Claude: the arbiter is Codex.
        self.use(_task())
        self.open()
        self.refused('DISPUTE_ARBITER_MISMATCH', self.rule, reviewer='claude-reviewer',
                     session='arbiter-session-1')
        self.rule()

    def test_a_same_reviewer_arbiter_must_rule_in_a_fresh_session(self):
        # Claude-authored Task, first reviewed by Codex: only Codex is cross-family.
        older = _review_claim(head=NEW_HEAD, version=1, actor='Codex', account_mode='CODEX_CLI',
                              session='older-review-session')
        self.use(_task(developer={'provider_profile': 'claude', 'model': 'claude-opus-5-5'},
                       claims=[older, _review_claim(actor='Codex', account_mode='CODEX_CLI',
                                                    session='first-review-session')]))
        opened = self.open()
        self.assertEqual(opened['arbiter'], {'reviewer': 'codex-reviewer', 'family': 'codex',
                                             'fresh_session': True})
        self.refused('DISPUTE_ARBITER_MISMATCH', self.rule, reviewer='claude-reviewer')
        self.refused('DISPUTE_ARBITER_NOT_FRESH', self.rule, session='first-review-session')
        # No recorded review run of this PR is fresh either.
        self.refused('DISPUTE_ARBITER_NOT_FRESH', self.rule, session='older-review-session')
        self.rule(session='arbiter-session-1')
        self.assertEqual(self.disputes()[0]['ruling']['reviewer_session'], 'arbiter-session-1')

    def test_without_an_arbiter_the_dispute_stays_open_and_cannot_be_ruled(self):
        self.use(_task(developer={'provider_profile': 'unknown-route', 'model': None}))
        result = self.open()
        self.assertEqual(result['arbiter'], {'code': 'DISPUTE_ARBITER_UNAVAILABLE'})
        self.assertEqual(self.disputes()[0]['state'], 'OPEN')
        self.refused('DISPUTE_ARBITER_UNAVAILABLE', self.rule)

    def test_open_and_rule_never_run_or_launch_a_review(self):
        self.use(_task())
        launch = Mock(side_effect=AssertionError('no process may start'))
        with patch.object(subprocess, 'run', launch), patch.object(subprocess, 'Popen', launch):
            self.assertIs(self.open()['review_launched'], False)
            self.rule()
            self.defer('F2')
        launch.assert_not_called()


class ArbiterAuthenticationTests(_Case):
    """Security v91 F1: the ruling's Reviewer Task, session and comment are authenticated."""

    def setUp(self):
        super().setUp()
        self.use(_task())
        self.open()

    def test_a_registered_arbiter_and_its_own_ruling_comment_are_accepted(self):
        self.rule()
        task_completion.read_d6_registration.assert_called_with(
            'arbiter-d6', COMMON_DIR, allowed_states=task_completion._D6_EVIDENCE_STATES)
        task_completion.collect_git_snapshot.assert_called_with('/registered/arbiter-d6')
        task_completion._github_get.assert_any_call(f'issues/comments/{RULING_ID}')
        self.assertEqual(self.disputes()[0]['state'], 'RULED')

    def test_a_fabricated_reviewer_task_is_refused(self):
        self.d6.update({
            EXECUTOR: _registration(task=EXECUTOR),
            'claude-d6': _registration(task='claude-d6', agent='claude-reviewer'),
            'developer-d6': _registration(task='developer-d6', agent='agy'),
            'old-head-d6': _registration(task='old-head-d6', head=NEW_HEAD),
            'renamed-d6': _registration(task='other-d6'),
            'moved-d6': _registration(task='moved-d6'),
        })
        self.worktree_heads['/registered/moved-d6'] = NEW_HEAD
        for reviewer_task in ('made-up-d6',     # no registered D6 record at all
                              EXECUTOR,         # the Executor's own D6
                              'claude-d6',      # not the chosen arbiter (Codex)
                              'developer-d6',   # not a Reviewer record
                              'old-head-d6',    # registered at another HEAD
                              'renamed-d6',     # the registry answered for another Task
                              'moved-d6'):      # its worktree left the disputed HEAD
            with self.subTest(reviewer_task=reviewer_task):
                self.refused('DISPUTE_ARBITER_UNVERIFIED', self.rule,
                             reviewer_task=reviewer_task)
        # The first review run's own D6 is never the arbiter.
        self.d6['reviewer-d6'] = _registration(task='reviewer-d6')
        self.refused('DISPUTE_ARBITER_NOT_FRESH', self.rule, reviewer_task='reviewer-d6')

    def test_a_task_without_a_d6_common_dir_cannot_verify_the_arbiter(self):
        record = _task()
        record['completion'] = {'executor_task_id': EXECUTOR, 'evidence': {}}
        self.use(record)
        self.open()
        self.refused('DISPUTE_ARBITER_UNVERIFIED', self.rule)

    def test_a_foreign_session_is_refused(self):
        self.d6['other-d6'] = _registration(task='other-d6', session='other-session')
        # Another registered Reviewer's session, and a session no record holds.
        for session in ('other-session', 'arbiter-session-9'):
            with self.subTest(session=session):
                self.refused('DISPUTE_ARBITER_SESSION_MISMATCH', self.rule, session=session)
        self.d6['arbiter-d6'] = _registration(session=None)
        self.refused('DISPUTE_ARBITER_SESSION_MISMATCH', self.rule)

    def test_the_developer_run_session_is_never_the_arbiter_session(self):
        self.use(_task(developer={'execution': {'native_session_id': 'arbiter-session-1'}}))
        self.open()
        self.refused('DISPUTE_ARBITER_NOT_INDEPENDENT', self.rule)

    def test_an_unrelated_comment_url_is_refused(self):
        base = f'https://github.com/{REPOSITORY}/'
        for ref in (base + f'pull/{PR + 1}#issuecomment-{RULING_ID}',   # another PR
                    ISSUE_REF):                                         # an Issue thread
            with self.subTest(ref=ref):
                self.refused('DISPUTE_EVIDENCE_REF_INVALID', self.rule, evidence_ref=ref)
        # A comment URL of this PR that GitHub does not return.
        self.refused('DISPUTE_RULING_EVIDENCE_INVALID', self.rule,
                     evidence_ref=base + f'pull/{PR}#issuecomment-1009')

    def test_a_comment_that_is_not_this_arbiters_ruling_is_refused(self):
        first_review = dict(_ruling_comment(), body=_verdict())
        second_line = f'Ruling: F1 UPHELD | PR: #{PR} | HEAD: {HEAD}\n'
        cases = {
            'on another PR thread': _ruling_comment(issue=PR + 1),
            'another comment id': _ruling_comment(comment_id=RULING_ID + 1),
            'the other family': _ruling_comment(actor='Claude'),
            'another HEAD': _ruling_comment(head=NEW_HEAD),
            'another PR in the ruling line': _ruling_comment(pr=PR + 1),
            'the other outcome': _ruling_comment(outcome='UPHELD'),
            'another finding': _ruling_comment('F3'),
            'no Task binding line': _ruling_comment(binding=False),
            'edited after posting': _ruling_comment(edited=True),
            'older than the dispute': _ruling_comment(created='2000-01-01T00:00:00Z'),
            'two ruling lines': _ruling_comment(extra=second_line),
            # The same lines, but not the arbiter claim's review_text byte for byte.
            'CRLF line ends': _ruling_comment(body=_ruling_body().replace('\n', '\r\n')),
            'a trailing space': _ruling_comment(body=_ruling_body() + ' '),
            'an added closing note': _ruling_comment(extra='Edited by the PM.\n'),
            'the first review verdict': first_review,
            'no body': dict(_ruling_comment(), body=None),
            'not a comment': ['not', 'a', 'comment'],
        }
        for name, comment in cases.items():
            with self.subTest(case=name):
                self.refused('DISPUTE_RULING_EVIDENCE_INVALID', self.rule, comment=comment)
        self.rule()

    def test_the_comment_login_is_never_identity(self):
        # High Debug #1775 (CWE-290): all agents post as one login (#926), so the
        # author's login neither authenticates nor refuses a ruling. The arbiter
        # run's claim binds the comment through its exact review_text.
        self.refused('DISPUTE_RULING_EVIDENCE_INVALID', self.rule,
                     comment=_ruling_comment(login=REVIEW_LOGIN, extra='Edited by the PM.\n'))
        self.assertEqual(self.disputes()[0]['state'], 'OPEN')
        self.rule(comment=_ruling_comment(login='another-account'))
        self.assertEqual(self.disputes()[0]['state'], 'RULED')


class RulingClaimProvenanceTests(_Case):
    """High Debug #1775 (CWE-290): one COMPLETED claim binds the arbiter run's ruling."""

    def setUp(self):
        super().setUp()
        self.use(_task())
        self.open()

    def test_exactly_one_matching_claim_and_its_exact_text_are_accepted(self):
        result = self.rule()
        self.assertEqual(result['state'], 'RULED')
        [claim] = [item for item in self.store.get(TASK)['review_dispatch_claim']
                   if item['reviewer_task'] == 'arbiter-d6']
        comment = task_completion._github_get(f'issues/comments/{RULING_ID}')
        self.assertEqual(comment['body'].encode('utf-8'),
                         claim['review_result']['review_text'].encode('utf-8'))

    def test_a_ruling_without_an_arbiter_claim_is_refused(self):
        self.refused('DISPUTE_RULING_CLAIM_MISSING', self.rule, arbiter_claims=[])
        self.assertEqual(self.disputes()[0]['state'], 'OPEN')

    def test_more_than_one_candidate_claim_is_refused(self):
        cases = {
            'two completed runs of the arbiter D6 on one HEAD':
                [_arbiter_claim(version=2), _arbiter_claim(version=3)],
            'a completed run and a retry still dispatched':
                [_arbiter_claim(version=2), _arbiter_claim(version=3, state='DISPATCHED')],
            'the same run recorded on another HEAD too':
                [_arbiter_claim(version=2), _arbiter_claim(head=NEW_HEAD, version=1)],
        }
        for name, claims in cases.items():
            with self.subTest(case=name):
                self.refused('DISPUTE_RULING_CLAIM_AMBIGUOUS', self.rule, arbiter_claims=claims)
        self.assertEqual(self.disputes()[0]['state'], 'OPEN')

    def test_a_claim_of_the_wrong_family_d6_session_or_task_is_refused(self):
        cases = {
            'the Claude family, not the chosen Codex arbiter':
                _arbiter_claim(account_mode='EXPLICIT_CLAUDE_CONFIG_DIR'),
            'an account mode with no known family': _arbiter_claim(account_mode='LEGACY'),
            'another Executor D6': _arbiter_claim(executor='other-executor-d6'),
            'another Task': _arbiter_claim(task_id='f' * 32),
            'another run': _arbiter_claim(run_id='f' * 32),
            'another contract': _arbiter_claim(contract_digest='f' * 64),
            'another PR': _arbiter_claim(pr=PR + 1),
            'not completed': _arbiter_claim(state='DISPATCHED'),
            'an empty review text': _arbiter_claim(text=''),
            'a blank review text': _arbiter_claim(text='\n  \n'),
            'no review result': dict(_arbiter_claim(), review_result=None),
        }
        for name, claim in cases.items():
            with self.subTest(case=name):
                self.refused('DISPUTE_RULING_CLAIM_INVALID', self.rule, arbiter_claims=[claim])
        self.assertEqual(self.disputes()[0]['state'], 'OPEN')

    def test_a_claim_of_a_stale_head_is_refused(self):
        result_moved = _arbiter_claim()
        result_moved['review_result'] = dict(result_moved['review_result'], head=NEW_HEAD)
        for claim in (_arbiter_claim(head=NEW_HEAD, version=1),   # the run reviewed another HEAD
                      result_moved):                              # its result names another HEAD
            with self.subTest(claim_head=claim['head_sha'],
                              result_head=claim['review_result']['head']):
                self.refused('DISPUTE_RULING_CLAIM_STALE', self.rule, arbiter_claims=[claim])
        self.assertEqual(self.disputes()[0]['state'], 'OPEN')

    def test_a_claim_older_than_the_disputed_review_is_refused(self):
        self.use(_task(claims=[_review_claim(version=3)]))
        self.open()
        self.assertEqual(self.disputes()[0]['review_version'], 3)
        self.refused('DISPUTE_RULING_CLAIM_INVALID', self.rule,
                     arbiter_claims=[_arbiter_claim(version=2)])
        self.rule(arbiter_claims=[_arbiter_claim(version=4)])
        self.assertEqual(self.disputes()[0]['state'], 'RULED')

    def test_a_claim_of_another_session_or_d6_is_never_the_arbiter_run(self):
        # The run that produced the text is not this arbiter's run: a recorded
        # run of another session or Reviewer D6 is not fresh.
        for claim in (_arbiter_claim(session='arbiter-session-2'),
                      _arbiter_claim(reviewer_task='claude-d6')):
            with self.subTest(claim=(claim['reviewer_task'], claim['native_session_id'])):
                self.refused('DISPUTE_ARBITER_NOT_FRESH', self.rule, arbiter_claims=[claim])
        self.assertEqual(self.disputes()[0]['state'], 'OPEN')

    def test_the_validator_alone_binds_d6_session_family_and_head(self):
        # Defence in depth: _dispute_ruling_claim refuses on its own, without
        # the freshness or D6 registry checks in front of it.
        entry = _entry()
        arbiter = {'reviewer': 'codex-reviewer', 'family': 'codex', 'fresh_session': False,
                   'excluded_session': 'review-session'}
        cases = (
            ('DISPUTE_RULING_CLAIM_INVALID', [_arbiter_claim(session='arbiter-session-2')]),
            ('DISPUTE_RULING_CLAIM_INVALID', [_arbiter_claim(reviewer_task='claude-d6')]),
            ('DISPUTE_RULING_CLAIM_INVALID', [_arbiter_claim(actor='Claude')]),
            ('DISPUTE_RULING_CLAIM_INVALID', ['not a claim']),
            ('DISPUTE_RULING_CLAIM_STALE', [_arbiter_claim(head=NEW_HEAD, version=1)]),
            ('DISPUTE_RULING_CLAIM_AMBIGUOUS',
             [_arbiter_claim(version=2), _arbiter_claim(session='other-session', version=3,
                                                        reviewer_task='arbiter-d6')]),
            ('DISPUTE_RULING_CLAIM_MISSING', []),
        )
        for code, claims in cases:
            with self.subTest(code=code, claims=claims):
                record = _task(claims=[_review_claim()] + claims)
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli._dispute_ruling_claim(
                        record, entry, pr=PR, head=HEAD, arbiter=arbiter,
                        reviewer_task='arbiter-d6', reviewer_session='arbiter-session-1')
                self.assertEqual(caught.exception.dispute_code, code)
        record = _task(claims=[_review_claim(), _arbiter_claim()])
        claim = task_submission_cli._dispute_ruling_claim(
            record, entry, pr=PR, head=HEAD, arbiter=arbiter,
            reviewer_task='arbiter-d6', reviewer_session='arbiter-session-1')
        self.assertEqual(claim['review_result']['review_text'], _ruling_body())

    def test_a_ruling_claim_is_never_taken_for_the_disputed_verdict(self):
        # Once the arbiter's claim is recorded, the FAIL it rules on still decides.
        self.arm()
        self.assertEqual(self.open('F3')['severity'], 'Medium')
        self.assertEqual([item['review_version'] for item in self.disputes()], [1, 1])


class LiveHeadTests(_Case):
    """Security v91 F3: the dispute HEAD comes from a live PR read."""

    def test_open_defer_and_rule_read_the_live_pr_head(self):
        self.use(_task(published_head=NEW_HEAD))
        self.assertEqual(self.open(live=True)['head_sha'], HEAD)
        self.assertEqual(self.defer('F2', live=True)['head_sha'], HEAD)
        self.assertEqual(self.rule(live=True)['head_sha'], HEAD)

    def test_a_recorded_published_head_never_stands_in_for_the_live_one(self):
        self.use(_task(published_head=HEAD))
        self.live(head={'sha': NEW_HEAD})
        self.refused('DISPUTE_NO_FAIL_VERDICT', self.open, live=True)
        self.refused('DISPUTE_NO_FAIL_VERDICT', self.defer, 'F2', live=True)

    def test_a_moved_live_head_makes_the_open_dispute_stale(self):
        self.use(_task())
        self.open(live=True)
        self.live(head={'sha': NEW_HEAD})
        self.refused('DISPUTE_STALE', self.rule, live=True)
        self.assertEqual(self.disputes()[0]['state'], 'OPEN')

    def test_an_unreadable_or_malformed_pr_read_refuses(self):
        self.use(_task())
        self.open(live=True)
        cases = (
            ('DISPUTE_HEAD_UNKNOWN', None),                                   # read failed
            ('DISPUTE_HEAD_UNKNOWN', ['not', 'a', 'pull']),
            ('DISPUTE_HEAD_UNKNOWN', {'number': PR + 1, 'state': 'open',
                                      'head': {'sha': HEAD}}),
            ('DISPUTE_HEAD_UNKNOWN', {'number': PR, 'state': 'open'}),
            ('DISPUTE_HEAD_UNKNOWN', {'number': PR, 'state': 'open',
                                      'head': {'sha': 'not-a-sha'}}),
            ('DISPUTE_PR_NOT_OPEN', {'number': PR, 'state': 'closed',
                                     'head': {'sha': HEAD}}),
        )
        for code, answer in cases:
            with self.subTest(code=code, answer=answer):
                if answer is None:
                    self.github.pop(f'pulls/{PR}', None)
                else:
                    self.github[f'pulls/{PR}'] = answer
                self.refused(code, self.open, 'F3', live=True)
                self.refused(code, self.defer, 'F2', live=True)
                self.refused(code, self.rule, live=True)

    def test_an_injected_reader_that_fails_refuses_head_unknown(self):
        self.use(_task())

        def broken(_record, _pr):
            raise RuntimeError('read failed')

        self.refused('DISPUTE_HEAD_UNKNOWN', task_submission_cli.dispute_open, self.store,
                     TASK, pr=PR, finding_id='F1', evidence_ref=REF, current_head=broken)


class DisputeCliTests(_Case):
    def _parse(self, argv):
        parser = argparse.ArgumentParser()
        task_submission_cli.add_task_submission_parsers(parser.add_subparsers(dest='action'))
        return parser.parse_args(argv)

    def _run(self, argv):
        out = io.StringIO()
        with patch('llmtool.pm_caller.check_caller', return_value=None):
            code = task_submission_cli.run_task_dispute(self._parse(argv), store=self.store,
                                                        stdout=out, current_head=_head(HEAD))
        return code, json.loads(out.getvalue())

    def test_open_defer_and_rule_through_the_cli_adapter(self):
        self.use(_task())
        common = ['--pr', str(PR), '--evidence-ref']
        code, out = self._run(['dispute', 'open', TASK, '--finding', 'F1'] + common + [REF])
        self.assertEqual((code, out['state'], out['review_launched']), (0, 'OPEN', False))
        code, out = self._run(['dispute', 'defer', TASK, '--finding', 'F1'] + common + [REF])
        self.assertEqual((code, out), (1, {'task_id': TASK, 'code': 'DEFER_BLOCKING_REFUSED'}))
        code, out = self._run(['dispute', 'defer', TASK, '--finding', 'F4'] + common + [REF])
        self.assertEqual((code, out['state']), (0, 'DEFERRED'))
        self.arm()   # the arbiter run's one COMPLETED claim (High Debug #1775)
        code, out = self._run(['dispute', 'rule', TASK, '--finding', 'F1'] + common +
                              [RULING_REF, '--outcome', 'WITHDRAWN',
                               '--reviewer', 'codex-reviewer', '--reviewer-task', 'arbiter-d6',
                               '--reviewer-session', 'arbiter-session-1'])
        self.assertEqual((code, out['state'], out['formal_pass_required']), (0, 'RULED', True))
        code, out = self._run(['dispute', 'open', TASK, '--finding', 'F1'] + common + [REF])
        self.assertEqual((code, out['code']), (1, 'DISPUTE_ALREADY_RULED'))

    def test_the_parser_limits_outcome_and_reviewer_choices(self):
        base = ['dispute', 'rule', TASK, '--pr', str(PR), '--finding', 'F1',
                '--evidence-ref', RULING_REF, '--reviewer-task', 'arbiter-d6',
                '--reviewer-session', 'arbiter-session-1']
        for extra in (['--outcome', 'DENIED', '--reviewer', 'codex-reviewer'],
                      ['--outcome', 'UPHELD', '--reviewer', 'pm']):
            with self.subTest(extra=extra), patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit):
                    self._parse(base + extra)

    def test_a_caller_that_does_not_own_the_task_is_refused_first(self):
        from llmtool import pm_caller
        self.use(_task())
        args = self._parse(['dispute', 'open', TASK, '--pr', str(PR), '--finding', 'F1',
                            '--evidence-ref', REF])
        # Built without its constructor: only the code attribute is read here.
        error = pm_caller.PMCallerRefused.__new__(pm_caller.PMCallerRefused)
        error.code = 'PM_CALLER_NOT_OWNER'
        with patch.object(pm_caller, 'check_caller', side_effect=error), \
             patch.object(pm_caller, 'refusal', return_value={'code': 'PM_CALLER_NOT_OWNER'}):
            code = task_submission_cli.run_task_dispute(args, store=self.store,
                                                        stdout=io.StringIO(),
                                                        current_head=_head(HEAD))
        self.assertEqual(code, 1)
        self.assertNotIn('review_disputes', self.store.get(TASK))


class DisputeCliRouteTests(_Case):
    """Security v91 F2: `llmtool task dispute` reaches run_task_dispute via cli.main."""

    def test_task_actions_routes_dispute_without_a_fresh_source_check(self):
        self.assertEqual(cli.TASK_ACTIONS['dispute'], ('run_task_dispute', False))
        for action in ('open', 'defer', 'rule'):
            argv = ['task', 'dispute', action, TASK, '--pr', str(PR), '--finding', 'F1',
                    '--evidence-ref', REF]
            if action == 'rule':
                argv[-1] = RULING_REF
                argv += ['--outcome', 'WITHDRAWN', '--reviewer', 'codex-reviewer',
                         '--reviewer-task', 'arbiter-d6', '--reviewer-session', 'arbiter-session-1']
            with self.subTest(action=action), \
                    patch.object(task_submission_cli, 'run_task_dispute',
                                 return_value=17) as handler, \
                    patch('llmtool.source_freshness.require_fresh_source') as fresh:
                self.assertEqual(cli.main(argv), 17)
                [call] = handler.call_args_list
                self.assertEqual((call.args[0].action, call.args[0].dispute_action),
                                 ('dispute', action))
                fresh.assert_not_called()

    def _main(self, argv):
        out = io.StringIO()
        with patch.object(task_submission_cli, 'TaskStore', return_value=self.store), \
             patch('llmtool.pm_caller.check_caller', return_value=None), \
             patch('llmtool.source_freshness.require_fresh_source'), \
             patch('sys.stdout', out):
            code = cli.main(argv)
        return code, json.loads(out.getvalue())

    def test_open_defer_and_rule_through_cli_main_with_the_live_head(self):
        self.use(_task(published_head=NEW_HEAD))
        common = ['--pr', str(PR), '--evidence-ref']
        code, out = self._main(['task', 'dispute', 'open', TASK, '--finding', 'F1'] +
                               common + [REF])
        self.assertEqual((code, out['state'], out['head_sha'], out['review_launched']),
                         (0, 'OPEN', HEAD, False))
        code, out = self._main(['task', 'dispute', 'defer', TASK, '--finding', 'F4'] +
                               common + [REF])
        self.assertEqual((code, out['state']), (0, 'DEFERRED'))
        self.arm()   # the arbiter run's one COMPLETED claim (High Debug #1775)
        code, out = self._main(['task', 'dispute', 'rule', TASK, '--finding', 'F1'] + common +
                               [RULING_REF, '--outcome', 'WITHDRAWN',
                                '--reviewer', 'codex-reviewer', '--reviewer-task', 'arbiter-d6',
                                '--reviewer-session', 'arbiter-session-1'])
        self.assertEqual((code, out['state'], out['formal_pass_required']), (0, 'RULED', True))
        # A refusal through cli.main prints only its code and exits 1.
        code, out = self._main(['task', 'dispute', 'rule', TASK, '--finding', 'F1'] + common +
                               [RULING_REF, '--outcome', 'UPHELD',
                                '--reviewer', 'codex-reviewer', '--reviewer-task', 'arbiter-d6',
                                '--reviewer-session', 'arbiter-session-1'])
        self.assertEqual((code, out), (1, {'task_id': TASK, 'code': 'DISPUTE_ALREADY_RULED'}))
        self.assertEqual([(item['finding_id'], item['state']) for item in self.disputes()],
                         [('F1', 'RULED'), ('F4', 'DEFERRED')])


class DisputeRefPartsTests(unittest.TestCase):
    def test_only_a_valid_comment_url_has_parts(self):
        self.assertEqual(task_store.review_dispute_ref_parts(RULING_REF, REPOSITORY),
                         (PR, RULING_ID))
        self.assertEqual(task_store.review_dispute_ref_parts(ISSUE_REF, REPOSITORY), (1502, 1003))
        for value in (None, 42, 'see the PR', REF.split('#')[0],
                      f'https://github.com/{OTHER_REPOSITORY}/pull/42#issuecomment-1001'):
            with self.subTest(value=value):
                self.assertIsNone(task_store.review_dispute_ref_parts(value, REPOSITORY))

    def test_the_repository_is_the_callers_not_the_modules(self):
        # The same URL is valid only for the repository the caller passes in.
        other = f'https://github.com/{OTHER_REPOSITORY}/pull/42#issuecomment-1001'
        self.assertEqual(task_store.review_dispute_ref_parts(other, OTHER_REPOSITORY),
                         (42, 1001))
        self.assertIsNone(task_store.review_dispute_ref_parts(REF, OTHER_REPOSITORY))
        for repository in (None, '', 'no-slash', 'a/b/c', 'example/..', REPOSITORY.upper()):
            with self.subTest(repository=repository):
                self.assertFalse(task_store.valid_review_dispute_ref(REF, repository))

    def test_dispute_production_code_holds_no_repository_literal(self):
        # PR #1775 Linux CI: the owner literal guard; the slug comes from the caller.
        owner = REPOSITORY.split('/')[0]
        for function in (task_store.valid_review_dispute_ref,
                         task_store.review_dispute_ref_parts,
                         task_submission_cli._dispute_arguments,
                         task_submission_cli._dispute_ruling_comment):
            with self.subTest(function=function.__name__):
                self.assertNotIn(owner, inspect.getsource(function))
        self.assertNotIn(owner, task_store._REVIEW_DISPUTE_REF.pattern)


class TrustedRepositoryTests(_Case):
    """PR #1775: evidence is checked against the trusted slug the commands read."""

    OTHER_REF = f'https://github.com/{OTHER_REPOSITORY}/pull/42#issuecomment-1001'
    OTHER_RULING_REF = f'https://github.com/{OTHER_REPOSITORY}/pull/42#issuecomment-1002'

    def test_evidence_must_name_the_trusted_repository(self):
        self.use(_task())
        self.trusted = OTHER_REPOSITORY
        self.refused('DISPUTE_EVIDENCE_REF_INVALID', self.open)          # REF names REPOSITORY
        self.open(evidence_ref=self.OTHER_REF)
        self.assertEqual(self.disputes()[0]['evidence_ref'], self.OTHER_REF)
        self.refused('DISPUTE_EVIDENCE_REF_INVALID', self.rule)          # RULING_REF likewise
        # The live comment's thread must be of the trusted repository too.
        self.refused('DISPUTE_RULING_EVIDENCE_INVALID', self.rule,
                     evidence_ref=self.OTHER_RULING_REF)
        self.rule(evidence_ref=self.OTHER_RULING_REF,
                  comment=lambda: _ruling_comment(repository=OTHER_REPOSITORY))
        self.assertEqual(self.disputes()[0]['ruling']['evidence_ref'], self.OTHER_RULING_REF)

    def test_an_unreadable_or_malformed_trusted_slug_refuses(self):
        self.use(_task())
        for trusted in (LLMToolError('trust source unreadable'), RuntimeError('broken'),
                        None, '', 'no-slash', 'a/b/c', 'example/..', 42):
            with self.subTest(trusted=trusted):
                self.trusted = trusted
                self.refused('DISPUTE_REPOSITORY_UNKNOWN', self.open)
                self.refused('DISPUTE_REPOSITORY_UNKNOWN', self.defer, 'F2')
        self.trusted = REPOSITORY
        self.open()
        for trusted in (LLMToolError('trust source unreadable'), None, 'a/b/c'):
            with self.subTest(rule_trusted=trusted):
                self.trusted = trusted
                self.refused('DISPUTE_REPOSITORY_UNKNOWN', self.rule)
        self.assertEqual(self.disputes()[0]['state'], 'OPEN')


class ArbiterChooserTests(unittest.TestCase):
    def choose(self, developer, first_reviewer, session='first-session'):
        return review_repair.choose_arbiter(
            developer_family=review_repair.developer_family(developer),
            first_reviewer=first_reviewer, first_review_session=session)

    def test_the_pm_has_no_input_to_the_choice(self):
        self.assertEqual(set(inspect.signature(review_repair.choose_arbiter).parameters),
                         {'developer_family', 'first_reviewer', 'first_review_session'})

    def test_claude_authored_uses_a_fresh_codex_session(self):
        chosen = self.choose({'provider_profile': 'claude'}, 'codex-reviewer')
        self.assertEqual(chosen, {'reviewer': 'codex-reviewer', 'family': 'codex',
                                  'fresh_session': True, 'excluded_session': 'first-session'})

    def test_codex_authored_uses_a_fresh_claude_session(self):
        chosen = self.choose({'provider_profile': 'codex'}, 'claude-reviewer')
        self.assertEqual((chosen['reviewer'], chosen['fresh_session']),
                         ('claude-reviewer', True))

    def test_gw_agent_authored_prefers_the_other_reviewer_family(self):
        developer = {'provider_profile': 'gw-agent', 'model': 'home-model-7b'}
        self.assertEqual(review_repair.developer_family(developer), 'gw-agent')
        for first, expected in (('claude-reviewer', 'codex-reviewer'),
                                ('codex-reviewer', 'claude-reviewer')):
            with self.subTest(first=first):
                chosen = self.choose(developer, first)
                self.assertEqual((chosen['reviewer'], chosen['fresh_session']),
                                 (expected, False))

    def test_opencode_authored_prefers_the_other_reviewer_family(self):
        developer = {'provider_profile': 'opencode', 'model': 'deepseek-free'}
        self.assertEqual(review_repair.developer_family(developer), 'opencode')
        chosen = self.choose(developer, 'claude-reviewer')
        self.assertEqual((chosen['reviewer'], chosen['fresh_session']),
                         ('codex-reviewer', False))

    def test_a_route_serving_another_vendors_model_takes_that_family(self):
        for developer, family, first, expected in (
                ({'provider_profile': 'opencode', 'model': 'Claude-Sonnet'}, 'claude',
                 'codex-reviewer', ('codex-reviewer', True)),
                ({'provider_profile': 'gw-agent', 'model': 'gpt-oss-20b'}, 'codex',
                 'claude-reviewer', ('claude-reviewer', True))):
            with self.subTest(developer=developer):
                self.assertEqual(review_repair.developer_family(developer), family)
                chosen = self.choose(developer, first)
                self.assertEqual((chosen['reviewer'], chosen['fresh_session']), expected)

    def test_agy_authored_is_gemini_and_prefers_the_other_reviewer(self):
        self.assertEqual(review_repair.developer_family({'provider_profile': 'agy'}), 'gemini')
        chosen = self.choose({'provider_profile': 'agy'}, 'claude-reviewer')
        self.assertEqual(chosen['reviewer'], 'codex-reviewer')

    def test_the_arbiter_is_never_the_developer_family(self):
        # A same-family first review (never a formal one) still leaves a cross-family arbiter.
        chosen = self.choose({'provider_profile': 'claude'}, 'claude-reviewer')
        self.assertEqual((chosen['reviewer'], chosen['fresh_session']),
                         ('codex-reviewer', False))

    def test_unknown_inputs_refuse_arbiter_unavailable(self):
        for developer_family, first, session in (
                (None, 'codex-reviewer', 's'), ('', 'codex-reviewer', 's'),
                ('claude', 'pm', 's'), ('claude', None, 's'),
                ('claude', 'codex-reviewer', ''), ('claude', 'codex-reviewer', None)):
            with self.subTest(family=developer_family, first=first, session=session):
                with self.assertRaises(LLMToolError) as caught:
                    review_repair.choose_arbiter(developer_family=developer_family,
                                                 first_reviewer=first,
                                                 first_review_session=session)
                self.assertEqual(caught.exception.dispute_code, 'DISPUTE_ARBITER_UNAVAILABLE')
        self.assertIsNone(review_repair.developer_family(None))
        self.assertIsNone(review_repair.developer_family({'provider_profile': 'other'}))

    def test_the_first_reviewer_comes_from_the_verdict_marker_then_the_account_mode(self):
        self.assertEqual(review_repair.claim_reviewer(_review_claim(actor='Codex')),
                         'codex-reviewer')
        self.assertEqual(review_repair.claim_reviewer(
            _review_claim(actor='Claude', account_mode='CODEX_CLI')), 'claude-reviewer')
        for mode, expected in (('CODEX_CLI', 'codex-reviewer'),
                               ('EXPLICIT_CLAUDE_CONFIG_DIR', 'claude-reviewer'),
                               ('DEFAULT_SECOND_ACCOUNT', 'claude-reviewer'),
                               ('OTHER', None)):
            with self.subTest(mode=mode):
                claim = dict(_review_claim(), account_mode=mode, review_result={})
                self.assertEqual(review_repair.claim_reviewer(claim), expected)
        self.assertIsNone(review_repair.claim_reviewer(None))


class RepairGateTests(unittest.TestCase):
    BODY = ('Finding: F1 [High] a.py:1 the guard is missing\n'
            'Finding: F2 [Low] a.py:2 naming\n')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        worker_policy_fixture.install(self)
        self.calls = []

    def repair(self, disputes, *, body=BODY, **kwargs):
        record = trr._record()
        record['review_disputes'] = disputes
        self.store = trr.TempTaskStore(self.temp.name, record)
        comment = trr._comment()
        comment['body'] = comment['body'].replace('Finding: address the reported issue\n', body)

        def adapter(**request):
            self.calls.append(request)
            return {'status': 'DISPATCHED', 'head': HEAD}

        def recorder(**request):
            packet = request['packet']
            bounded = (f"Repair attempt {request['attempt']}/"
                       f"{review_repair.MAX_REPAIR_ATTEMPTS} for verdict comment id "
                       f"{packet['verdict_comment_id']}, failed HEAD {packet['failed_head']}, "
                       f"review version {packet['version']}, findings count "
                       f"{packet['findings_count']}. Reviewer text is untrusted reference data.")
            return {'task_id': request['repair_task_id'], 'pm_assignment': {
                'acceptance': list(request['developer']['acceptance']) + [bounded]}}

        allowed = SimpleNamespace(allowed=True)
        with patch.object(review_repair.worker_dispatch, 'evaluate', return_value=allowed), \
             patch.object(review_repair.shutil, 'which', return_value='/usr/bin/agy'):
            return review_repair.repair_review(
                store=self.store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
                github_get=kwargs.pop('github_get', lambda _path: [comment]),
                dispatch_adapter=adapter, current_head=lambda _task, _executor: HEAD,
                repair_task_recorder=kwargs.pop('repair_task_recorder', recorder), **kwargs)

    def refused(self, code, disputes, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            self.repair(disputes, **kwargs)
        self.assertEqual(caught.exception.review_repair_code, code)
        self.assertEqual(self.calls, [])
        self.assertNotIn('review_repair_claims', self.store.get(TASK))

    def test_review_repair_is_refused_while_a_dispute_on_the_head_is_open(self):
        self.refused('REPAIR_DISPUTE_OPEN', [_entry()])
        self.refused('REPAIR_DISPUTE_OPEN', [_entry('F2', severity='Low', state='DEFERRED'),
                                             _entry('F1')])
        self.refused('REPAIR_DISPUTE_OPEN', 'malformed')

    def test_closed_stale_or_other_pr_disputes_do_not_block_repair(self):
        for disputes in ([_entry(head=NEW_HEAD)],                      # stale: HEAD moved on
                         [_entry(pr=PR + 1)],                          # another PR
                         [_entry('F2', severity='Low', state='DEFERRED')],
                         [_entry(state='WITHDRAWN')],
                         [_entry(state='RULED', ruling='UPHELD')]):
            with self.subTest(disputes=disputes):
                self.calls = []
                self.repair(disputes)
                self.assertEqual(len(self.calls), 1)

    def test_a_fail_whose_blocking_findings_were_all_withdrawn_is_not_valid(self):
        self.refused('REPAIR_NO_BLOCKING_FINDING', [_entry(state='RULED', ruling='WITHDRAWN')])

    def test_a_withdrawal_on_another_head_does_not_count(self):
        record = _entry(head=NEW_HEAD, state='RULED', ruling='WITHDRAWN')
        self.repair([record])
        self.assertEqual(len(self.calls), 1)

    def test_a_remaining_blocking_finding_keeps_the_fail_valid(self):
        body = self.BODY + 'Finding: F3 [Medium] a.py:3 edge case\n'
        self.repair([_entry(state='RULED', ruling='WITHDRAWN')], body=body)
        self.assertEqual(len(self.calls), 1)

    def test_findings_without_ids_stay_blocking(self):
        self.repair([_entry(state='RULED', ruling='WITHDRAWN')],
                    body='Finding: address the reported issue\n')
        self.assertEqual(len(self.calls), 1)

    def test_a_test_failure_repair_is_not_a_review_repair(self):
        receipt = {'task_id': TASK, 'run_id': trr.RUN, 'executor_task': EXECUTOR,
                   'source_sha': HEAD, 'result': 'FAIL', 'modules': ['test_remote'],
                   'failure_excerpt': 'FAIL: test_remote\nAssertionError'}
        receipt['receipt_digest'] = hashlib.sha256(task_store._json_bytes(receipt)).hexdigest()
        self.repair([_entry()], remote_test_receipt=receipt,
                    repair_task_recorder=lambda **request: {
                        'task_id': request['repair_task_id'],
                        'pm_assignment': {'acceptance': ['Repair the reported issue.']}})
        self.assertEqual(len(self.calls), 1)


class _Reached(Exception):
    pass


class HighDebugGateTests(unittest.TestCase):
    def debug(self, disputes):
        record = trr._record()
        record['review_disputes'] = disputes
        store = SimpleNamespace(get=lambda _task_id: record)
        registration = SimpleNamespace(result_sha=HEAD, root='/registered/d6',
                                       common_dir='/canonical/.git')
        never = Mock(side_effect=AssertionError('no Debug run may start'))
        with patch.object(high_debug, '_require_screen_cleared', return_value=None), \
             patch.object(high_debug.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True)), \
             patch.object(high_debug.task_completion, 'collect_git_snapshot',
                          return_value=SimpleNamespace(head=HEAD)), \
             patch.object(high_debug, '_admit_debug', side_effect=_Reached):
            try:
                high_debug.run_high_debug(
                    store=store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
                    family=f'pr-{PR}', early_reason='Second valid review FAIL',
                    read_registration=lambda *_args, **_kwargs: registration,
                    runner=never, create_d6=never)
            finally:
                never.assert_not_called()

    def test_debug_is_refused_while_a_dispute_on_the_head_is_open(self):
        for disputes in ([_entry()], 'malformed'):
            with self.subTest(disputes=disputes):
                with self.assertRaises(LLMToolError) as caught:
                    self.debug(disputes)
                self.assertEqual(caught.exception.high_debug_code, 'DEBUG_DISPUTE_OPEN')

    def test_debug_passes_the_gate_without_an_open_dispute_on_the_head(self):
        for disputes in ([], [_entry(head=NEW_HEAD)], [_entry(pr=PR + 1)],
                         [_entry(state='RULED', ruling='UPHELD')],
                         [_entry('F2', severity='Low', state='DEFERRED')]):
            with self.subTest(disputes=disputes):
                with self.assertRaises(_Reached):
                    self.debug(disputes)


if __name__ == '__main__':
    unittest.main()
