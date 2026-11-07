import hashlib
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from llmtool import merge_train
from llmtool import task_completion


class MergeTrainTests(unittest.TestCase):
    """#1182 T2: merge-train candidate build and exact-delta proof."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(os.path.join(self.tmp.name, 'repo'))
        os.mkdir(self.root)
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_OPTIONAL_LOCKS='0', LC_ALL='C')
        for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
                     'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
            self.env.pop(name, None)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Train Test')
        self.git('config', 'user.email', 'train-test@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.write('base.txt', 'base\n')
        self.git('add', '--', 'base.txt')
        self.git('commit', '-q', '-m', 'base')
        self.base = self.rev('HEAD')

    def git(self, *args, env=None, input=None):
        return subprocess.run(['git', *args], cwd=self.root, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=self.env if env is None else env, input=input).stdout

    def rev(self, name):
        return self.git('rev-parse', '--verify', name).strip()

    def write(self, path, text):
        with open(os.path.join(self.root, path), 'w', encoding='utf-8') as stream:
            stream.write(text)

    def branch(self, name, files, start=None):
        self.git('checkout', '-q', '-b', name, start or self.base)
        for path, text in files.items():
            self.write(path, text)
            self.git('add', '--', path)
        self.git('commit', '-q', '-m', name)
        sha = self.rev('HEAD')
        self.git('checkout', '-q', 'main')
        return sha

    def parents(self, sha):
        return self.git('rev-list', '--parents', '-n', '1', sha).split()[1:]

    def merge_tree(self, first, second):
        return self.git('merge-tree', '--write-tree', first, second).splitlines()[0]

    def commit_tree(self, tree, *parents, message='hand made'):
        args = ['commit-tree', tree]
        for parent in parents:
            args += ['-p', parent]
        return self.git(*args, '-m', message).strip()

    def tree_with(self, tree, path, text):
        blob = self.git('hash-object', '-w', '--stdin', input=text).strip()
        index = os.path.join(self.tmp.name, 'alt-index')
        env = dict(self.env, GIT_INDEX_FILE=index)
        try:
            self.git('read-tree', tree, env=env)
            self.git('update-index', '--add', '--cacheinfo', f'100644,{blob},{path}', env=env)
            return self.git('write-tree', env=env).strip()
        finally:
            if os.path.exists(index):
                os.unlink(index)

    def state(self):
        with open(os.path.join(self.root, '.git', 'index'), 'rb') as stream:
            index = hashlib.sha256(stream.read()).hexdigest()
        return {
            'head': self.rev('HEAD'),
            'symbolic': self.git('symbolic-ref', 'HEAD'),
            'index': index,
            'stage': self.git('ls-files', '--stage'),
            'status': self.git('status', '--porcelain=v1', '--untracked-files=all'),
            'refs': self.git('for-each-ref'),
        }

    def members(self, count=2):
        return [(f'pr-{number}',
                 self.branch(f'm{number}', {f'm{number}.txt': f'member {number}\n'}))
                for number in range(1, count + 1)]

    # Clean trains

    def test_two_member_clean_train_builds_and_verifies(self):
        members = self.members(2)
        result = merge_train.build_candidate(self.root, self.base, members)
        self.assertEqual(result['merged'], ['pr-1', 'pr-2'])
        self.assertEqual(result['excluded'], [])
        self.assertEqual(result['codes'], [])
        head = result['head']
        self.assertRegex(head, r'\A[0-9a-f]{40}\Z')
        first, second = self.parents(head)
        self.assertEqual(second, members[1][1])
        self.assertEqual(self.parents(first), [self.base, members[0][1]])
        self.assertEqual(self.git('ls-tree', '--name-only', head).split(),
                         ['base.txt', 'm1.txt', 'm2.txt'])
        self.assertEqual(self.git('log', '-1', '--format=%B', head).strip(),
                         'llmtool merge train member pr-2')
        self.assertEqual(self.git('log', '-1', '--format=%an <%ae>', head).strip(),
                         'llmtool merge train <llmtool-merge-train@invalid>')
        self.assertTrue(merge_train.train_delta_verified(
            self.root, self.base, [sha for _, sha in members], head))

    def test_three_member_clean_train_builds_and_verifies(self):
        members = self.members(3)
        result = merge_train.build_candidate(self.root, self.base, members)
        self.assertEqual(result['merged'], ['pr-1', 'pr-2', 'pr-3'])
        self.assertEqual(result['codes'], [])
        self.assertTrue(merge_train.train_delta_verified(
            self.root, self.base, tuple(sha for _, sha in members), result['head']))

    # Exclusions

    def test_conflicting_member_is_excluded_and_others_merge(self):
        one = self.branch('one', {'base.txt': 'one\n'})
        clash = self.branch('clash', {'base.txt': 'clash\n'})
        two = self.branch('two', {'two.txt': 'two\n'})
        result = merge_train.build_candidate(
            self.root, self.base, [('pr-1', one), ('pr-9', clash), ('pr-2', two)])
        self.assertEqual(result['merged'], ['pr-1', 'pr-2'])
        self.assertEqual(result['excluded'],
                         [{'key': 'pr-9', 'code': 'TRAIN_MEMBER_CONFLICT'}])
        self.assertEqual(result['codes'], [])
        self.assertTrue(merge_train.train_delta_verified(
            self.root, self.base, [one, two], result['head']))
        self.assertFalse(merge_train.train_delta_verified(
            self.root, self.base, [one, clash, two], result['head']))

    def test_member_already_on_base_is_excluded(self):
        one, two = (sha for _, sha in self.members(2))
        result = merge_train.build_candidate(
            self.root, self.base, [('on-base', self.base), ('pr-1', one), ('pr-2', two)])
        self.assertEqual(result['merged'], ['pr-1', 'pr-2'])
        self.assertEqual(result['excluded'],
                         [{'key': 'on-base', 'code': 'TRAIN_MEMBER_ALREADY_MERGED'}])
        self.assertTrue(merge_train.train_delta_verified(
            self.root, self.base, [one, two], result['head']))

    def test_duplicate_head_and_ancestor_of_tip_are_already_merged(self):
        one = self.branch('one', {'one.txt': 'one\n'})
        child = self.branch('child', {'child.txt': 'child\n'}, start=one)
        two = self.branch('two', {'two.txt': 'two\n'})
        result = merge_train.build_candidate(self.root, self.base, [
            ('pr-child', child), ('pr-one', one), ('pr-2', two), ('pr-2-again', two)])
        self.assertEqual(result['merged'], ['pr-child', 'pr-2'])
        self.assertEqual(result['excluded'], [
            {'key': 'pr-one', 'code': 'TRAIN_MEMBER_ALREADY_MERGED'},
            {'key': 'pr-2-again', 'code': 'TRAIN_MEMBER_ALREADY_MERGED'}])
        self.assertTrue(merge_train.train_delta_verified(
            self.root, self.base, [child, two], result['head']))

    def test_unavailable_member_heads_are_excluded(self):
        one, two = (sha for _, sha in self.members(2))
        blob = self.git('hash-object', '-w', '--stdin', input='blob\n').strip()
        tree = self.rev('HEAD^{tree}')
        bad = [('missing', '0' * 40), ('short', one[:12]), ('upper', one.upper()),
               ('not-str', None), ('blob', blob), ('tree', tree), ('ref', 'main')]
        result = merge_train.build_candidate(
            self.root, self.base, [*bad, ('pr-1', one), ('pr-2', two)])
        self.assertEqual(result['merged'], ['pr-1', 'pr-2'])
        self.assertEqual(result['excluded'],
                         [{'key': key, 'code': 'TRAIN_MEMBER_UNAVAILABLE'} for key, _ in bad])
        self.assertIsNotNone(result['head'])

    def test_fewer_than_two_merged_members_has_no_head(self):
        one = self.branch('one', {'base.txt': 'one\n'})
        clash = self.branch('clash', {'base.txt': 'clash\n'})
        for members in ([('pr-1', one)], [('pr-1', one), ('pr-9', clash)], []):
            result = merge_train.build_candidate(self.root, self.base, members)
            self.assertIsNone(result['head'])
            self.assertEqual(result['codes'], ['TRAIN_TOO_FEW_MEMBERS'])

    def test_invalid_base_has_no_head(self):
        members = self.members(2)
        blob = self.git('hash-object', '-w', '--stdin', input='blob\n').strip()
        for base in ('f' * 40, 'bad', None, self.base.upper(), blob, 'main'):
            result = merge_train.build_candidate(self.root, base, members)
            self.assertIsNone(result['head'])
            self.assertEqual(result['codes'], ['TRAIN_BASE_UNAVAILABLE'])

    def test_invalid_members_input_has_no_head(self):
        one, two = (sha for _, sha in self.members(2))
        for members in (None, 'pr-1', [('pr-1', one, 'x')], [('bad\nkey', one), ('pr-2', two)],
                        [('pr-1', one), ('pr-1', two)], [(1, one), ('pr-2', two)]):
            result = merge_train.build_candidate(self.root, self.base, members)
            self.assertIsNone(result['head'])
            self.assertEqual(result['codes'], ['TRAIN_MEMBERS_INVALID'])

    def test_build_changes_no_worktree_index_head_or_ref(self):
        self.write('untracked.txt', 'left alone\n')
        self.write('base.txt', 'dirty\n')
        before = self.state()
        # Branch creation needs checkout, so make members with plumbing only.
        tree_one = self.tree_with(self.rev(f'{self.base}^{{tree}}'), 'one.txt', 'one\n')
        tree_two = self.tree_with(self.rev(f'{self.base}^{{tree}}'), 'two.txt', 'two\n')
        # pr-9 adds one.txt with other text, so it conflicts with pr-1 (add/add).
        tree_clash = self.tree_with(self.rev(f'{self.base}^{{tree}}'), 'one.txt', 'clash\n')
        one = self.commit_tree(tree_one, self.base)
        two = self.commit_tree(tree_two, self.base)
        clash = self.commit_tree(tree_clash, self.base)
        self.assertEqual(self.state(), before)
        result = merge_train.build_candidate(
            self.root, self.base, [('pr-1', one), ('pr-9', clash), ('pr-2', two)])
        self.assertEqual(result['merged'], ['pr-1', 'pr-2'])
        self.assertEqual(result['excluded'],
                         [{'key': 'pr-9', 'code': 'TRAIN_MEMBER_CONFLICT'}])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.git('cat-file', '-t', result['head']).strip(), 'commit')
        self.assertTrue(merge_train.train_delta_verified(
            self.root, self.base, [one, two], result['head']))
        self.assertEqual(self.state(), before)

    # Proof failures

    def built(self, count=2):
        members = self.members(count)
        result = merge_train.build_candidate(self.root, self.base, members)
        self.assertIsNotNone(result['head'])
        return [sha for _, sha in members], result['head']

    def test_extra_commit_on_top_fails(self):
        heads, head = self.built()
        extra = self.commit_tree(self.rev(f'{head}^{{tree}}'), head)
        self.assertFalse(merge_train.train_delta_verified(self.root, self.base, heads, extra))
        changed = self.commit_tree(
            self.tree_with(self.rev(f'{head}^{{tree}}'), 'extra.txt', 'x\n'), head)
        self.assertFalse(merge_train.train_delta_verified(self.root, self.base, heads, changed))

    def test_extra_commit_inside_chain_fails(self):
        one, two = (sha for _, sha in self.members(2))
        # base -> merge(one) -> extra -> merge(two)
        first = self.commit_tree(self.merge_tree(self.base, one), self.base, one)
        extra = self.commit_tree(self.rev(f'{first}^{{tree}}'), first)
        second = self.commit_tree(self.merge_tree(extra, two), extra, two)
        self.assertFalse(merge_train.train_delta_verified(
            self.root, self.base, [one, two], second))
        # base -> extra -> merge(one) -> merge(two)
        bottom = self.commit_tree(self.rev(f'{self.base}^{{tree}}'), self.base)
        first = self.commit_tree(self.merge_tree(bottom, one), bottom, one)
        second = self.commit_tree(self.merge_tree(first, two), first, two)
        self.assertFalse(merge_train.train_delta_verified(
            self.root, self.base, [one, two], second))
        # The same hand-made chain without the extra commit verifies.
        first = self.commit_tree(self.merge_tree(self.base, one), self.base, one)
        second = self.commit_tree(self.merge_tree(first, two), first, two)
        self.assertTrue(merge_train.train_delta_verified(
            self.root, self.base, [one, two], second))

    def test_hand_made_merge_with_extra_bytes_fails(self):
        one, two = (sha for _, sha in self.members(2))
        for path, text in (('extra.txt', 'extra\n'), ('m1.txt', 'member 1 changed\n')):
            tree = self.tree_with(self.merge_tree(self.base, one), path, text)
            first = self.commit_tree(tree, self.base, one)
            second = self.commit_tree(self.merge_tree(first, two), first, two)
            self.assertFalse(merge_train.train_delta_verified(
                self.root, self.base, [one, two], second))
            first = self.commit_tree(self.merge_tree(self.base, one), self.base, one)
            tree = self.tree_with(self.merge_tree(first, two), path, text)
            second = self.commit_tree(tree, first, two)
            self.assertFalse(merge_train.train_delta_verified(
                self.root, self.base, [one, two], second))

    def test_three_parent_and_redundant_merges_fail(self):
        one, two = (sha for _, sha in self.members(2))
        tree = self.tree_with(self.merge_tree(self.base, one), 'm2.txt', 'member 2\n')
        octopus = self.commit_tree(tree, self.base, one, two)
        self.assertFalse(merge_train.train_delta_verified(
            self.root, self.base, [one], octopus))
        first = self.commit_tree(self.merge_tree(self.base, one), self.base, one)
        redundant = self.commit_tree(self.rev(f'{first}^{{tree}}'), first, self.base)
        self.assertFalse(merge_train.train_delta_verified(
            self.root, self.base, [one, self.base], redundant))

    def test_wrong_order_fails(self):
        heads, head = self.built(3)
        for order in ([heads[1], heads[0], heads[2]], list(reversed(heads))):
            self.assertFalse(merge_train.train_delta_verified(
                self.root, self.base, order, head))

    def test_missing_member_fails(self):
        heads, head = self.built(3)
        for subset in (heads[:2], heads[1:], [heads[0], heads[2]], heads[:1]):
            self.assertFalse(merge_train.train_delta_verified(
                self.root, self.base, subset, head))

    def test_extra_member_fails(self):
        heads, head = self.built(2)
        other = self.branch('other', {'other.txt': 'other\n'})
        for extended in ([*heads, other], [other, *heads], [heads[0], other, heads[1]]):
            self.assertFalse(merge_train.train_delta_verified(
                self.root, self.base, extended, head))

    def test_duplicate_member_heads_fail(self):
        heads, head = self.built(2)
        self.assertFalse(merge_train.train_delta_verified(
            self.root, self.base, [heads[0], heads[0]], head))
        self.assertFalse(merge_train.train_delta_verified(
            self.root, self.base, [heads[0], heads[1], heads[1]], head))

    def test_wrong_base_fails(self):
        heads, head = self.built(2)
        self.write('later.txt', 'later\n')
        self.git('add', '--', 'later.txt')
        self.git('commit', '-q', '-m', 'later')
        later = self.rev('HEAD')
        before_base = self.commit_tree(self.rev(f'{self.base}^{{tree}}'))
        for base in (later, before_base, heads[0], head):
            self.assertFalse(merge_train.train_delta_verified(self.root, base, heads, head))
        self.assertFalse(merge_train.train_delta_verified(self.root, self.base, heads, self.base))

    def test_graft_file_fails_closed(self):
        members = self.members(2)
        heads = [sha for _, sha in members]
        head = merge_train.build_candidate(self.root, self.base, members)['head']
        self.assertTrue(merge_train.train_delta_verified(self.root, self.base, heads, head))
        info = os.path.join(self.root, '.git', 'info')
        os.makedirs(info, exist_ok=True)
        with open(os.path.join(info, 'grafts'), 'w', encoding='ascii') as stream:
            stream.write('')
        self.assertFalse(merge_train.train_delta_verified(self.root, self.base, heads, head))
        result = merge_train.build_candidate(self.root, self.base, members)
        self.assertIsNone(result['head'])
        self.assertEqual(result['codes'], ['TRAIN_GRAFTS_UNSUPPORTED'])

    def test_invalid_input_fails_closed(self):
        heads, head = self.built(2)
        cases = (
            (self.base, heads, 'bad'), (self.base, heads, head.upper()),
            (None, heads, head), ('f' * 40, heads, head), (self.base, heads, '0' * 40),
            (self.base, [], head), (self.base, None, head), (self.base, heads[0], head),
            (self.base, [heads[0], None], head), (self.base, [heads[0], heads[1][:12]], head),
        )
        for base, member_heads, train_head in cases:
            self.assertFalse(merge_train.train_delta_verified(
                self.root, base, member_heads, train_head))
        self.assertFalse(merge_train.train_delta_verified(
            os.path.join(self.tmp.name, 'missing'), self.base, heads, head))

    def test_git_failure_fails_closed(self):
        heads, head = self.built(2)

        def broken(*_args, **_kwargs):
            task_completion._stop('GIT_INSPECTION_FAILED')

        with patch.object(task_completion, '_merge_tree_conflict_paths_raw', broken):
            self.assertFalse(merge_train.train_delta_verified(
                self.root, self.base, heads, head))
        with patch.object(task_completion, '_remerge_diff_paths_raw', broken):
            self.assertFalse(merge_train.train_delta_verified(
                self.root, self.base, heads, head))
        with patch.object(task_completion, '_is_ancestor', side_effect=OSError):
            self.assertFalse(merge_train.train_delta_verified(
                self.root, self.base, heads, head))
        with patch.object(task_completion, '_git', side_effect=OSError):
            self.assertFalse(merge_train.train_delta_verified(
                self.root, self.base, heads, head))
        members = [(f'pr-{number}', sha) for number, sha in enumerate(heads, 1)]
        with patch.object(task_completion, '_merge_tree_conflict_paths_raw', broken):
            result = merge_train.build_candidate(self.root, self.base, members)
        self.assertIsNone(result['head'])
        self.assertEqual(result['codes'], ['TRAIN_BUILD_FAILED'])


if __name__ == '__main__':
    unittest.main()
