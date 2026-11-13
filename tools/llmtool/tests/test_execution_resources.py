"""Shape checks for the informational execution-resource inventory."""

import json
from pathlib import Path
import unittest

from llmtool import worker_dispatch


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = REPOSITORY_ROOT / 'tools' / 'llmtool' / 'execution-resources.json'
POLICY_PATH = REPOSITORY_ROOT / 'tools' / 'llmtool' / 'worker-dispatch.json'


import review_trust_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class ExecutionResourcesTests(unittest.TestCase):
    def test_inventory_shape_and_no_retired_self_hosted_runners(self):
        inventory = json.loads(INVENTORY_PATH.read_text(encoding='utf-8'))

        self.assertEqual(set(inventory), {
            'schema_version', 'purpose', 'as_of', 'hosts', 'live_online_state',
            'live_busy_state', 'remote_development_authority',
        })
        self.assertEqual(inventory['schema_version'], 1)
        self.assertEqual(inventory['remote_development_authority'], 'NOT_GRANTED')
        self.assertEqual(inventory['live_online_state'], 'UNKNOWN')
        self.assertEqual(inventory['live_busy_state'], 'UNKNOWN')

        ids = [host['id'] for host in inventory['hosts']]
        self.assertEqual(ids, ['local-mac', 'aws-ec2', 'aws-ec2-2'])
        self.assertEqual(len(ids), len(set(ids)))

        # Self-hosted CI on the Owner Mac and EC2 is retired (#1253, #1502
        # WP-G): no runner entries and no self-hosted CI capability remain.
        for host in inventory['hosts']:
            with self.subTest(host=host['id']):
                self.assertNotIn('runners', host)
                self.assertFalse(any('self-hosted' in capability
                                     for capability in host['capabilities']))
                self.assertTrue((REPOSITORY_ROOT / host['reference']).is_file())

        # The second, smaller EC2 host (#1093) stays listed; no host address.
        ec2_2 = next(host for host in inventory['hosts'] if host['id'] == 'aws-ec2-2')
        self.assertEqual(ec2_2['reported_vcpu'], 2)
        self.assertEqual(ec2_2['reported_memory_gib'], 3)

    def test_retired_self_hosted_mac_smoke_workflow_is_gone(self):
        # #1502 WP-G: the manual smoke job ran on the retired self-hosted Mac runner.
        workflow = REPOSITORY_ROOT / '.github' / 'workflows' / 'mac-runner-smoke.yml'
        self.assertFalse(workflow.exists())

    def test_worker_dispatch_policy_stays_separate_from_inventory(self):
        policy = worker_dispatch._parse_policy(POLICY_PATH.read_bytes())
        self.assertEqual(set(policy['workers']),
                         worker_dispatch.WORKERS | worker_dispatch.DEBUG_WORKERS)
        self.assertNotIn('hosts', policy)
        self.assertNotIn('runners', policy)

        # The gate discovers only worker-dispatch.json policy locations.
        self.assertNotIn('execution-resources.json',
                         Path(worker_dispatch.__file__).read_text(encoding='utf-8'))
        self.assertTrue(all(path.name == 'worker-dispatch.json'
                            for path in worker_dispatch._policy_candidates()))

    def test_codex_writes_no_code_and_agy_reason_names_no_model(self):
        # #1502 O1 / WP-C: Codex is Reviewer and AI Debug only.
        workers = worker_dispatch._parse_policy(POLICY_PATH.read_bytes())['workers']
        self.assertEqual(workers['codex-developer']['dispatch'], 'PAUSED')
        self.assertEqual(workers['codex-reviewer']['dispatch'], 'ALLOWED')
        self.assertEqual(workers['codex-debug']['dispatch'], 'ALLOWED')
        self.assertNotIn('gemini', workers['agy']['reason'].lower())


if __name__ == '__main__':
    unittest.main()
