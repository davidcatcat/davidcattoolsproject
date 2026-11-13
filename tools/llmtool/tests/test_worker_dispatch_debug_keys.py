"""#1502 WP-B: the optional codex-debug and claude-debug worker keys."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import worker_dispatch
import worker_policy_fixture

REPOSITORY = {'slug': 'davidcatcat/davidcattoolsproject',
              'dispatch_issue': 289, 'dispatch_evidence_pr': 283}


def policy(keys):
    # #1692: the shared all-ALLOWED test policy limited to ``keys``.
    return worker_policy_fixture.policy(keys=keys)


class DebugWorkerKeyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'worker-dispatch.json'
        for target, value in ((worker_dispatch, '_POLICY_PATH'),):
            patcher = patch.object(target, value, self.path)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(worker_dispatch, 'repository_config', return_value=REPOSITORY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, value):
        self.path.write_text(json.dumps(value), encoding='utf-8')

    def _parse(self, value):
        patterns = worker_dispatch._build_reference_patterns(REPOSITORY)
        return worker_dispatch._parse_policy(json.dumps(value).encode('utf-8'), patterns)

    def test_debug_keys_are_separate_from_the_base_workers(self):
        self.assertEqual(worker_dispatch.DEBUG_WORKERS, {'codex-debug', 'claude-debug'})
        self.assertFalse(worker_dispatch.DEBUG_WORKERS & worker_dispatch.WORKERS)

    def test_old_and_new_policy_files_both_parse(self):
        old = self._parse(policy(worker_dispatch.WORKERS))
        self.assertEqual(set(old['workers']), worker_dispatch.WORKERS)
        new = self._parse(policy(worker_dispatch.WORKERS | worker_dispatch.DEBUG_WORKERS))
        self.assertEqual(set(new['workers']),
                         worker_dispatch.WORKERS | worker_dispatch.DEBUG_WORKERS)

    def test_one_debug_key_alone_or_an_unknown_key_is_invalid(self):
        for extra in ({'codex-debug'}, {'claude-debug'}, {'gemini-debug'}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    self._parse(policy(worker_dispatch.WORKERS | extra))

    def test_debug_key_in_a_new_file_follows_its_switch(self):
        value = policy(worker_dispatch.WORKERS | worker_dispatch.DEBUG_WORKERS)
        value['workers']['claude-debug']['dispatch'] = 'PAUSED'
        self._write(value)
        allowed = worker_dispatch.evaluate('codex-debug')
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.reason_code, 'OWNER_POLICY_ALLOWED')
        paused = worker_dispatch.evaluate('claude-debug')
        self.assertFalse(paused.allowed)
        self.assertEqual(paused.reason_code, 'OWNER_PAUSED')

    def test_debug_key_missing_from_an_old_file_is_refused(self):
        self._write(policy(worker_dispatch.WORKERS))
        for key in sorted(worker_dispatch.DEBUG_WORKERS):
            with self.subTest(key=key):
                decision = worker_dispatch.evaluate(key)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason_code, 'WORKER_POLICY_ENTRY_MISSING')
                self.assertIsNotNone(decision.policy_sha256)
        self.assertTrue(worker_dispatch.evaluate('codex-reviewer').allowed)

    def test_an_unknown_key_is_still_unknown(self):
        self._write(policy(worker_dispatch.WORKERS | worker_dispatch.DEBUG_WORKERS))
        self.assertEqual(worker_dispatch.evaluate('gemini-debug').reason_code, 'WORKER_UNKNOWN')


if __name__ == '__main__':
    unittest.main()
