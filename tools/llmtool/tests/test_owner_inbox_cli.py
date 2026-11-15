"""CLI tests for the local Owner instruction inbox (#1160 PR 4)."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from llmtool import cli
from llmtool import owner_inbox_cli
from llmtool import owner_instruction_inbox as store


class OwnerInboxCliTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.home.chmod(0o700)
        self.addCleanup(shutil.rmtree, self.home)
        self.path = self.home / 'inbox' / 'inbox.json'

    def _run(self, *arguments):
        output = io.StringIO()
        with mock.patch.object(owner_inbox_cli.time, 'time', return_value=1000), \
                contextlib.redirect_stdout(output):
            code = cli.main(['owner-inbox', *arguments])
        raw = output.getvalue()
        self.assertNotIn(str(self.path), raw)
        self.assertNotIn(str(self.home), raw)
        self.assertNotIn('Traceback', raw)
        self.assertEqual(raw.count('\n'), 1)
        self.assertTrue(raw.endswith('\n'))
        return code, json.loads(raw), raw

    def _init(self):
        code, values, _raw = self._run('init', '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(values, {'initialized': True})

    def _add(self, alias, update_id, text):
        return store.add(str(self.path), alias, update_id, text, 1000)

    def test_init_creates_private_parent_then_peek_is_empty(self):
        self._init()
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

        code, values, _raw = self._run('peek', '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(set(values), {'items', 'notice'})
        self.assertEqual(values['items'], [])
        self.assertEqual(values['notice'], owner_inbox_cli.NOTICE)
        self.assertIn('lower-trust data from Telegram', values['notice'])

    def test_init_does_not_change_an_existing_parent_mode(self):
        parent = self.home / 'existing'
        parent.mkdir(mode=0o700)
        os.chmod(parent, 0o750)
        target = parent / 'inbox.json'
        output = io.StringIO()
        with mock.patch.object(owner_inbox_cli.time, 'time', return_value=1000), \
                contextlib.redirect_stdout(output):
            code = cli.main(['owner-inbox', 'init', '--path', str(target)])
        self.assertEqual(code, 0)
        self.assertEqual(parent.stat().st_mode & 0o777, 0o750)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_peek_returns_stored_item_and_notice(self):
        self._init()
        ident = store.instruction_id('owner', 7)
        self._add('owner', 7, 'read the local plan')

        code, values, _raw = self._run('peek', '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(values['items'], [{
            'code': ident[:12], 'alias': 'owner', 'received_at': 1000,
            'state': 'QUEUED', 'text': 'read the local plan'}])
        self.assertEqual(values['notice'], owner_inbox_cli.NOTICE)

    def test_seen_ack_and_status_with_and_without_alias_and_code(self):
        self._init()
        owner_code = store.instruction_id('owner', 11)[:12]
        desk_code = store.instruction_id('desk', 12)[:12]
        self._add('owner', 11, 'read the local status')
        self._add('desk', 12, 'queue the local plan')

        code, values, _raw = self._run('status', '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(values, {'items': [
            {'code': owner_code, 'state': 'QUEUED'}]})

        code, values, _raw = self._run(
            'status', '--alias', 'desk', desk_code, '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(values, {'items': [
            {'code': desk_code, 'state': 'QUEUED'}]})
        self.assertNotIn('text', values['items'][0])

        code, values, _raw = self._run(
            'status', '--alias', 'desk', '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(values, {'items': [
            {'code': desk_code, 'state': 'QUEUED'}]})

        code, values, _raw = self._run(
            'seen', owner_code, '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(values, {'code': owner_code, 'state': 'SEEN'})

        code, values, _raw = self._run(
            'status', owner_code, '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(values, {'items': [
            {'code': owner_code, 'state': 'SEEN'}]})

        code, values, _raw = self._run(
            'ack', owner_code, '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(values, {'code': owner_code, 'state': 'ACKED'})

        code, values, _raw = self._run(
            'status', '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(values, {'items': [
            {'code': owner_code, 'state': 'ACKED'}]})

    def test_disable_refuses_add_then_enable_accepts_it(self):
        self._init()
        code, values, _raw = self._run('disable', '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(values, {'enabled': False})

        with self.assertRaises(store.OwnerInboxError) as caught:
            self._add('owner', 1, 'queue the local plan')
        self.assertEqual(caught.exception.code, store.DISABLED)

        code, values, _raw = self._run('enable', '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(values, {'enabled': True})
        self._add('owner', 2, 'queue the local plan')
        code, values, _raw = self._run('peek', '--path', str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(len(values['items']), 1)
        self.assertEqual(values['items'][0]['text'], 'queue the local plan')

    def test_errors_print_only_one_fixed_code_not_the_path(self):
        code, values, _raw = self._run('peek', '--path', str(self.path))
        self.assertEqual(code, 1)
        self.assertEqual(values, {'code': store.STATE_MISSING})

        self._init()
        for action in ('seen', 'ack'):
            with self.subTest(action=action):
                code, values, _raw = self._run(
                    action, 'not-a-code', '--path', str(self.path))
                self.assertEqual(code, 1)
                self.assertEqual(values, {'code': store.ARGUMENT_INVALID})

        code, values, _raw = self._run(
            'ack', '0' * 12, '--path', str(self.path))
        self.assertEqual(code, 1)
        self.assertEqual(values, {'code': store.NOT_FOUND})

        code, values, _raw = self._run(
            'init', '--path', str(self.path))
        self.assertEqual(code, 1)
        self.assertEqual(values, {'code': store.STATE_EXISTS})


if __name__ == '__main__':
    unittest.main()
