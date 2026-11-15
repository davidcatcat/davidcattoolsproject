"""#1230 refusal-code pin: task_completion D6 root and ENV-2 marker codes. Synthetic temp state only."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from llmtool import task_completion as tc
from llmtool.errors import LLMToolError

TASK = 'rootcodes-task-1'


class CompletionRegistryCodeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None)
        self.addCleanup(tmp.cleanup)
        base = Path(os.path.realpath(tmp.name))
        self.common = base/'repo'/'.git'
        (self.common/'llmtool-worktrees').mkdir(parents=True, mode=0o700)
        (self.common/'llmtool-worktrees').chmod(0o700)
        self.root = base/'d6'
        self.root.mkdir(mode=0o700)

    def code(self, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        return caught.exception.completion_code

    def test_missing_d6_root_is_root_missing(self):
        info = self.root.stat()
        value = {'schema_version': 1, 'task_id': TASK, 'trusted_agent_id': 'opencode-developer',
                 'session_ref': TASK, 'root_realpath': str(self.root),
                 'physical_identity': {'device': info.st_dev, 'inode': info.st_ino, 'uid': info.st_uid,
                                       'mode': oct(stat.S_IMODE(info.st_mode))},
                 'git_common_dir': str(self.common), 'branch': 'test/rootcodes', 'base_sha': 'a'*40,
                 'head_sha': 'a'*40, 'state': 'ACTIVE', 'lock_state': 'LOCKED'}
        path = self.common/'llmtool-worktrees'/(TASK + '.json')
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        self.assertEqual(tc.read_d6_registration(TASK, str(self.common)).root, str(self.root))
        self.root.rmdir()
        self.assertEqual(self.code(lambda: tc.read_d6_registration(TASK, str(self.common))), 'D6_ROOT_MISSING')

    def test_malformed_env2_marker_is_invalid(self):
        for marker in ('PASS', [], {}, {'schema_version': 1}):
            registration = SimpleNamespace(env2_pass_marker=marker, task_id=TASK, root=str(self.root))
            with self.subTest(marker=marker):
                self.assertEqual(self.code(lambda: tc.validate_env2_pass_marker(
                    registration, expected_root=str(self.root), current_head='a'*40)),
                    'D6_ENV2_PASS_MARKER_INVALID')


if __name__ == '__main__':
    unittest.main()
