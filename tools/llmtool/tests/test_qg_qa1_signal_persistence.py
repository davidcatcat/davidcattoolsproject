"""D-F1/D-50: handled real CLI signals publish a completed interrupted run."""
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch

import test_cli_provider_lifecycle as lifecycle_fixture
from llmtool import cli

FAKE, ROOT, PROMPT = lifecycle_fixture.FAKE, lifecycle_fixture.ROOT, lifecycle_fixture.PROMPT

SETTINGS = {'persist_metadata': True, 'max_runs': 1, 'max_total_bytes': 1048576,
            'max_run_bytes': 65536, 'max_age_days': 14}


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'fork'), 'requires POSIX persistence/signals')
class HandledSignalPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = lifecycle_fixture.CLIProviderLifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def reader(self, *args):
        f = self.fixture
        return subprocess.run([sys.executable, '-m', 'llmtool', *args], cwd=ROOT,
            env=f.environment, stdin=subprocess.DEVNULL, capture_output=True, timeout=10)

    def signal_run(self, provider, number, *, disabled=False, unsafe=False):
        f = self.fixture
        state = (f.directory / 'state').resolve()
        if unsafe:
            state.mkdir(mode=0o755)
            state.chmod(0o755)
        f.launch(provider, diagnostics=SETTINGS, flags=('--no-save-diagnostics',) if disabled else ())
        f.process.send_signal(number)
        out, err = f.process.communicate(timeout=5)
        self.assertEqual((f.process.returncode, out), (128 + number, b''))
        f.assert_private_omitted(out, err)
        self.assertFalse(Path(f.evidence['cwd']).exists())
        deadline = time.monotonic() + 5
        while f.running(f.evidence['pids']) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(f.running(f.evidence['pids']), {})
        if disabled:
            self.assertFalse(state.exists())
            return
        if unsafe:
            self.assertIn(b'code=DIAGNOSTICS_STORE_UNSAFE', err)
            self.assertEqual(list(state.iterdir()), [])
            return
        runs = list((state / 'runs').iterdir())
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertTrue((run / 'summary.json').is_file(), 'handled signal must durably publish summary')
        summary = json.loads((run / 'summary.json').read_text())
        self.assertEqual(summary['status'], 'interrupted')
        events = [json.loads(line) for line in (run / 'trace.jsonl').read_text().splitlines()]
        self.assertEqual(events[-1]['type'], 'run.end')
        self.assertEqual(events[-1]['data']['status'], 'interrupted')
        self.assertEqual({event['run_id'] for event in events}, {run.name})
        self.assertEqual(summary['run_id'], run.name)
        self.assertFalse((run / '.reservation').exists())
        self.assertFalse((run / 'trace.partial.jsonl').exists())
        listing = self.reader('runs')
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn(b'interrupted', listing.stdout)
        self.assertNotIn(b'incomplete', listing.stdout)
        trace = self.reader('trace', run.name)
        self.assertEqual(trace.returncode, 0, trace.stderr)
        self.assertIn(b'state=complete', trace.stdout)
        doctor = self.reader('doctor')
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        self.assertIn(b'store=present run_count=1 active_count=0 unknown_count=0', doctor.stdout)
        for path in run.iterdir():
            if path.suffix in ('.json', '.jsonl'):
                for marker in (PROMPT, 'DG1_FINAL', 'DG1_PRIVATE_STDERR', 'DG1_SYNTHETIC_SECRET', 'reasoning'):
                    self.assertNotIn(marker.encode(), path.read_bytes())
        # Immediately save again with max_runs=1; no age/grace manipulation.
        executable = f.bin / provider.split('-')[0]
        executable.write_text(FAKE.format(python=sys.executable, mode='success',
            starts=str(f.starts), capture=str(f.directory / 'second.json'),
            release=str(f.directory / 'release')))
        following = self.reader('ask', PROMPT, '--save-diagnostics', '--verbose')
        self.assertEqual((following.returncode, following.stdout), (0, b'DG1_FINAL\n'), following.stderr)
        self.assertIn(b'diagnostics_persisted=true', following.stderr)
        self.assertNotIn(b'DIAGNOSTICS_STORE_FULL', following.stderr)
        self.assertEqual(len(f.starts.read_text().splitlines()), 2)
        remaining = list((state / 'runs').iterdir())
        self.assertEqual(len(remaining), 1)
        self.assertEqual(json.loads((remaining[0] / 'summary.json').read_text())['status'], 'success')
        self.assertFalse(Path(json.loads((f.directory / 'second.json').read_text())['cwd']).exists())

    def test_claude_term_finalizes_and_immediate_next_save(self):
        self.signal_run('claude-cli', signal.SIGTERM)

    def test_codex_term_finalizes_and_immediate_next_save(self):
        self.signal_run('codex-cli', signal.SIGTERM)

    @unittest.skipUnless(hasattr(signal, 'SIGHUP'), 'platform has no SIGHUP')
    def test_claude_hup_finalizes_and_immediate_next_save(self):
        self.signal_run('claude-cli', signal.SIGHUP)

    @unittest.skipUnless(hasattr(signal, 'SIGHUP'), 'platform has no SIGHUP')
    def test_codex_hup_finalizes_and_immediate_next_save(self):
        self.signal_run('codex-cli', signal.SIGHUP)

    def test_claude_int_contract_and_persisted_summary(self):
        self.signal_run('claude-cli', signal.SIGINT)

    def test_codex_int_contract_and_persisted_summary(self):
        self.signal_run('codex-cli', signal.SIGINT)

    def test_term_persistence_off_does_not_create_store(self):
        self.signal_run('claude-cli', signal.SIGTERM, disabled=True)

    @unittest.skipUnless(hasattr(signal, 'SIGHUP'), 'platform has no SIGHUP')
    def test_hup_persistence_off_does_not_create_store(self):
        self.signal_run('codex-cli', signal.SIGHUP, disabled=True)

    def test_term_secondary_store_failure_preserves_exit(self):
        self.signal_run('codex-cli', signal.SIGTERM, unsafe=True)

    @unittest.skipUnless(hasattr(signal, 'SIGHUP'), 'platform has no SIGHUP')
    def test_hup_secondary_store_failure_preserves_exit(self):
        self.signal_run('claude-cli', signal.SIGHUP, unsafe=True)

    def test_unrelated_system_exit_is_not_finalized_as_interrupted(self):
        client = Mock()
        client.complete.side_effect = SystemExit(143)
        out, err = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch('llmtool.cli.sys.stdin', io.StringIO()))
            stack.enter_context(patch('llmtool.cli.resolve_config', return_value={}))
            stack.enter_context(patch('llmtool.cli.api_key', return_value=None))
            stack.enter_context(patch('llmtool.cli.create_client', return_value=client))
            run = stack.enter_context(patch('llmtool.diagnostics_runtime.RunContext')).return_value
            stack.enter_context(patch('llmtool.diagnostics_settings.resolve_diagnostics_settings'))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            with self.assertRaises(SystemExit) as caught:
                cli.main(['ask', 'synthetic'])
            self.assertEqual(caught.exception.code, 143)
            run.finish.assert_not_called()
            client.complete.assert_called_once()
