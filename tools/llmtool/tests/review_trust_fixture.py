"""Fixture TrustConfig for review-path tests (#369 PR-6); never reads a real trust.json."""
import sys
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

from llmtool import trust

OWNER_SLUG = 'davidcatcat/davidcattoolsproject'
# Legacy anchors, equal to the Owner's verify-legacy MATCH values.
OWNER_CANONICAL_ROOT = '/Users/davidsun/git/davidcataiporject/davidcattoolsproject'
OWNER_WORKSPACE_ROOT = '/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject'


def make_trust(slug=OWNER_SLUG, *, origins=None, host_policy_issue=120,
               canonical_root=OWNER_CANONICAL_ROOT, workspace_root=OWNER_WORKSPACE_ROOT):
    if origins is None:
        origins = (f'git@github.com:{slug}.git', f'https://github.com/{slug}.git')
    repository = MappingProxyType({
        'slug': slug,
        'allowed_origins': tuple(origins),
        'approval_refs': MappingProxyType({
            'reviewer_host_policy_issue': host_policy_issue,
            'worker_dispatch_issue': 289,
            'worker_dispatch_evidence_pr': 283,
            'retain_release_issues': (289, 352),
        }),
    })
    return trust.TrustConfig(1, canonical_root, workspace_root, workspace_root + '/tasks',
                             repository, MappingProxyType({}), None, '0' * 64, '1' * 64)


def use_trust(config=None):
    """Patch ``trust.load_trust`` (all lazy review readers call it at use time)."""
    config = make_trust() if config is None else config
    return patch.object(trust, 'load_trust', return_value=config)


_module_patcher = None


def start_module():
    """setUpModule helper: owner-equivalent fixture trust for the whole module."""
    global _module_patcher
    stop_module()
    _module_patcher = use_trust()
    _module_patcher.start()


def stop_module():
    global _module_patcher
    if _module_patcher is not None:
        _module_patcher.stop()
        _module_patcher = None


_TESTS_DIR = str(Path(__file__).resolve().parent)
_SOURCE_ROOT = str(Path(__file__).resolve().parents[1])
# Child processes do not inherit the parent's mock. ``-I`` ignores PYTHONPATH and
# would import whichever llmtool is installed, so pin this checkout and install
# the fixture before the CLI runs; the child never reads a real trust.json.
_CLI_BOOTSTRAP = (
    'import sys\n'
    'sys.path[:0] = [{source!r}, {tests!r}]\n'
    'import review_trust_fixture\n'
    'review_trust_fixture.start_module()\n'
    'import llmtool\n'
    'assert llmtool.__file__.startswith({source!r} + "/"), "wrong llmtool source"\n'
    'from llmtool.entrypoint import main\n'
    'raise SystemExit(main(sys.argv[1:]))\n'
).format(source=_SOURCE_ROOT, tests=_TESTS_DIR)


def cli_command(*argv):
    """Isolated ``llmtool`` CLI child command running under the fixture trust."""
    return [sys.executable, '-B', '-I', '-c', _CLI_BOOTSTRAP, *argv]
