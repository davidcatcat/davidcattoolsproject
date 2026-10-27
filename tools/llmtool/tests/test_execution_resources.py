"""Shape checks for the informational execution-resource inventory."""

import json
from pathlib import Path
import unittest

from llmtool import worker_dispatch


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = REPOSITORY_ROOT / 'tools' / 'llmtool' / 'execution-resources.json'
POLICY_PATH = REPOSITORY_ROOT / 'tools' / 'llmtool' / 'worker-dispatch.json'


class ExecutionResourcesTests(unittest.TestCase):
    def test_inventory_shape_and_runner_accounts(self):
        inventory = json.loads(INVENTORY_PATH.read_text(encoding='utf-8'))

        self.assertEqual(set(inventory), {
            'schema_version', 'purpose', 'as_of', 'hosts', 'live_online_state',
            'live_busy_state', 'remote_development_authority',
        })
        self.assertEqual(inventory['schema_version'], 1)
        self.assertEqual(inventory['remote_development_authority'], 'NOT_GRANTED')

        runners = [runner for host in inventory['hosts']
                   for runner in host['runners']]
        names = [runner['name'] for runner in runners]
        self.assertEqual(len(names), len(set(names)))

        ec2 = next(host for host in inventory['hosts'] if host['id'] == 'aws-ec2')
        for runner in ec2['runners']:
            with self.subTest(runner=runner['name']):
                self.assertTrue(runner['service_account'])
                self.assertTrue(runner['runner_label'])
                self.assertTrue(runner['role'])

        runner3 = next(runner for runner in ec2['runners']
                       if runner['name'] == 'ec2-self-runner-3')
        self.assertEqual(runner3, {
            'name': 'ec2-self-runner-3',
            'runner_label': 'local-ec2-linux-ci-pending until #446 merges',
            'service_account': 'ghrunner3',
            'role': 'Linux CI shard',
        })

    def test_worker_dispatch_policy_stays_separate_from_inventory(self):
        policy = worker_dispatch._parse_policy(POLICY_PATH.read_bytes())
        self.assertEqual(set(policy['workers']), worker_dispatch.WORKERS)
        self.assertNotIn('hosts', policy)
        self.assertNotIn('runners', policy)

        # The gate discovers only worker-dispatch.json policy locations.
        self.assertNotIn('execution-resources.json',
                         Path(worker_dispatch.__file__).read_text(encoding='utf-8'))
        self.assertTrue(all(path.name == 'worker-dispatch.json'
                            for path in worker_dispatch._policy_candidates()))


if __name__ == '__main__':
    unittest.main()
