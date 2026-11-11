"""Non-semantic size probes only. One invocation per command; no automatic retry."""

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path('/Users/davidsun/git/davidcataiporject/davidcattoolsproject')
SIZES = (32, 48, 64, 96, 128, 192, 256)
FIELDS = {'input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
          'output_tokens', 'reasoning_output_tokens'}
PREFIX = ('Return exactly SIZE_PREFLIGHT_OK. Do not analyze or summarize the filler. '
          'Do not use tools, read files, or perform any other task.\nFILLER_BEGIN\n')
SUFFIX = '\nFILLER_END\nReturn exactly SIZE_PREFLIGHT_OK.\n'
CONFIG = '''active_provider: company-preflight
providers:
  company-preflight:
    type: openai-compatible
    base_url: http://192.0.2.10:4000
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

# This observer never edits the Request, reads responses, or sends its own traffic.
# The installed llmtool entrypoint remains responsible for every actual request.
COMPANY_OBSERVER = r'''
import hashlib, json, os
from pathlib import Path
from llmtool.providers.openai_compatible import OpenAICompatibleClient
from llmtool.entrypoint import main
original_init = OpenAICompatibleClient.__init__
expected_text = Path(os.environ['PREFLIGHT_PROMPT_FILE']).read_text()
audit_path = Path(os.environ['PREFLIGHT_AUDIT_FILE'])

def audited_init(self, provider, generation, key=None):
    assert provider['type'] == 'openai-compatible'
    assert provider['base_url'] == 'http://192.0.2.10:4000'
    assert provider['model'] == 'gw' and provider['timeout_seconds'] == 120
    assert provider['api_key'] is None and provider['api_key_env'] is None
    assert provider['provider_options'] == {'enable_thinking': False}
    assert generation['temperature'] == 0 and generation['max_tokens'] == 4096
    assert key is None
    original_init(self, provider, generation, key)
    original_open = self.opener.open
    def audited_open(req, *args, **kwargs):
        body = json.loads(req.data)
        assert req.full_url == 'http://192.0.2.10:4000/v1/chat/completions'
        assert req.get_method() == 'POST'
        assert not req.has_header('Authorization')
        assert body['chat_template_kwargs'] == {'enable_thinking': False}
        assert body['model'] == 'gw' and body['temperature'] == 0 and body['max_tokens'] == 4096
        users = [m['content'] for m in body['messages'] if m['role'] == 'user']
        assert users == [expected_text]
        audit = {'resolved_profile_matches': True, 'enable_thinking': False,
                 'actual_payload_field': 'chat_template_kwargs.enable_thinking',
                 'request_model': body['model'], 'temperature': body['temperature'],
                 'max_tokens': body['max_tokens'], 'authorization_header_present': False,
                 'user_message_sha256': hashlib.sha256(users[0].encode()).hexdigest(),
                 'request_body_sha256': hashlib.sha256(req.data).hexdigest(),
                 'observer_changed_request': False}
        with audit_path.open('a') as f:
            f.write(json.dumps(audit, sort_keys=True) + '\n')
        return original_open(req, *args, **kwargs)
    self.opener.open = audited_open

OpenAICompatibleClient.__init__ = audited_init
raise SystemExit(main())
'''


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def filler(kib):
    if kib not in SIZES:
        raise ValueError('undeclared size')
    # Lowercase alphabet rotations, spaces, newline. No semantic words/IDs/answers.
    alphabet = b'abcdefghijklmnopqrstuvwxyz'
    line = b' '.join((alphabet + alphabet)[i:i + 8] for i in range(0, 26, 2)) + b'\n'
    size = kib * 1024
    return (line * ((size + len(line) - 1) // len(line)))[:size]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def capture(argv, target, env=None, prompt=None):
    started = time.monotonic()
    with (target / 'stdout.raw').open('w') as out, (target / 'stderr.raw').open('w') as err:
        process = subprocess.Popen(argv, cwd=ROOT, env=env, text=True,
                                   stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True)
        timed_out = False
        try:
            process.communicate(prompt, timeout=600)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    return process.returncode, round(time.monotonic() - started, 3), timed_out


def main():
    mode, kib, attempt, location = sys.argv[1:]
    runtime = Path(location)
    kib, attempt = int(kib), int(attempt)
    if mode == 'init':
        runtime.mkdir(exist_ok=True)
        home = runtime / 'company-home'
        config_path = home / '.config/llmtool/config.yaml'
        config_path.parent.mkdir(parents=True, mode=0o700)
        config_path.write_text(CONFIG)
        config_path.chmod(0o600)
        (runtime / 'company_observer.py').write_text(COMPANY_OBSERVER)
        for size in SIZES:
            raw = filler(size)
            (runtime / ('filler-' + str(size) + '.txt')).write_bytes(raw)
            (runtime / ('prompt-' + str(size) + '.txt')).write_text(PREFIX + raw.decode() + SUFFIX)
        print(json.dumps({'config_sha256': digest(config_path.read_bytes()),
                          'helper_sha256': digest(Path(__file__).read_bytes()),
                          'company_observer_sha256': digest(COMPANY_OBSERVER.encode())}))
        return 0
    assert mode in ('codex', 'company') and kib in SIZES and attempt in (1, 2)
    target = runtime / (mode + '-' + str(kib) + '-attempt-' + str(attempt))
    target.mkdir()  # Never overwrite or silently repeat an attempted probe.
    raw = (runtime / ('filler-' + str(kib) + '.txt')).read_bytes()
    assert raw == filler(kib)
    prompt = (runtime / ('prompt-' + str(kib) + '.txt')).read_text()
    record = {'lane': mode, 'size_kib': kib, 'attempt': attempt,
              'requested_bytes': kib * 1024, 'actual_bytes': len(raw), 'filler_sha256': digest(raw),
              'prompt_bytes': len(prompt.encode()), 'prompt_sha256': digest(prompt.encode()),
              'repo_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'helper_sha256': digest(Path(__file__).read_bytes())}
    if mode == 'codex':
        version = subprocess.check_output(['codex', '--version'], cwd=ROOT, text=True).strip()
        record['codex_version'] = version
        if version != 'codex-cli 0.159.2':
            record.update({'result': 'STOP', 'failure_category': 'CODEX_USAGE_SCHEMA_CHANGED'})
            write_json(target / 'metrics.json', record)
            print(json.dumps(record)); return 4
        argv = ['codex', 'exec', '--json', '--ephemeral', '--ignore-user-config', '--ignore-rules',
                '--model', 'gpt-6.1-sol', '--config', 'model_reasoning_effort="high"',
                '--config', 'service_tier="default"', '--config', 'approval_policy="never"',
                '--sandbox', 'read-only', '--cd', str(ROOT), '-']
        code, seconds, timed_out = capture(argv, target, prompt=prompt)
        events = [json.loads(line) for line in (target / 'stdout.raw').read_text().splitlines() if line.strip()]
        turns = [e for e in events if e.get('type') == 'turn.completed']
        usage = turns[0].get('usage') if len(turns) == 1 else None
        items = [e['item'] for e in events if e.get('type') == 'item.completed']
        messages = [i['text'] for i in items if i.get('type') == 'agent_message']
        tools = [i for i in items if i.get('type') not in ('agent_message', 'reasoning', 'todo_list')]
        record.update({'thread_started_count': sum(e.get('type') == 'thread.started' for e in events),
                       'turn_started_count': sum(e.get('type') == 'turn.started' for e in events),
                       'turn_completed_count': len(turns), 'usage_fields': sorted(usage) if isinstance(usage, dict) else [],
                       'usage': usage, 'tool_call_count': len(tools),
                       'marker_exact': bool(messages) and messages[-1].strip() == 'SIZE_PREFLIGHT_OK',
                       'jsonl_sha256': digest((target / 'stdout.raw').read_bytes()),
                       'measurement_source': 'codex exec --json / turn.completed.usage'})
        shape = (isinstance(usage, dict) and set(usage) == FIELDS and len(turns) == 1
                 and record['thread_started_count'] == record['turn_started_count'] == 1
                 and all(type(usage[k]) is int and usage[k] >= 0 for k in FIELDS))
        record['comparable_total'] = usage['input_tokens'] + usage['output_tokens'] if shape else None
        error_text = json.dumps([e for e in events if e.get('type') in ('error', 'turn.failed')]) + (target / 'stderr.raw').read_text()
        if (usage is not None and not shape) or (code == 0 and not shape):
            category = 'CODEX_USAGE_SCHEMA_CHANGED'
        elif code == 0 and record['marker_exact'] and not tools:
            category = None
        elif re.search(r'context.{0,35}(exceed|limit|too (long|large))|context_window_exceeded|maximum context|payload too large|413', error_text, re.I):
            category = 'CAPACITY_REJECTION'
        elif timed_out or re.search(r'timed? out|connection|unreachable|temporarily unavailable|502|503|504', error_text, re.I):
            category = 'TRANSIENT_TRANSPORT_FAILURE'
        else:
            category = 'UNCLASSIFIED_FAILURE' if code else 'MARKER_OR_TOOL_CONTRACT_FAILURE'
    else:
        env = {k: v for k, v in os.environ.items() if not k.startswith('LLMTOOL_')}
        env.update({'HOME': str(runtime / 'company-home'), 'PYTHONDONTWRITEBYTECODE': '1',
                    'PREFLIGHT_PROMPT_FILE': str(runtime / ('prompt-' + str(kib) + '.txt')),
                    'PREFLIGHT_AUDIT_FILE': str(target / 'request_audit.jsonl')})
        config_path = runtime / 'company-home/.config/llmtool/config.yaml'
        assert config_path.read_text() == CONFIG
        argv = [str(ROOT / 'tools/llmtool/.venv/bin/python'), '-B', str(runtime / 'company_observer.py'),
                'ask', '--provider', 'company-preflight', '--file', env['PREFLIGHT_PROMPT_FILE'], '--verbose']
        code, seconds, timed_out = capture(argv, target, env=env)
        stdout = (target / 'stdout.raw').read_text()
        stderr = (target / 'stderr.raw').read_text()
        metadata = {}
        for key in ('model', 'prompt_tokens', 'completion_tokens', 'total_tokens', 'elapsed_seconds'):
            match = re.search(r'^' + key + r'=(.+)$', stderr, re.M)
            if match:
                metadata[key] = int(match[1]) if key.endswith('tokens') else float(match[1]) if key == 'elapsed_seconds' else match[1]
        audit_path = target / 'request_audit.jsonl'
        audit = [json.loads(x) for x in audit_path.read_text().splitlines()] if audit_path.exists() else []
        verified = (len(audit) == 1 and audit[0]['enable_thinking'] is False
                    and audit[0]['user_message_sha256'] == digest(prompt.encode()))
        record.update({'temporary_config_sha256': digest(config_path.read_bytes()),
                       'request_audit': audit, 'enable_thinking_false_verified': verified,
                       'marker_exact': stdout.strip() == 'SIZE_PREFLIGHT_OK', 'provider_metadata': metadata,
                       'usage_source': 'llmtool --verbose / normalized Reply.metadata',
                       'usage_unknown': not all(k in metadata for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')),
                       'stdout_sha256': digest(stdout.encode())})
        match = re.search(r'code=([A-Z_]+)', stderr)
        record['safe_error_code'] = match[1] if match else None
        if not verified:
            category = 'ENABLE_THINKING_NOT_VERIFIED'
        elif code == 0 and record['marker_exact']:
            category = None
        elif record['safe_error_code'] in ('PROVIDER_TIMEOUT', 'NETWORK_PROVIDER_UNREACHABLE') or timed_out:
            category = 'TRANSIENT_TRANSPORT_FAILURE'
        elif re.search(r'HTTP 413', stderr):
            category = 'CAPACITY_REJECTION'
        elif record['safe_error_code'] == 'MODEL_OUTPUT_INCOMPLETE':
            category = 'OUTPUT_LENGTH_REJECTION'
        else:
            category = 'UNCLASSIFIED_FAILURE' if code else 'MARKER_CONTRACT_FAILURE'
    record.update({'argv': argv, 'exit': code, 'wrapper_elapsed_seconds': seconds,
                   'result': 'PASS' if category is None else 'FAIL', 'failure_category': category,
                   'wrapper_timeout': timed_out})
    write_json(target / 'metrics.json', record)
    print(json.dumps(record))
    return 0 if category is None else 4 if category in ('CODEX_USAGE_SCHEMA_CHANGED', 'ENABLE_THINKING_NOT_VERIFIED') else 3


if __name__ == '__main__':
    raise SystemExit(main())
