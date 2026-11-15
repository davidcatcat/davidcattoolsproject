"""#1230 refusal-code pin: pm_runtime state, history and runtime codes. Synthetic temp state only."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from llmtool import pm_runtime
from llmtool.errors import LLMToolError

SESSION = '12345678-1234-4123-8123-123456789abc'


class PmRuntimeCodeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)/'state'

    def code(self, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        return caught.exception.pm_runtime_code

    def history(self):
        return pm_runtime.read_history(SESSION, state_root=self.root)

    def test_state_dir_must_be_an_absolute_user_directory(self):
        self.assertEqual(self.code(lambda: pm_runtime._ensure_state_dir('relative/state')), 'PM_STATE_INVALID')
        self.root.parent.joinpath('file').write_text('x')
        self.assertEqual(self.code(lambda: pm_runtime._ensure_state_dir(self.root.parent/'file')),
                         'PM_STATE_INVALID')

    def test_unknown_runtime_is_unsupported(self):
        self.assertEqual(self.code(lambda: pm_runtime.create_dedicated_pm(
            str(self.root.parent), runtime='other-runtime', state_root=self.root)), 'PM_RUNTIME_UNSUPPORTED')

    def test_unreadable_or_malformed_history_is_history_invalid(self):
        self.assertEqual(self.history()['turns'], [])
        path = pm_runtime._history_path(self.root, 'pm-' + SESSION)
        for label, content in (('not json', '{'), ('not a dict', '[]'),
                               ('other session', json.dumps({'pm_session_id': 'other', 'turns': []})),
                               ('turns not a list', json.dumps({'pm_session_id': SESSION, 'turns': {}}))):
            with self.subTest(label=label):
                path.write_text(content)
                self.assertEqual(self.code(self.history), 'PM_HISTORY_INVALID')
        path.unlink()
        path.mkdir()
        self.assertEqual(self.code(self.history), 'PM_HISTORY_INVALID')


if __name__ == '__main__':
    unittest.main()
