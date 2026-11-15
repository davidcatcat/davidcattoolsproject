"""#1502 WP-D phase 2 R2 run 2c: shared test fixtures for the delegate chain gate.

Every Developer route claim runs task_submission_cli._delegate_chain_gate. The
real gate reads the whole store (store.task_ids() and store.get()), takes the
chain locks and checks the continuation link before it calls claim().

Prefer giving a fake store what the real gate needs. A module that only tests
route wiring and has no usable store may instead start pass_through_chain_gate
in setUp: the patched gate calls claim() and returns its result, exactly as the
real gate does when the chain admits the claim. The gate itself is covered by
test_task_delegate_repair_cap and test_lineage_delegate_integration.
Standard library only.
"""
from unittest.mock import patch

from llmtool import task_submission_cli


def _pass_through(_args, _store, _d6_binding, claim):
    return claim()


def pass_through_chain_gate(test_case):
    """Patch the chain gate to call claim() and return it, for one test.

    Returns the mock so a test can check the gate was reached. The patch is
    stopped by test_case's cleanup.
    """
    patcher = patch.object(task_submission_cli, '_delegate_chain_gate',
                           side_effect=_pass_through)
    gate = patcher.start()
    test_case.addCleanup(patcher.stop)
    return gate
