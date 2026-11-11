"""C3-A experiment wrapper; executable CLI offers zero-network freeze ONLY.

Future run_next requires separate C3-B review/authorization. No retry, fallback,
Codex, or production edits. Scoped monkeypatches require serial, main-thread use.
"""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.request
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT / 'tools/llmtool') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools/llmtool'))
import format_probe as probe

ENDPOINT = 'http://192.0.2.10:4000/v1/chat/completions'
FIELDS = frozenset(('model', 'temperature', 'max_tokens', 'messages', 'chat_template_kwargs'))
GOLD_SHA = '0e54eb372345ba64105512744c09a7cb60027fbebeebd04fd7834f51bbf2e8d8'
FROZEN = HERE.parent / '20261006_round2/stage_a'
FIXTURES = {'L02': FROZEN / 'L02_large_log/fixture.txt',
            'L03': FROZEN / 'L03_large_context/fixture.txt'}
GOLD = FROZEN / 'L03_large_context/gold.json'
SKILLS = {lane: ROOT / ('tools/llmtool/llmtool/skills/' + name + '/SKILL.md')
          for lane, name in (('L02', 'log-triage'), ('L03', 'context-pack'))}
CONFIG = b'''active_provider: company-round3-phase0
providers:
  company-round3-phase0:
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
RUNTIME_FILES = ('cli.py', 'entrypoint.py', 'inputs.py', 'prompts.py', 'config.py',
                 'skills.py', 'context.py', 'providers/openai_compatible.py',
                 'errors.py', 'diagnostics.py', 'terminal.py',
                 'providers/base.py', 'providers/__init__.py')


class WrapperIntegrityFailure(BaseException):
    """Safe stop that cannot be converted to a production transport error."""

    def __init__(self):
        super().__init__('WRAPPER_INTEGRITY_FAILURE: STOP BEFORE NETWORK/ACCOUNTING')


class DryRunCaptured(BaseException):
    """Escapes provider Exception handlers, caught only by dry-run harness."""


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition):
    if not condition:
        raise WrapperIntegrityFailure()


def fixture_bytes(lane):
    raw = FIXTURES[lane].read_bytes()
    require(sha(raw) == probe.FIXTURE_SHA256[lane])
    return raw


def user_message(lane, contract=None):
    """No target metadata or gold is available on this construction path."""
    instruction = (HERE / 'instructions' / (lane + '.txt')).read_bytes()
    fixture = fixture_bytes(lane)
    if contract is not None:
        require(sha(instruction) == contract['instruction_sha256'])
        require(sha(fixture) == contract['fixture_sha256'])
        require(sha(SKILLS[lane].read_bytes()) == contract['skill_sha256'])
        require(sha(CONFIG) == contract['config_sha256'])
    raw = instruction + b'\n\n' + fixture
    if contract is not None:
        require(sha(raw) == contract['user_message_sha256'])
    return raw


def validate_request(request, expected_user, contract=None):
    """Full byte/shape admission before reviewed observer or any delegation."""
    try:
        require(type(request) is urllib.request.Request)
        raw = request.data
        require(type(raw) is bytes)
        body = probe._json(raw)  # reviewed bounded, strict duplicate/NaN decoder
        require(type(body) is dict and set(body) == FIELDS)
        require(request.full_url == ENDPOINT and request.get_method() == 'POST')
        require(not any(name.lower() == 'authorization' for name, _ in request.header_items()))
        require(body['model'] == 'gw')
        require(type(body['temperature']) in (int, float) and body['temperature'] == 0)
        require(type(body['max_tokens']) is int and body['max_tokens'] == 4096)
        require(type(body['chat_template_kwargs']) is dict
                and set(body['chat_template_kwargs']) == {'enable_thinking'}
                and body['chat_template_kwargs']['enable_thinking'] is False)
        messages = body['messages']
        require(type(messages) is list and len(messages) == 2)
        require(all(type(item) is dict and set(item) == {'role', 'content'}
                    and type(item['content']) is str for item in messages))
        roles = [item['role'] for item in messages]
        require(roles == ['system', 'user'])
        user = messages[1]['content'].encode('utf-8')
        require(user == expected_user)
        facts = {'request_body_sha256': sha(raw), 'user_message_sha256': sha(user),
                 'message_count': len(messages), 'ordered_roles': roles,
                 'endpoint': request.full_url, 'method': request.get_method(),
                 'allowed_body_fields': sorted(body)}
        if contract is not None:
            require(all(facts[key] == contract[key] for key in facts))
        before = (raw, request.full_url, request.get_method(), tuple(request.header_items()))
        observation = probe.observe_request_without_mutation(request)
        require(before == (request.data, request.full_url, request.get_method(),
                           tuple(request.header_items())))
        return facts, observation
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise WrapperIntegrityFailure() from None


@contextlib.contextmanager
def offline_guard():
    """Fail loudly before socket construction, DNS, subprocess or original open."""
    def forbidden(*args, **kwargs):
        raise WrapperIntegrityFailure()

    with contextlib.ExitStack() as stack:
        for target in ('socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
                       'subprocess.Popen', 'urllib.request.OpenerDirector.open'):
            stack.enter_context(patch(target, side_effect=forbidden))
        yield


def invoke_cli(lane, raw_user, opener_observer):
    """Real CLI/client with isolated HOME; observer replaces instance open only.

    No raw stderr is returned. Metadata and typed safe error code are captured
    directly at the real client boundary, without inferring from display prose.
    """
    from llmtool.entrypoint import main
    from llmtool.errors import LLMToolError
    from llmtool.providers.openai_compatible import OpenAICompatibleClient as Client
    require(Path.cwd().resolve() == ROOT)
    # Local project configuration could override the fixed experiment settings.
    require(not (ROOT / '.llmtool.yaml').exists())
    original_init, original_complete = Client.__init__, Client.complete
    captured = {'metadata': None, 'safe_error_code': None}

    def initialize(client, *args, **kwargs):
        original_init(client, *args, **kwargs)
        original_open = client.opener.open
        client.opener.open = lambda req, **options: opener_observer(req, original_open, options)

    def complete(client, messages):
        try:
            reply = original_complete(client, messages)
        except LLMToolError as exc:
            diagnostic = getattr(exc, 'diagnostic', None)
            code = getattr(diagnostic, 'code', getattr(exc, 'diagnostic_code', None))
            captured['safe_error_code'] = getattr(code, 'value', None)
            raise
        captured['metadata'] = dict(reply.metadata)
        return reply

    buffer = io.BytesIO()
    stdout = io.TextIOWrapper(buffer, encoding='utf-8', newline='\n')
    try:
        with tempfile.TemporaryDirectory(prefix='llmtool-r3-home-') as directory:
            home = Path(directory)
            config = home / '.config/llmtool/config.yaml'
            config.parent.mkdir(parents=True, mode=0o700)
            config.write_bytes(CONFIG); config.chmod(0o600)
            supplied = home / 'supplied.txt'
            supplied.write_bytes(raw_user); supplied.chmod(0o600)
            args = (['ask', '--skill', 'log-triage'] if lane == 'L02'
                    else ['context-pack', '--json'])
            args += ['--provider', 'company-round3-phase0', '--file', str(supplied)]
            with patch.dict(os.environ, {'HOME': str(home)}, clear=True), \
                    patch.object(Client, '__init__', initialize), \
                    patch.object(Client, 'complete', complete), \
                    patch('sys.stdin', io.StringIO('')), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                captured['exit'] = main(args)
            stdout.flush()
            captured['raw_stdout'] = buffer.getvalue()
        return captured
    finally:
        stdout.detach()


def dry_run(lane):
    """Capture a REAL production Request; no original opener or sockets allowed."""
    user = user_message(lane)
    captures = []

    def intercept(request, original_open, options):
        require(not captures and options == {'timeout': 120})
        facts, _ = validate_request(request, user)
        captures.append(facts)
        raise DryRunCaptured()

    with offline_guard():
        try:
            invoke_cli(lane, user, intercept)
        except DryRunCaptured:
            pass
        else:
            raise WrapperIntegrityFailure()
    require(len(captures) == 1)
    facts = captures[0]
    facts.update(instruction_sha256=sha((HERE / 'instructions' / (lane + '.txt')).read_bytes()),
                 fixture_sha256=probe.FIXTURE_SHA256[lane], skill_sha256=sha(SKILLS[lane].read_bytes()),
                 config_sha256=sha(CONFIG), captured_requests=1, network_calls=0, socket_opens=0)
    return facts


def build_preregistration():
    """Gold never opened: known grading digest is copied from the handoff."""
    return {'contract': 'ROUND3_C3_A_V1', 'live_authorization': 'C3-B REQUIRED',
            'lanes': {lane: dry_run(lane) for lane in ('L02', 'L03')},
            'config_sha256': sha(CONFIG), 'l03_evaluation_metadata_sha256': GOLD_SHA,
            'order': list(probe.TRIAL_ORDER), 'ceiling': probe.TOKEN_CEILING,
            'upper_bounds': probe.UPPER_BOUNDS, 'max_requests_per_trial': 1, 'retry': False,
            'artifacts': {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in (
                HERE / 'format_probe.py', HERE / 'test_format_probe.py',
                HERE / 'live_wrapper.py', HERE / 'test_live_wrapper.py',
                *(ROOT / 'tools/llmtool/llmtool' / name for name in RUNTIME_FILES))}}


def verify_registration(registration):
    require(registration['contract'] == 'ROUND3_C3_A_V1')
    require(registration['config_sha256'] == sha(CONFIG))
    require(registration['l03_evaluation_metadata_sha256'] == GOLD_SHA)
    require(registration['order'] == list(probe.TRIAL_ORDER))
    require(registration['ceiling'] == probe.TOKEN_CEILING)
    require(registration['upper_bounds'] == probe.UPPER_BOUNDS)
    require(registration['max_requests_per_trial'] == 1 and registration['retry'] is False)
    expected_paths = {str(path.relative_to(ROOT)) for path in (
        HERE / 'format_probe.py', HERE / 'test_format_probe.py', HERE / 'live_wrapper.py',
        HERE / 'test_live_wrapper.py',
        *(ROOT / 'tools/llmtool/llmtool' / name for name in RUNTIME_FILES))}
    require(set(registration['artifacts']) == expected_paths)
    for filename, digest in registration['artifacts'].items():
        require(sha((ROOT / filename).read_bytes()) == digest)
    for lane in ('L02', 'L03'):
        user_message(lane, registration['lanes'][lane])


def load_preregistration(expected_sha256):
    """Future handoff must pin this manifest digest from independent review."""
    require(type(expected_sha256) is str and re.fullmatch('[0-9a-f]{64}', expected_sha256))
    raw = (HERE / 'phase0_preregistration.json').read_bytes()
    require(sha(raw) == expected_sha256)
    registration = probe._json(raw)
    verify_registration(registration)
    return registration


def admitted_open(request, original_open, options, lane, expected_manifest_sha256, state, trial_id):
    """FUTURE live observer; tests delegate to in-memory fake responses only."""
    registration = load_preregistration(expected_manifest_sha256)
    require(options == {'timeout': 120})
    require(lane == trial_id.split('-')[0])
    user = user_message(lane, registration['lanes'][lane])
    _, observation = validate_request(request, user, registration['lanes'][lane])
    # Re-pin at the last admission boundary, including any drift during observer
    # validation. No caller-provided registration can authorize delegation.
    require(load_preregistration(expected_manifest_sha256) == registration)
    state.mark_request_emitted(trial_id, observation)  # no retry / before potential consumption
    return original_open(request, **options)


def validated_usage(metadata):
    if type(metadata) is not dict:
        return None
    names = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    if any(type(metadata.get(name)) is not int or metadata[name] < 0 for name in names):
        return None
    if metadata['total_tokens'] != metadata['prompt_tokens'] + metadata['completion_tokens']:
        return None
    return {name: metadata[name] for name in names}


def failure_reason(safe_code):
    if safe_code == 'MODEL_OUTPUT_INCOMPLETE':
        return safe_code
    if safe_code in ('NETWORK_PROVIDER_UNREACHABLE', 'PROVIDER_TIMEOUT', 'PROVIDER_RUNTIME_ERROR',
                     'PROVIDER_HTTP_ERROR', 'PROVIDER_RATE_LIMITED', 'AUTH_PROVIDER_REJECTED',
                     'MODEL_RESPONSE_INVALID', 'MODEL_RESPONSE_TOO_LARGE'):
        return 'TRANSPORT_OR_REQUEST_FAILURE'
    raise WrapperIntegrityFailure()


def load_target_metadata():
    raw = GOLD.read_bytes()
    require(sha(raw) == GOLD_SHA)  # BEFORE JSON parsing or consuming target_ids
    value = probe._json(raw)
    return {'target_ids': value['target_ids']}


def evaluate_raw(lane, raw):
    """Exact bytes -> digest/scan -> fixture-only anchors -> post-freeze grading."""
    stdout = probe.RawStdout(raw)
    require(stdout.publication_allowed)
    fixture = fixture_bytes(lane)
    if lane == 'L02':
        return probe.evaluate_l02_without_gold(stdout, probe.parse_l02_fixture(fixture))
    require(lane == 'L03')
    anchors = probe.evaluate_l03_anchors_without_gold(stdout, probe.parse_l03_fixture(fixture))
    return probe.evaluate_l03_target_recall_after_freeze(anchors, load_target_metadata)


def reconcile(lane, preserved_raw, evaluation):
    require(type(evaluation) is probe.FrozenEvaluation and evaluation.lane == lane)
    replay = evaluate_raw(lane, preserved_raw)
    require(evaluation.stdout.raw == preserved_raw)
    require(replay.stdout.sha256 == evaluation.stdout.sha256)
    require(replay.result_json.encode('utf-8') == evaluation.result_json.encode('utf-8'))
    require(sha(replay.result_json.encode('utf-8')) == sha(evaluation.result_json.encode('utf-8')))
    return replay


def complete_success(state, trial_id, preserved_path, evaluation, metadata):
    raw = Path(preserved_path).read_bytes()
    final = reconcile(trial_id.split('-')[0], raw, evaluation)
    usage = validated_usage(metadata)
    return state.complete_evaluation(trial_id, final, None if usage is None else usage['total_tokens'])


class Phase0Wrapper:
    """Future serial experiment wrapper; construction sends/creates NOTHING.

    run_next is NOT a C3-A executable command. Separate C3-B review and explicit
    live authorization are required. C3-A tests use fake delegates exclusively.
    """

    def __init__(self, expected_manifest_sha256):
        registration = load_preregistration(expected_manifest_sha256)
        self._expected_manifest_sha256 = expected_manifest_sha256
        self._registration = registration
        self.accounting = probe.Phase0Accounting()
        self.halted = False

    @property
    def expected_manifest_sha256(self):
        return self._expected_manifest_sha256

    @property
    def registration(self):
        # Inspection gets a detached copy, never an execution authority input.
        return json.loads(json.dumps(self._registration))

    def run_next(self, output_directory):
        require(not self.halted)
        try:
            pin = self._expected_manifest_sha256
            registration = load_preregistration(pin)
            self._registration = registration
            trial = self.accounting.begin_next()
            if trial.status == 'NOT_STARTED':
                return trial
            lane = trial.trial_id.split('-')[0]

            def observe(req, original, options):
                return admitted_open(req, original, options, lane, pin,
                                     self.accounting, trial.trial_id)

            outcome = invoke_cli(lane, user_message(lane, registration['lanes'][lane]), observe)
            usage = validated_usage(outcome['metadata'])
            total = None if usage is None else usage['total_tokens']
            if outcome['exit'] != 0:
                return self.accounting.complete_pre_output_failure(
                    trial.trial_id, failure_reason(outcome['safe_error_code']), total)
            # Preserve unedited stdout before digest, scan, parse. Exclusive write.
            raw_path = Path(output_directory) / (trial.trial_id + '.stdout')
            descriptor = os.open(raw_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(outcome['raw_stdout']); stream.flush(); os.fsync(stream.fileno())
            raw = raw_path.read_bytes()
            require(raw == outcome['raw_stdout'])
            final = evaluate_raw(lane, raw)
            return complete_success(self.accounting, trial.trial_id, raw_path, final, outcome['metadata'])
        except BaseException:
            self.halted = True
            raise


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='C3-A ZERO-NETWORK Request freeze only')
    parser.add_argument('--freeze', action='store_true', required=True)
    parser.parse_args()
    destination = HERE / 'phase0_preregistration.json'
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(build_preregistration(), stream, indent=2, sort_keys=True)
        stream.write('\n')
