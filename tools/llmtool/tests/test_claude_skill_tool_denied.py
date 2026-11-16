"""#1502 E7 (PR #2186 security v92): llmtool-built Claude Code launches deny the Skill tool."""
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from llmtool import active_pm_inbox, pm_runtime  # noqa: E402
from test_pm_runtime import SESSION, FakeProcess, encoded  # noqa: E402

DONE = {'type': 'result', 'result': 'Done', 'usage': {'input_tokens': 1}}


class PMRuntimeSkillDeniedTests(unittest.TestCase):
    def setUp(self):
        handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGHUP)}
        self.addCleanup(self.check_handlers, handlers)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root, self.cwd = Path(temp.name) / 'state', temp.name
        pm_runtime._ensure_state_dir(self.root)
        observe = patch.object(active_pm_inbox, 'observe_claude_session', return_value=self.cwd)
        observe.start()
        self.addCleanup(observe.stop)
        binding = dict(runtime='claude-code', native_session_id=SESSION, cwd=self.cwd)
        binding.update(dedicated=True, created_at=pm_runtime._timestamp(lambda: 1))
        binding['session_started'] = False
        path = pm_runtime._binding_path(self.root, SESSION)
        pm_runtime._write_json_atomic(self.root, path, binding)

    def check_handlers(self, handlers):
        for signum, handler in handlers.items():
            self.assertIs(signal.getsignal(signum), handler)

    def test_pm_runtime_argv_denies_the_skill_tool(self):
        calls, process = [], FakeProcess(encoded(DONE))

        def runner(argv, cwd, env):
            calls.append(argv)
            return process

        options = dict(runner=runner, state_root=self.root, clock=lambda: 1000)
        options['environ'] = {'HOME': self.cwd}
        runtime = pm_runtime.PMRuntime(SESSION, cwd=self.cwd, **options)
        self.addCleanup(runtime.close)
        runtime.open()
        list(runtime.send('Owner request'))
        argv = calls[0]
        self.assertEqual(argv.count('--disallowedTools'), 1)
        self.assertEqual(argv[argv.index('--disallowedTools') + 1], 'Skill')
        self.assertNotIn('--tools', argv)


class RecoverySkillDeniedTests(unittest.TestCase):
    def recovery(self, runtime):
        provenance = dict(pm_runtime=runtime, pm_native_session_id=SESSION, pm_cwd='/work')
        return active_pm_inbox.recovery(dict(pm_provenance=provenance))['resume_argv']

    def test_claude_code_resume_argv_denies_the_skill_tool(self):
        expected = ['claude', '--resume', SESSION, '--disallowedTools', 'Skill']
        self.assertEqual(self.recovery('claude-code'), expected)

    def test_codex_resume_argv_is_unchanged(self):
        self.assertEqual(self.recovery('codex'), ['codex', 'resume', SESSION])


if __name__ == '__main__':
    unittest.main()
