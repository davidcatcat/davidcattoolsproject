"""#2640: the publish step's own guidance hash-refresh commit is not Developer work.

An AGY Developer edits AGENTS.md but cannot compute the new hash; the publish
step (#2497) adds a "(refresh guidance hash)" commit that changes only
tools/llmtool/guidance-drift.json. Completion compares the Developer artifact
with the Developer tip, so that commit must be stripped from the tip -- and
only that exact commit: any other change to guidance-drift.json, or any other
path in the same commit, stays Developer work (fail closed).
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from llmtool import guidance_refresh
from llmtool import task_completion


def _git(root, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
               GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
    return subprocess.run(['git', *args], cwd=root, check=True, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL).stdout.decode().strip()


def _config(agents, claude):
    return {'schema_version': 1,
            'hashes': {'AGENTS.md': hashlib.sha256(agents).hexdigest(),
                       'CLAUDE.md': hashlib.sha256(claude).hexdigest()},
            'codex_only': []}


class GuidanceRefreshArtifactTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        _git(self.root, 'init', '-q', '-b', 'main')
        self.agents, self.claude = b'# shared rules\n', b'@AGENTS.md\n'
        (Path(self.root) / 'AGENTS.md').write_bytes(self.agents)
        (Path(self.root) / 'CLAUDE.md').write_bytes(self.claude)
        self.config_path = Path(self.root) / guidance_refresh.CONFIG_REL_PATH
        self.config_path.parent.mkdir(parents=True)
        self.write_config(_config(self.agents, self.claude))
        _git(self.root, 'add', '-A')
        _git(self.root, 'commit', '-q', '-m', 'source')
        self.source = _git(self.root, 'rev-parse', 'HEAD')
        (Path(self.root) / 'AGENTS.md').write_bytes(b'# shared rules, edited\n')
        _git(self.root, 'commit', '-q', '-am', 'developer edit')
        self.developer = _git(self.root, 'rev-parse', 'HEAD')

    def write_config(self, config):
        self.config_path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')

    def commit(self, message):
        _git(self.root, 'commit', '-q', '-am', message)
        return _git(self.root, 'rev-parse', 'HEAD')

    def tip(self, head):
        return task_completion._developer_tip(self.root, self.source, head)

    def test_real_publish_refresh_commit_is_stripped(self):
        result = guidance_refresh.refresh_guidance_hashes(
            root=self.root, head=self.developer,
            write_paths=['AGENTS.md', guidance_refresh.CONFIG_REL_PATH],
            task_commits=[(self.developer, [self.source])],
            commit_message=('title (refresh guidance hash)', 'llmtool Task x'))
        self.assertTrue(result['refreshed'])
        self.assertNotEqual(result['head'], self.developer)
        self.assertEqual(self.tip(result['head']), self.developer)

    def test_wrong_hash_value_stays_developer_work(self):
        config = _config(b'something else\n', self.claude)
        self.write_config(config)
        head = self.commit('hand-made hash change')
        self.assertEqual(self.tip(head), head)

    def test_other_config_change_stays_developer_work(self):
        config = _config(b'# shared rules, edited\n', self.claude)
        config['codex_only'] = [{'value': 'x', 'reason': 'smuggled'}]
        self.write_config(config)
        head = self.commit('refresh plus extra entry')
        self.assertEqual(self.tip(head), head)

    def test_extra_path_in_same_commit_stays_developer_work(self):
        self.write_config(_config(b'# shared rules, edited\n', self.claude))
        (Path(self.root) / 'AGENTS.md').write_bytes(b'# shared rules, edited twice\n')
        head = self.commit('refresh plus another edit')
        self.assertEqual(self.tip(head), head)

    def test_no_op_refresh_is_not_a_refresh(self):
        parent = json.dumps(_config(self.agents, self.claude), indent=2).encode() + b'\n'
        blobs = {'AGENTS.md': self.agents, 'CLAUDE.md': self.claude}
        self.assertFalse(guidance_refresh.is_refresh_content(parent, parent, blobs))

    def test_invalid_inputs_are_not_a_refresh(self):
        blobs = {'AGENTS.md': self.agents, 'CLAUDE.md': self.claude}
        self.assertFalse(guidance_refresh.is_refresh_content(b'not json', b'{}', blobs))
        parent = json.dumps(_config(b'x', self.claude), indent=2).encode() + b'\n'
        self.assertFalse(guidance_refresh.is_refresh_content(parent, parent, {'AGENTS.md': b'x'}))


if __name__ == '__main__':
    unittest.main()
