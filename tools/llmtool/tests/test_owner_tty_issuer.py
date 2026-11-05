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
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import cli, owner_tty_issuer as owner
from llmtool.authorization_ledger import LedgerCode, LedgerError, ReceiptLedger


SHA = 'a' * 40
OTHER_SHA = 'b' * 40


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
            binding = owner.merge_binding(7, SHA)
            nonce, expires = ledger.issue_owner_approval(binding, ttl_seconds=30)
            self.assertEqual(len(nonce), 64)
            self.assertEqual(expires, 1030)
            self.assertTrue(ledger.owner_approval_live(binding))
            self.assertFalse(ledger.owner_approval_live(owner.merge_binding(7, OTHER_SHA)))
            self.assertTrue(ledger.consume_owner_approval(binding))
            self.assertFalse(ledger.consume_owner_approval(binding))

            another = owner.merge_binding(8, SHA)
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
            binding = owner.merge_binding(7, SHA)
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
        binding = owner.merge_binding(7, SHA)
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
                ledger.issue_owner_approval(owner.merge_binding(7, SHA), ttl_seconds=30)
            finally:
                ledger.close()
        with patch.dict(os.environ, {'HOME': str(self.home)}), patch.object(owner, '_clock', return_value=999):
            self.assertFalse(owner.is_approved(owner.merge_binding(7, SHA)))

    def test_merge_and_install_bindings_are_distinct_and_inputs_are_exact(self):
        self.assertNotEqual(owner.merge_binding(7, SHA), owner.install_binding(SHA, OTHER_SHA))
        for pr in (True, False, 0, -1, '7'):
            with self.subTest(pr=pr), self.assertRaises(ValueError):
                owner.merge_binding(pr, SHA)
        for value in ('A' * 40, '0' * 39, 'g' * 40, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                owner.merge_binding(7, value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                owner.install_binding(value, OTHER_SHA)

class OwnerApprovalCliTests(unittest.TestCase):
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
                ({'state': 'CLOSED', 'headRefOid': SHA, 'title': 'x', 'files': []},
                 'OWNER_APPROVAL_PR_NOT_OPEN'),
                ({'state': 'OPEN', 'headRefOid': OTHER_SHA, 'title': 'x', 'files': []},
                 'OWNER_APPROVAL_HEAD_MISMATCH')):
            with self.subTest(code=code), patch.object(owner, '_gh_pr_view', return_value=data), \
                    patch.object(owner, 'confirm_on_tty') as challenge, \
                    patch('sys.stderr') as stderr:
                result = owner.run_owner_approval(SimpleNamespace(pr=7, head=SHA))
                self.assertEqual(result, 1)
                challenge.assert_not_called()
                self.assertEqual(stderr.write.call_args_list[0].args[0], 'llmtool: ' + code)

    def test_terminal_display_sanitizes_title_and_paths_and_parser_is_narrow(self):
        hostile = {'state': 'OPEN', 'headRefOid': SHA,
                   'title': 'title\x1b[31m\u202e',
                   'files': [{'path': 'bad\x1b[2J\u202e.py'}]}
        with patch.object(owner, 'grant', return_value=123) as granted:
            with patch.object(owner, '_gh_pr_view', return_value=hostile), patch('sys.stdout'):
                self.assertEqual(owner.run_owner_approval(SimpleNamespace(pr=7, head=SHA)), 0)
            lines = granted.call_args.args[1]
        self.assertTrue(lines)
        self.assertTrue(any('PR 7: title' in line for line in lines))
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
        response = {'number': 7, 'title': 'review me', 'state': 'OPEN',
                    'headRefOid': SHA, 'files': [{'path': 'tools/change.py'}]}
        fake_gh.write_text('#!/bin/sh\nprintf %s ' + repr(json.dumps(response)) + '\n',
                           encoding='utf-8')
        fake_gh.chmod(0o700)
        pid, master = pty.fork()
        if pid == 0:
            os.environ['HOME'] = str(root)
            os.environ['PATH'] = str(bindir) + os.pathsep + os.environ.get('PATH', '')
            try:
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
            self.assertTrue(owner.is_approved(owner.merge_binding(7, SHA)))

    def test_wrong_code_and_closed_pty_do_not_grant(self):
        for answer in ('wrong', 'close'):
            with self.subTest(answer=answer):
                status, output, root = self._run_case(answer)
                if answer == 'wrong':
                    self.assertEqual(status, 1, output.decode('utf-8', 'replace'))
                else:
                    self.assertNotEqual(status, 0, output.decode('utf-8', 'replace'))
                with patch.dict(os.environ, {'HOME': str(root)}):
                    self.assertFalse(owner.is_approved(owner.merge_binding(7, SHA)))


if __name__ == '__main__':
    unittest.main()
