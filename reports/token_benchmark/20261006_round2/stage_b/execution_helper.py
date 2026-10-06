"""L01-only Stage-B adapter; reuse unchanged Stage-A measurement construction.

Each live command is explicit and single-use. No automatic retry or semantic repair.
Gold is used only by the separately invoked post-pair grade command.
"""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path('/Users/davidsun/git/davidcataiporject/davidcattoolsproject')
STAGE_A = ROOT / 'reports/token_benchmark/20261006_round2/stage_a'
sys.path.insert(0, str(STAGE_A))
sys.path.insert(0, str(ROOT / 'tools/llmtool'))
# Import by path because this adapter has the same filename as its predecessor.
import importlib.util
spec = importlib.util.spec_from_file_location('execution_helper', STAGE_A / 'execution_helper.py')
a = importlib.util.module_from_spec(spec)
sys.modules['execution_helper'] = a
spec.loader.exec_module(a)
import grading_helper

BASE = 'bfa314c3d3b10587fac7e1adaf423c48ad61f44b'
UPSTREAM_SHA = '30e279993e1fba6f60447b64d0d5a4e156a9d050dbe1ea879a61d9ba1f08f483'
GRADER_SHA = '6b8b72f8297d03676c00831077a35307c6028dff53516790d864ae4c94a97684'
ORDER = [['L01', 'DOGFOOD'], ['L01', 'CONTROL']]
original_gates = a.gates
a.EXPECTED_SHA = BASE


def gates():
    assert a.sha((STAGE_A / 'execution_helper.py').read_bytes()) == UPSTREAM_SHA
    assert a.sha((STAGE_A / 'grading_helper.py').read_bytes()) == GRADER_SHA
    assert subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip() == 'bench/llmtool-token-savings-large-evidence-20261006'
    registration = original_gates()
    version = subprocess.check_output(['codex', '--version'], cwd=ROOT, text=True).strip()
    if version != 'codex-cli 0.159.2':
        raise RuntimeError('CODEX_USAGE_SCHEMA_CHANGED')
    # Only an in-memory execution-order adapter; immutable manifest stays untouched.
    registration['run_order'] = ORDER
    return registration


a.gates = gates


def state_for(runtime):
    state = json.loads((runtime / 'state.json').read_text())
    assert state['stage_b_adapter_sha256'] == a.sha(Path(__file__).read_bytes())
    assert state['stage_b_ceiling'] == 200000
    assert len(state['codex_sessions']) <= 2
    assert [[s['fixture'], s['variant']] for s in state['codex_sessions']] == ORDER[:len(state['codex_sessions'])]
    return state


def initialize():
    gates()
    previous = json.loads((STAGE_A / 'results.json').read_text())
    assert previous['stage_b_trigger']['L01']['met']
    assert next(p for p in previous['pairs'] if p['fixture'] == 'L01')['quality_approved']
    runtime = Path(tempfile.mkdtemp(prefix='llmtool-round2-stage-b-'))
    runtime.chmod(0o700)
    config = runtime / 'company-home/.config/llmtool/config.yaml'
    config.parent.mkdir(parents=True, mode=0o700)
    config.write_text(a.CONFIG)
    config.chmod(0o600)
    (runtime / 'company_observer.py').write_text(a.OBSERVER)
    config_path = Path.home() / '.config/llmtool/config.yaml'
    stat = config_path.stat() if config_path.exists() else None
    config_metadata = None if stat is None else {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'inode': stat.st_ino}
    a.write(runtime / 'state.json', {
        'codex_sessions': [], 'company_attempts': [], 'completed_pairs': [],
        'remaining_budget': 200000, 'stage_b_ceiling': 200000,
        'pair_start_checks': [],
        'stage_b_adapter_sha256': a.sha(Path(__file__).read_bytes()),
        'prior_stage_a_session': previous['codex_sessions'][-1],
        'real_config_metadata_before': config_metadata,
    })
    print(json.dumps({'runtime': str(runtime), 'stage_b_ceiling': 200000,
                      'adapter_sha256': a.sha(Path(__file__).read_bytes()),
                      'config_sha256': a.sha(a.CONFIG.encode()),
                      'observer_sha256': a.sha(a.OBSERVER.encode())}))


def company(runtime, attempt):
    state = state_for(runtime)
    assert not state['codex_sessions']
    assert len(state['company_attempts']) == attempt - 1
    # Upstream wrapper enforces attempt 2 only for a recorded transient transport failure.
    a.company('L01', attempt, runtime)


def measured(runtime, variant):
    state = state_for(runtime)
    assert ORDER[len(state['codex_sessions'])] == ['L01', variant]
    if variant == 'DOGFOOD':
        attempt = state['company_attempts'][-1]
        if not attempt.get('coverage_valid'):
            raise RuntimeError('COMPANY_TRIAGE_COVERAGE_MISS')
        if not attempt.get('selector_valid'):
            raise RuntimeError('SELECTIVE_READ_CAP_INSUFFICIENT')
        assert attempt['thinking_false_verified'] and attempt['schema_valid'] and attempt['exit'] == 0
        cv = attempt['coverage']
        assert len(cv['fixture_failure_ids']) == len(cv['company_validated_failure_ids']) == 4
        assert not cv['missing_failure_ids'] and not cv['duplicate_failure_ids'] and not cv['unexpected_failure_ids']
        assert attempt['selection']['selected_raw_bytes'] > 0
    # Same actual argv, prompt bytes/order, output schema and JSONL extraction as Stage A.
    with contextlib.redirect_stdout(io.StringIO()):
        a.codex('L01', variant, runtime)
    state = state_for(runtime)
    record = state['codex_sessions'][-1]
    record['stage_b_adapter_sha256'] = state['stage_b_adapter_sha256']
    if variant == 'DOGFOOD':
        previous = state['prior_stage_a_session']
        # Cross-stage background only; it never enters the measured prompt/context.
        record['cache_background'].update({
            'prior_measured_session_id': previous['session_id'],
            'prior_fixture': previous['fixture'], 'prior_variant': previous['variant'],
            'prior_stage': 'A',
            'elapsed_seconds_since_previous_codex_session': round(record['started_epoch'] - previous['ended_epoch'], 3),
            'prior_session_scope': 'last recorded benchmark session; intervening unmeasured traffic unknown',
        })
    else:
        record['cache_background']['prior_stage'] = 'B'
    a.write(runtime / ('L01-' + variant) / 'metrics.json', record)
    a.write(runtime / 'state.json', state)
    print(json.dumps(record))
    if len(state['codex_sessions']) == 2 and state['remaining_budget'] < 0:
        raise RuntimeError('STAGE_B_BUDGET_EXCEEDED')


def grade(runtime):
    gates()
    state = state_for(runtime)
    assert len(state['codex_sessions']) == 2
    grading_helper.main('L01', runtime)


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'init': initialize()
    elif mode == 'company': company(Path(sys.argv[2]), int(sys.argv[3]))
    elif mode == 'codex': measured(Path(sys.argv[2]), sys.argv[3])
    elif mode == 'grade': grade(Path(sys.argv[2]))
    else: raise ValueError('unsupported Stage-B operation')
