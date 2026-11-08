"""Stage-A measurement wrapper. Explicit one-call commands; never repair or retry."""

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path('/Users/davidsun/git/davidcataiporject/davidcattoolsproject')
STAGE = ROOT / 'reports/token_benchmark/20261006_round2/stage_a'
sys.path.insert(0, str(STAGE))
sys.path.insert(0, str(ROOT / 'tools/llmtool'))
import coverage
import selector

DIRECTORIES = {'L01': 'L01_large_test', 'L02': 'L02_large_log', 'L03': 'L03_large_context'}
EXPECTED_SHA = '6858ad60780b280cbc2c3f619b8aee7380145b73'
PREREG_SHA = '6ee3ddc5a9b015510bc4d5051a050bcd714d4771af6512c429b50a8de73d9683'
CONFIG = '''active_provider: company-round2
providers:
  company-round2:
    type: openai-compatible
    base_url: http://home-llm.example:8000
    model: gw
    timeout_seconds: 120
    api_key: null
    api_key_env: null
    provider_options:
      enable_thinking: false
generation:
  temperature: 0
  max_tokens: 4096
'''
OBSERVER = r'''
import hashlib, json, os, sys
from pathlib import Path
from llmtool.providers.openai_compatible import OpenAICompatibleClient
from llmtool.entrypoint import main
original_init = OpenAICompatibleClient.__init__
expected = Path(os.environ['STAGE_A_INPUT']).read_text()
audit = Path(os.environ['STAGE_A_AUDIT'])
def observed_init(self, provider, generation, key=None):
    assert provider['type'] == 'openai-compatible'
    assert provider['base_url'] == 'http://home-llm.example:8000' and provider['model'] == 'gw'
    assert provider['timeout_seconds'] == 120
    assert provider['api_key'] is None and provider['api_key_env'] is None and key is None
    assert provider['provider_options'] == {'enable_thinking': False}
    assert generation['temperature'] == 0 and generation['max_tokens'] == 4096
    original_init(self, provider, generation, key)
    original_open = self.opener.open
    def observed_open(req, *args, **kwargs):
        body = json.loads(req.data)
        assert req.full_url == 'http://home-llm.example:8000/v1/chat/completions'
        assert req.get_method() == 'POST' and not req.has_header('Authorization')
        assert body['model'] == 'gw' and body['temperature'] == 0 and body['max_tokens'] == 4096
        assert body['chat_template_kwargs'] == {'enable_thinking': False}
        users = [m['content'] for m in body['messages'] if m['role'] == 'user']
        assert users == [expected]
        item = {'enable_thinking': False, 'actual_payload_field': 'chat_template_kwargs.enable_thinking',
                'model': 'gw', 'temperature': 0, 'max_tokens': 4096,
                'authorization_header_present': False, 'resolved_profile_matches': True,
                'observer_changed_request': False,
                'user_message_sha256': hashlib.sha256(users[0].encode()).hexdigest(),
                'request_body_sha256': hashlib.sha256(req.data).hexdigest()}
        with audit.open('a') as stream: stream.write(json.dumps(item, sort_keys=True) + '\n')
        return original_open(req, *args, **kwargs)
    self.opener.open = observed_open
OpenAICompatibleClient.__init__ = observed_init
try:
    raise SystemExit(main())
except AssertionError:
    print('stage-a: observer contract failed', file=sys.stderr)
    raise SystemExit(4)
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def strict_json(text):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('duplicate object key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('non-standard JSON constant')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def gates():
    raw = (STAGE / 'preregistration.json').read_bytes()
    if sha(raw) != PREREG_SHA:
        raise RuntimeError('PREREGISTRATION_DRIFT')
    registration = json.loads(raw)
    for filename, digest in registration['artifact_sha256'].items():
        if sha((STAGE / filename).read_bytes()) != digest:
            raise RuntimeError('PREREGISTRATION_DRIFT')
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip() == EXPECTED_SHA
    return registration


def capture(argv, target, env=None, prompt=None):
    started = time.time()
    with (target / 'stdout.raw').open('w') as out, (target / 'stderr.raw').open('w') as err:
        process = subprocess.Popen(argv, cwd=ROOT, env=env, text=True, start_new_session=True,
                                   stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
                                   stdout=out, stderr=err)
        timed_out = False
        try:
            process.communicate(prompt, timeout=900)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    ended = time.time()
    return {'exit': process.returncode, 'started_epoch': started, 'ended_epoch': ended,
            'wall_seconds': round(ended - started, 3), 'wrapper_timeout': timed_out}


def schema_validate(value, schema):
    import jsonschema
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(value), key=lambda x: str(x.path))
    return {'valid': not errors, 'error_paths': [list(e.path) for e in errors]}


def company_schema(lane):
    # Existing skill schemas, unchanged. No benchmark gold/answer is used.
    if lane == 'L03':
        return None
    previous = 'B01_test_triage' if lane == 'L01' else 'B02_log_triage'
    return json.loads((ROOT / 'reports/token_benchmark/20261006' / previous / 'output_schema.json').read_text())


def init():
    registration = gates()
    runtime = Path(tempfile.mkdtemp(prefix='llmtool-round2-stage-a-'))
    runtime.chmod(0o700)
    config = runtime / 'company-home/.config/llmtool/config.yaml'
    config.parent.mkdir(parents=True, mode=0o700)
    config.write_text(CONFIG); config.chmod(0o600)
    (runtime / 'company_observer.py').write_text(OBSERVER)
    write(runtime / 'state.json', {'codex_sessions': [], 'company_attempts': [], 'completed_pairs': [],
                                 'remaining_budget': 400000, 'pair_start_checks': []})
    print(json.dumps({'runtime': str(runtime), 'config_sha256': sha(CONFIG.encode()),
                      'helper_sha256': sha(Path(__file__).read_bytes()), 'observer_sha256': sha(OBSERVER.encode()),
                      'frozen_artifacts_verified': len(registration['artifact_sha256'])}))


def company(lane, attempt, runtime):
    registration = gates()
    state = json.loads((runtime / 'state.json').read_text())
    assert attempt in (1, 2)
    assert registration['run_order'][len(state['codex_sessions'])] == [lane, 'DOGFOOD']
    if not any(x['fixture'] == lane for x in state['codex_sessions']):
        if state['remaining_budget'] < registration['pair_allowances'][lane]:
            raise RuntimeError('BUDGET_CEILING_NOT_STARTED')
    target = runtime / ('company-' + lane + '-' + str(attempt)); target.mkdir()
    folder = STAGE / DIRECTORIES[lane]
    raw = (folder / 'fixture.txt').read_bytes()
    prompt = registration['company_phase']['company_instructions'][lane].encode() + b'\n\n' + raw
    (target / 'input.txt').write_bytes(prompt)
    config = runtime / 'company-home/.config/llmtool/config.yaml'
    assert config.read_text() == CONFIG
    if attempt == 2:
        prior = [x for x in state['company_attempts'] if x['fixture'] == lane]
        assert len(prior) == 1 and prior[0]['transient_transport_failure']
    env = {key: value for key, value in os.environ.items() if not key.startswith('LLMTOOL_')}
    env.update({'HOME': str(runtime / 'company-home'), 'PYTHONDONTWRITEBYTECODE': '1',
                'STAGE_A_INPUT': str(target / 'input.txt'), 'STAGE_A_AUDIT': str(target / 'request_audit.jsonl')})
    argv = [str(ROOT / 'tools/llmtool/.venv/bin/python'), '-B', str(runtime / 'company_observer.py')]
    argv += (['context-pack', '--json'] if lane == 'L03' else ['ask', '--skill', 'test-triage' if lane == 'L01' else 'log-triage'])
    argv += ['--provider', 'company-round2', '--file', str(target / 'input.txt'), '--verbose']
    record = {'fixture': lane, 'attempt': attempt, 'argv': argv, **capture(argv, target, env=env),
              'input_bytes': len(prompt), 'input_sha256': sha(prompt), 'fixture_bytes': len(raw),
              'config_sha256': sha(config.read_bytes()), 'observer_sha256': sha(OBSERVER.encode())}
    stdout, stderr = (target / 'stdout.raw').read_text(), (target / 'stderr.raw').read_text()
    audit = target / 'request_audit.jsonl'
    observations = [json.loads(line) for line in audit.read_text().splitlines()] if audit.exists() else []
    record['request_observations'] = observations
    record['outgoing_request_count'] = len(observations)
    record['thinking_false_verified'] = (len(observations) == 1 and observations[0]['enable_thinking'] is False
                                        and observations[0]['user_message_sha256'] == sha(prompt))
    metadata = {}
    for key in ('model', 'prompt_tokens', 'completion_tokens', 'total_tokens', 'elapsed_seconds'):
        match = re.search('^' + key + '=(.+)$', stderr, re.M)
        metadata[key] = (int(match[1]) if key.endswith('tokens') else float(match[1]) if key == 'elapsed_seconds' else match[1]) if match else None
    record['metadata'] = metadata
    code = re.search(r'code=([A-Z_]+)', stderr)
    record['safe_error_code'] = code[1] if code else None
    record['transient_transport_failure'] = record['safe_error_code'] in ('PROVIDER_TIMEOUT', 'NETWORK_PROVIDER_UNREACHABLE') or record['wrapper_timeout']
    record['stdout_sha256'] = sha(stdout.encode())
    record['schema_valid'] = False
    if record['exit'] == 0:
        try:
            value = strict_json(stdout)
            if lane == 'L03':
                from llmtool.context import parse_context_packet
                parse_context_packet(stdout)
                valid = True
            else:
                valid = schema_validate(value, company_schema(lane))['valid']
            record['schema_valid'] = valid
            if valid:
                result = {'L01': coverage.test_coverage, 'L02': coverage.log_coverage,
                          'L03': coverage.context_coverage}[lane](raw, value)
                write(target / 'coverage.json', result)
                record['coverage'] = result
                field = {'L01': 'failure_id_coverage_complete', 'L02': 'event_coverage_complete', 'L03': 'citation_membership_valid'}[lane]
                record['coverage_valid'] = result[field]
                record['flags'] = [] if result[field] else ['COMPANY_TRIAGE_COVERAGE_MISS']
                try:
                    manifest, selected = selector.select(raw, result['validated_anchors'])
                    write(target / 'selection.json', manifest)
                    (target / 'selected.raw').write_bytes(selected)
                    record['selection'] = manifest
                    record['selector_valid'] = True
                    record['selection_frozen_epoch'] = time.time()
                    if lane == 'L03':
                        contained = coverage.context_coverage(raw, value, manifest['selected_ranges'])
                        record['post_selection_citation_check'] = contained
                except ValueError:
                    record['flags'].append('SELECTIVE_READ_CAP_INSUFFICIENT')
                    record['selector_valid'] = False
        except (ValueError, TypeError, KeyError):
            record['validation_error'] = 'company structure/canonical validation failed; details omitted'
    write(target / 'metrics.json', record)
    state['company_attempts'].append(record); write(runtime / 'state.json', state)
    print(json.dumps(record))


def codex(lane, variant, runtime):
    registration = gates()
    version = subprocess.check_output(['codex', '--version'], cwd=ROOT, text=True).strip()
    if version != 'codex-cli 0.159.2':
        raise RuntimeError('CODEX_USAGE_SCHEMA_CHANGED')
    state = json.loads((runtime / 'state.json').read_text())
    assert [lane, variant] == registration['run_order'][len(state['codex_sessions'])]
    first_in_pair = not any(x['fixture'] == lane for x in state['codex_sessions'])
    if first_in_pair:
        allowance = registration['pair_allowances'][lane]
        if state['remaining_budget'] < allowance:
            raise RuntimeError('BUDGET_CEILING_NOT_STARTED')
        state['pair_start_checks'].append({'fixture': lane, 'remaining_before_pair': state['remaining_budget'],
                                           'allowance': allowance, 'pass': True})
    target = runtime / (lane + '-' + variant); target.mkdir()
    folder = STAGE / DIRECTORIES[lane]
    task, schema = (folder / 'task.txt').read_bytes(), (folder / 'output_schema.json').read_bytes()
    prompt = b'TASK\n' + task + b'\nFINAL OUTPUT SCHEMA\n' + schema + b'\nSUPPLIED EVIDENCE\n'
    if variant == 'CONTROL':
        prompt += (folder / 'fixture.txt').read_bytes()
    else:
        attempts = [r for r in state['company_attempts'] if r['fixture'] == lane]
        attempt = attempts[-1]
        assert attempt['exit'] == 0 and attempt['schema_valid'] and attempt['thinking_false_verified'] and attempt['selector_valid']
        # Coverage failure remains recorded; only validated returned anchors were selected.
        company_target = runtime / ('company-' + lane + '-' + str(attempt['attempt']))
        prompt += b'COMPANY RESULT\n' + (company_target / 'stdout.raw').read_bytes()
        prompt += b'\nGOLD-INDEPENDENT COVERAGE\n' + (company_target / 'coverage.json').read_bytes()
        prompt += b'\nFROZEN RANGE METADATA\n' + (company_target / 'selection.json').read_bytes()
        prompt += b'\nVERBATIM SELECTED RAW BYTES\n' + (company_target / 'selected.raw').read_bytes()
    (target / 'prompt.txt').write_bytes(prompt)
    previous = state['codex_sessions'][-1] if state['codex_sessions'] else None
    cache = {'prior_measured_session_id': previous['session_id'] if previous else None,
             'prior_fixture': previous['fixture'] if previous else None,
             'prior_variant': previous['variant'] if previous else None,
             'elapsed_seconds_since_previous_codex_session': round(time.time() - previous['ended_epoch'], 3) if previous else None}
    argv = ['codex', 'exec', '--json', '--ephemeral', '--ignore-user-config', '--ignore-rules',
            '--model', 'gpt-6.1-sol', '--config', 'model_reasoning_effort="high"',
            '--config', 'service_tier="default"', '--config', 'approval_policy="never"',
            '--sandbox', 'read-only', '--cd', str(ROOT), '--output-schema', str(folder / 'output_schema.json'), '-']
    record = {'fixture': lane, 'variant': variant, 'codex_version': version, 'argv': argv,
              'repo_sha': EXPECTED_SHA, 'helper_sha256': sha(Path(__file__).read_bytes()),
              'prompt_bytes': len(prompt), 'prompt_sha256': sha(prompt), 'cache_background': cache,
              **capture(argv, target, prompt=prompt.decode())}
    events = [json.loads(line) for line in (target / 'stdout.raw').read_text().splitlines() if line.strip()]
    threads = [e for e in events if e.get('type') == 'thread.started']
    turns = [e for e in events if e.get('type') == 'turn.completed']
    usage = turns[0].get('usage') if len(turns) == 1 else None
    items = [e['item'] for e in events if e.get('type') == 'item.completed']
    messages = [i['text'] for i in items if i.get('type') == 'agent_message']
    tool_items = [i for i in items if i.get('type') not in ('agent_message', 'reasoning', 'todo_list')]
    valid = (isinstance(usage, dict) and set(usage) == set(registration['codex_config']['usage_fields'])
             and all(type(v) is int and v >= 0 for v in usage.values()))
    record.update({'session_id': threads[0].get('thread_id') if len(threads) == 1 else None,
                   'thread_started_count': len(threads), 'turn_started_count': sum(e.get('type') == 'turn.started' for e in events),
                   'turn_completed_count': len(turns), 'usage_fields': sorted(usage) if isinstance(usage, dict) else None,
                   'usage': usage, 'comparable_total': usage['input_tokens'] + usage['output_tokens'] if valid else None,
                   'non_cached_total': usage['input_tokens'] - usage['cached_input_tokens'] + usage['output_tokens'] if valid else None,
                   'tool_call_count': len(tool_items), 'tool_item_types': [i['type'] for i in tool_items],
                   'jsonl_sha256': sha((target / 'stdout.raw').read_bytes()),
                   'measurement_source': 'codex exec --json / turn.completed.usage'})
    record['cache_background']['cached_input_tokens'] = usage['cached_input_tokens'] if valid else None
    record['shape_valid'] = valid and record['thread_started_count'] == record['turn_started_count'] == record['turn_completed_count'] == 1
    if messages:
        (target / 'final.raw').write_text(messages[-1])
        record['final_sha256'] = sha(messages[-1].encode())
        record['final_bytes'] = len(messages[-1].encode())
        try:
            value = strict_json(messages[-1])
            record['final_schema_validation'] = schema_validate(value, json.loads(schema))
        except ValueError:
            record['final_schema_validation'] = {'valid': False, 'reason': 'not strict JSON'}
    write(target / 'metrics.json', record)
    state['codex_sessions'].append(record)
    if len([s for s in state['codex_sessions'] if s['fixture'] == lane]) == 2:
        pair = [s for s in state['codex_sessions'] if s['fixture'] == lane]
        if all(s['comparable_total'] is not None for s in pair):
            actual = sum(s['comparable_total'] for s in pair)
            state['remaining_budget'] -= actual
            state['completed_pairs'].append({'fixture': lane, 'actual_comparable_usage': actual,
                                             'remaining_budget': state['remaining_budget']})
    write(runtime / 'state.json', state)
    print(json.dumps(record))
    if not record['shape_valid']:
        raise RuntimeError('CODEX_USAGE_SCHEMA_CHANGED')


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'init': init()
    elif mode == 'company': company(sys.argv[2], int(sys.argv[3]), Path(sys.argv[4]))
    elif mode == 'codex': codex(sys.argv[2], sys.argv[3], Path(sys.argv[4]))
    else: raise ValueError('unknown command')
