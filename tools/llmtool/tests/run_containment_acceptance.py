"""Actual-Mac gate, exactly --output PATH; never produces schema-only success."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

from test_git_containment import AcceptanceScenarios, retained_processes
from llmtool import _git_containment_protocol as wire
from llmtool import _git_process_identity as kernel

SCENARIOS = ('fd_inheritance', 'sigint', 'sigterm', 'sighup', 'hung_supervisor',
             'leader_exit_descendant', 'identity_reuse', 'macos_start_time',
             'kqueue_non_reaping', 'protocol_framing', 'code_runtime_binding',
             'public_import_boundary', 'parent_sigkill_plain', 'parent_sigkill_popen')
ROOT = Path(__file__).resolve().parents[3]
CANONICAL = Path('/Users/davidsun/git/davidcataiporject/davidcattoolsproject')
EXPECTED_FILES = (
    'tools/llmtool/llmtool/_git_containment.py',
    'tools/llmtool/llmtool/_git_supervisor.py',
    'tools/llmtool/llmtool/_git_containment_protocol.py',
    'tools/llmtool/llmtool/_git_process_identity.py',
    'tools/llmtool/llmtool/_git_containment_child.py',
    'tools/llmtool/tests/test_git_containment.py',
    'tools/llmtool/tests/run_containment_acceptance.py',
    'tools/llmtool/tests/containment_probe_parent.py',
    'tools/llmtool/tests/test_git_mutation.py',
    'reports/llmtool_a11b2_2_1_containment_f3_recovery_validation_20261007.md')


def hashes(contract):
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
            for name in contract['allowed_files']
            if not name.startswith('reports/') and (ROOT/name).is_file()}


def write(output, evidence):
    raw = json.dumps(evidence, sort_keys=True, allow_nan=False, indent=2).encode('utf-8')
    if len(raw) > 128*1024:
        raise ValueError('evidence exceeds contract bound')
    output.write_bytes(raw+b'\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.absolute()
    if output.is_symlink() or not output.parent.is_dir():
        raise SystemExit('invalid evidence destination')
    evidence = {'schema_version': 1, 'platform': sys.platform,
        'os_version': platform.mac_ver()[0], 'python_version': platform.python_version(),
        'tested_sha256': {}, 'scenarios': {}}
    # Invalidate any earlier evidence before doing checks; no stale PASS reuse.
    write(output, evidence)
    current = 'preflight'
    scenarios = None
    try:
        if sys.platform != 'darwin' or ROOT != CANONICAL or Path.cwd().resolve() != ROOT:
            raise ValueError('NOT SUPPORTED: actual canonical Mac invocation required')
        raw_contract = (output.parent/'task.json').read_bytes()
        if len(raw_contract) > 128*1024:
            raise ValueError('contract size limit')
        contract = wire.strict_load(raw_contract)
        if (contract['task_id'] != 'A11b.2.2.1-containment-f3-recovery-20261007'
                or contract['base_sha'] != 'ceae425eacaa8729a9eb8d796e43304918a8c908'
                or tuple(contract['allowed_files']) != EXPECTED_FILES):
            raise ValueError('pinned runner contract mismatch')
        if any(not (ROOT/name).is_file() for name in contract['required_files']):
            raise ValueError('required artifact missing')
        before = hashes(contract)
        evidence['tested_sha256'] = before
        # The closed acceptance controller also owns unreaped native fixtures;
        # custom/auto reapers or native workers cannot supply their PID pin.
        kernel.host_check()
        scenarios = AcceptanceScenarios()
        for position, current in enumerate(SCENARIOS, 1):
            scenarios.begin_subcase(current)
            scenarios.completed_subcases = []
            print('[{}/{}] {} START'.format(position, len(SCENARIOS), current), flush=True)
            started, initial = time.monotonic(), scenarios.checks
            repetitions = getattr(scenarios, current)()
            checks = scenarios.checks-initial
            if (checks <= 0 or type(repetitions) is not list
                    or (current.startswith('parent_sigkill_') and
                        (len(repetitions) < 20 or checks < len(repetitions)))
                    or (not current.startswith('parent_sigkill_') and repetitions)):
                raise ValueError('empty acceptance scenario')
            evidence['scenarios'][current] = {'status': 'PASS', 'checks': checks,
                                             'skipped': 0, 'repetitions': repetitions}
            write(output, evidence)
            print('[{}/{}] {} PASS {:.3f}s'.format(position, len(SCENARIOS),
                        current, time.monotonic()-started), flush=True)
        if hashes(contract) != before:
            raise ValueError('tested files changed during acceptance')
        write(output, evidence)
        return 0
    except BaseException as exc:
        # Retain all completed/partial invocation evidence. Deliberately invalid
        # for the PASS validator and nonzero; never fabricate missing scenarios.
        evidence['failure'] = {'scenario': current, 'status': 'FAIL',
                               **(getattr(exc, 'primary_before_cleanup', None)
                                  or wire.exception_evidence(exc))}
        diagnostics = getattr(exc, 'probe_diagnostics', None)
        if diagnostics is not None:
            evidence['failure']['diagnostics'] = diagnostics
        cleanup = getattr(exc, 'fixture_cleanup', None)
        if cleanup is None and scenarios is not None:
            cleanup = scenarios.last_fixture_cleanup
        if cleanup is not None:
            evidence['failure']['fixture_cleanup'] = cleanup
        secondary = getattr(exc, 'secondary_cleanup_errors', None)
        if secondary:
            evidence['failure']['secondary_cleanup_errors'] = secondary
        if scenarios is not None and scenarios.retained:
            evidence['failure']['retained'] = scenarios.retained
        if scenarios is not None:
            evidence['failure']['subcase'] = scenarios.current_subcase
            evidence['failure']['completed_subcases'] = scenarios.completed_subcases.copy()
        owned = retained_processes()
        evidence['failure']['owned_processes'] = owned
        write(output, evidence)
        print('{} FAIL ({})'.format(current, type(exc).__name__), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
