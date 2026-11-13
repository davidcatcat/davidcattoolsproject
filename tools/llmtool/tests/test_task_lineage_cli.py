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


class LineageSplitRaceTests(LineageCase):
    """PR #1768 v91 (CWE-362): split admission and child reservation are atomic per ROOT.

    Interleavings are driven by injected hooks: the main SHA reader runs between the
    preflight and the lineage lock, and a wrapped park runs while the lock is held.
    A rival that meets the held lock gets a zero lock wait, so no test sleeps.
    """

    def split(self, root, children, main_sha=lambda: MAIN, store=None):
        return cli.lineage_split(self.store if store is None else store, root,
                                 list(children), pm_session=PM, reason='Review FAIL 3.',
                                 main_sha_reader=main_sha)

    def split_args(self, root, children):
        from types import SimpleNamespace
        return SimpleNamespace(root_id=root, child=list(children), reason='Review FAIL 3.',
                               pm_session=PM)

    def member_ids(self, root):
        return sorted(item['id'] for item in cli._lineage_members(self.store, root))

    def assert_untouched(self, digests):
        for task_id, digest in digests.items():
            record = self.store.get(task_id)
            self.assertEqual(record['record_sha256'], digest)
            self.assertNotIn('lineage', record)

    def over_the_limit_code(self):
        """Two plus two children exceed the child limit; two alone do not."""
        from llmtool import repair_policy
        policy = repair_policy.load_policy()
        self.assertIsNone(repair_policy.admit_split(kind='root', child_count=0,
                                                    new_children=2, policy=policy))
        code = repair_policy.admit_split(kind='root', child_count=2, new_children=2,
                                         policy=policy)
        self.assertIsNotNone(code)
        return code

    def holding_the_lock(self, rival):
        """Patch the ROOT commit so rival() runs inside a split that holds the lineage lock.

        Owner B: the ROOT PARKED commit replaced the old park as the split's
        commit point; rival() runs just before it, with the lock held.
        """
        from unittest import mock
        from llmtool import task_store
        real_commit = task_store.commit_split_root

        def commit(store, task_id, lineage, **kwargs):
            with mock.patch.object(task_store, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0):
                rival()
            return real_commit(store, task_id, lineage, **kwargs)
        return mock.patch.object(task_store, 'commit_split_root', new=commit)

    def test_a_split_that_ran_after_the_preflight_is_counted_and_the_limit_holds(self):
        # Interleaving 1: split B runs whole after split A's preflight, before A's lock.
        over = self.over_the_limit_code()
        root = self.task()
        first, second = [self.task(), self.task()], [self.task(), self.task()]
        untouched = {task_id: self.store.get(task_id)['record_sha256'] for task_id in first}

        def rival_then_main():
            self.split(root, second)
            return MAIN
        self.refused(over, lambda _store: self.split(root, first, main_sha=rival_then_main))
        self.assertEqual(self.state(root), 'PARKED')
        self.assertEqual(self.member_ids(root), sorted(second))
        self.assert_untouched(untouched)

    def test_a_split_started_while_another_holds_the_lock_writes_nothing(self):
        # Interleaving 2: split B passes its preflight while split A holds the lock.
        root = self.task()
        first, second = [self.task(), self.task()], [self.task(), self.task()]
        untouched = {task_id: self.store.get(task_id)['record_sha256'] for task_id in second}
        seen = []
        with self.holding_the_lock(lambda: seen.append(run_command(
                cli.run_task_split, self.split_args(root, second), store=self.store,
                main_sha_reader=lambda: MAIN))):
            result = self.split(root, first)
        self.assertEqual(seen, [(1, {'code': 'LINEAGE_LOCK_BUSY'})])
        self.assertEqual((result['state'], result['children']), ('PARKED', first))
        self.assertEqual(self.member_ids(root), sorted(first))
        self.assert_untouched(untouched)

    def test_a_child_that_changed_after_the_preflight_refuses_with_nothing_written(self):
        changes = (
            ('merge claimed', lambda child: self.store.update(child, merge_attempt=CLAIM)),
            ('joined another split', lambda child: self.split(self.task(), [child])),
        )
        for name, change in changes:
            with self.subTest(name):
                root, child, other = self.task(), self.task(), self.task()
                untouched = {task_id: self.store.get(task_id)['record_sha256']
                             for task_id in (root, other)}

                def change_then_main(child=child, change=change):
                    change(child)
                    return MAIN
                self.assertEqual(
                    run_command(cli.run_task_split, self.split_args(root, [child, other]),
                                store=self.store, main_sha_reader=change_then_main),
                    (1, {'code': 'SPLIT_CHILD_CHANGED'}))
                self.assert_untouched(untouched)
                self.assertEqual(self.member_ids(root), [])
                # The child keeps only its own change: the claim or the other lineage.
                record = self.store.get(child)
                if 'lineage' in record:
                    self.assertNotEqual(record['lineage']['lineage_id'], root)
                else:
                    self.assertEqual(record['merge_attempt'], CLAIM)

    def test_end_and_close_wait_for_a_split_that_holds_the_lineage_lock(self):
        parked = self.parked_root()
        child, done = self.member(parked), self.member(parked)
        merged(self.store, done)
        root, first = self.task(), self.task()
        closes = []

        def rivals():
            self.refused('LINEAGE_LOCK_BUSY', cli.lineage_end, child, 'STOPPED',
                         pm_session=PM, reason='test failures used up')
            closes.append(cli.lineage_close_merged(self.store, done))
        with self.holding_the_lock(rivals):
            self.split(root, [first])
        self.assertEqual(closes, [{'code': 'LINEAGE_CLOSE_FAILED'}])
        self.assertEqual([self.state(child), self.state(done), self.state(parked)],
                         ['OPEN', 'OPEN', 'PARKED'])
        # Once the split released the lock, both run.
        self.assertEqual(cli.lineage_close_merged(self.store, done)['state'], 'CLOSED')
        self.assertEqual(cli.lineage_end(self.store, child, 'STOPPED', pm_session=PM,
                                         reason='test failures used up')['state'], 'STOPPED')
        self.assertEqual([self.state(child), self.state(done)], ['STOPPED', 'CLOSED'])

    def test_a_store_without_the_lineage_lock_refuses_before_any_write(self):
        real = self.store

        class Unlocked:
            def __getattr__(self, name):
                if name in ('_dirfd', '_lock_sidecar', '_unlock_sidecar'):
                    raise AttributeError(name)
                return getattr(real, name)
        root, first = self.task(), self.task()
        untouched = {task_id: self.store.get(task_id)['record_sha256']
                     for task_id in (root, first)}
        self.refused('LINEAGE_LOCK_UNAVAILABLE',
                     lambda _store: self.split(root, [first], store=Unlocked()))
        self.assert_untouched(untouched)


NEW_MAIN = 'f' * 40


class LineageReplanTests(LineageCase):
    """#1502 D10d1/D10d2: the lineage's one REPLAN, chosen at PM_DECIDE of a child."""

    def split(self, count=2):
        root, children = self.task(), [self.task() for _ in range(count)]
        cli.lineage_split(self.store, root, children, pm_session=PM,
                          reason='Review FAIL 3.', main_sha_reader=lambda: MAIN)
        return root, children

    def replan_args(self, child, replan, replaces, reason='Review route used up.',
                    session=PM):
        from types import SimpleNamespace
        return SimpleNamespace(child_id=child, replan_task=replan, replaces=list(replaces),
                               reason=reason, pm_session=session)

    def test_replan_records_the_one_replan_from_latest_main_and_stops_replaced_children(self):
        from llmtool import repair_policy
        self.assertEqual(repair_policy.load_policy()['replan_max'], 1)
        root, (first, second) = self.split()
        replan = self.task()
        result = cli.lineage_replan(self.store, first, replan, [first], pm_session=PM,
                                    reason='Review route used up.',
                                    main_sha_reader=lambda: NEW_MAIN)
        line = self.store.get(replan)['lineage']
        self.assertEqual((line['lineage_id'], line['kind'], line['parent'], line['replaces'],
                          line['state'], line['base_sha']),
                         (root, 'replan', root, [first], 'OPEN', NEW_MAIN))
        self.assertEqual((result['task_id'], result['base_sha'], result['stopped'],
                          result['stop_failed']), (replan, NEW_MAIN, [first], []))
        self.assertEqual([notice['state'] for notice in result['owner_notices']], ['STOPPED'])
        self.assertEqual(result['owner_notices'][0]['marker'], cli.OWNER_NOTICE_MARKER)
        self.assertEqual([self.state(first), self.state(second), self.state(root)],
                         ['STOPPED', 'OPEN', 'PARKED'])
        # The lineage's one REPLAN is used: a second one is refused with nothing written.
        other, before = self.task(), self.store.get(second)['record_sha256']
        self.refused('REPLAN_USED', cli.lineage_replan, second, other, [second],
                     pm_session=PM, reason='again', main_sha_reader=lambda: NEW_MAIN)
        self.assertNotIn('lineage', self.store.get(other))
        self.assertEqual(self.store.get(second)['record_sha256'], before)

    def test_replan_refusals_write_nothing_read_no_main_sha_and_print_only_the_code(self):
        root, (first, second) = self.split()
        merged(self.store, second)
        cli.lineage_close_merged(self.store, second)  # second is CLOSED, root stays PARKED
        replan, plain = self.task(), self.task()
        reads = []

        def main_sha():
            reads.append(True)
            return NEW_MAIN
        cases = (
            ('REPLAN_NOT_CHILD', self.replan_args(root, replan, [root])),
            ('REPLAN_REPLACES_INVALID', self.replan_args(first, replan, [])),
            ('REPLAN_REPLACES_INVALID', self.replan_args(first, replan, [second])),
            ('REPLAN_REPLACES_INVALID', self.replan_args(first, replan, [first, second])),
            ('REPLAN_REPLACES_INVALID', self.replan_args(first, replan, [first, plain])),
            ('REPLAN_REPLACES_INVALID', self.replan_args(first, replan, [first, first])),
            ('LINEAGE_MEMBER_INVALID', self.replan_args(first, first, [first])),
            ('LINEAGE_MEMBER_INVALID', self.replan_args(first, second, [first])),
            ('LINEAGE_PM_SESSION_DENIED',
             self.replan_args(first, replan, [first], session='pm-other')),
            ('REPLAN_REASON_REQUIRED', self.replan_args(first, replan, [first], reason=' ')),
        )
        every = (root, first, second, replan, plain)
        before = {task_id: self.store.get(task_id)['record_sha256'] for task_id in every}
        for code, args in cases:
            with self.subTest(code=code, replaces=args.replaces):
                self.assertEqual(run_command(cli.run_task_replan, args, store=self.store,
                                             main_sha_reader=main_sha), (1, {'code': code}))
        self.assertEqual(reads, [])
        for task_id in every:
            self.assertEqual(self.store.get(task_id)['record_sha256'], before[task_id])
        self.assertNotIn('lineage', self.store.get(replan))
        # A child that already ended is not at PM_DECIDE.
        cli.lineage_end(self.store, first, 'STOPPED', pm_session=PM, reason='tests used up')
        self.refused('REPLAN_CHILD_NOT_OPEN', cli.lineage_replan, first, replan, [first],
                     pm_session=PM, reason='x', main_sha_reader=main_sha)
        self.assertEqual(reads, [])

    def test_a_replan_task_that_changed_after_the_preflight_refuses_with_nothing_written(self):
        _root, (first, _second) = self.split()
        replan = self.task()
        before = self.store.get(first)['record_sha256']

        def change_then_main():
            self.store.update(replan, merge_attempt=CLAIM)
            return NEW_MAIN
        self.refused('REPLAN_TASK_CHANGED', cli.lineage_replan, first, replan, [first],
                     pm_session=PM, reason='x', main_sha_reader=change_then_main)
        self.assertNotIn('lineage', self.store.get(replan))
        self.assertEqual(self.store.get(first)['record_sha256'], before)

    def test_a_closed_replan_meets_the_obligation_and_closes_the_root_without_notice(self):
        # D10d2: the adapter records the REPLAN; once it is merged the ROOT is CLOSED.
        root, (first, second) = self.split()
        merged(self.store, first)
        self.assertIsNone(cli.lineage_close_merged(self.store, first)['root'])
        replan = self.task()
        code, result = run_command(cli.run_task_replan, self.replan_args(second, replan,
                                   [second]), store=self.store,
                                   main_sha_reader=lambda: NEW_MAIN)
        self.assertEqual((code, result['stopped'], result['base_sha']),
                         (0, [second], NEW_MAIN))
        self.assertEqual(result['owner_notices'][0]['state'], 'STOPPED')
        self.assertEqual(self.state(root), 'PARKED')
        merged(self.store, replan)
        closed = cli.lineage_close_merged(self.store, replan)
        self.assertEqual((closed['state'], closed['owner_notice']), ('CLOSED', None))
        self.assertEqual(closed['root'], {'task_id': root, 'state': 'CLOSED',
                                          'owner_notice': None})
        self.assertEqual([self.state(second), self.state(replan), self.state(root)],
                         ['STOPPED', 'CLOSED', 'CLOSED'])


class LineageReplanCommandTests(LineageCase):
    def test_replan_through_the_real_parser(self):
        import contextlib
        import io
        from llmtool import cli as main_cli
        root, first, second = self.task(), self.task(), self.task()
        cli.lineage_split(self.store, root, [first, second], pm_session=PM,
                          reason='Review FAIL 3.', main_sha_reader=lambda: MAIN)
        replan = self.task()
        args = parse('replan', first, '--replan-task', replan, '--replaces', first, second,
                     '--pm-session', PM, '--reason', 'Review route used up.')
        self.assertEqual((args.action, args.child_id, args.replan_task, args.replaces),
                         ('replan', first, replan, [first, second]))
        self.assertEqual(parse('replan', first, '--replan-task', replan, '--replaces', first,
                               '--replaces', second, '--pm-session', PM,
                               '--reason', 'x').replaces, [first, second])
        self.assertEqual(main_cli.TASK_ACTIONS['replan'], ('run_task_replan', False))
        function = getattr(cli, main_cli.TASK_ACTIONS[args.action][0])
        code, result = run_command(function, args, store=self.store,
                                   main_sha_reader=lambda: NEW_MAIN)
        self.assertEqual((code, result['task_id'], result['stopped']),
                         (0, replan, [first, second]))
        self.assertEqual([self.state(first), self.state(second), self.state(replan)],
                         ['STOPPED', 'STOPPED', 'OPEN'])
        bad = (('replan', first, '--replaces', first, '--pm-session', PM, '--reason', 'x'),
               ('replan', first, '--replan-task', replan, '--pm-session', PM, '--reason', 'x'),
               ('replan', first, '--replan-task', replan, '--replaces', first, '--reason', 'x'),
               ('replan', first, '--replan-task', replan, '--replaces', first,
                '--pm-session', PM))
        for argv in bad:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parse(*argv)
        self.assertEqual(list(main_cli.TASK_ACTIONS), sorted(main_cli.TASK_ACTIONS))


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


class LineageEndVsAdvanceTests(LineageCase):
    """PR #1789 v91 F1 (CWE-841): a lineage end honours the Task's advance lease.

    task advance and task drive hold the lease shared for their whole run (the
    lease is held here directly, as SplitVsStartedChildTests does for a split).
    Every end path (task drop, a REPLAN's STOP, the ROOT roll-up) takes it
    exclusively without waiting, so it refuses LINEAGE_TASK_ADVANCING and writes
    nothing while an advance runs; once the advance ends, the same end runs.
    """

    def digests(self, *task_ids):
        return {task_id: self.store.get(task_id)['record_sha256'] for task_id in task_ids}

    def test_drop_stop_and_failed_final_refuse_while_the_task_is_advanced(self):
        root = self.parked_root()
        child = self.member(root)
        replan = self.member(root, kind='replan', replaces=[child])
        for task_id, state in ((child, 'DROPPED'), (child, 'STOPPED'),
                               (replan, 'FAILED_FINAL')):
            with self.subTest(state=state):
                before = self.digests(root, child, replan)
                with self.store.advance_lease(task_id):
                    self.refused('LINEAGE_TASK_ADVANCING', cli.lineage_end, task_id, state,
                                 pm_session=PM, reason='Original function is not affected.')
                self.assertEqual(self.digests(root, child, replan), before)
        self.assertEqual([self.state(child), self.state(replan), self.state(root)],
                         ['OPEN', 'OPEN', 'PARKED'])
        # The adapter prints only the refusal code.
        from types import SimpleNamespace
        args = SimpleNamespace(task_id=child, state='STOPPED', pm_session=PM,
                               reason='test failures used up')
        with self.store.advance_lease(child):
            self.assertEqual(run_command(cli.run_task_drop, args, store=self.store),
                             (1, {'code': 'LINEAGE_TASK_ADVANCING'}))
        self.assertEqual(self.state(child), 'OPEN')
        # After the advance, the same end runs and an advance then sees it ended.
        code, result = run_command(cli.run_task_drop, args, store=self.store)
        self.assertEqual((code, result['state'], result['owner_notice']['state']),
                         (0, 'STOPPED', 'STOPPED'))
        self.refused('TASK_LINEAGE_ENDED', lambda store: store.check_external_action(child))

    def test_a_replan_never_stops_a_replaced_child_that_is_being_advanced(self):
        root, first, second = self.task(), self.task(), self.task()
        cli.lineage_split(self.store, root, [first, second], pm_session=PM,
                          reason='Review FAIL 3.', main_sha_reader=lambda: MAIN)
        replan = self.task()
        with self.store.advance_lease(second):
            result = cli.lineage_replan(self.store, first, replan, [first, second],
                                        pm_session=PM, reason='Review route used up.',
                                        main_sha_reader=lambda: MAIN)
        self.assertEqual((result['stopped'], result['stop_failed']), ([first], [second]))
        self.assertEqual([notice['task_id'] for notice in result['owner_notices']], [first])
        self.assertEqual([self.state(first), self.state(second), self.state(replan)],
                         ['STOPPED', 'OPEN', 'OPEN'])
        # The PM ends the child with task drop once its advance has ended.
        self.assertEqual(cli.lineage_end(self.store, second, 'STOPPED', pm_session=PM,
                                         reason='Replaced by the REPLAN.')['state'], 'STOPPED')

    def test_the_root_roll_up_waits_for_an_advance_of_the_root(self):
        root = self.parked_root()
        only = self.member(root)
        with self.store.advance_lease(root):
            result = cli.lineage_end(self.store, only, 'DROPPED', pm_session=PM,
                                     reason='Original function is not affected.')
        # The child's end is saved and notified; the ROOT stays PARKED (fail closed).
        self.assertEqual((result['state'], result['root']),
                         ('DROPPED', {'code': 'LINEAGE_ROLL_UP_FAILED'}))
        self.assertEqual(result['owner_notice']['state'], 'DROPPED')
        self.assertEqual(self.state(root), 'PARKED')
        self.assertEqual(self.store.check_external_action(root)['task_id'], root)
        # Without the advance the roll-up ends the ROOT, which then takes no action.
        self.assertEqual(cli._lineage_roll_up(self.store, root)['state'], 'FAILED_FINAL')
        self.refused('TASK_LINEAGE_ENDED', lambda store: store.check_external_action(root))
        # A merged member's roll-up refuses the same way and is retried by the replay.
        other = self.parked_root()
        member = self.member(other)
        merged(self.store, member)
        with self.store.advance_lease(other):
            self.assertEqual(cli.lineage_close_merged(self.store, member),
                             {'code': 'LINEAGE_CLOSE_FAILED'})
        self.assertEqual([self.state(member), self.state(other)], ['CLOSED', 'PARKED'])
        self.assertEqual(cli.lineage_close_merged(self.store, member)['root']['state'], 'CLOSED')


if __name__ == '__main__':
    unittest.main()