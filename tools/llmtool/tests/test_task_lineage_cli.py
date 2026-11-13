"""#1502 WP-D D10: task split, replan and drop through the D3 lineage gates."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from llmtool import task_submission_cli as cli
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id, record_lineage

MAIN = 'e' * 40
PM = 'pm-lineage-test'
AT = '2026-11-13T00:00:00Z'
CLAIM = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
         'claimed_at': '2026-11-13T00:00:00.000000Z'}


class LineageCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')

    def task(self, session=PM):
        request = 'Bounded lineage test Task.'
        task = cli._record(new_id(), new_id(), session, 'pm-profile', request,
                           state='PM_RESPONDED', provider_type='openai-compatible',
                           model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
                           request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None}, response='PM reply.')
        self.store.create(task)
        return task['task_id']

    def member(self, root, kind='child', replaces=()):
        task_id = self.task()
        record_lineage(self.store, task_id, {
            'lineage_id': root, 'kind': kind, 'parent': root, 'replaces': list(replaces),
            'state': 'OPEN', 'reason': None, 'base_sha': MAIN, 'recorded_at': AT})
        return task_id

    def parked_root(self):
        root = self.task()
        record_lineage(self.store, root, {
            'lineage_id': root, 'kind': 'root', 'parent': None, 'replaces': [],
            'state': 'PARKED', 'reason': 'split', 'base_sha': None, 'recorded_at': AT})
        return root

    def refused(self, code, call, *args, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            call(self.store, *args, **kwargs)
        self.assertEqual(getattr(caught.exception, 'task_store_error_code', None) or
                         str(caught.exception), code)

    def state(self, task_id):
        return self.store.get(task_id)['lineage']['state']


class LineageReadTests(LineageCase):
    def test_a_task_without_lineage_is_its_own_open_root(self):
        plain = self.task()
        line = cli._lineage_of(self.store.get(plain))
        self.assertEqual((line['lineage_id'], line['kind'], line['state'], line['replaces']),
                         (plain, 'root', 'OPEN', []))
        self.refused('LINEAGE_X', lambda _store: cli._lineage_refuse('LINEAGE_X'))
        # A present but malformed lineage fails closed instead of reading as a ROOT.
        for bad in ('x', [], {'kind': 'root'}):
            self.refused('TASK_LINEAGE_INVALID',
                         lambda _store, bad=bad: cli._lineage_of({'task_id': plain, 'lineage': bad}))


class LineageNoneTests(LineageCase):
    def test_an_explicit_none_lineage_fails_closed(self):
        # PR #1698 security Low: a present None lineage is validated, not read as a ROOT.
        plain = self.task()
        self.refused('TASK_LINEAGE_INVALID',
                     lambda _store: cli._lineage_of({'task_id': plain, 'lineage': None}))
        self.assertEqual(cli._lineage_of({'task_id': plain})['kind'], 'root')


class LineageMemberTests(LineageCase):
    def test_lineage_tasks_belong_to_their_pm_and_members_are_read_from_the_store(self):
        root = self.parked_root()
        child, plain = self.member(root), self.task()
        self.assertEqual(cli._lineage_task(self.store, child, PM)['task_id'], child)
        self.refused('LINEAGE_PM_SESSION_DENIED', cli._lineage_task, child, 'pm-other')
        self.assertEqual([item['id'] for item in cli._lineage_members(self.store, root)],
                         [child])
        self.assertEqual(cli._lineage_members(self.store, plain), [])


class LineageNoticeTests(LineageCase):
    def test_owner_notice_only_for_failed_end_states_and_reasons_are_screened(self):
        notice = cli.lineage_owner_notice('a' * 32, 'DROPPED', 'not needed')
        self.assertTrue(notice['body'].startswith(cli.OWNER_NOTICE_MARKER + '\n'))
        self.assertEqual((notice['state'], notice['marker']),
                         ('DROPPED', cli.OWNER_NOTICE_MARKER))
        for state in ('CLOSED', 'OPEN', 'PARKED'):
            self.assertIsNone(cli.lineage_owner_notice('a' * 32, state, 'merged'))
        for bad in ('', 'x' * 501, 'line\nbreak', None):
            self.refused('LINEAGE_REASON_INVALID',
                         lambda _store, value: cli.lineage_owner_notice('a' * 32, 'STOPPED', value), bad)
        self.assertEqual(cli._lineage_reason('Original function unaffected.', 'R'),
                         'Original function unaffected.')
        for reason in ('', 'x' * 501, 'line\nbreak', None):
            self.refused('R', lambda _store, value: cli._lineage_reason(value, 'R'), reason)


class LineageRollUpTests(LineageCase):
    def end(self, task_id, state):
        line = self.store.get(task_id)['lineage']
        record_lineage(self.store, task_id, dict(line, state=state, reason='ended'))

    def test_a_parked_root_rolls_up_once_and_never_notifies(self):
        root = self.parked_root()
        child = self.member(root)
        replan = self.member(root, kind='replan', replaces=[child])
        self.assertIsNone(cli._lineage_roll_up(self.store, root))
        self.end(child, 'STOPPED')
        self.assertIsNone(cli._lineage_roll_up(self.store, root))
        self.end(replan, 'CLOSED')
        self.assertEqual(cli._lineage_roll_up(self.store, root),
                         {'task_id': root, 'state': 'CLOSED', 'owner_notice': None})
        self.assertEqual(self.state(root), 'CLOSED')
        self.assertIsNone(cli._lineage_roll_up(self.store, root))
        other = self.parked_root()
        self.end(self.member(other), 'DROPPED')
        self.assertEqual(cli._lineage_roll_up(self.store, other)['state'], 'FAILED_FINAL')
        self.assertIsNone(cli._lineage_roll_up(self.store, self.task()))


class LineageEndTests(LineageCase):
    def test_drop_needs_a_reason_and_a_child_and_notifies_the_owner(self):
        root = self.parked_root()
        first, second = self.member(root), self.member(root)
        self.refused('DROP_REASON_REQUIRED', cli.lineage_end, first, 'DROPPED',
                     pm_session=PM, reason=' ')
        self.refused('DROP_NOT_CHILD', cli.lineage_end, root, 'DROPPED',
                     pm_session=PM, reason='not needed')
        self.refused('LINEAGE_STATE_INVALID', cli.lineage_end, first, 'CLOSED',
                     pm_session=PM, reason='not needed')
        result = cli.lineage_end(self.store, first, 'DROPPED', pm_session=PM,
                                 reason='Original function is not affected.')
        self.assertEqual(self.state(first), 'DROPPED')
        self.assertEqual(result['owner_notice']['marker'], cli.OWNER_NOTICE_MARKER)
        self.assertIsNone(result['root'])
        self.assertEqual(self.state(root), 'PARKED')
        self.refused('LINEAGE_NOT_OPEN', cli.lineage_end, first, 'STOPPED',
                     pm_session=PM, reason='again')
        self.assertEqual(self.state(second), 'OPEN')


def merged(store, task_id):
    """Record a merge claim and its MERGED outcome, as task advance does."""
    claim = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
             'claimed_at': '2026-11-13T00:00:00.000000Z'}
    store.update(task_id, merge_attempt=claim)
    store.update(task_id, merge_attempt=dict(claim, state='MERGED'))


class LineageMergedTests(LineageCase):
    def test_merged_tasks_close_without_notice_and_close_the_root(self):
        root = self.parked_root()
        first, second = self.member(root), self.member(root)
        self.assertIsNone(cli.lineage_close_merged(self.store, first))
        self.assertIsNone(cli.lineage_owner_notice(first, 'CLOSED', 'merged'))
        merged(self.store, first)
        self.assertEqual(cli.lineage_close_merged(self.store, first),
                         {'task_id': first, 'state': 'CLOSED', 'owner_notice': None,
                          'root': None})
        merged(self.store, second)
        closed = cli.lineage_close_merged(self.store, second)
        self.assertEqual(closed['root'], {'task_id': root, 'state': 'CLOSED',
                                          'owner_notice': None})
        self.assertEqual(self.state(root), 'CLOSED')
        self.assertIsNone(cli.lineage_close_merged(self.store, second))
        plain = self.task()
        merged(self.store, plain)
        self.assertIsNone(cli.lineage_close_merged(self.store, plain))

    def test_a_failed_roll_up_after_close_is_retried(self):
        root = self.parked_root()
        only = self.member(root)
        merged(self.store, only)
        real = cli._lineage_roll_up

        def failing(store, lineage_id):
            raise LLMToolError('LINEAGE_ROLL_UP_FAILED')
        cli._lineage_roll_up = failing
        try:
            self.assertEqual(cli.lineage_close_merged(self.store, only),
                             {'code': 'LINEAGE_CLOSE_FAILED'})
        finally:
            cli._lineage_roll_up = real
        self.assertEqual(self.state(only), 'CLOSED')
        self.assertEqual(self.state(root), 'PARKED')
        retried = cli.lineage_close_merged(self.store, only)
        self.assertEqual(retried['root']['state'], 'CLOSED')
        self.assertEqual(self.state(root), 'CLOSED')
        self.assertIsNone(cli.lineage_close_merged(self.store, only))


class LineageDropTests(LineageCase):
    def test_drop_command_prints_the_result_or_only_a_code(self):
        import io
        import json
        from types import SimpleNamespace
        child = self.member(self.parked_root())
        outputs = []
        for reason, code in ((' ', 1), ('Original function is not affected.', 0)):
            out = io.StringIO()
            args = SimpleNamespace(task_id=child, state='DROPPED', reason=reason,
                                   pm_session=PM)
            self.assertEqual(cli.run_task_drop(args, store=self.store, stdout=out), code)
            outputs.append(json.loads(out.getvalue()))
        self.assertEqual(outputs[0], {'code': 'DROP_REASON_REQUIRED'})
        self.assertEqual(outputs[1]['state'], 'DROPPED')


class LineageNewMemberTests(LineageCase):
    def test_only_a_new_unmerged_task_of_the_same_pm_joins_a_lineage(self):
        root = self.parked_root()
        child, fresh = self.member(root), self.task()
        self.assertEqual(cli._lineage_new_member(self.store, fresh, PM, {root})['task_id'],
                         fresh)
        for task_id, taken in ((child, set()), (root, set()), (fresh, {fresh})):
            self.refused('LINEAGE_MEMBER_INVALID', cli._lineage_new_member, task_id, PM, taken)
        merged(self.store, fresh)
        self.refused('LINEAGE_MEMBER_INVALID', cli._lineage_new_member, fresh, PM, set())

    def test_stopped_and_failed_final_notify_and_the_root_rolls_up_once(self):
        root = self.parked_root()
        child = self.member(root)
        replan = self.member(root, kind='replan', replaces=[child])
        self.refused('FAILED_FINAL_NOT_REPLAN', cli.lineage_end, child, 'FAILED_FINAL',
                     pm_session=PM, reason='review route used up')
        stopped = cli.lineage_end(self.store, child, 'STOPPED', pm_session=PM,
                                  reason='test failures used up')
        self.assertEqual(stopped['owner_notice']['state'], 'STOPPED')
        self.assertIsNone(stopped['root'])
        final = cli.lineage_end(self.store, replan, 'FAILED_FINAL', pm_session=PM,
                                reason='review route used up')
        self.assertEqual(final['owner_notice']['state'], 'FAILED_FINAL')
        self.assertEqual(final['root'], {'task_id': root, 'state': 'FAILED_FINAL',
                                         'owner_notice': None})
        self.assertEqual(self.state(root), 'FAILED_FINAL')
        self.assertIsNone(cli._lineage_roll_up(self.store, root))


class LineageSplitTests(LineageCase):
    def test_split_parks_the_root_and_children_start_from_main(self):
        root, first, second = self.task(), self.task(), self.task()
        result = cli.lineage_split(self.store, root, [first, second], pm_session=PM,
                                   reason='Review FAIL 3.', main_sha_reader=lambda: MAIN)
        self.assertEqual((result['state'], result['base_sha']), ('PARKED', MAIN))
        self.assertEqual(self.state(root), 'PARKED')
        for child in (first, second):
            line = self.store.get(child)['lineage']
            self.assertEqual((line['kind'], line['parent'], line['state'], line['base_sha']),
                             ('child', root, 'OPEN', MAIN))

    def split_racing(self, race):
        """Split a fresh ROOT; race(root) runs after the split's checks, before its park."""
        root, first, second = self.task(), self.task(), self.task()

        def main_sha():
            race(root)
            return MAIN
        return root, (first, second), lambda _store: cli.lineage_split(
            self.store, root, [first, second], pm_session=PM, reason='Review FAIL 3.',
            main_sha_reader=main_sha)

    def assert_nothing_recorded(self, root, children):
        self.assertNotIn('lineage', self.store.get(root))
        for child in children:
            self.assertNotIn('lineage', self.store.get(child))

    def test_merge_claimed_before_the_park_refuses_the_split_and_records_no_child(self):
        # Security v91 (CWE-362): task advance claims the merge after split read OPEN.
        root, children, split = self.split_racing(lambda root: self.store.update(
            root, merge_attempt=CLAIM))
        self.refused('SPLIT_ROOT_MERGE_STARTED', split)
        self.assert_nothing_recorded(root, children)
        self.assertEqual(self.store.get(root)['merge_attempt'], CLAIM)

    def test_root_ended_before_the_park_refuses_the_split_and_records_no_child(self):
        root, children, split = self.split_racing(lambda root: record_lineage(
            self.store, root, {'lineage_id': root, 'kind': 'root', 'parent': None,
                               'replaces': [], 'state': 'CLOSED', 'reason': None,
                               'base_sha': None, 'recorded_at': AT}))
        self.refused('SPLIT_ROOT_NOT_OPEN', split)
        self.assertEqual(self.state(root), 'CLOSED')
        for child in children:
            self.assertNotIn('lineage', self.store.get(child))

    def test_a_root_whose_merge_started_is_refused_before_any_write(self):
        root, first = self.task(), self.task()
        self.store.update(root, merge_attempt=CLAIM)
        self.refused('SPLIT_ROOT_NOT_OPEN', cli.lineage_split, root, [first], pm_session=PM,
                     reason='Review FAIL 3.', main_sha_reader=lambda: MAIN)
        self.assert_nothing_recorded(root, [first])

    def test_park_first_then_a_merge_claim_is_refused_under_the_lock(self):
        # Security v91: a task advance that read the ROOT OPEN before the park, then
        # claims with its stale record_sha256, still gets MERGE_ROOT_PARKED.
        root, first = self.task(), self.task()
        stale = self.store.get(root)['record_sha256']
        cli.lineage_split(self.store, root, [first], pm_session=PM,
                          reason='Review FAIL 3.', main_sha_reader=lambda: MAIN)
        self.refused('MERGE_ROOT_PARKED', lambda store: store.compare_and_update(
            root, expected={'record_sha256': stale}, merge_attempt=CLAIM))
        self.refused('MERGE_ROOT_PARKED', lambda store: store.update(
            root, merge_attempt=CLAIM))
        self.assertNotIn('merge_attempt', self.store.get(root))
        self.assertEqual(self.state(root), 'PARKED')
        self.assertEqual(self.store.get(first)['lineage']['state'], 'OPEN')


def run_command(function, args, **kwargs):
    """Run one lineage adapter; return its exit code and the one printed JSON object."""
    import io
    import json
    out = io.StringIO()
    code = function(args, stdout=out, **kwargs)
    return code, json.loads(out.getvalue())


def parse(*argv):
    from llmtool import cli as main_cli
    return main_cli.parser_for_cli().parse_args(['task', *argv])


class LineageSplitCommandTests(LineageCase):
    def split_args(self, root, children, reason='Review FAIL 3.', session=PM):
        from types import SimpleNamespace
        return SimpleNamespace(root_id=root, child=list(children), reason=reason,
                               pm_session=session)

    def test_split_refusals_write_nothing_read_no_main_sha_and_print_only_the_code(self):
        # D10c3: every refusal exits 1, prints only {'code': ...}, records no lineage
        # on any Task and never reads the main SHA.
        root, first, second, claimed = self.task(), self.task(), self.task(), self.task()
        self.store.update(claimed, merge_attempt=CLAIM)
        reads = []

        def main_sha():
            reads.append(True)
            return MAIN
        cases = (
            ('SPLIT_REASON_REQUIRED', self.split_args(root, [first], reason=' ')),
            ('LINEAGE_PM_SESSION_DENIED',
             self.split_args(root, [first], session='pm-other')),
            ('LINEAGE_MEMBER_INVALID', self.split_args(root, [root])),
            ('LINEAGE_MEMBER_INVALID', self.split_args(root, [first, first])),
            ('LINEAGE_MEMBER_INVALID', self.split_args(root, [first, claimed])),
            ('SPLIT_ROOT_NOT_OPEN', self.split_args(claimed, [first])),
        )
        every = (root, first, second, claimed)
        before = {task_id: self.store.get(task_id)['record_sha256'] for task_id in every}
        for code, args in cases:
            with self.subTest(code=code):
                self.assertEqual(run_command(cli.run_task_split, args, store=self.store,
                                             main_sha_reader=main_sha),
                                 (1, {'code': code}))
        self.assertEqual(reads, [])
        for task_id in every:
            record = self.store.get(task_id)
            self.assertEqual(record['record_sha256'], before[task_id])
            self.assertNotIn('lineage', record)

    def test_a_parked_race_refusal_prints_only_the_code_and_records_no_child(self):
        # The locked park refuses after the main SHA read: still only a code, no child.
        root, first = self.task(), self.task()

        def main_sha():
            self.store.update(root, merge_attempt=CLAIM)
            return MAIN
        self.assertEqual(run_command(cli.run_task_split, self.split_args(root, [first]),
                                     store=self.store, main_sha_reader=main_sha),
                         (1, {'code': 'SPLIT_ROOT_MERGE_STARTED'}))
        self.assertNotIn('lineage', self.store.get(root))
        self.assertNotIn('lineage', self.store.get(first))

    def test_split_through_the_real_parser_parks_the_root(self):
        from llmtool import cli as main_cli
        root, first, second = self.task(), self.task(), self.task()
        args = parse('split', root, '--child', first, '--child', second,
                     '--pm-session', PM, '--reason', 'Review FAIL 3.')
        self.assertEqual((args.command, args.action, args.root_id, args.child),
                         ('task', 'split', root, [first, second]))
        self.assertEqual(parse('split', root, '--child', first, second, '--pm-session', PM,
                               '--reason', 'x').child, [first, second])
        self.assertEqual(main_cli.TASK_ACTIONS['split'], ('run_task_split', False))
        function = getattr(cli, main_cli.TASK_ACTIONS[args.action][0])
        code, result = run_command(function, args, store=self.store,
                                   main_sha_reader=lambda: MAIN)
        self.assertEqual(code, 0)
        self.assertEqual((result['state'], result['children'], result['base_sha'],
                          result['owner_notice']), ('PARKED', [first, second], MAIN, None))
        self.assertEqual(self.state(root), 'PARKED')
        self.assertEqual([self.state(first), self.state(second)], ['OPEN', 'OPEN'])


class LineageCommandRegistrationTests(LineageCase):
    def test_drop_through_the_real_parser_and_the_actions_stay_sorted(self):
        import contextlib
        import io
        from llmtool import cli as main_cli
        child = self.member(self.parked_root())
        self.assertEqual(parse('drop', child, '--pm-session', PM, '--reason', 'x').state,
                         'DROPPED')
        args = parse('drop', child, '--state', 'STOPPED', '--pm-session', PM,
                     '--reason', 'test failures used up')
        self.assertEqual((args.action, args.task_id, args.state), ('drop', child, 'STOPPED'))
        self.assertEqual(main_cli.TASK_ACTIONS['drop'], ('run_task_drop', False))
        function = getattr(cli, main_cli.TASK_ACTIONS[args.action][0])
        code, result = run_command(function, args, store=self.store)
        self.assertEqual((code, result['state'], result['owner_notice']['state']),
                         (0, 'STOPPED', 'STOPPED'))
        self.assertEqual(self.state(child), 'STOPPED')
        bad = (('drop', child, '--state', 'CLOSED', '--pm-session', PM, '--reason', 'x'),
               ('drop', child, '--reason', 'x'),
               ('split', child, '--pm-session', PM, '--reason', 'x'),
               ('split', child, '--child', child, '--pm-session', PM))
        for argv in bad:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parse(*argv)
        self.assertEqual(list(main_cli.TASK_ACTIONS), sorted(main_cli.TASK_ACTIONS))


if __name__ == '__main__':
    unittest.main()