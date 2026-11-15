"""#1160: a /do resend of a compacted (tombstoned) update replies COMPACTED, not ACKED."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llmtool import owner_instruction_inbox as inbox  # noqa: E402
from llmtool import telegram_cursor as cursor  # noqa: E402
from llmtool import telegram_instruction as ti  # noqa: E402

NOW = 1_700_000_000
ALIAS = 'owner'


def do_update(update_id, now):
    body = dict(message_id=update_id, date=now, text='/do hello')
    body['chat'] = dict(id=7, type='private')
    body['from'] = dict(id=7, is_bot=False)
    return dict(update_id=update_id, message=body)


class CompactedResendTests(unittest.TestCase):
    def setUp(self):
        self.directory = os.path.realpath(tempfile.mkdtemp())
        os.chmod(self.directory, 0o700)
        self.inbox_path = os.path.join(self.directory, 'inbox.json')
        inbox.init_state(self.inbox_path)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def run_do(self, update_id, now, cursor_name='cursor.json'):
        cursor_path = os.path.join(self.directory, cursor_name)
        if not os.path.exists(cursor_path):
            cursor.init_state(cursor_path)
        kwargs = dict(alias=ALIAS, is_allowed=lambda update: True, cursor_path=cursor_path)
        kwargs.update(inbox_path=self.inbox_path, now=now, limiter=ti.RateLimiter())
        return ti.handle_update(do_update(update_id, now), **kwargs)

    def compact_first(self):
        with mock.patch.object(inbox, 'MAX_ENTRIES', 1):
            self.run_do(1, NOW)
            code = inbox.peek(self.inbox_path, NOW)[0]['code']
            inbox.ack(self.inbox_path, code, NOW)
            self.run_do(2, NOW + 1)
        return code

    def test_resend_after_cursor_loss_replies_compacted(self):
        code = self.compact_first()
        before = Path(self.inbox_path).read_bytes()
        resend = self.run_do(1, NOW + 2, 'lost-cursor.json')
        self.assertEqual(resend, dict(outcome=ti.HANDLED, reply=code + ' COMPACTED'))
        self.assertEqual(Path(self.inbox_path).read_bytes(), before)

    def test_resend_with_the_cursor_is_ignored(self):
        self.compact_first()
        resend = self.run_do(1, NOW + 2)
        self.assertEqual(resend, dict(outcome=ti.IGNORED_DUPLICATE, reply=None))


if __name__ == '__main__':
    unittest.main()
