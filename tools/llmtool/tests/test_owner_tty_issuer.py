"""Tests for the explicit, direct-terminal Owner approval issuer."""

import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import cli, owner_tty_issuer as owner
from llmtool.authorization_ledger import LedgerCode, LedgerError, ReceiptLedger


SHA = 'a' * 40
OTHER_SHA = 'b' * 40
REPOSITORY = 'davidcatcat/davidcattoolsproject'


def binding(pr=7, head=SHA, base='main', repository=REPOSITORY):
    with patch.object(owner, '_repository_slug', return_value=REPOSITORY):
        return owner.merge_binding(pr, head, base, repository)


def pull(number=7, head=SHA, base='main', repository=REPOSITORY, **overrides):
    value = {'number': number, 'title': 'review me', 'state': 'open', 'merged': False,
             'head': {'sha': head}, 'base': {'ref': base, 'repo': {'full_name': repository}},
             'changed_files': 1}
    value.update(overrides)
    return value


def pull_files():
    return [{'filename': 'tools/change.py', 'status': 'modified'}]


class OwnerApprovalLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / 'approvals.sqlite3'
        self.now = [1000]

    def create(self):
        return ReceiptLedger.create(self.path, clock=lambda: self.now[0], kind='OWNER_APPROVAL')

    def test_issue_live_consume_once_expiry_and_reissue(self):
        ledger = self.create()
        try:
            binding = globals()['binding']()
            nonce, expires = ledger.issue_owner_approval(binding, ttl_seconds=30)
            self.assertEqual(len(nonce), 64)
            self.assertEqual(expires, 1030)
            self.assertTrue(ledger.owner_approval_live(binding))
            self.assertFalse(ledger.owner_approval_live(globals()['binding'](head=OTHER_SHA)))
            self.assertTrue(ledger.consume_owner_approval(binding))
            self.assertFalse(ledger.consume_owner_approval(binding))

            another = globals()['binding'](pr=8)
            ledger.issue_owner_approval(another, ttl_seconds=2)
            self.now[0] += 2
            self.assertFalse(ledger.owner_approval_live(another))
            ledger.issue_owner_approval(another, ttl_seconds=10)
            self.assertTrue(ledger.owner_approval_live(another))
        finally:
            ledger.close()

    def test_live_duplicate_and_clock_rollback_fail_closed(self):
        ledger = self.create()
        try:
            binding = globals()['binding']()
            ledger.issue_owner_approval(binding, ttl_seconds=30)
            with self.assertRaises(LedgerError) as duplicate:
                ledger.issue_owner_approval(binding, ttl_seconds=30)
            self.assertIs(duplicate.exception.code, LedgerCode.NOT_ALLOWED)
            self.now[0] = 999
            with self.assertRaises(LedgerError) as rollback:
                ledger.owner_approval_live(binding)
            self.assertIs(rollback.exception.code, LedgerCode.CLOCK_ROLLBACK)
        finally:
            ledger.close()

    def test_bad_bindings_and_ttls_are_refused(self):
        ledger = self.create()
        try:
            for binding in ('', 'x' * 513, 3, '\ud800'):
                with self.subTest(binding=repr(binding)), self.assertRaises(LedgerError):
                    ledger.issue_owner_approval(binding, ttl_seconds=5)
            for ttl in (True, 0, 3601):
                with self.subTest(ttl=ttl), self.assertRaises(LedgerError):
                    ledger.issue_owner_approval('binding', ttl_seconds=ttl)
        finally:
            ledger.close()

    def test_public_helpers_never_create_missing_ledger_and_refuse_insecure_file(self):
        binding = globals()['binding']()
        with patch.dict(os.environ, {'HOME': str(self.home)}):
            self.assertFalse(owner.is_approved(binding))
            self.assertFalse(owner.consume(binding))
            self.assertFalse((self.home / '.local/state/llmtool/owner-approvals').exists())
            ledger = owner._ledger_create()
            ledger.close()
            path = self.home / '.local/state/llmtool/owner-approvals/approvals.sqlite3'
            path.chmod(0o644)
            self.assertFalse(owner.is_approved(binding))
            self.assertFalse(owner.consume(binding))

    def test_public_helper_returns_false_on_clock_rollback(self):
        with patch.dict(os.environ, {'HOME': str(self.home)}), patch.object(owner, '_clock', return_value=1000):
            ledger = owner._ledger_create()
            try:
                ledger.issue_owner_approval(globals()['binding'](), ttl_seconds=30)
            finally:
                ledger.close()
        with patch.dict(os.environ, {'HOME': str(self.home)}), patch.object(owner, '_clock', return_value=999):
            self.assertFalse(owner.is_approved(globals()['binding']()))

    def test_merge_and_install_bindings_are_distinct_and_inputs_are_exact(self):
        self.assertNotEqual(globals()['binding'](), owner.install_binding(SHA, OTHER_SHA))
        for pr in (True, False, 0, -1, '7'):
            with self.subTest(pr=pr), self.assertRaises(ValueError):
                owner.merge_binding(pr, SHA, 'main', REPOSITORY)
        for value in ('A' * 40, '0' * 39, 'g' * 40, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                owner.merge_binding(7, value, 'main', REPOSITORY)
            with self.subTest(value=value), self.assertRaises(ValueError):
                owner.install_binding(value, OTHER_SHA)

    def test_binding_from_pr_round_trips_and_retarget_changes_binding(self):
        with patch.object(owner, '_repository_slug', return_value=REPOSITORY):
            original = owner.merge_binding_from_pr(pull())
            retargeted = owner.merge_binding_from_pr(pull(head=OTHER_SHA))
            self.assertNotEqual(original, retargeted)
            ledger = self.create()
            try:
                ledger.issue_owner_approval(original, ttl_seconds=30)
                self.assertTrue(ledger.owner_approval_live(original))
                self.assertFalse(ledger.owner_approval_live(retargeted))
            finally:
                ledger.close()

    def test_binding_requires_main_and_trusted_repository(self):
        with patch.object(owner, '_repository_slug', return_value=REPOSITORY):
            with self.assertRaises(ValueError):
                owner.merge_binding(7, SHA, 'stack-x', REPOSITORY)
            with self.assertRaises(ValueError):
                owner.merge_binding_from_pr(pull(repository='attacker/repo'))

    def test_owner_approval_recover_is_not_allowed(self):
        ledger = self.create()
        try:
            with self.assertRaises(LedgerError) as raised:
                ledger.recover()
            self.assertIs(raised.exception.code, LedgerCode.NOT_ALLOWED)
        finally:
            ledger.close()

    def test_two_threads_consume_one_approval_exactly_once(self):
        ledger = self.create()
        try:
            current_binding = globals()['binding']()
            ledger.issue_owner_approval(current_binding, ttl_seconds=30)
        finally:
            ledger.close()
        barrier = threading.Barrier(2)
        results = []

        def consume_in_thread():
            current = ReceiptLedger.open(self.path, clock=lambda: self.now[0],
                                         kind='OWNER_APPROVAL')
            try:
                barrier.wait()
                results.append(current.consume_owner_approval(current_binding))
            finally:
                current.close()

        workers = [threading.Thread(target=consume_in_thread) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(sorted(results), [False, True])

class OwnerApprovalCliTests(unittest.TestCase):
    def test_gh_api_uses_trusted_explicit_repository_not_gh_repo(self):
        result = SimpleNamespace(returncode=0, stdout=b'{}')
        with patch.dict(os.environ, {'GH_REPO': 'wrong/repository'}), \
                patch.object(owner, '_repository_slug', return_value=REPOSITORY), \
                patch.object(owner.subprocess, 'run', return_value=result) as run:
            owner._gh_api('pulls/7')
        self.assertEqual(run.call_args.args[0], ['gh', 'api',
                                                 'repos/' + REPOSITORY + '/pulls/7'])
        self.assertEqual(run.call_args.kwargs['timeout'], 30)

    def test_metadata_head_change_between_reads_refuses(self):
        with patch.object(owner, '_repository_slug', return_value=REPOSITORY), \
                patch.object(owner, '_gh_api', side_effect=[pull(), pull_files(), pull(head=OTHER_SHA)]):
            with self.assertRaises(owner.OwnerApprovalRefusal) as raised:
                owner._read_pr(7)
        self.assertEqual(raised.exception.code, 'OWNER_APPROVAL_PR_UNAVAILABLE')

    def test_file_limits_and_path_size_refuse_before_challenge(self):
        for metadata, files, code in (
                (pull(changed_files=100), [], 'OWNER_APPROVAL_TOO_MANY_FILES'),
                (pull(changed_files=2), pull_files(), 'OWNER_APPROVAL_TOO_MANY_FILES'),
                (pull(), [{'filename': 'x' * 1025, 'status': 'modified'}],
                 'OWNER_APPROVAL_DISPLAY_TOO_LARGE')):
            with self.subTest(code=code), patch.object(owner, '_read_pr', return_value=(metadata, files)), \
                    patch.object(owner, '_repository_slug', return_value=REPOSITORY), \
                    patch.object(owner, 'confirm_on_tty') as challenge:
                result = owner.run_owner_approval(SimpleNamespace(pr=7, head=SHA))
                self.assertEqual(result, 1)
                challenge.assert_not_called()

    def test_display_lists_every_file_rename_and_cuts_only_title(self):
        files = [{'filename': f'file-{index}.py', 'status': 'modified'} for index in range(99)]
        files[0] = {'filename': 'new.py', 'previous_filename': 'old.py', 'status': 'renamed'}
        data = pull(title='t' * 301, changed_files=99)
        lines = owner._display_lines(data, files, 7, SHA)
        self.assertTrue(lines[0].startswith('Action: merge PR #7 into main'))
        self.assertTrue(lines[1].endswith('[cut]'))
        self.assertEqual(sum(line.startswith(('modified:', 'renamed:')) for line in lines), 99)
        self.assertIn('renamed: old.py -> new.py', lines)
        self.assertFalse(any('+ more' in line for line in lines))

    def test_base_and_repository_refuse_before_challenge(self):
        for metadata, code in ((pull(base='stack-x'), 'OWNER_APPROVAL_BASE_NOT_MAIN'),
                               (pull(repository='other/repo'),
                                'OWNER_APPROVAL_REPOSITORY_MISMATCH')):
            with self.subTest(code=code), patch.object(owner, '_read_pr', return_value=(metadata, pull_files())), \
                    patch.object(owner, '_repository_slug', return_value=REPOSITORY), \
                    patch.object(owner, 'confirm_on_tty') as challenge, \
                    patch.object(owner, 'grant') as grant, \
                    patch.object(owner, '_ledger_for_grant') as ledger_for_grant, \
                    patch.object(owner, '_ledger_open') as ledger_open, \
                    patch('sys.stderr') as stderr:
                self.assertEqual(owner.run_owner_approval(SimpleNamespace(pr=7, head=SHA)), 1)
                challenge.assert_not_called()
                self.assertEqual(''.join(call.args[0] for call in stderr.write.call_args_list),
                                 'llmtool: ' + code + '\n')
                grant.assert_not_called()
                ledger_for_grant.assert_not_called()
                ledger_open.assert_not_called()

    def test_unicode_line_separators_fillers_and_whitespace_are_sanitized(self):
        value = 'a\u2028\u3164   b\u2029\u115f\u1160\uffa0\n c'
        self.assertEqual(owner.safe_terminal_text(value, 100), 'a?? b????? c')

    def test_duplicate_live_grant_refuses_before_challenge(self):
        with patch.object(owner, '_read_pr', return_value=(pull(), pull_files())), \
                patch.object(owner, '_repository_slug', return_value=REPOSITORY), \
                patch.object(owner, 'is_approved', return_value=True), \
                patch.object(owner, 'confirm_on_tty') as challenge, \
                patch.object(owner, 'grant') as grant, \
                patch.object(owner, '_ledger_for_grant') as ledger_for_grant, \
                patch('sys.stderr') as stderr:
            self.assertEqual(owner.run_owner_approval(SimpleNamespace(pr=7, head=SHA)), 1)
            challenge.assert_not_called()
            self.assertEqual(''.join(call.args[0] for call in stderr.write.call_args_list),
                             'llmtool: OWNER_APPROVAL_ALREADY_LIVE\n')
            grant.assert_not_called()
            ledger_for_grant.assert_not_called()

    def test_non_tty_pipe_is_refused_without_creating_ledger(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'HOME': directory}):
            saved_in, saved_out = os.dup(0), os.dup(1)
            in_read, in_write = os.pipe()
            out_read, out_write = os.pipe()
            try:
                os.write(in_write, b'code from a pipe\n')
                os.close(in_write)
                os.dup2(in_read, 0)
                os.dup2(out_write, 1)
                with self.assertRaises(owner.OwnerApprovalRefusal) as refusal:
                    owner.confirm_on_tty(['prompt'])
                self.assertEqual(refusal.exception.code, 'OWNER_APPROVAL_NOT_INTERACTIVE')
                self.assertFalse((Path(directory) / '.local/state/llmtool/owner-approvals').exists())
            finally:
                os.dup2(saved_in, 0)
                os.dup2(saved_out, 1)
                for fd in (saved_in, saved_out, in_read, out_read, out_write):
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def test_cli_blocks_closed_and_mismatched_heads_before_challenge(self):
        for data, code in (
                (pull(state='closed'),
                 'OWNER_APPROVAL_PR_NOT_OPEN'),
                (pull(head=OTHER_SHA),
                 'OWNER_APPROVAL_HEAD_MISMATCH')):
            with self.subTest(code=code), patch.object(owner, '_read_pr', return_value=(data, pull_files())), \
                    patch.object(owner, '_repository_slug', return_value=REPOSITORY), \
                    patch.object(owner, 'confirm_on_tty') as challenge, \
                    patch('sys.stderr') as stderr:
                result = owner.run_owner_approval(SimpleNamespace(pr=7, head=SHA))
                self.assertEqual(result, 1)
                challenge.assert_not_called()
                self.assertEqual(stderr.write.call_args_list[0].args[0], 'llmtool: ' + code)

    def test_terminal_display_sanitizes_title_and_paths_and_parser_is_narrow(self):
        hostile = pull(title='title\x1b[31m\u202e')
        files = [{'filename': 'bad\x1b[2J\u202e.py', 'status': 'modified'}]
        with patch.object(owner, 'grant', return_value=123) as granted:
            with patch.object(owner, '_read_pr', return_value=(hostile, files)), \
                    patch.object(owner, '_repository_slug', return_value=REPOSITORY), \
                    patch('sys.stdout'):
                self.assertEqual(owner.run_owner_approval(SimpleNamespace(pr=7, head=SHA)), 0)
            lines = granted.call_args.args[1]
        self.assertTrue(lines)
        self.assertTrue(lines[0].startswith('Action: merge PR #7 into main'))
        self.assertTrue(any('Title: title' in line for line in lines))
        self.assertTrue(all('\x1b' not in line and '\u202e' not in line for line in lines))
        parser = cli.parser_for_cli()
        parsed = parser.parse_args(['owner-approval', 'grant', '--pr', '7', '--head', SHA])
        self.assertEqual((parsed.command, parsed.owner_approval_command, parsed.pr, parsed.head),
                         ('owner-approval', 'grant', 7, SHA))
        grant_parser = parser._subparsers._group_actions[0].choices['owner-approval']
        grant_parser = grant_parser._subparsers._group_actions[0].choices['grant']
        self.assertEqual({action.dest for action in grant_parser._actions} - {'help'},
                         {'pr', 'head'})


@unittest.skipUnless(hasattr(pty, 'fork'), 'pty.fork unavailable')
class OwnerApprovalPtyTests(unittest.TestCase):
    def _run_case(self, answer):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        bindir = root / 'bin'
        bindir.mkdir()
        fake_gh = bindir / 'gh'
        response = pull()
        files = pull_files()
        fake_gh.write_text('#!/bin/sh\ncase "$2" in *files*) printf \'%s\\n\' \'' +
                           json.dumps(files) + '\' ;; *) printf \'%s\\n\' \'' +
                           json.dumps(response) + '\' ;; esac\n',
                           encoding='utf-8')
        fake_gh.chmod(0o700)
        pid, master = pty.fork()
        if pid == 0:
            os.environ['HOME'] = str(root)
            os.environ['PATH'] = str(bindir) + os.pathsep + os.environ.get('PATH', '')
            os.environ['GH_REPO'] = 'wrong/repository'
            try:
                owner._repository_slug = lambda: 'davidcatcat/davidcattoolsproject'
                if answer == 'timeout':
                    owner.CHALLENGE_TIMEOUT_SECONDS = 0.05
                if answer == 'background':
                    owner.os.tcgetpgrp = lambda _fd: -1
                code = cli.main(['owner-approval', 'grant', '--pr', '7', '--head', SHA])
                sys.stdout.flush()
                sys.stderr.flush()
            except BaseException:
                code = 99
            os._exit(code)
        output = bytearray()
        deadline = time.monotonic() + 15
        code = None
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
            found = re.search(rb'(?m)^([0-9a-f]{12})\r?$', output)
            if found:
                if answer == 'correct':
                    os.write(master, found.group(1) + b'\n')
                elif answer == 'wrong':
                    os.write(master, b'wrong\n')
                elif answer == 'close':
                    os.close(master)
                    master = -1
                    break
                answer = 'sent'
            if answer == 'sent' and b'OWNER_APPROVAL_GRANTED' in output:
                break
        if master >= 0:
            os.close(master)
        end = time.monotonic() + 5
        waited = 0
        status = 0
        while time.monotonic() < end:
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited:
                break
            time.sleep(0.05)
        if not waited:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            self.fail('PTY child did not exit within bounded wait')
        self.assertEqual(waited, pid)
        return os.waitstatus_to_exitcode(status), bytes(output), root

    def test_correct_code_grants_bound_approval(self):
        status, output, root = self._run_case('correct')
        self.assertEqual(status, 0, output.decode('utf-8', 'replace'))
        self.assertIn(b'OWNER_APPROVAL_GRANTED pr=7 head=' + SHA.encode(), output)
        with patch.dict(os.environ, {'HOME': str(root)}):
            self.assertTrue(owner.is_approved(globals()['binding']()))

    def test_wrong_code_and_closed_pty_do_not_grant(self):
        for answer in ('wrong', 'close'):
            with self.subTest(answer=answer):
                status, output, root = self._run_case(answer)
                if answer == 'wrong':
                    self.assertEqual(status, 1, output.decode('utf-8', 'replace'))
                else:
                    self.assertNotEqual(status, 0, output.decode('utf-8', 'replace'))
                with patch.dict(os.environ, {'HOME': str(root)}):
                    self.assertFalse(owner.is_approved(globals()['binding']()))

    def test_timeout_and_background_process_group_refuse(self):
        status, output, _ = self._run_case('timeout')
        self.assertEqual(status, 1, output.decode('utf-8', 'replace'))
        self.assertIn(b'OWNER_APPROVAL_TIMEOUT', output)
        status, output, _ = self._run_case('background')
        self.assertEqual(status, 1, output.decode('utf-8', 'replace'))
        self.assertIn(b'OWNER_APPROVAL_NOT_INTERACTIVE', output)


if __name__ == '__main__':
    unittest.main()
