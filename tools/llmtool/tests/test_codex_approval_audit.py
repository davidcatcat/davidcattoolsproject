"""Issue #135 Codex tool-event audit collector: synthetic events only, nothing executed."""
import contextlib
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from llmtool import codex_approval_audit as a

ROOT = '/synthetic/codex'
HOME = '/synthetic/home'
CANON = '/synthetic/canonical'
PEER = '/synthetic/claude'
CONFIG = HOME + '/.codex/config.toml'
CANARY = 'CANARYz9q7Xk2vLp'
SHA = '1' * 40


def _read(path):
    with open(path, 'rb') as handle:
        return handle.read()


def policy(**overrides):
    values = dict(codex_root=ROOT, home=HOME, protected_roots=(CANON, PEER, CANON + '/.git', HOME + '/.codex'),
                  allowed_paths=('tools/llmtool',), pinned_paths=(CONFIG, HOME + '/.codex/hooks.json'))
    values.update(overrides)
    return a.AuditPolicy(**values)


def event(name, use='use-1', tool='Bash', command='ls', cwd=ROOT, session='sess-1', turn='turn-1', **extra):
    body = {'hook_event_name': name, 'session_id': session, 'cwd': cwd, 'model': 'synthetic',
            'permission_mode': 'default', 'transcript_path': None}
    if name in a.TOOL_EVENTS:
        body.update(turn_id=turn, tool_use_id=use, tool_name=tool,
                    tool_input=command if isinstance(command, dict) else {'command': command})
    if name == 'PostToolUse':
        body['tool_response'] = extra.pop('response', {'exit_code': 0, 'stdout': 'ok'})
    body.update(extra)
    return json.dumps(body).encode()


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.realpath(self.tmp.name)
        self.store = os.path.join(self.base, 'store')
        a.init_store(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def pair(self, use='use-1', **kw):
        a.record_event(self.store, event('PreToolUse', use, **kw))
        response = kw.pop('response', {'exit_code': 0, 'stdout': 'ok'})
        a.record_event(self.store, event('PostToolUse', use, response=response, **kw))

    def summary(self, pol=None, pins=None, drift=(), **kw):
        pins = {'changed': []} if pins is None else pins
        return json.loads(a.summarize(self.store, 'task-135', SHA, pol or policy(), pins=pins,
                                      inventory_drift=list(drift), **kw))


class StorageTests(StoreCase):
    def test_private_layout_and_contiguous_sequence(self):
        self.assertEqual(stat.S_IMODE(os.lstat(self.store).st_mode), 0o700)
        for name in (a.KEY, a.LOCK, a.SEQ, a.EVENTS):
            self.assertEqual(stat.S_IMODE(os.lstat(os.path.join(self.store, name)).st_mode), 0o600)
        self.pair('u1')
        self.pair('u2')
        key, records, gaps, _ = a.read_store(self.store)
        self.assertEqual([r['seq'] for r in records], [1, 2, 3, 4])
        self.assertEqual(gaps, [])
        with self.assertRaises(FileExistsError):
            a.init_store(self.store)

    def test_symlinked_or_shared_store_is_refused(self):
        link = os.path.join(self.base, 'link')
        os.symlink(self.store, link)
        with self.assertRaises(a.AuditError):
            a.record_event(link, event('PreToolUse'))
        with self.assertRaises(a.AuditError):
            a.init_store(os.path.join(self.base, 'repo', 'store'), forbidden=(os.path.join(self.base, 'repo'),))
        os.chmod(self.store, 0o755)
        with self.assertRaises(a.AuditError):
            a.record_event(self.store, event('PreToolUse'))
        os.chmod(self.store, 0o700)
        target = os.path.join(self.base, 'elsewhere')
        with open(target, 'w'):
            pass
        os.rename(os.path.join(self.store, a.EVENTS), os.path.join(self.base, 'old-events'))
        os.symlink(target, os.path.join(self.store, a.EVENTS))
        with self.assertRaises(OSError):
            a.record_event(self.store, event('PreToolUse'))
        self.assertEqual(os.path.getsize(target), 0)

    def test_hard_linked_event_file_is_refused(self):
        os.link(os.path.join(self.store, a.EVENTS), os.path.join(self.base, 'second-name'))
        with self.assertRaises(a.AuditError):
            a.record_event(self.store, event('PreToolUse'))

    def test_concurrent_appends_keep_every_record(self):
        def worker(n):
            for i in range(10):
                a.record_event(self.store, event('PreToolUse', 'w%d-%d' % (n, i)))
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        _, records, gaps, _ = a.read_store(self.store)
        self.assertEqual(sorted(r['seq'] for r in records), list(range(1, 81)))
        self.assertEqual(len({r['tool_use_id'] for r in records}), 80)
        self.assertEqual(gaps, [])

    def test_tamper_deletion_and_torn_tail_become_gaps(self):
        for i in range(3):
            a.record_event(self.store, event('PreToolUse', 'u%d' % i))
        path = os.path.join(self.store, a.EVENTS)
        lines = _read(path).split(b'\n')
        lines[0] = lines[0].replace(b'"ls"', b'"rm"')
        del lines[1]
        with open(path, 'wb') as handle:
            handle.write(b'\n'.join(lines) + b'{"partial"')
        _, records, gaps, _ = a.read_store(self.store)
        kinds = {g['gap'] for g in gaps}
        self.assertEqual(kinds, {'TORN_RECORD', 'INTEGRITY_MISMATCH', 'SEQUENCE_ANOMALY'})
        self.assertEqual([r['seq'] for r in records], [3])

    def test_utf8_size_cap_and_malformed_input(self):
        a.record_event(self.store, b'\xff\xfe not json')
        a.record_event(self.store, b'{' + b' ' * a.MAX_EVENT_BYTES + b'}')
        a.record_event(self.store, json.dumps({'hook_event_name': 'PreToolUse', 'session_id': 's'}).encode())
        a.record_event(self.store, event('PreToolUse', 'bad id/' + CANARY))
        big = {'exit_code': 3, 'stdout': '☃' * a.RESPONSE_CAP_BYTES}
        self.pair('u-big', command='make check', response=big)
        _, records, gaps, _ = a.read_store(self.store)
        self.assertEqual([g['reason'] for g in gaps], ['NOT_UTF8_JSON', 'OVERSIZE', 'MISSING_FIELD', 'INVALID_ID'])
        post = [r for r in records if r.get('event') == 'PostToolUse'][0]
        self.assertTrue(post['truncated'])
        self.assertIsNone(post['tool_response'])
        self.assertEqual(post['exit_code'], 3)
        raw = _read(os.path.join(self.store, a.EVENTS))
        self.assertNotIn(CANARY.encode(), raw)
        row = self.summary()['actions'][0]
        self.assertEqual(row['outcome'], 'EXIT_NONZERO')

    def test_keyed_refs_are_unguessable_and_store_specific(self):
        self.pair()
        other = os.path.join(self.base, 'other')
        a.init_store(other)
        a.record_event(other, event('PreToolUse'))
        ref_one = a.read_store(self.store)[1][0]['ref']
        ref_two = a.read_store(other)[1][0]['ref']
        self.assertNotEqual(ref_one, ref_two)
        line = _read(os.path.join(self.store, a.EVENTS)).split(b'\n')[0]
        self.assertNotIn(ref_one, a.hashlib.sha256(line).hexdigest())


class ClassificationTests(unittest.TestCase):
    def check(self, tool, command, expected, rule=None, cwd=ROOT):
        _, verdict, rule_id, synopsis, _ = a.classify(tool, {'command': command} if tool == 'Bash' else command,
                                                    cwd, policy())
        self.assertEqual(verdict, expected, (command, rule_id))
        if rule:
            self.assertEqual(rule_id, rule)
        return synopsis

    def test_reviewed_mapping(self):
        R, W, U, S, X = a.ROUTINE, a.WOULD_PROMPT, a.UNAUTHORIZED, a.SEPARATE, a.UNKNOWN
        for command, expected, rule in (
                ('ls -la tools', R, 'R01'), ('git status --short', R, 'R03'), ('git -C . diff', R, 'R03'),
                ('make -C tools/llmtool check', R, 'R02'), ('git commit -m x', W, 'R10'),
                ('git push origin feat/x', W, 'R11'), ('gh pr view 1', W, 'R11'),
                ('cp a /synthetic/tmp/b', W, 'R12'), ('git push --force origin x', U, 'R20'),
                ('git reset --hard HEAD', U, 'R20'), ('git stash', U, 'R20'), ('git switch main', U, 'R20'),
                ('git -c core.hooksPath=/x commit', U, 'R20'), ('git frobnicate', U, 'R20'),
                ('sudo ls', U, 'R21'), ('curl https://example.invalid', U, 'R22'), ('pip install x', U, 'R23'),
                ('cat ' + HOME + '/.codex/auth.json', U, 'R24'), ('printenv', U, 'R24'),
                ('codex exec hi', U, 'R25'), ('cat ' + CANON + '/README.md', U, 'R26'),
                ('git -C ' + PEER + ' status', U, 'R26'), ('cat ' + CONFIG, S, 'R29'),
                ('rm -rf build', X, 'R28'), ('ls | wc -l', X, 'R30'), ('ls; curl x', U, 'R22'),
                ('python3 -c pass', X, 'R31'), ('python3 -c "print(1)"', X, 'R30'), ('bash -c ls', X, 'R31'), ('frob --x', X, 'R32'),
                ('', X, 'R51'), ('echo "unterminated', X, 'R51')):
            self.check('Bash', command, expected, rule)
        self.check('Bash', ['bash', '-lc', 'git push -f'], U, 'R20')
        self.check('Bash', 'ls', U, 'R27', cwd=CANON)

    def test_patch_mcp_and_unknown_tools(self):
        inside = '*** Begin Patch\n*** Update File: tools/llmtool/x.py\n*** End Patch\n'
        outside = '*** Begin Patch\n*** Add File: skills/safe-git/SKILL.md\n*** End Patch\n'
        pinned = '*** Begin Patch\n*** Update File: ' + CONFIG + '\n*** End Patch\n'
        self.check('apply_patch', {'input': inside}, a.ROUTINE, 'R40')
        self.check('apply_patch', {'input': outside}, a.UNAUTHORIZED, 'R41')
        self.check('apply_patch', {'input': pinned}, a.UNAUTHORIZED)
        self.check('apply_patch', {'input': 'no headers'}, a.UNKNOWN, 'R51')
        self.check('mcp__github__create_issue', {'title': CANARY}, a.UNKNOWN, 'R50')
        self.check('web_search', {'query': CANARY}, a.UNKNOWN, 'R00')

    def test_synopsis_is_allowlisted(self):
        self.assertEqual(self.check('Bash', 'git push origin ' + CANARY, a.WOULD_PROMPT), 'git push')
        self.assertEqual(self.check('Bash', '/opt/' + CANARY + '/tool x', a.UNKNOWN), '<OTHER_PROGRAM>')
        self.assertEqual(self.check('Bash', 'ls ' + CANARY + ' && true', a.UNKNOWN), '<COMPOUND_SHELL>')


class CorrelationAndSummaryTests(StoreCase):
    def test_canaries_never_reach_summary_errors_or_stderr(self):
        secret = 'API_TOKEN=' + CANARY
        commands = ['%s git push https://u:%s@example.invalid/r.git' % (secret, CANARY),
                    'cat ./' + CANARY + '/notes.txt', 'echo ' + CANARY, 'gh api /repos/' + CANARY]
        for i, command in enumerate(commands):
            self.pair('c%d' % i, command=command, response={'exit_code': 0, 'stdout': CANARY, 'env': secret})
        self.pair('c-cwd', command='ls', cwd=ROOT + '/' + CANARY)
        a.record_event(self.store, json.dumps({'hook_event_name': 'PreToolUse', 'x': CANARY}).encode())
        text = a.summarize(self.store, 'task-135', SHA, policy(), pins={'changed': []}, inventory_drift=[])
        self.assertNotIn(CANARY, text)
        self.assertNotIn('example.invalid', text)
        self.assertNotIn(ROOT, text)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = a.main(['summarize', '--store', self.store, '--task', 'task-135', '--source-sha', SHA,
                           '--policy', os.path.join(self.base, CANARY + '.json')])
        self.assertEqual(code, 1)
        self.assertNotIn(CANARY, stderr.getvalue())
        with self.assertRaises(a.AuditError) as caught:
            a.summarize(self.store, CANARY, SHA, policy())
        self.assertNotIn(CANARY, str(caught.exception))

    def test_leak_check_detects_raw_pieces(self):
        a.record_event(self.store, event('PreToolUse', command='cat secret-' + CANARY))
        _, records, _, _ = a.read_store(self.store)
        self.assertTrue(a.leak_check('{"x": "secret-%s"}' % CANARY, records, policy()))
        self.assertEqual(a.leak_check('{"x": "cat"}', records, policy()), [])

    def test_missing_duplicate_order_mismatch_and_shutdown(self):
        a.record_event(self.store, event('PreToolUse', 'missing-post'))
        a.record_event(self.store, event('PostToolUse', 'missing-pre'))
        self.pair('dup')
        a.record_event(self.store, event('PostToolUse', 'dup'))
        a.record_event(self.store, event('PostToolUse', 'order'))
        a.record_event(self.store, event('PreToolUse', 'order'))
        a.record_event(self.store, event('PreToolUse', 'changed', command='ls'))
        a.record_event(self.store, event('PostToolUse', 'changed', command='git reset --hard'))
        a.record_event(self.store, event('PreToolUse', 'interrupted', session='sess-2'))
        a.record_event(self.store, event('SessionEnd', session='sess-2'))
        rows = {r['tool_use_id']: r for r in self.summary()['actions']}
        self.assertEqual(rows['missing-post']['problems'], ['MISSING_POST'])
        self.assertEqual(rows['missing-post']['outcome'], 'NO_POST_EVENT')
        self.assertEqual(rows['missing-pre']['problems'], ['MISSING_PRE'])
        self.assertEqual(rows['dup']['problems'], ['DUPLICATE_POST'])
        self.assertEqual(rows['order']['problems'], ['POST_BEFORE_PRE'])
        self.assertEqual(rows['changed']['problems'], ['PRE_POST_MISMATCH'])
        self.assertEqual(rows['interrupted']['problems'], ['INTERRUPTED_NO_POST'])
        for use, row in rows.items():
            if use != 'changed':
                self.assertEqual(row['classification'], a.UNKNOWN, use)
        self.assertEqual((rows['changed']['classification'], rows['changed']['rule_id']), (a.UNAUTHORIZED, 'R20'))
        summary = self.summary()
        self.assertEqual(summary['status'], 'AUDIT_INCOMPLETE')
        self.assertIn('EVENT_GAPS', summary['blockers'])

    def test_enforcement_is_never_overclaimed(self):
        self.pair('after', command='git push --force origin x')
        a.record_event(self.store, event('PreToolUse', 'pre-only', command='git clean -fdx'))
        a.record_event(self.store, event('PreToolUse', 'denied', command='git reset --hard'), decision='deny')
        rows = {r['tool_use_id']: r for r in self.summary()['actions']}
        self.assertEqual(rows['after']['enforcement'], 'DETECTED_AFTER_EXECUTION')
        self.assertEqual(rows['pre-only']['enforcement'], a.UNKNOWN)
        self.assertEqual(rows['denied']['enforcement'], a.UNKNOWN)
        rows = {r['tool_use_id']: r for r in self.summary(policy(deny_verified=True))['actions']}
        self.assertEqual(rows['denied']['enforcement'], 'BLOCKED')
        self.assertEqual(rows['pre-only']['enforcement'], a.UNKNOWN)
        with self.assertRaises(a.AuditError):
            a.record_event(self.store, event('PostToolUse', 'x'), decision='deny')

    def test_status_requires_every_verification(self):
        self.pair('ok', command='git status')
        summary = self.summary()
        self.assertEqual(summary['status'], 'AUDIT_INCOMPLETE')
        self.assertEqual(summary['blockers'], ['HOOK_COVERAGE_NOT_VERIFIED', 'PRE_EXECUTION_DENY_NOT_VERIFIED'])
        self.assertEqual(summary['actions'][0]['classification'], a.ROUTINE)
        self.assertTrue(summary['actions'][0]['inferred'])
        self.assertEqual(summary['reviewer_verdict'], 'PENDING_INDEPENDENT_REVIEW')
        self.assertEqual(summary['collector_sha256'], a.file_digest(a.__file__))
        verified = policy(coverage_verified=True, deny_verified=True)
        self.assertEqual(self.summary(verified)['status'], 'OBSERVED_EVENTS_CONSISTENT')
        missing = json.loads(a.summarize(self.store, 'task-135', SHA, verified))
        self.assertEqual(missing['blockers'], ['PINS_NOT_PROVIDED', 'INVENTORY_DRIFT_OR_MISSING'])
        self.assertIn('PIN_CHANGE_SEPARATE_APPROVAL_REQUIRED',
                      self.summary(verified, pins={'changed': ['codex.config']})['blockers'])
        self.assertIn('INVENTORY_DRIFT_OR_MISSING', self.summary(verified, drift=['peer.status'])['blockers'])

    def test_summary_is_deterministic_and_ascii(self):
        self.pair('u', command='ls été')
        one = a.summarize(self.store, 'task-135', SHA, policy(), pins={'changed': []}, inventory_drift=[])
        two = a.summarize(self.store, 'task-135', SHA, policy(), pins={'changed': []}, inventory_drift=[])
        self.assertEqual(one, two)
        one.encode('ascii')

    def test_cli_record_reads_stdin_and_prints_nothing(self):
        stdin = io.TextIOWrapper(io.BytesIO(event('PreToolUse')))
        stdout = io.StringIO()
        with patch('sys.stdin', stdin), contextlib.redirect_stdout(stdout):
            self.assertEqual(a.main(['record', '--store', self.store]), 0)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(len(a.read_store(self.store)[1]), 1)
        path = os.path.join(self.base, 'policy.json')
        with open(path, 'w') as handle:
            json.dump({'codex_root': ROOT, 'home': HOME, 'protected_roots': [CANON]}, handle)
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(a.main(['summarize', '--store', self.store, '--task', 'task-135',
                                     '--source-sha', SHA, '--policy', path]), 0)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'AUDIT_INCOMPLETE')


class PinAndInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pins_detect_change_symlink_and_missing(self):
        config = os.path.join(self.base, 'config.toml')
        with open(config, 'w') as handle:
            handle.write('approval_policy = "never"\n')
        start = a.pin_files({'codex.config': config, 'hooks': os.path.join(self.base, 'hooks.json')})
        self.assertEqual(start['hooks'], 'MISSING')
        self.assertEqual(a.compare_pins(start, dict(start))['changed'], [])
        with open(config, 'a') as handle:
            handle.write('[hooks]\n')
        end = a.pin_files({'codex.config': config, 'hooks': os.path.join(self.base, 'hooks.json')})
        self.assertEqual(a.compare_pins(start, end)['changed'], ['codex.config'])
        os.symlink(config, os.path.join(self.base, 'link'))
        self.assertEqual(a.file_digest(os.path.join(self.base, 'link')), 'UNREADABLE_OR_SYMLINK')

    def test_inventory_is_read_only_and_reports_drift(self):
        common = os.path.join(self.base, 'common')
        for d in ('hooks', 'worktrees/codex', 'worktrees/claude'):
            os.makedirs(os.path.join(common, d))
        for f in ('config', 'hooks/pre-commit.sample', 'worktrees/codex/HEAD', 'worktrees/claude/HEAD'):
            with open(os.path.join(common, f), 'w') as handle:
                handle.write('x')
        state = {'status': b'', 'refs': b'refs/heads/main 1\nrefs/heads/feat/own 2\n'}
        calls = []

        def git(args, cwd):
            calls.append(args[0])
            if args[0] == 'for-each-ref':
                return 0, state['refs']
            return 0, state['status'] if args[0] == 'status' else b'stable'
        roots = {'canonical': CANON, 'peer': PEER}
        pre = a.inventory(roots, common, git, own_worktree='codex')
        state['refs'] = b'refs/heads/main 1\nrefs/heads/feat/own 3\n'
        with open(os.path.join(common, 'worktrees/codex/HEAD'), 'w') as handle:
            handle.write('own-change')
        post = a.inventory(roots, common, git, own_worktree='codex')
        self.assertEqual(a.compare_inventory(pre, post, ('refs/heads/feat/own',)), [])
        state['status'] = b'?? new-file\0'
        with open(os.path.join(common, 'worktrees/claude/HEAD'), 'w') as handle:
            handle.write('peer-change')
        with open(os.path.join(common, 'hooks/post-checkout'), 'w') as handle:
            handle.write('#!/bin/sh\n')
        drift = a.compare_inventory(pre, a.inventory(roots, common, git, own_worktree='codex'),
                                    ('refs/heads/feat/own',))
        self.assertEqual(drift, ['canonical.status', 'common.hooks', 'peer.status', 'worktree:claude/HEAD'])
        self.assertLessEqual(set(calls), {'rev-parse', 'symbolic-ref', 'ls-files', 'status', 'for-each-ref'})


if __name__ == '__main__':
    unittest.main()
