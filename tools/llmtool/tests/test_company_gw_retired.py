import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

_MODULE_HOME = tempfile.TemporaryDirectory()
_IMPORT_HOME_PATCH = patch.dict(os.environ, {'HOME': _MODULE_HOME.name})
_IMPORT_HOME_PATCH.start()
from llmtool import worker_dispatch
try:
    from review_trust_fixture import start_module as _start_trust_fixture
    from review_trust_fixture import stop_module as _stop_trust_fixture
except ImportError:
    _start_trust_fixture = None
    _stop_trust_fixture = None
_IMPORT_HOME_PATCH.stop()


def setUpModule():
    if _start_trust_fixture is not None:
        _start_trust_fixture()
    global _MODULE_HOME_PATCH
    _MODULE_HOME_PATCH = patch.dict(os.environ, {'HOME': _MODULE_HOME.name})
    _MODULE_HOME_PATCH.start()
    user_config = Path(_MODULE_HOME.name) / '.config' / 'llmtool' / 'config.yaml'
    user_config.parent.mkdir(parents=True)
    user_config.write_text('generation: {}\n', encoding='utf-8')


def tearDownModule():
    _MODULE_HOME_PATCH.stop()
    if _stop_trust_fixture is not None:
        _stop_trust_fixture()
    _MODULE_HOME.cleanup()


class CompanyGwRetiredTests(unittest.TestCase):
    def setUp(self):
        # Configure repository evidence references for dispatch issue 289.
        self.repository_patch = patch.object(
            worker_dispatch, 'repository_config',
            return_value={'slug': 'davidcatcat/davidcattoolsproject',
                          'dispatch_issue': 289, 'dispatch_evidence_pr': 283})
        self.repository_patch.start()
        self.addCleanup(self.repository_patch.stop)

    def test_real_worker_dispatch_marks_company_gw_paused(self):
        # Read the real repository worker-dispatch.json.
        policy_path = Path(worker_dispatch._DEFAULT_POLICY_PATH)
        raw = policy_path.read_text(encoding='utf-8')
        policy = json.loads(raw)

        self.assertEqual(policy['schema_version'], 1)

        company_gw = policy['workers']['company-gw']
        self.assertEqual(company_gw['dispatch'], 'PAUSED')
        self.assertEqual(company_gw['decided_by'], 'Owner')
        self.assertEqual(company_gw['decided_at'], '2026-11-11')
        self.assertEqual(
            company_gw['reason'],
            'Owner decision: company/home LLM runs only through the API Agent Gateway '
            '(gw-agent); the company-gw direct/proposal route is retired as Developer, '
            'PM triage and fallback',
        )
        self.assertEqual(
            company_gw['evidence'],
            'https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-6773709231',
        )

        # The gw-agent route is the only home LLM route; the parser accepts only
        # its exact Owner-allowed or PM-paused row.
        gw_agent = policy['workers']['company-gw-agent']
        if gw_agent['dispatch'] == 'ALLOWED':
            self.assertEqual(gw_agent['decided_by'], 'Owner')
            self.assertEqual(gw_agent['evidence'], worker_dispatch._GW_AGENT_OWNER_REF)

        # Ensure the policy parses directly without failure.
        raw = policy_path.read_bytes()
        parsed = worker_dispatch._parse_policy(raw)
        self.assertIsInstance(parsed, dict)

        # #1692: evaluate a byte-identical copy so the real content goes through
        # the real reader, parser and gate, without coupling to an installed
        # data-file copy (the layout check has its own test). Other rows follow
        # their live switch; only the company-gw retirement is pinned here.
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory).resolve() / 'worker-dispatch.json'
            copy.write_bytes(raw)
            with patch.object(worker_dispatch, '_POLICY_PATH', copy):
                decision = worker_dispatch.evaluate('company-gw')
                others = {key: worker_dispatch.evaluate(key)
                          for key in ('company-gw-agent', 'claude-developer')}

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, 'OWNER_PAUSED')
        self.assertEqual(decision.policy_sha256, hashlib.sha256(raw).hexdigest())
        expected = {'ALLOWED': (True, 'OWNER_POLICY_ALLOWED'),
                    'PAUSED': (False, 'OWNER_PAUSED'),
                    'OWNER_ASSIGNMENT_ONLY': (False, 'OWNER_ASSIGNMENT_REQUIRED')}
        for key, other in others.items():
            with self.subTest(worker=key):
                self.assertEqual((other.allowed, other.reason_code),
                                 expected[policy['workers'][key]['dispatch']])


if __name__ == '__main__':
    unittest.main()
