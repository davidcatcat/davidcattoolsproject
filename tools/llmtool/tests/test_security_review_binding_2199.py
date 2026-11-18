"""#2199 PR 2: security_required, bound security dispatch and result (no caller).

Design: docs/design/security-review-binding-2199.md, section "PR 2". Fixtures
use a real TaskStore under a temporary directory, hex task, run and head ids,
and injected github_get, codex_runner, create_d6 and comment I/O. No test text
is credential-shaped, and no production check is stubbed to pass: the screen,
the classifiers, the registry and security_required run for real unless a
case says it monkeypatches one to fail.
"""

import ast
import contextlib
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_dispatch, review_result, sensitive_paths, task_store
from llmtool.developer_routes import load_route_config
from llmtool.errors import LLMToolError

import review_trust_fixture


TASK, RUN = 'a' * 32, 'b' * 32
HEAD, HEAD2, HEAD0, HEAD1 = 'c' * 40, 'e' * 40, '1' * 40, '2' * 40
BASE = 'd' * 40
PR = 42
EXECUTOR = 'executor-d6'
STAMP = '2026-11-18T00:00:00Z'
RULE_FILE = 'tools/llmtool/worker-dispatch.json'
PLAIN_FILE = 'docs/notes/plain-example.md'
CODEX_REVIEW_MODEL = load_route_config()['roles']['codex-reviewer']['allowed_models'][0]
_STUB_RULES_PREFIX = 'RULES stub for security review binding tests\nRULES_END\n\n'

_MODULE_PATCHES = []
_MODULE_TEMPS = []


def _offline_comment_list(_pr_number):
    raise AssertionError('a test reached the GitHub comment list')


def _offline_comment_post(_root, _pr_number, _body):
    raise AssertionError('a test reached the GitHub comment post')


def setUpModule():
    review_trust_fixture.start_module()
    posts = tempfile.TemporaryDirectory()
    _MODULE_TEMPS.append(posts)
    post_dir = Path(posts.name).resolve() / 'posts'
    rules = (_STUB_RULES_PREFIX,
             hashlib.sha256(_STUB_RULES_PREFIX.encode('utf-8')).hexdigest())
    for name, value in (
            ('_verdict_post_dir', lambda _store: review_dispatch._private_post_dir(post_dir)),
            ('_list_issue_comments', _offline_comment_list),
            ('_create_issue_comment', _offline_comment_post),
            ('_review_rules', lambda _repo_root: rules)):
        patcher = patch.object(review_dispatch, name, value)
        patcher.start()
        _MODULE_PATCHES.append(patcher)


def tearDownModule():
    while _MODULE_PATCHES:
        _MODULE_PATCHES.pop().stop()
    while _MODULE_TEMPS:
        _MODULE_TEMPS.pop().cleanup()
    review_trust_fixture.stop_module()


def _real_store(root):
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': TASK,
        'goal': 'test bound security review', 'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response', 'description': 'Record result.',
                      'kind': 'ARTIFACT'}],
        'forbidden_scope': [], 'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    record = {
        'schema_version': 2, 'task_id': TASK, 'run_id': RUN,
        'pm_session_id': 'pm-test', 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': STAMP, 'updated_at': STAMP,
        'task_contract': contract, 'task_binding': vars(parsed.binding(RUN)),
        'request': 'test bound security review',
        'request_sha256': hashlib.sha256(b'test bound security review').hexdigest(),
        'response': None, 'usage': None, 'error_code': None,
        'developer': None, 'pm_assignment': None, 'completion': None,
    }
    store = task_store.TaskStore(Path(root).resolve())
    store.create(record)
    return store


class DeveloperStore:
    """A real TaskStore whose get() adds a Developer of one family (as FamilyStore does)."""

    def __init__(self, store, family='claude'):
        self.store, self.family = store, family
        self.root = getattr(store, 'root', None)

    def get(self, task_id):
        record = self.store.get(task_id)
        record['developer'] = {'provider_profile': self.family}
        return record

    def compare_and_update(self, task_id, **kwargs):
        return self.store.compare_and_update(task_id, **kwargs)

    def record_completion_event(self, task_id, **kwargs):
        return self.store.record_completion_event(task_id, **kwargs)


def _claim(kind, head, version, *, pr=PR, state='CLAIMED', reviewer=None):
    entry = {'executor_task': EXECUTOR, 'pr': pr, 'head_sha': head, 'version': version,
             'state': state, 'reviewer_task': reviewer or f'reviewer-{kind}-{version}-{head[:4]}',
             'native_session_id': None, 'account_mode': 'ROLES',
             'claimed_at': STAMP, 'updated_at': STAMP}
    if kind is not None:
        entry['review_kind'] = kind
    return entry


def _security_text(status='PASS', *, contract, head=HEAD, version=91, run=RUN,
                   accept=True, extra=()):
    marker = 'PASS' if status == 'PASS' else 'FIX'
    shown = 'PASS' if status == 'PASS' else 'FAIL'
    lines = ['<!-- activity: type=security_review_verdict actor=Codex target=PM '
             f'status={marker} -->',
             f'Verdict: {shown} | Version: v{version} | HEAD: {head}']
    if status == 'PASS':
        if accept:
            lines.append('SECURITY ACCEPT')
    else:
        lines.append('Finding: F1 [High] tools/llmtool/llmtool/example.py:10 '
                     'input is not validated')
    lines.append(review_result.security_binding_line(TASK, run, contract, head))
    lines.extend(extra)
    return '\n'.join(lines)


def _formal_codex_text(*, contract, head=HEAD, version=1):
    return ('<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n'
            f'Verdict: PASS | Version: v{version} | HEAD: {head}\n'
            'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
            f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} '
            f'contract={contract} head={head}')


def _codex_stream(review_text, *, verdict='PASS', head=HEAD, **changes):
    reply = {'task_id': TASK, 'run_id': RUN, 'head': head, 'verdict': verdict,
             'review': review_text}
    reply.update(changes)
    events = [
        {'type': 'thread.started', 'thread_id': '0123456789abcdef'},
        {'type': 'item.completed', 'item': {'type': 'agent_message',
                                            'text': json.dumps(reply)}},
    ]
    return '\n'.join(json.dumps(event) for event in events).encode('utf-8')


def _github(files, head=HEAD, *, pr_heads=None, files_error=False):
    """An injected github_get for one PR: PR reads, then its file list."""
    sequence = list(pr_heads) if pr_heads is not None else None
    calls = []

    def get(path):
        calls.append(path)
        if path == f'pulls/{PR}':
            current = sequence.pop(0) if sequence else head
            return {'number': PR, 'head': {'sha': current}}
        if path.startswith(f'pulls/{PR}/files?'):
            if files_error:
                raise LLMToolError('file list unavailable')
            return [dict(entry) if type(entry) is dict else {'filename': entry}
                    for entry in files]
        raise AssertionError('unexpected GitHub read: ' + path)

    get.calls = calls
    return get


def _no_github(path):
    raise AssertionError('unexpected GitHub read: ' + path)


def _event_id(identity):
    return hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:32]


class SecurityRequiredTests(unittest.TestCase):
    """security_required on plain records (design §3.4, §3.6)."""

    def required(self, files, record=None, **github):
        get = _github(files, **github)
        return review_result.security_required(
            record if record is not None else {}, PR, HEAD, get), get.calls

    def test_rule_file_cases_use_the_real_registry(self):
        cases = (
            RULE_FILE,                                            # exact rule
            'tools/llmtool/llmtool/task_advance.py',              # listed module
            'tools/llmtool/llmtool/review_dispatch.py',           # listed module
            '.github/workflows/ci.yml',                           # prefix .github/
            'tools/ec2-runner/run-tests.sh',                      # prefix tools/ec2-runner/
            'tools/mac-runner/run-tests.sh',                      # prefix tools/mac-runner/
            'tools/api-agent-gateway/api_agent_gateway/app.py',   # gateway prefix
            '.claude/skills/role-cards/developer.md',             # glob .claude/skills/**
            'tools/llmtool/llmtool/authorization_ledger.py',      # glob authorization_*.py
            'tools/llmtool/llmtool/merge_train_ops.py',           # glob merge_train*.py
            'tools/llmtool/llmtool/developer_routes/example_route.py',  # glob developer_routes/*.py
        )
        for path in cases:
            with self.subTest(path=path):
                answer, calls = self.required([path])
                self.assertIs(answer, True)
                self.assertEqual(calls[0], f'pulls/{PR}')
                self.assertTrue(calls[1].startswith(f'pulls/{PR}/files?'))
                self.assertEqual(calls[-1], f'pulls/{PR}')

    def test_every_rule_in_the_current_registry_has_a_case(self):
        # The registry is re-read here, so a rule added since b4bcb11e is covered.
        registry = json.loads(sensitive_paths._list_path().read_text(encoding='utf-8'))
        self.assertTrue({'.github/', 'tools/ec2-runner/', 'tools/mac-runner/',
                         'tools/api-agent-gateway/api_agent_gateway/'} <=
                        set(registry['prefixes']))
        self.assertTrue({'.claude/skills/**', 'tools/llmtool/llmtool/authorization_*.py',
                         'tools/llmtool/llmtool/merge_train*.py',
                         'tools/llmtool/llmtool/developer_routes/*.py'} <=
                        set(registry['globs']))
        paths = list(registry['exact'])
        paths += [prefix + 'example.txt' for prefix in registry['prefixes']]
        paths += [glob.replace('**', 'example/inner').replace('*', 'example')
                  for glob in registry['globs']]
        for path in paths:
            with self.subTest(path=path):
                self.assertIs(self.required([path])[0], True)

    def test_rename_whose_previous_filename_alone_is_sensitive(self):
        answer, _ = self.required([{'filename': PLAIN_FILE, 'previous_filename': RULE_FILE}])
        self.assertIs(answer, True)
        answer, _ = self.required([{'filename': PLAIN_FILE,
                                    'previous_filename': 'docs/notes/older.md'}])
        self.assertIs(answer, False)

    def test_review_rules_floor_is_a_rule_file_whatever_the_registry_answers(self):
        for path in ('AGENTS.md', 'CLAUDE.md', '.claude/skills/role-cards/common-core.md'):
            with self.subTest(path=path, registry='real'):
                self.assertIs(self.required([path])[0], True)
            for answer in (False, None):
                with self.subTest(path=path, registry=answer), \
                        patch.object(sensitive_paths, 'is_sensitive', return_value=answer):
                    self.assertIs(self.required([path])[0], True)
            with self.subTest(path=path, registry='raises'), \
                    patch.object(sensitive_paths, 'is_sensitive',
                                 side_effect=RuntimeError('matcher failed')):
                self.assertIs(self.required([path])[0], True)

    def test_a_path_the_registry_does_not_match_is_not_required(self):
        self.assertIs(self.required([PLAIN_FILE])[0], False)
        self.assertIs(self.required([])[0], False)

    def test_matcher_exception_or_non_boolean_answer_is_unknown(self):
        for label, kwargs in (('raises', {'side_effect': RuntimeError('matcher failed')}),
                              ('none', {'return_value': None}),
                              ('text', {'return_value': 'yes'})):
            with self.subTest(matcher=label), patch.object(sensitive_paths, 'is_sensitive',
                                                           **kwargs):
                answer, _ = self.required([PLAIN_FILE])
                self.assertIsNone(answer)

    def test_floor_definite_match_or_sticky_claim_wins_over_an_unknown_path(self):
        real = sensitive_paths.is_sensitive

        def unknown_for_plain(path):
            if path == PLAIN_FILE:
                raise RuntimeError('matcher failed')
            return real(path)

        with patch.object(sensitive_paths, 'is_sensitive', side_effect=unknown_for_plain):
            self.assertIs(self.required([PLAIN_FILE, 'AGENTS.md'])[0], True)
            self.assertIs(self.required([PLAIN_FILE, RULE_FILE])[0], True)
            self.assertIsNone(self.required([PLAIN_FILE, 'docs/notes/other.md'])[0])
        sticky = {'review_dispatch_claim': [_claim('security', HEAD0, 91)]}
        with patch.object(sensitive_paths, 'is_sensitive',
                          side_effect=RuntimeError('matcher failed')):
            self.assertIs(review_result.security_required(sticky, PR, HEAD, _no_github), True)

    def test_exact_head_reads_must_both_show_the_head(self):
        for heads in ((HEAD, HEAD2), (HEAD2, HEAD)):
            with self.subTest(heads=heads):
                answer, _ = self.required([RULE_FILE], pr_heads=heads)
                self.assertIsNone(answer)
        answer, _ = self.required([RULE_FILE], pr_heads=(HEAD, HEAD))
        self.assertIs(answer, True)

    def test_unreadable_or_too_long_file_list_is_unknown(self):
        self.assertIsNone(self.required([RULE_FILE], files_error=True)[0])
        full_page = [{'filename': PLAIN_FILE} for _ in range(100)]
        answer, calls = self.required(full_page)
        self.assertIsNone(answer)
        self.assertEqual(sum(1 for call in calls if '/files?' in call), 30)
        for bad in ([{'filename': ''}], [{'filename': PLAIN_FILE, 'previous_filename': None}],
                    ['not-a-dict-entry']):
            with self.subTest(entries=bad):
                get = _github([])
                get_with_bad = (lambda path, get=get, bad=bad:
                                bad if '/files?' in path else get(path))
                self.assertIsNone(review_result.security_required({}, PR, HEAD, get_with_bad))

    def test_sticky_rule_holds_on_a_repair_head_that_drops_the_rule_file(self):
        record = {'review_dispatch_claim': [_claim('security', HEAD0, 91, state='COMPLETED')]}
        # No formal claim at the repair head: required without any GitHub read.
        self.assertIs(review_result.security_required(record, PR, HEAD1, _no_github), True)
        # A formal claim at the repair head appended after the security claim.
        record['review_dispatch_claim'].append(_claim('formal', HEAD1, 1))
        self.assertIs(review_result.security_required(record, PR, HEAD1, _no_github), True)

    def test_frozen_rule_a_later_security_claim_does_not_count(self):
        record = {'review_dispatch_claim': [_claim('formal', HEAD, 1),
                                            _claim('security', HEAD2, 91)]}
        get = _github([PLAIN_FILE])
        self.assertIs(review_result.security_required(record, PR, HEAD, get), False)
        # A security claim for another PR never counts for this one.
        other = {'review_dispatch_claim': [_claim('security', HEAD0, 91, pr=PR + 1)]}
        self.assertIs(review_result.security_required(other, PR, HEAD, _github([PLAIN_FILE])),
                      False)

    def test_unknown_claim_kind_is_unknown(self):
        for claims in ([_claim(None, HEAD0, 91)],                       # legacy unkinded >= 91
                       [dict(_claim('formal', HEAD0, 1), review_kind='other')],
                       [_claim(None, HEAD, 92)],
                       ['not-a-claim']):
            with self.subTest(claims=claims):
                record = {'review_dispatch_claim': claims}
                self.assertIsNone(
                    review_result.security_required(record, PR, HEAD, _no_github))
        # An unknown kind before the security claim does not hide it (sticky first).
        record = {'review_dispatch_claim': [_claim(None, HEAD0, 91),
                                            _claim('security', HEAD0, 92)]}
        self.assertIs(review_result.security_required(record, PR, HEAD, _no_github), True)

    def test_invalid_arguments_are_unknown(self):
        get = _github([RULE_FILE])
        for record, number, head, reader in (
                (None, PR, HEAD, get), ({}, True, HEAD, get), ({}, 0, HEAD, get),
                ({}, PR, 'x' * 40, get), ({}, PR, HEAD, None),
                ({'review_dispatch_claim': 'bad'}, PR, HEAD, get)):
            with self.subTest(number=number, head=head):
                self.assertIsNone(review_result.security_required(record, number, head, reader))


class SecurityClassifierTests(unittest.TestCase):
    """The security shape is disjoint from the formal shape (design §3.3)."""

    def setUp(self):
        self.contract = 'f' * 64
        self.task = {'task_id': TASK, 'run_id': RUN,
                     'task_binding': {'contract_digest': self.contract},
                     'developer': {'provider_profile': 'claude'}}

    def verdict(self, text, version=91, task=None):
        return review_result._security_verdict(text, HEAD, version, task or self.task)

    def test_strict_pass_shape_and_every_other_bound_verdict_fails(self):
        self.assertEqual(self.verdict(_security_text(contract=self.contract))[2], 'REVIEW_PASS')
        self.assertEqual(
            self.verdict(_security_text(contract=self.contract, accept=False))[2], 'REVIEW_FAIL')
        failed = self.verdict(_security_text('FIX', contract=self.contract))
        self.assertEqual(failed[2], 'REVIEW_FAIL')
        self.assertEqual(failed[3], 1)
        self.assertEqual(failed[1], 0)
        mixed = _security_text(contract=self.contract).replace('Verdict: PASS', 'Verdict: FAIL')
        self.assertEqual(self.verdict(mixed)[2], 'REVIEW_FAIL')

    def test_unbound_or_mixed_texts_are_not_security_verdicts(self):
        text = _security_text(contract=self.contract)
        cases = {
            'other run': _security_text(contract=self.contract, run='9' * 32),
            'other contract': _security_text(contract='0' * 64),
            'other head': _security_text(contract=self.contract, head=HEAD2),
            'other version': _security_text(contract=self.contract, version=92),
            'two bindings': text + '\n' + review_result.security_binding_line(
                TASK, RUN, self.contract, HEAD2),
            'formal binding too': text + '\n' + (
                f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} '
                f'contract={self.contract} head={HEAD}'),
            'formal marker': _formal_codex_text(contract=self.contract, version=91),
            'claude actor': text.replace('actor=Codex', 'actor=Claude'),
            'not text': None,
        }
        for label, body in cases.items():
            with self.subTest(case=label):
                self.assertIsNone(self.verdict(body))
        self.assertIsNone(self.verdict(text, version=1))
        for family in ('codex', None):
            with self.subTest(developer=family):
                task = dict(self.task, developer={'provider_profile': family}
                            if family else None)
                self.assertIsNone(self.verdict(text, task=task))

    def test_formal_comment_classifier_ignores_a_security_body_and_the_reverse(self):
        for status in ('PASS', 'FIX'):
            body = _security_text(status, contract=self.contract)
            comment = {'id': 5, 'body': body, 'created_at': STAMP, 'updated_at': STAMP}
            with self.subTest(status=status):
                self.assertIsNone(review_result._comment_verdict(
                    comment, head=HEAD, version=91, task=self.task))
        formal = _formal_codex_text(contract=self.contract)
        self.assertEqual(review_result._comment_verdict(
            {'id': 5, 'body': formal, 'created_at': STAMP, 'updated_at': STAMP},
            head=HEAD, version=1, task=self.task)[2], 'REVIEW_PASS')
        self.assertIsNone(self.verdict(formal, version=91))

    def test_parser_takes_the_kind_and_refuses_the_other_shape(self):
        security = _security_text(contract=self.contract)
        parsed = review_dispatch._parse_codex_review_inner(
            _codex_stream(security), task_id=TASK, run_id=RUN,
            contract_digest=self.contract, head=HEAD, version=91, kind='security')
        self.assertEqual((parsed['verdict'], parsed['review_text']), ('PASS', security))
        failed = review_dispatch._parse_codex_review_inner(
            _codex_stream(_security_text('FIX', contract=self.contract), verdict='FAIL'),
            task_id=TASK, run_id=RUN, contract_digest=self.contract, head=HEAD,
            version=91, kind='security')
        self.assertEqual(failed['verdict'], 'FAIL')
        for text, version, kind in (
                (_formal_codex_text(contract=self.contract, version=91), 91, 'security'),
                (security.replace('v91', 'v1'), 1, 'formal')):
            with self.subTest(kind=kind), self.assertRaises(LLMToolError) as raised:
                review_dispatch._parse_codex_review_inner(
                    _codex_stream(text), task_id=TASK, run_id=RUN,
                    contract_digest=self.contract, head=HEAD, version=version, kind=kind)
            self.assertEqual(raised.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')
            self.assertEqual(raised.exception.review_failure_reason, 'REVIEW_HEADER_MISMATCH')
        # A security PASS without the accept line is refused by the classifier.
        with self.assertRaises(LLMToolError) as raised:
            review_dispatch._parse_codex_review_inner(
                _codex_stream(_security_text(contract=self.contract, accept=False)),
                task_id=TASK, run_id=RUN, contract_digest=self.contract, head=HEAD,
                version=91, kind='security')
        self.assertEqual(raised.exception.review_failure_reason, 'REVIEW_CLASSIFIER_REJECTED')

    def test_binding_mismatch_is_refused(self):
        raw = _codex_stream(_security_text(contract=self.contract), head=HEAD2)
        with self.assertRaises(LLMToolError) as raised:
            review_dispatch._parse_codex_review(
                raw, task_id=TASK, run_id=RUN, contract_digest=self.contract,
                head=HEAD, version=91, kind='security')
        self.assertEqual(raised.exception.review_failure_reason, 'VERDICT_BINDING_MISMATCH')
        self.assertEqual(raised.exception.review_binding_mismatch_keys, ('head',))

    def test_publication_lookup_takes_the_kind(self):
        text = _security_text(contract=self.contract)
        claim = {'pr': PR, 'head_sha': HEAD, 'review_kind': 'security',
                 'review_result': {'task_id': TASK, 'run_id': RUN,
                                   'contract_digest': self.contract, 'head': HEAD,
                                   'verdict': 'PASS', 'review_text': text}}
        line = review_dispatch._verdict_binding_line(self.task, TASK, claim, text,
                                                     kind='security')
        self.assertEqual(line, review_result.security_binding_line(
            TASK, RUN, self.contract, HEAD))
        with self.assertRaises(review_dispatch._PostError):
            review_dispatch._verdict_binding_line(self.task, TASK, claim, text)
        posted = {'id': 9, 'body': text, 'user': {'login': 'reviewer-user'}}
        self.assertEqual(review_dispatch._find_posted_verdict(
            [posted], 'reviewer-user', text, line, kind='security'), (posted, False, False))
        self.assertEqual(review_dispatch._find_posted_verdict(
            [posted], 'reviewer-user', text, line, kind='formal'), (None, True, False))


class SecurityDispatchTests(unittest.TestCase):
    """dispatch_review(kind='security') and record_review_result(kind='security')."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = _real_store(Path(self.temp.name) / 'store')
        self.contract = self.store.get(TASK)['task_binding']['contract_digest']
        self.created, self.bound, self.sent, self.runs, self.prompts = [], [], [], [], []
        self.preflights = []

    def resolve_role(self, role):
        self.assertEqual(role, 'codex-reviewer')
        return {'provider': 'codex', 'config_dir': None, 'model': CODEX_REVIEW_MODEL,
                'source': 'roles'}

    def dispatch(self, *, head, github_get, raw=None, version=91, family='claude',
                 reviewer=None, prompt=True):
        executor = SimpleNamespace(root='/repo/executor', common_dir='/repo/.git')

        def preflight(*_args, **kwargs):
            self.preflights.append(kwargs.get('reviewer'))
            return (TASK, executor, head, BASE, kwargs.get('reviewer') or 'claude-reviewer')

        def run_codex(**kwargs):
            self.runs.append(kwargs)
            return review_dispatch._parse_codex_review(
                raw, task_id=TASK, run_id=RUN, contract_digest=self.contract,
                head=kwargs['head'], version=kwargs['version'],
                kind=kwargs.get('kind', 'formal'))

        with patch.object(review_dispatch, '_preflight', side_effect=preflight), \
                patch.object(review_dispatch.worker_dispatch, 'evaluate',
                             return_value=SimpleNamespace(allowed=True, reason_code='')), \
                patch.object(review_dispatch, '_resolve_github_login',
                             return_value='reviewer-user'), \
                patch.object(review_dispatch.role_config, 'resolve_role',
                             side_effect=self.resolve_role), \
                patch.object(review_dispatch.task_completion, 'read_d6_registration',
                             return_value=SimpleNamespace(root='/repo/reviewer')):
            return review_dispatch.dispatch_review(
                store=DeveloperStore(self.store, family), task_id=TASK,
                executor_task=EXECUTOR, pr_number=PR, version=version, kind='security',
                reviewer=reviewer, github_get=github_get,
                create_d6=lambda **kw: self.created.append(kw),
                bind_session=lambda **kw: self.bound.append(kw),
                dispatch_session=lambda **kw: self.sent.append(kw),
                prompt_renderer=((lambda *args: (self.prompts.append(args) or
                                                 'security prompt'))
                                 if prompt else None),
                codex_runner=run_codex,
                comment_lister=_offline_comment_list, comment_poster=_offline_comment_post)

    def record(self, *, version=91, github_get=_no_github, **kwargs):
        return review_result.record_review_result(
            store=DeveloperStore(self.store), task_id=TASK, executor_task=EXECUTOR,
            pr_number=PR, version=version, kind='security', github_get=github_get, **kwargs)

    def append_claim(self, claim):
        record = self.store.get(TASK)
        claims = list(record.get('review_dispatch_claim') or [])
        self.store.compare_and_update(
            TASK, expected={'record_sha256': record['record_sha256']},
            review_dispatch_claim=claims + [claim])

    def assert_nothing_started(self):
        self.assertEqual(self.created, [])
        self.assertEqual(self.runs, [])
        self.assertEqual(self.bound, [])
        self.assertEqual(self.sent, [])

    def test_canned_pass_or_fail_gives_one_completed_claim_and_one_bound_event(self):
        for status, verdict, outcome in (('PASS', 'PASS', 'REVIEW_PASS'),
                                         ('FIX', 'FAIL', 'REVIEW_FAIL')):
            with self.subTest(verdict=verdict):
                self.setUp()
                text = _security_text(status, contract=self.contract)
                raw = _codex_stream(text, verdict=verdict)
                github = _github([RULE_FILE])
                first = self.dispatch(head=HEAD, github_get=github, raw=raw)
                self.assertEqual(first['state'], 'COMPLETED')
                self.assertEqual(first['verdict'], verdict)
                self.assertEqual(first['review_kind'], 'security')
                claims = self.store.get(TASK)['review_dispatch_claim']
                self.assertEqual(len(claims), 1)
                claim = claims[0]
                self.assertEqual((claim['review_kind'], claim['version'], claim['head_sha'],
                                  claim['state']), ('security', 91, HEAD, 'COMPLETED'))
                self.assertEqual(claim['review_result']['verdict'], verdict)
                self.assertEqual(claim['review_result']['review_text'], text)
                # Codex route only: kind reaches the runner and the D6 lease.
                self.assertEqual(self.preflights, ['codex-reviewer', 'codex-reviewer'])
                self.assertEqual(self.runs[0]['kind'], 'security')
                self.assertEqual(self.created[0]['review_kind'], 'security')
                self.assertEqual(self.created[0]['reviewer_key'], 'codex-reviewer')
                self.assertEqual(self.sent, [])

                result = self.record()
                self.assertEqual(result['state'], outcome)
                self.assertTrue(result['created'])
                self.assertEqual(result['comment_id'], 0)
                expected = _event_id({'task': TASK, 'run': RUN, 'head': HEAD, 'version': 91,
                                      'outcome': outcome, 'comment_id': 0,
                                      'kind': 'security'})
                self.assertEqual(result['event_id'], expected)
                self.assertNotEqual(expected, _event_id({
                    'task': TASK, 'run': RUN, 'head': HEAD, 'version': 91,
                    'outcome': outcome, 'comment_id': 0}))
                events = self.store.get(TASK)['completion_events']
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['source'], 'REVIEWER')
                self.assertEqual(events[0]['source_sha'], HEAD)
                self.assertEqual(events[0]['session_id'], 'secreview-' + expected)
                self.assertEqual(events[0]['review_kind'], 'security')

                # Replays: no second event, no second Reviewer run, no new claim.
                again = self.record()
                self.assertFalse(again['created'])
                self.assertEqual(again['event_id'], expected)
                replay = self.dispatch(head=HEAD, github_get=github, raw=raw)
                self.assertEqual(replay['verdict'], verdict)
                self.assertEqual(replay['verdict_post'], {'state': 'IN_PROGRESS'})
                self.assertEqual(len(self.runs), 1)
                self.assertEqual(len(self.store.get(TASK)['completion_events']), 1)
                self.assertEqual(len(self.store.get(TASK)['review_dispatch_claim']), 1)

    def test_a_security_comment_without_a_claim_gives_no_event(self):
        comment = {'id': 7, 'body': _security_text(contract=self.contract),
                   'created_at': STAMP, 'updated_at': STAMP,
                   'user': {'login': 'reviewer-user'}}
        reads = []

        def github_get(path):
            reads.append(path)
            return [comment]

        for done in (False, True):
            with self.subTest(reviewer_session_done=done), \
                    self.assertRaises(LLMToolError) as raised:
                self.record(github_get=github_get, reviewer_session_done=done)
            self.assertEqual(raised.exception.review_dispatch_code,
                             'REVIEW_RESULT_CLAIM_MISSING')
        self.assertEqual(reads, [])
        self.assertEqual(self.store.get(TASK).get('completion_events') or [], [])

    def test_a_contradicting_fail_comment_does_not_change_a_stored_pass(self):
        self.dispatch(head=HEAD, github_get=_github([RULE_FILE]),
                      raw=_codex_stream(_security_text(contract=self.contract)))
        fail_comment = {'id': 8, 'body': _security_text('FIX', contract=self.contract),
                        'created_at': STAMP, 'updated_at': STAMP,
                        'user': {'login': 'reviewer-user'}}
        reads = []
        result = self.record(github_get=lambda path: (reads.append(path) or [fail_comment]),
                             reviewer_session_done=True)
        self.assertEqual(result['state'], 'REVIEW_PASS')
        self.assertEqual(reads, [])

    def test_binding_mismatch_keeps_an_unknown_claim_and_comments_never_settle_it(self):
        raw = _codex_stream(_security_text(contract=self.contract), head=HEAD2)
        with self.assertRaises(LLMToolError) as raised:
            self.dispatch(head=HEAD, github_get=_github([RULE_FILE]), raw=raw)
        self.assertEqual(raised.exception.review_failure_reason, 'VERDICT_BINDING_MISMATCH')
        claim = self.store.get(TASK)['review_dispatch_claim'][0]
        self.assertEqual((claim['state'], claim['review_kind'], claim['failure_reason']),
                         ('UNKNOWN', 'security', 'VERDICT_BINDING_MISMATCH'))
        self.assertNotIn('review_result', claim)
        pass_comment = {'id': 9, 'body': _security_text(contract=self.contract),
                        'created_at': STAMP, 'updated_at': STAMP,
                        'user': {'login': 'reviewer-user'}}
        reads = []
        reader = lambda path: (reads.append(path) or [pass_comment])
        self.assertEqual(self.record(github_get=reader),
                         {'state': 'NOT_READY', 'event': None})
        blocked = self.record(github_get=reader, reviewer_session_done=True)
        self.assertEqual(blocked['state'], 'REVIEW_BLOCKED')
        self.assertEqual(reads, [])
        event = self.store.get(TASK)['completion_events'][0]
        self.assertEqual(event['session_id'], 'secreview-' + blocked['event_id'])

    def test_claude_reviewer_and_codex_family_developer_are_refused(self):
        before = self.store.get(TASK)
        for family, reviewer, code in (
                ('claude', 'claude-reviewer', 'SECURITY_REVIEWER_INVALID'),
                ('codex', None, 'SECURITY_REVIEWER_SAME_FAMILY'),
                ('codex', 'codex-reviewer', 'SECURITY_REVIEWER_SAME_FAMILY')):
            with self.subTest(family=family, reviewer=reviewer), \
                    self.assertRaises(LLMToolError) as raised:
                self.dispatch(head=HEAD, github_get=_no_github, family=family,
                              reviewer=reviewer,
                              raw=_codex_stream(_security_text(contract=self.contract)))
            self.assertEqual(raised.exception.review_dispatch_code, code)
        self.assertEqual(self.preflights, [])
        self.assert_nothing_started()
        self.assertEqual(self.store.get(TASK), before)

    def test_precondition_refuses_before_any_claim_d6_or_reviewer(self):
        raw = _codex_stream(_security_text(contract=self.contract))
        before = self.store.get(TASK)
        cases = (
            ('no rule file', _github([PLAIN_FILE]), {}, 'SECURITY_REVIEW_NOT_REQUIRED'),
            ('file list unreadable', _github([RULE_FILE], files_error=True), {},
             'SECURITY_REVIEW_REQUIRED_UNKNOWN'),
            ('head moved', _github([RULE_FILE], pr_heads=(HEAD, HEAD2)), {},
             'SECURITY_REVIEW_REQUIRED_UNKNOWN'),
            ('matcher raises', _github([PLAIN_FILE]),
             {'side_effect': RuntimeError('matcher failed')},
             'SECURITY_REVIEW_REQUIRED_UNKNOWN'),
            ('matcher answers None', _github([PLAIN_FILE]), {'return_value': None},
             'SECURITY_REVIEW_REQUIRED_UNKNOWN'),
        )
        for label, github, matcher, code in cases:
            with self.subTest(case=label):
                matcher_patch = (patch.object(sensitive_paths, 'is_sensitive', **matcher)
                                 if matcher else contextlib.nullcontext())
                with matcher_patch, self.assertRaises(LLMToolError) as raised:
                    self.dispatch(head=HEAD, github_get=github, raw=raw)
                self.assertEqual(raised.exception.review_dispatch_code, code)
                self.assert_nothing_started()
                self.assertEqual(self.prompts, [])
                self.assertEqual(self.store.get(TASK), before)

    def test_security_dispatch_needs_an_injected_prompt_until_the_cli_renders_one(self):
        before = self.store.get(TASK)
        with self.assertRaises(LLMToolError) as raised:
            self.dispatch(head=HEAD, github_get=_github([RULE_FILE]), prompt=False,
                          raw=_codex_stream(_security_text(contract=self.contract)))
        self.assertEqual(raised.exception.review_dispatch_code,
                         'SECURITY_REVIEW_PROMPT_UNAVAILABLE')
        self.assert_nothing_started()
        self.assertEqual(self.store.get(TASK), before)

    def test_frozen_rule_after_a_formal_claim_at_the_head(self):
        # A formal claim at (PR, HEAD), where HEAD touches no rule file.
        self.append_claim(_claim('formal', HEAD, 1))
        # Then a security claim at HEAD2, which touches a rule file.
        self.dispatch(head=HEAD2, github_get=_github([RULE_FILE], head=HEAD2),
                      raw=_codex_stream(_security_text(contract=self.contract, head=HEAD2),
                                        head=HEAD2))
        record = self.store.get(TASK)
        self.assertEqual([c['review_kind'] for c in record['review_dispatch_claim']],
                         ['formal', 'security'])
        self.assertIs(review_result.security_required(
            record, PR, HEAD, _github([PLAIN_FILE])), False)
        # The PR head moves back to HEAD: a security dispatch there is refused.
        runs = len(self.runs)
        with self.assertRaises(LLMToolError) as raised:
            self.dispatch(head=HEAD, github_get=_github([PLAIN_FILE]), version=92,
                          raw=_codex_stream(_security_text(contract=self.contract,
                                                           version=92)))
        self.assertEqual(raised.exception.review_dispatch_code, 'SECURITY_REVIEW_NOT_REQUIRED')
        self.assertEqual(len(self.runs), runs)
        self.assertEqual(self.store.get(TASK), record)

    def test_sticky_rule_a_repair_head_without_the_rule_file_still_needs_review(self):
        self.dispatch(head=HEAD0, github_get=_github([RULE_FILE], head=HEAD0),
                      raw=_codex_stream(_security_text('FIX', contract=self.contract,
                                                       head=HEAD0),
                                        verdict='FAIL', head=HEAD0))
        record = self.store.get(TASK)
        self.assertIs(review_result.security_required(record, PR, HEAD1, _no_github), True)
        self.append_claim(_claim('formal', HEAD1, 1))
        record = self.store.get(TASK)
        self.assertIs(review_result.security_required(record, PR, HEAD1, _no_github), True)
        # The repair head is admitted to its own bound security review.
        result = self.dispatch(
            head=HEAD1, github_get=_no_github, version=92,
            raw=_codex_stream(_security_text(contract=self.contract, head=HEAD1, version=92),
                              head=HEAD1))
        self.assertEqual((result['state'], result['version'], result['review_kind']),
                         ('COMPLETED', 92, 'security'))


_BUILTIN_TYPE_NAMES = frozenset(('str', 'int', 'bool', 'dict', 'list', 'tuple', 'type',
                                 'object', 'float', 'set', 'frozenset'))


def _review_kind_writes():
    """Every place in tools/llmtool/llmtool/ that gives review_kind a value.

    Yields (module, enclosing function, kind of write, value node, claim-shaped).
    Only a dict literal is classed as claim-shaped, when it also has a
    claim-only key (reviewer_task, account_mode, native_session_id); the other
    write forms are checked separately below.
    """
    package = Path(review_dispatch.__file__).resolve().parent
    for path in sorted(package.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        parents = {child: node for node in ast.walk(tree)
                   for child in ast.iter_child_nodes(node)}

        def enclosing(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node.name
            return '<module>'

        module = path.relative_to(package).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = {key.value for key in node.keys
                        if isinstance(key, ast.Constant) and type(key.value) is str}
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == 'review_kind':
                        yield (module, enclosing(node), 'dict', value,
                               bool(keys & {'reviewer_task', 'account_mode',
                                            'native_session_id'}))
            elif isinstance(node, ast.Call):
                name = (node.func.id if isinstance(node.func, ast.Name) else
                        node.func.attr if isinstance(node.func, ast.Attribute) else None)
                for keyword in node.keywords:
                    if keyword.arg == 'review_kind':
                        yield (module, enclosing(node), 'call:' + str(name), keyword.value,
                               False)
            elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if (isinstance(target, ast.Subscript) and
                            isinstance(target.slice, ast.Constant) and
                            target.slice.value == 'review_kind'):
                        yield (module, enclosing(node), 'subscript', node.value, False)


def _may_be_security(value):
    """False for the constant 'formal', another constant, a type name or a
    collection of them (schema values); any computed value may be 'security'."""
    if value is None:
        return False
    if isinstance(value, ast.Constant):
        return value.value == 'security'
    if isinstance(value, ast.Name) and value.id in _BUILTIN_TYPE_NAMES:
        return False
    if isinstance(value, (ast.Tuple, ast.List, ast.Set, ast.Dict, ast.Lambda)):
        return False
    return True


class SingleSecurityWriterScanTests(unittest.TestCase):
    """Design §3.3: dispatch_review is the only writer of security claims."""

    def test_security_review_kind_is_written_only_in_dispatch_review(self):
        writes = list(_review_kind_writes())
        self.assertTrue(writes)
        # No module hard-codes the security kind into a claim, event or entry.
        hard_coded = [(module, function, how) for module, function, how, value, _ in writes
                      if isinstance(value, ast.Constant) and value.value == 'security']
        self.assertEqual(hard_coded, [])
        # The one claim-shaped literal that may carry the security kind is the
        # new claim in dispatch_review (its kind is the validated argument).
        claim_writers = {(module, function) for module, function, how, value, claim in writes
                         if claim and _may_be_security(value)}
        self.assertEqual(claim_writers, {('review_dispatch.py', 'dispatch_review')})
        # The review modules never set review_kind on a copied claim: state
        # transitions keep the stored kind, so no second writer can change it.
        copies = [(module, function, how) for module, function, how, value, _ in writes
                  if module in ('review_dispatch.py', 'review_result.py') and
                  how in ('call:dict', 'subscript')]
        self.assertEqual(copies, [])
        # The only REVIEWER event writer that may pass the security kind.
        event_writers = {(module, function) for module, function, how, value, _ in writes
                         if how == 'call:record_completion_event' and _may_be_security(value)}
        self.assertEqual(event_writers, {('review_result.py', 'record_review_result')})


if __name__ == '__main__':
    unittest.main()
