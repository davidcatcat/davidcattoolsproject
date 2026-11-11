"""#1485 case M: every Mac workflow checkout tests the SHA its run reports."""

from pathlib import Path
import unittest

import yaml

WORKFLOW = Path(__file__).resolve().parents[3] / '.github/workflows/llmtool-mac-ci.yml'
REF = '${{ github.event.pull_request.head.sha || github.sha }}'


class MacCheckoutRefTests(unittest.TestCase):
    def test_every_checkout_uses_pr_head_or_run_sha(self):
        jobs = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))['jobs']
        refs = [step['with']['ref'] for job in jobs.values() for step in job['steps']
                if str(step.get('uses', '')).startswith('actions/checkout@')]
        self.assertEqual(refs, [REF] * 3)


if __name__ == '__main__':
    unittest.main()
