"""Issue #135 Codex tool-event audit collector: synthetic events only, nothing executed."""
import contextlib
import fcntl
import io
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from llmtool import codex_approval_audit as a

ROOT = '/synthetic/codex'
HOME = '/synthetic/home'
CANON = '/synthetic/canonical'
PEER = '/synthetic/claude'
CONFIG = HOME + '/.codex/config.toml'
CANARY = 'CANARYz9q7Xk2vLp'
UTF8_CANARY = 'CANÄRY-ü-z9q7Xk2vLp-ß'
SHA = '1' * 40
TASK = 'issue-135'


def _read(path):
    with open(path, 'rb') as handle:
        return handle.read()


def policy(**overrides):
    values = dict(codex_root=ROOT, home=HOME, protected_roots=(CANON, PEER, CANON + '/.git', HOME + '/.codex'),
                  allowed_paths=('tools/llmtool',), pinned_paths=(CONFIG, HOME + '/.codex/hooks.json'))
    values.update(overrides)
    return a.AuditPolicy(**values)


def good_pins():
    digests = {label: 'a' * 64 for label in a.REQUIRED_PINS}
    return {'start': digests, 'end': dict(digests)}


def good_inventory():
    snap = {'canonical.head': 'b' * 64, 'common.config': 'c' * 64, 'common.hooks': 'd' * 64,
            'shared.refs': 'OK', 'ref:refs/heads/main': '2' * 40}
    return {'pre': snap, 'post': dict(snap), 'allowed_refs': []}


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


def strings(value):
    out = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            out += strings(k) + strings(v)
    elif isinstance(value, list):
        for v in value:
            out += strings(v)
    return out


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

    def end(self, session='sess-1'):
        a.record_event(self.store, event('SessionEnd', session=session))

    def summary(self, pol=None, pins='good', inventory='good', expected=('sess-1',), **kw):
        pins = good_pins() if pins == 'good' else pins
        inventory = good_inventory() if inventory == 'good' else inventory
        return json.loads(a.summarize(self.store, TASK, SHA, pol or policy(), pins=pins, inventory=inventory,
                                      expected_sessions=expected, **kw))

    def rows(self, summary):
        key = a.read_store(self.store)[0]
        by_ref = {r['use_ref']: r for r in summary['actions']}
        uses = {r['tool_use_id'] for r in a.read_store(self.store)[1] if 'tool_use_id' in r}
        return {use: by_ref[a.public_ref(key, 'tool_use', use)] for use in uses
                if a.public_ref(key, 'tool_use', use) in by_ref}


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
            self.assertEqual(rule_id, rule, command)
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
                ('python3 -c pass', X, 'R31'), ('python3 -c "print(1)"', X, 'R30'), ('bash -c ls', X, 'R31'),
                ('frob --x', X, 'R32'), ('', X, 'R51'), ('echo "unterminated', X, 'R51')):
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

    def test_f1_git_overrides_and_implicit_writes_are_never_routine(self):
        """Review F1: pure classification only; none of these commands is executed."""
        R, U, X = a.ROUTINE, a.UNAUTHORIZED, a.UNKNOWN
        for command, expected, rule in (
                ('git -c core.fsmonitor=/synthetic/evil status', U, 'R20'),
                ('GIT_DIR=/synthetic/common git status', U, 'R20'),
                ('git --git-dir=/synthetic/tmp/other status', U, 'R20'),
                ('git --git-dir /synthetic/tmp/other status', U, 'R20'),
                ('git --work-tree=/synthetic/tmp log', U, 'R20'),
                ('git --exec-path=/synthetic/bin status', U, 'R20'),
                ('git --config-env=core.pager=X log', U, 'R20'),
                ('git diff --output=/synthetic/tmp/out', X, 'R34'),
                ('git log --output=out.txt', X, 'R34'),
                ('git diff --ext-diff', X, 'R34'),
                ('git show --textconv HEAD', X, 'R34'),
                ('git grep -Oless needle', X, 'R34'),
                ('git cat-file --filters HEAD:x', X, 'R34'),
                ('git -p log', X, 'R33'),
                ('git --paginate status', X, 'R33'),
                ('PATH=/synthetic/bin ls', X, 'R33'),
                ('PAGER=/synthetic/evil git log', X, 'R33'),
                ('API_TOKEN=' + CANARY + ' ls', U, 'R24'),
                ('LC_ALL=C git status', R, 'R03'),
                ('git --no-pager log --oneline', R, 'R03'),
                ('git diff --no-ext-diff', R, 'R03'),
                ('ls', R, 'R01')):
            self.check('Bash', command, expected, rule)
        self.check('Bash', ['env', 'GIT_DIR=/synthetic/common', 'git', 'status'], U)
        self.check('Bash', 'GIT_DIR=' + CANON + '/.git git status', U, 'R20')


class CorrelationAndSummaryTests(StoreCase):
    def test_canaries_never_reach_summary_errors_or_stderr(self):
        secret = 'API_TOKEN=' + CANARY
        commands = ['%s git push https://u:%s@example.invalid/r.git' % (secret, CANARY),
                    'cat ./' + CANARY + '/notes.txt', 'echo ' + CANARY, 'gh api /repos/' + CANARY]
        for i, command in enumerate(commands):
            self.pair('c%d' % i, command=command, response={'exit_code': 0, 'stdout': CANARY, 'env': secret})
        self.pair('c-cwd', command='ls', cwd=ROOT + '/' + CANARY)
        a.record_event(self.store, json.dumps({'hook_event_name': 'PreToolUse', 'x': CANARY}).encode())
        text = a.summarize(self.store, TASK, SHA, policy(), pins=good_pins(), inventory=good_inventory(),
                           expected_sessions=('sess-1',))
        self.assertNotIn(CANARY, text)
        self.assertNotIn('example.invalid', text)
        self.assertNotIn(ROOT, text)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = a.main(['summarize', '--store', self.store, '--task', TASK, '--source-sha', SHA,
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
        summary = self.summary(expected=('sess-1', 'sess-2'))
        rows = self.rows(summary)
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
        self.assertEqual(summary['status'], 'AUDIT_INCOMPLETE')
        self.assertIn('EVENT_GAPS', summary['blockers'])
        self.assertIn('SESSION_END_NOT_OBSERVED', summary['blockers'])

    def test_enforcement_is_never_overclaimed(self):
        self.pair('after', command='git push --force origin x')
        a.record_event(self.store, event('PreToolUse', 'pre-only', command='git clean -fdx'))
        a.record_event(self.store, event('PreToolUse', 'denied', command='git reset --hard'), decision='deny')
        rows = self.rows(self.summary())
        self.assertEqual(rows['after']['enforcement'], 'DETECTED_AFTER_EXECUTION')
        self.assertEqual(rows['pre-only']['enforcement'], a.UNKNOWN)
        self.assertEqual(rows['denied']['enforcement'], a.UNKNOWN)
        rows = self.rows(self.summary(policy(deny_verified=True)))
        self.assertEqual(rows['denied']['enforcement'], 'BLOCKED')
        self.assertEqual(rows['pre-only']['enforcement'], a.UNKNOWN)
        with self.assertRaises(a.AuditError):
            a.record_event(self.store, event('PostToolUse', 'x'), decision='deny')

    def test_status_requires_every_verification(self):
        self.pair('ok', command='git status')
        self.end()
        summary = self.summary()
        self.assertEqual(summary['status'], 'AUDIT_INCOMPLETE')
        self.assertEqual(summary['blockers'], ['HOOK_COVERAGE_NOT_VERIFIED', 'PRE_EXECUTION_DENY_NOT_VERIFIED'])
        self.assertEqual(summary['actions'][0]['classification'], a.ROUTINE)
        self.assertTrue(summary['actions'][0]['inferred'])
        self.assertEqual(summary['reviewer_verdict'], 'PENDING_INDEPENDENT_REVIEW')
        self.assertEqual(summary['collector_sha256'], a.file_digest(a.__file__))
        self.assertIn('A1 content of already-dirty or ignored files is not inventoried', summary['not_verified'])
        verified = policy(coverage_verified=True, deny_verified=True)
        self.assertEqual(self.summary(verified)['status'], 'OBSERVED_EVENTS_CONSISTENT')
        missing = json.loads(a.summarize(self.store, TASK, SHA, verified))
        self.assertEqual(missing['blockers'], ['PINS_NOT_PROVIDED', 'INVENTORY_NOT_PROVIDED',
                                               'EXPECTED_SESSIONS_NOT_PROVIDED'])
        changed = good_pins()
        changed['end']['codex.config'] = 'e' * 64
        self.assertIn('PIN_CHANGE_SEPARATE_APPROVAL_REQUIRED', self.summary(verified, pins=changed)['blockers'])
        drift = good_inventory()
        drift['post'] = dict(drift['post'], **{'canonical.head': 'f' * 64})
        self.assertEqual(self.summary(verified, inventory=drift)['inventory_drift'], ['canonical.head'])

    def test_summary_is_deterministic_and_ascii(self):
        self.pair('u', command='ls été')
        one = a.summarize(self.store, TASK, SHA, policy(), pins=good_pins(), inventory=good_inventory())
        two = a.summarize(self.store, TASK, SHA, policy(), pins=good_pins(), inventory=good_inventory())
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
            self.assertEqual(a.main(['summarize', '--store', self.store, '--task', TASK,
                                     '--source-sha', SHA, '--policy', path]), 0)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'AUDIT_INCOMPLETE')


class ReviewRegressionTests(StoreCase):
    """Independent review F2-F5 reproducers (synthetic, private temporary store only)."""

    def verified(self):
        return policy(coverage_verified=True, deny_verified=True)

    def test_f2_failed_or_missing_evidence_is_never_consistent(self):
        self.pair('ok', command='git status')
        self.end()
        self.assertEqual(self.summary(self.verified())['status'], 'OBSERVED_EVENTS_CONSISTENT')
        missing = good_pins()
        missing['start']['codex.hooks'] = missing['end']['codex.hooks'] = 'MISSING'
        self.assertEqual(a.compare_pins(missing['start'], missing['end'])['changed'], [])
        self.assertEqual(a.compare_pins(missing['start'], missing['end'])['unverified'], ['codex.hooks'])
        summary = self.summary(self.verified(), pins=missing)
        self.assertEqual((summary['status'], summary['blockers']), ('AUDIT_INCOMPLETE', ['PINS_NOT_VERIFIED']))
        partial = good_pins()
        del partial['start']['codex.binary'], partial['end']['codex.binary']
        self.assertIn('PINS_INCOMPLETE', self.summary(self.verified(), pins=partial)['blockers'])
        failed = good_inventory()
        for side in ('pre', 'post'):
            failed[side] = dict(failed[side], **{'canonical.head': 'GIT_EXIT_128'})
        summary = self.summary(self.verified(), inventory=failed)
        self.assertEqual(summary['blockers'], ['INVENTORY_NOT_VERIFIED'])
        self.assertEqual(summary['inventory_unverified'], ['canonical.head'])
        thin = {'pre': {'common.config': 'c' * 64}, 'post': {'common.config': 'c' * 64}}
        self.assertIn('INVENTORY_NOT_VERIFIED', self.summary(self.verified(), inventory=thin)['blockers'])
        for bad in ({'changed': []}, {'start': {}, 'end': {'codex.config': 1}}, []):
            with self.assertRaises(a.AuditError):
                self.summary(self.verified(), pins=bad)
        for bad in ([], {'pre': {}, 'post': [], 'allowed_refs': []}, {'pre': {}, 'post': {}, 'allowed_refs': 'x'}):
            with self.assertRaises(a.AuditError):
                self.summary(self.verified(), inventory=bad)
        for flags in ({'coverage_verified': 'false'}, {'deny_verified': 1}):
            with self.assertRaises(a.AuditError):
                self.summary(policy(**flags))
        path = os.path.join(self.base, 'policy.json')
        with open(path, 'w') as handle:
            json.dump({'codex_root': ROOT, 'home': HOME, 'protected_roots': [CANON],
                       'coverage_verified': 'false', 'deny_verified': 'false'}, handle)
        with self.assertRaises(a.AuditError):
            a._policy(path)

    def test_f2_empty_or_unexpected_sessions_are_never_consistent(self):
        empty = self.summary(self.verified())
        self.assertEqual(empty['status'], 'AUDIT_INCOMPLETE')
        self.assertEqual(empty['blockers'], ['EXPECTED_SESSION_NOT_OBSERVED', 'SESSION_END_NOT_OBSERVED',
                                             'NO_TOOL_EVENTS_OBSERVED'])
        self.pair('ok', command='git status')
        self.assertIn('SESSION_END_NOT_OBSERVED', self.summary(self.verified())['blockers'])
        self.end()
        self.pair('other', command='ls', session='sess-9')
        self.assertIn('UNEXPECTED_SESSION', self.summary(self.verified())['blockers'])
        self.assertIn('EXPECTED_SESSIONS_NOT_PROVIDED', self.summary(self.verified(), expected=None)['blockers'])
        for bad in ((), 'sess-1', ('bad id/x',), [1]):
            with self.assertRaises(a.AuditError):
                self.summary(self.verified(), expected=bad)

    def test_f3_suffix_and_whole_log_deletion_are_gaps(self):
        self.pair('u1', command='git status')
        self.pair('u2', command='git status')
        self.end()
        path = os.path.join(self.store, a.EVENTS)
        lines = _read(path).split(b'\n')
        with open(path, 'wb') as handle:
            handle.write(b'\n'.join(lines[:2]) + b'\n')
        _, records, gaps, _ = a.read_store(self.store)
        self.assertEqual(len(records), 2)
        self.assertEqual(gaps, [{'gap': 'TAIL_TRUNCATED'}])
        summary = self.summary(self.verified())
        self.assertEqual(summary['status'], 'AUDIT_INCOMPLETE')
        self.assertIn('EVENT_GAPS', summary['blockers'])
        with open(path, 'wb'):
            pass
        self.assertEqual(a.read_store(self.store)[2], [{'gap': 'TAIL_TRUNCATED'}])

    def test_f3_missing_corrupt_or_behind_sequence_state(self):
        self.pair('u1')
        seq = os.path.join(self.store, a.SEQ)
        good = _read(seq)
        with open(seq, 'wb') as handle:
            handle.write(b'2')
        self.assertEqual(a.read_store(self.store)[2], [{'gap': 'SEQ_STATE_INVALID'}])
        with self.assertRaises(a.AuditError):
            a.record_event(self.store, event('PreToolUse', 'u2'))
        with open(seq, 'wb') as handle:
            handle.write(good)
        os.rename(seq, os.path.join(self.base, 'seq-moved'))
        self.assertEqual(a.read_store(self.store)[2], [{'gap': 'SEQ_STATE_INVALID'}])
        with self.assertRaises(a.AuditError):
            a.record_event(self.store, event('PreToolUse', 'u2'))
        os.rename(os.path.join(self.base, 'seq-moved'), seq)
        with patch.object(a, '_write_seq', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                a.record_event(self.store, event('PreToolUse', 'u3'))
        _, records, gaps, _ = a.read_store(self.store)
        self.assertEqual(len(records), 3)
        self.assertEqual(gaps, [{'gap': 'SEQ_FRONTIER_BEHIND'}])

    def test_f3_live_reader_sees_coherent_snapshots(self):
        seen, stop = [], threading.Event()

        def reader():
            while not stop.is_set():
                seen.append(a.read_store(self.store)[2])

        def writer(n):
            for i in range(10):
                a.record_event(self.store, event('PreToolUse', 'w%d-%d' % (n, i)))
        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        watcher = threading.Thread(target=reader)
        watcher.start()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stop.set()
        watcher.join()
        self.assertTrue(seen)
        self.assertEqual([g for g in seen if g], [])

    def test_f4_metadata_and_identifier_canaries_never_export(self):
        session = 'sess-' + CANARY
        for i, command in enumerate(('echo ' + session, 'ls ' + UTF8_CANARY, 'git log ' + CANARY + '-turn')):
            self.pair(CANARY + '-use%d' % i, command=command, session=session, turn=CANARY + '-turn',
                      response={'exit_code': 0, 'nested': {'list': [CANARY, UTF8_CANARY]}})
        self.end(session)
        inventory = good_inventory()
        inventory['post'] = dict(inventory['post'], **{'ref:refs/heads/' + CANARY: '3' * 40,
                                                       'worktree:' + CANARY + '/HEAD': '4' * 64})
        text = a.summarize(self.store, TASK, SHA, self.verified(), pins=good_pins(), inventory=inventory,
                           expected_sessions=(session,))
        decoded = json.loads(text)
        for value in [text] + strings(decoded):
            self.assertNotIn(CANARY, value)
            self.assertNotIn(UTF8_CANARY, value)
            self.assertNotIn('z9q7Xk2vLp', value)
        self.assertEqual(decoded['inventory_drift'], ['shared.refs', 'worktree.metadata'])
        self.assertEqual(len(decoded['actions']), 3)
        self.assertTrue(all(len(row['session_ref']) == 16 for row in decoded['actions']))
        labelled = good_pins()
        labelled['start'][CANARY] = labelled['end'][CANARY] = 'a' * 64
        for kwargs in ({'pins': labelled}, {'task_id': 'task-' + CANARY.lower()},
                       {'expected_sessions': ('bad/' + CANARY,)}):
            args = dict(task_id=TASK, pins=good_pins(), inventory=good_inventory(), expected_sessions=(session,))
            args.update(kwargs)
            with self.assertRaises(a.AuditError) as caught:
                a.summarize(self.store, args.pop('task_id'), SHA, self.verified(), **args)
            self.assertNotIn(CANARY, str(caught.exception))
        with self.assertRaises(a.AuditError):
            a.inventory({CANARY: CANON}, self.base, lambda args, cwd: (0, b''))

    def test_f5_bounded_readers(self):
        with patch.object(a.os, 'read', lambda fd, n: b'x' * (1 << 20)):
            with self.assertRaises(a.AuditError):
                a._read_limited(0, 2 * 1024 * 1024)
        key = os.path.join(self.store, a.KEY)
        with open(key, 'ab') as handle:
            handle.write(b'x' * 4096)
        with self.assertRaises(a.AuditError):
            a.record_event(self.store, event('PreToolUse'))
        with self.assertRaises(a.AuditError):
            a.read_store(self.store)
        big = os.path.join(self.base, 'big')
        with open(big, 'wb') as handle:
            handle.write(b'y' * 100)
        with patch.object(a, 'MAX_PIN_BYTES', 10):
            self.assertEqual(a.file_digest(big), 'TOO_LARGE')
        read_fd, write_fd = os.pipe()
        writer = threading.Thread(target=lambda: (os.write(write_fd, b'z' * 3000), os.close(write_fd)))
        writer.start()
        self.assertIsNone(a._read_pipe(read_fd, 1000, time.monotonic() + 5))
        writer.join()
        os.close(read_fd)
        idle_read, idle_write = os.pipe()
        self.assertIsNone(a._read_pipe(idle_read, 1000, time.monotonic() + 0.05))
        os.close(idle_read)
        os.close(idle_write)

    def test_f5_store_overflow_and_oversize_record_become_gaps(self):
        with patch.object(a, 'MAX_RECORD_BYTES', 200):
            a.record_event(self.store, event('PreToolUse', 'large', command='ls ' + 'q' * 300))
        with patch.object(a, 'MAX_STORE_BYTES', 1), patch.object(a, 'OVERFLOW_RESERVE', 600):
            a.record_event(self.store, event('PreToolUse', 'o1'))
            with self.assertRaises(a.AuditError):
                for i in range(20):
                    a.record_event(self.store, event('PreToolUse', 'o%d' % i))
            _, records, gaps, _ = a.read_store(self.store)
        self.assertEqual(gaps[0], {'gap': 'MALFORMED_EVENT', 'reason': 'RECORD_TOO_LARGE', 'seq': 1})
        self.assertIn({'gap': 'STORE_OVERFLOW', 'seq': 2}, gaps)
        self.assertNotIn(b'qqqq', _read(os.path.join(self.store, a.EVENTS)))
        with patch.object(a, 'MAX_EVENT_RECORDS', 1), patch.object(a, 'OVERFLOW_RECORDS', 0):
            with self.assertRaises(a.AuditError):
                a.record_event(self.store, event('PreToolUse', 'too-many'))

    def test_f5_lock_acquisition_is_bounded(self):
        fd = os.open(os.path.join(self.store, a.LOCK), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with patch.object(a, 'LOCK_TIMEOUT_SECONDS', 0.05):
                started = time.monotonic()
                with self.assertRaises(a.AuditError):
                    a.record_event(self.store, event('PreToolUse'))
                with self.assertRaises(a.AuditError):
                    a.read_store(self.store)
                self.assertLess(time.monotonic() - started, 5)
        finally:
            os.close(fd)
        a.record_event(self.store, event('PreToolUse'))


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
        start = a.pin_files({'codex.config': config, 'codex.hooks': os.path.join(self.base, 'hooks.json')})
        self.assertEqual(start['codex.hooks'], 'MISSING')
        self.assertEqual(a.compare_pins(start, dict(start))['changed'], [])
        self.assertEqual(a.compare_pins(start, dict(start))['unverified'], ['codex.hooks'])
        with open(config, 'a') as handle:
            handle.write('[hooks]\n')
        end = a.pin_files({'codex.config': config, 'codex.hooks': os.path.join(self.base, 'hooks.json')})
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
        roots = {'canonical': CANON, 'claude': PEER}
        pre = a.inventory(roots, common, git, own_worktree='codex')
        self.assertEqual(pre['worktree:claude/locked'], 'ABSENT')
        state['refs'] = b'refs/heads/main 1\nrefs/heads/feat/own 3\n'
        with open(os.path.join(common, 'worktrees/codex/HEAD'), 'w') as handle:
            handle.write('own-change')
        post = a.inventory(roots, common, git, own_worktree='codex')
        self.assertEqual(a.compare_inventory(pre, post, ('refs/heads/feat/own',))['drift'], [])
        state['status'] = b'?? new-file\0'
        with open(os.path.join(common, 'worktrees/claude/HEAD'), 'w') as handle:
            handle.write('peer-change')
        with open(os.path.join(common, 'hooks/post-checkout'), 'w') as handle:
            handle.write('#!/bin/sh\n')
        result = a.compare_inventory(pre, a.inventory(roots, common, git, own_worktree='codex'),
                                     ('refs/heads/feat/own',))
        self.assertEqual(result['drift'], ['canonical.status', 'claude.status', 'common.hooks',
                                           'worktree:claude/HEAD'])
        self.assertIn('worktree:claude/gitdir', result['unverified'])
        self.assertLessEqual(set(calls), {'rev-parse', 'symbolic-ref', 'ls-files', 'status', 'for-each-ref'})
        with patch.object(a, 'MAX_LISTING', 1):
            self.assertEqual(a.inventory(roots, common, git)['common.hooks'], 'TOO_MANY')
        failing = a.inventory(roots, common, lambda args, cwd: (128, b''))
        self.assertEqual(failing['shared.refs'], 'GIT_EXIT_128')
        self.assertIn('shared.refs', a.compare_inventory(failing, failing)['unverified'])


if __name__ == '__main__':
    unittest.main()
