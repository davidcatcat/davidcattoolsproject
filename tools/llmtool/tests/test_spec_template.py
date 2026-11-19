"""#1817 PR 1 tests: dispatch spec field sets and tool-side auto-fill."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llmtool.errors import LLMToolError
from llmtool.spec_template import (
    AUTHOR_ROUTES,
    FIELDS_BY_KIND,
    FRESH_CARRIED_COUNTS,
    KINDS,
    READ_ONLY_KINDS,
    SPEC_BASE_STALE,
    SPEC_CARRIED_COUNTS_INVALID,
    SPEC_CHECK_RESULTS_INVALID,
    SPEC_DIGEST_INVALID,
    SPEC_DISPATCH_UNAUTHORIZED,
    SPEC_FRESHNESS_INVALID,
    SPEC_FRESHNESS_MISSING,
    SPEC_ID_INVALID,
    SPEC_LINEAGE_INVALID,
    SPEC_PM_FIELD_MISSING,
    SPEC_PREDECESSOR_MISSING,
    SPEC_READONLY_ROLE_HAS_WRITES,
    SPEC_REVIEW_HEAD_INVALID,
    SPEC_REVIEW_RANGE_INVALID,
    SPEC_SCOPE_INVALID,
    SPEC_SCOPE_TEST_MODULE_MISSING,
    SPEC_SOURCE_FACTS_INVALID,
    SPEC_UNKNOWN_KIND,
    SPEC_WRITE_PATHS_EMPTY,
    DispatchSourceFacts,
    SpecTemplateError,
    build_spec,
)

TASK = '0123456789abcdef0123456789abcdef'
RUN = 'fedcba9876543210fedcba9876543210'
MAIN = '1' * 40
BASE = '2' * 40
HEAD = '3' * 40
FAILED = '4' * 40
CARD = 'a' * 64
CONTRACT = 'b' * 64
PREDECESSOR_TASK = '00112233445566778899aabbccddeeff'
PREDECESSOR_CONTRACT = 'c' * 64
WRITE = 'tools/llmtool/llmtool/spec_template.py'
TEST = 'tools/llmtool/tests/test_spec_template.py'


def make_facts(**overrides):
    values = {
        'task_id': TASK,
        'run_id': RUN,
        'base': MAIN,
        'result_head': MAIN,
        'canonical_main': MAIN,
        'fresh': True,
        'read_paths': ['tools/llmtool/WORKFLOW.md', TEST],
        'write_paths': [WRITE],
        'author_role': 'pm',
        'author_worker': 'claude-developer',
        'author_route': 'ALLOWED',
        'owner_ref': None,
        'role_card_digest': CARD,
        'contract_digest': CONTRACT,
        'check_results': {'preflight_status': 'SPEC_PREFLIGHT_OK'},
    }
    values.update(overrides)
    return DispatchSourceFacts(**values)


def facts_for(kind, **overrides):
    values = {}
    if kind in READ_ONLY_KINDS:
        values['write_paths'] = []
    if kind == 'reviewer':
        values.update(base=BASE, result_head=HEAD, fresh=True)
    elif kind == 'debug':
        values.update(
            base=BASE,
            result_head=HEAD,
            fresh=None,
            predecessor_spec={'task_id': PREDECESSOR_TASK,
                              'contract_digest': PREDECESSOR_CONTRACT},
            continued_lineage=[PREDECESSOR_TASK],
            carried_counts={'repairs': 1, 'test_failures': 0, 'attempts': 2},
        )
    elif kind == 'developer':
        values['fresh'] = True
    values.update(overrides)
    return make_facts(**values)


def build(kind, facts):
    return build_spec(
        kind,
        'Add a bounded dispatch helper.',
        'Build one dispatch spec from source facts.',
        ['The built spec uses the design field set.'],
        facts,
    )


def repaired_facts(**overrides):
    values = {
        'fresh': False,
        'base': BASE,
        'result_head': FAILED,
        'failed_head': FAILED,
        'predecessor_spec': {'task_id': PREDECESSOR_TASK,
                             'contract_digest': PREDECESSOR_CONTRACT},
        'continued_lineage': [PREDECESSOR_TASK],
        'carried_counts': {'repairs': 1, 'test_failures': 0, 'attempts': 2},
    }
    values.update(overrides)
    return facts_for('developer', **values)


class TemplateTestBase(unittest.TestCase):
    def assert_refusal(self, code, function, *args):
        with self.assertRaises(SpecTemplateError) as caught:
            function(*args)
        self.assertIsInstance(caught.exception, LLMToolError)
        self.assertEqual(caught.exception.spec_code, code)
        return caught.exception


class FieldSetTests(TemplateTestBase):
    def test_each_kind_builds_exactly_its_design_field_set(self):
        self.assertEqual(set(FIELDS_BY_KIND), set(KINDS))
        for kind in KINDS:
            with self.subTest(kind=kind):
                spec = build(kind, facts_for(kind))
                self.assertEqual(set(spec), set(FIELDS_BY_KIND[kind]))

    def test_only_reviewer_carries_review_range(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                spec = build(kind, facts_for(kind))
                self.assertEqual('review_range' in spec, kind == 'reviewer')

    def test_unknown_kind_is_refused(self):
        self.assert_refusal(SPEC_UNKNOWN_KIND, build_spec, 'unknown',
                            'A problem.', 'A goal.', ['An item.'], make_facts())

    def test_non_source_facts_are_refused(self):
        for value in (None, {}, 'facts'):
            with self.subTest(value=value):
                self.assert_refusal(
                    SPEC_SOURCE_FACTS_INVALID, build_spec, 'developer',
                    'A problem.', 'A goal.', ['An item.'], value)


class AutoFillTests(TemplateTestBase):
    def test_fresh_task_uses_the_canonical_main_for_both_revisions(self):
        for kind in ('developer', 'investigation', 'sub_pm'):
            with self.subTest(kind=kind):
                spec = build(kind, facts_for(kind))
                self.assertEqual(spec['base'], MAIN)
                self.assertEqual(spec['result_head'], MAIN)
                self.assertIsNone(spec['predecessor_spec'])
                self.assertEqual(spec['continued_lineage'], [])
                self.assertEqual(spec['carried_counts'], FRESH_CARRIED_COUNTS)
                if kind in READ_ONLY_KINDS:
                    self.assertEqual(spec['write_paths'], [])

    def test_developer_repair_keeps_the_failed_head_and_ancestor_base(self):
        spec = build('developer', repaired_facts())
        self.assertEqual(spec['base'], BASE)
        self.assertEqual(spec['result_head'], FAILED)
        self.assertNotIn('review_range', spec)
        self.assertEqual(spec['predecessor_spec']['task_id'], PREDECESSOR_TASK)
        self.assertEqual(spec['continued_lineage'], [PREDECESSOR_TASK])
        self.assertEqual(spec['carried_counts'],
                         {'repairs': 1, 'test_failures': 0, 'attempts': 2})

    def test_debug_uses_the_examined_head_and_mandatory_lineage(self):
        spec = build('debug', facts_for('debug'))
        self.assertEqual(spec['base'], BASE)
        self.assertEqual(spec['result_head'], HEAD)
        self.assertEqual(spec['write_paths'], [])
        self.assertEqual(spec['predecessor_spec']['task_id'], PREDECESSOR_TASK)
        self.assertEqual(spec['continued_lineage'], [PREDECESSOR_TASK])

    def test_reviewer_review_range_is_the_three_dot_scope(self):
        spec = build('reviewer', facts_for('reviewer'))
        self.assertEqual(spec['base'], BASE)
        self.assertEqual(spec['result_head'], HEAD)
        self.assertEqual(spec['review_range'], f'{BASE}...{HEAD}')
        self.assertEqual(spec['write_paths'], [])

    def test_reviewer_rereview_carries_lineage(self):
        spec = build('reviewer', facts_for(
            'reviewer',
            fresh=False,
            predecessor_spec={'task_id': PREDECESSOR_TASK,
                              'contract_digest': PREDECESSOR_CONTRACT},
            continued_lineage=[PREDECESSOR_TASK],
            carried_counts={'repairs': 1, 'test_failures': 0, 'attempts': 2},
        ))
        self.assertEqual(spec['continued_lineage'], [PREDECESSOR_TASK])
        self.assertEqual(spec['carried_counts']['repairs'], 1)

    def test_read_scope_covers_every_write_path(self):
        for kind in ('developer', 'sub_pm'):
            with self.subTest(kind=kind):
                spec = build(kind, facts_for(kind, write_paths=[TEST, WRITE]))
                self.assertEqual(spec['read_paths'],
                                 ['tools/llmtool/WORKFLOW.md', TEST, WRITE])

    def test_read_scope_includes_paths_cited_in_acceptance(self):
        cited = 'docs/design/dispatch-spec-template.md'
        spec = build_spec(
            'developer',
            'Add a bounded dispatch helper.',
            'Build one dispatch spec from source facts.',
            [f'Update {WRITE} as {cited} defines.'],
            facts_for('developer'),
        )
        self.assertIn(WRITE, spec['read_paths'])
        self.assertIn(cited, spec['read_paths'])
        self.assertEqual(len(spec['read_paths']), len(set(spec['read_paths'])))

    def test_acceptance_cited_test_module_is_admitted(self):
        spec = build_spec(
            'developer',
            'Add a bounded dispatch helper.',
            'Build one dispatch spec from source facts.',
            [f'Add regression coverage in {TEST}.'],
            facts_for('developer', read_paths=['tools/llmtool/WORKFLOW.md'],
                      write_paths=[WRITE]),
        )
        self.assertIn(TEST, spec['read_paths'])

    def test_author_route_and_owner_ref_are_filled_from_the_gate(self):
        self.assertEqual(set(AUTHOR_ROUTES),
                         {'ALLOWED', 'OWNER_ASSIGNMENT_ONLY', 'TEMPLATE_ONLY'})
        cases = (
            ('ALLOWED', None),
            ('OWNER_ASSIGNMENT_ONLY', 'owner-ref'),
            ('TEMPLATE_ONLY', None),
        )
        for route, owner_ref in cases:
            with self.subTest(route=route):
                spec = build('developer', facts_for(
                    'developer', author_route=route, owner_ref=owner_ref))
                self.assertEqual(spec['author'], {
                    'role': 'pm',
                    'worker': 'claude-developer',
                    'route': route,
                    'owner_ref': owner_ref,
                })

    def test_digests_and_check_receipt_are_copied(self):
        receipt = {'preflight_status': 'SPEC_PREFLIGHT_OK',
                   'd6_worktree_registered': True}
        spec = build('developer', facts_for('developer', check_results=receipt))
        self.assertEqual(spec['role_card_digest'], CARD)
        self.assertEqual(spec['contract_digest'], CONTRACT)
        self.assertEqual(spec['check_results'], receipt)
        self.assertIsNot(spec['check_results'], receipt)

    def test_ids_and_pm_fields_are_preserved(self):
        spec = build('developer', facts_for('developer'))
        self.assertEqual(spec['kind'], 'developer')
        self.assertEqual(spec['task_id'], TASK)
        self.assertEqual(spec['run_id'], RUN)
        self.assertEqual(spec['problem'], 'Add a bounded dispatch helper.')
        self.assertEqual(spec['goal'],
                         'Build one dispatch spec from source facts.')
        self.assertEqual(spec['acceptance'],
                         ['The built spec uses the design field set.'])


class RefusalTests(TemplateTestBase):
    def test_missing_pm_fields_are_refused(self):
        facts = facts_for('developer')
        cases = (
            ('problem', '', 'Add a goal.', ['An item.']),
            ('goal', 'A problem.', '', ['An item.']),
            ('acceptance', 'A problem.', 'A goal.', []),
        )
        for field, problem, goal, acceptance in cases:
            with self.subTest(field=field):
                self.assert_refusal(
                    SPEC_PM_FIELD_MISSING, build_spec, 'developer',
                    problem, goal, acceptance, facts)

    def test_invalid_ids_are_refused(self):
        cases = (
            ('task_id', {'task_id': 'not-a-task'}),
            ('run_id', {'run_id': 'not-a-run'}),
        )
        for field, overrides in cases:
            with self.subTest(field=field):
                error = self.assert_refusal(
                    SPEC_ID_INVALID, build, 'developer',
                    facts_for('developer', **overrides))
                self.assertEqual(error.spec_field, field)

    def test_stale_or_missing_fresh_revisions_are_refused(self):
        cases = (
            {'base': HEAD},
            {'result_head': HEAD},
            {'canonical_main': None},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assert_refusal(
                    SPEC_BASE_STALE, build, 'developer',
                    facts_for('developer', **overrides))

    def test_repair_with_equal_base_and_head_is_refused(self):
        self.assert_refusal(
            SPEC_BASE_STALE, build, 'developer',
            repaired_facts(base=FAILED, result_head=FAILED, failed_head=FAILED))

    def test_repair_result_head_must_match_the_failed_head(self):
        self.assert_refusal(
            SPEC_BASE_STALE, build, 'developer',
            repaired_facts(result_head=HEAD, failed_head=FAILED))

    def test_repair_requires_the_predecessor_failed_head(self):
        error = self.assert_refusal(
            SPEC_BASE_STALE, build, 'developer',
            repaired_facts(failed_head=None))
        self.assertEqual(error.spec_field, 'failed_head')

    def test_code_write_requires_an_admitted_test_module(self):
        for kind in ('developer', 'sub_pm'):
            with self.subTest(kind=kind):
                error = self.assert_refusal(
                    SPEC_SCOPE_TEST_MODULE_MISSING, build, kind,
                    facts_for(kind, read_paths=['tools/llmtool/WORKFLOW.md'],
                              write_paths=[WRITE]))
                self.assertEqual(error.spec_field, 'read_paths')

    def test_reviewer_head_is_checked(self):
        for overrides in ({'result_head': None}, {'result_head': BASE}):
            with self.subTest(overrides=overrides):
                self.assert_refusal(
                    SPEC_REVIEW_HEAD_INVALID, build, 'reviewer',
                    facts_for('reviewer', **overrides))

    def test_debug_head_is_checked(self):
        for overrides in ({'result_head': 'short'}, {'result_head': BASE}):
            with self.subTest(overrides=overrides):
                self.assert_refusal(
                    SPEC_REVIEW_HEAD_INVALID, build, 'debug',
                    facts_for('debug', **overrides))

    def test_contradictory_reviewer_range_is_refused(self):
        self.assert_refusal(
            SPEC_REVIEW_RANGE_INVALID, build, 'reviewer',
            facts_for('reviewer', review_range=f'{MAIN}...{HEAD}'))

    def test_write_paths_are_required_for_writer_kinds(self):
        for kind in ('developer', 'sub_pm'):
            with self.subTest(kind=kind):
                self.assert_refusal(
                    SPEC_WRITE_PATHS_EMPTY, build, kind,
                    facts_for(kind, write_paths=[]))

    def test_read_only_kinds_refuse_writes(self):
        for kind in READ_ONLY_KINDS:
            with self.subTest(kind=kind):
                self.assert_refusal(
                    SPEC_READONLY_ROLE_HAS_WRITES, build, kind,
                    facts_for(kind, write_paths=[WRITE]))

    def test_invalid_scope_shapes_are_refused(self):
        for overrides in ({'read_paths': None},
                          {'read_paths': ['']},
                          {'read_paths': None, 'write_paths': 'not-a-list'}):
            with self.subTest(overrides=overrides):
                self.assert_refusal(
                    SPEC_SCOPE_INVALID, build, 'developer',
                    facts_for('developer', **overrides))

    def test_repair_and_debug_require_a_predecessor(self):
        self.assert_refusal(
            SPEC_PREDECESSOR_MISSING, build, 'developer',
            repaired_facts(predecessor_spec=None))
        self.assert_refusal(
            SPEC_PREDECESSOR_MISSING, build, 'debug',
            facts_for('debug', predecessor_spec=None))
        self.assert_refusal(
            SPEC_PREDECESSOR_MISSING, build, 'reviewer',
            facts_for('reviewer', fresh=False))

    def test_missing_lineage_is_refused(self):
        self.assert_refusal(
            SPEC_PREDECESSOR_MISSING, build, 'developer',
            repaired_facts(continued_lineage=[]))
        self.assert_refusal(
            SPEC_PREDECESSOR_MISSING, build, 'debug',
            facts_for('debug', continued_lineage=[]))

    def test_contradictory_lineage_is_refused(self):
        cases = (
            repaired_facts(continued_lineage=[TASK]),
            repaired_facts(continued_lineage='not-a-list'),
            repaired_facts(continued_lineage=[PREDECESSOR_TASK, PREDECESSOR_TASK]),
            repaired_facts(predecessor_spec={'task_id': 'bad',
                                             'contract_digest': PREDECESSOR_CONTRACT}),
            facts_for('developer', predecessor_spec={'task_id': PREDECESSOR_TASK,
                                                     'contract_digest':
                                                         PREDECESSOR_CONTRACT}),
            facts_for('developer', continued_lineage=[PREDECESSOR_TASK]),
        )
        for facts in cases:
            with self.subTest(facts=facts):
                self.assert_refusal(SPEC_LINEAGE_INVALID, build, 'developer', facts)

    def test_repair_counts_must_show_a_failure_and_incremented_attempts(self):
        cases = (
            {'carried_counts': {'repairs': 0, 'test_failures': 0, 'attempts': 2}},
            {'carried_counts': {'repairs': 1, 'test_failures': 0, 'attempts': 1}},
            {'carried_counts': None},
            {'carried_counts': {'repairs': 1, 'test_failures': 0}},
            {'carried_counts': {'repairs': True, 'test_failures': 0, 'attempts': 2}},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assert_refusal(
                    SPEC_CARRIED_COUNTS_INVALID, build, 'developer',
                    repaired_facts(**overrides))

    def test_fresh_counts_must_match_the_design_values(self):
        self.assert_refusal(
            SPEC_CARRIED_COUNTS_INVALID, build, 'developer',
            facts_for('developer',
                      carried_counts={'repairs': 1, 'test_failures': 0,
                                      'attempts': 1}))

    def test_freshness_flag_is_required_for_developer_and_reviewer(self):
        for kind in ('developer', 'reviewer'):
            with self.subTest(kind=kind):
                self.assert_refusal(
                    SPEC_FRESHNESS_MISSING, build, kind,
                    facts_for(kind, fresh=None))

    def test_freshness_flag_must_match_the_kind(self):
        for kind in ('investigation', 'sub_pm'):
            with self.subTest(kind=kind):
                self.assert_refusal(
                    SPEC_FRESHNESS_INVALID, build, kind,
                    facts_for(kind, fresh=False))
        self.assert_refusal(
            SPEC_FRESHNESS_INVALID, build, 'debug',
            facts_for('debug', fresh=True))

    def test_dispatch_author_route_is_required_and_consistent(self):
        cases = (
            {'author_route': None},
            {'author_route': 'MAYBE'},
            {'author_worker': None},
            {'author_role': None},
            {'author_route': 'OWNER_ASSIGNMENT_ONLY', 'owner_ref': None},
            {'author_route': 'ALLOWED', 'owner_ref': 'owner-ref'},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assert_refusal(
                    SPEC_DISPATCH_UNAUTHORIZED, build, 'developer',
                    facts_for('developer', **overrides))

    def test_digests_must_be_sha256_values(self):
        for overrides in ({'role_card_digest': 'short'},
                          {'contract_digest': None}):
            with self.subTest(overrides=overrides):
                self.assert_refusal(
                    SPEC_DIGEST_INVALID, build, 'developer',
                    facts_for('developer', **overrides))

    def test_check_receipt_is_required(self):
        for value in (None, [], 'ok', {}, {'d6_worktree_registered': True},
                      {'preflight_status': ''}, {'preflight_status': None}):
            with self.subTest(value=value):
                self.assert_refusal(
                    SPEC_CHECK_RESULTS_INVALID, build, 'developer',
                    facts_for('developer', check_results=value))


if __name__ == '__main__':
    unittest.main()
