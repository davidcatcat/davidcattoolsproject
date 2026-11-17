"""Claude CLI adapter for the provider-neutral Developer route contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import uuid

from .base import DeveloperRoute, RouteError, resolve_route_max_prompt_bytes, usage_reported
from .. import github_identity

_MODEL = re.compile(r'claude-[a-z0-9][a-z0-9.-]{0,120}\Z')
_EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
_QUOTA = re.compile(r'quota|rate.?limit|usage limit|overloaded|spend.?limit', re.IGNORECASE)
MAX_PROMPT_BYTES = 65536
# #2302: refusal code for a chosen model this provider cannot run.
MODEL_UNSUPPORTED = 'MODEL_UNSUPPORTED'
# #2275: Claude CLI result subtypes kept as structured run evidence. Any other
# subtype string is OTHER; the CLI string itself is never kept.
CLI_SUBTYPES = {
    'success': 'SUCCESS',
    'error_max_turns': 'ERROR_MAX_TURNS',
    'error_during_execution': 'ERROR_DURING_EXECUTION',
    'error_max_budget_usd': 'ERROR_MAX_BUDGET_USD',
    'error_max_structured_output_retries': 'ERROR_MAX_STRUCTURED_OUTPUT_RETRIES',
}
CLI_SUBTYPE_OTHER = 'OTHER'
CLI_MAX_TURNS = 1_000_000
CLI_MAX_DURATION_MS = 86_400_000


def cli_run_fields(obj):
    """#2275: structured fields of one parsed Claude CLI result object.

    Returns a dict with only the keys the object reports with a valid type:
    subtype (a CLI_SUBTYPES value, or OTHER for any other string), is_error
    (bool), num_turns and duration_ms (bounded non-negative ints; bool is not
    an int). A missing or ill-typed field is absent, never invented. Anything
    that is not a CLI object of type "result" gives {}. No text is kept.
    """
    if type(obj) is not dict or obj.get('type') != 'result':
        return {}
    fields = {}
    subtype = obj.get('subtype')
    if type(subtype) is str:
        fields['subtype'] = CLI_SUBTYPES.get(subtype, CLI_SUBTYPE_OTHER)
    if type(obj.get('is_error')) is bool:
        fields['is_error'] = obj['is_error']
    for key, limit in (('num_turns', CLI_MAX_TURNS), ('duration_ms', CLI_MAX_DURATION_MS)):
        value = obj.get(key)
        if type(value) is int and 0 <= value <= limit:
            fields[key] = value
    return fields


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError('invalid JSON constant')


def _is_canonical_uuid(value):
    if type(value) is not str:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _quota_fallback():
    """The fixed result for a quota stderr with non-JSON stdout; no stderr text."""
    return {'status': 'error', 'summary': '', 'session_id': None, 'usage': None,
            'raw': {}}


def _is_quota_fallback(parsed):
    """Only the exact quota-fallback shape. A parsed CLI result always has a
    canonical session_id and a non-empty raw object, so it never matches."""
    try:
        return dict(parsed) == _quota_fallback()
    except Exception:
        return False


class ClaudeRoute(DeveloperRoute):
    name = 'claude'
    worker_key = 'claude-developer'
    # #2275: cli_run_fields() of this route's last parsed stdout; None until
    # parse_output runs, {} when stdout held no CLI result object.
    cli_fields = None

    @property
    def family(self):
        return 'claude'

    def validate_profile(self, model, effort):
        if (type(model) is not str or _MODEL.fullmatch(model) is None or
                type(effort) is not str or effort not in _EFFORTS):
            raise RouteError('CLAUDE_PROFILE_INVALID')
        return model, effort

    def validate_model(self, model):
        """#2302: return model when Claude can run it, else RouteError MODEL_UNSUPPORTED.

        Only a claude-* model name is supported; there is no fallback to
        another model.
        """
        if type(model) is not str or _MODEL.fullmatch(model) is None:
            raise RouteError(MODEL_UNSUPPORTED)
        return model

    def build_argv(self, executable, request):
        # --tools is variadic, so -- ends it before the user prompt.
        return [executable, '-p', '--output-format', 'json', '--model', request['model'],
                '--effort', request['effort'], '--no-session-persistence', '--restricted',
                '--tools', 'Read,Edit,Write,Glob,Grep', '--permission-mode', 'acceptEdits',
                '--permission-prompts', 'none', '--strict-mcp-config', '--', request['prompt']]

    def build_env(self, tmpdir):
        keys = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE',
                'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS')
        env = {key: os.environ[key] for key in keys if key in os.environ}
        config_dir_present = 'config_dir' in self.config
        if config_dir_present:
            env['CLAUDE_CONFIG_DIR'] = self.config['config_dir']
        for key in ('HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS',
                    'CLAUDE_CONFIG_DIR'):
            if key in env and (type(env[key]) is not str or not os.path.isabs(env[key]) or
                               any(ord(char) < 32 or 127 <= ord(char) <= 159
                                   for char in env[key])):
                raise RouteError('CLAUDE_ENV_INVALID')
        env['TMPDIR'] = tmpdir
        home = env.get('HOME') or str(Path.home())
        return github_identity.ai_github_env(env, home)

    def validate_request(self, request):
        value = super().validate_request(request)
        prompt = value.get('prompt')
        try:
            prompt_size = len(prompt.encode('utf-8')) if type(prompt) is str else None
        except UnicodeEncodeError:
            prompt_size = None
        max_prompt_bytes = resolve_route_max_prompt_bytes(self)
        if prompt_size is None or prompt_size > max_prompt_bytes:
            raise RouteError('CLAUDE_REQUEST_INVALID')
        if type(self.config.get('executable')) is not str:
            raise RouteError('CLAUDE_CONFIG_INVALID')
        if 'config_dir' in self.config:
            config_dir = self.config['config_dir']
            if type(config_dir) is not str or not os.path.isabs(config_dir):
                raise RouteError('CLAUDE_CONFIG_INVALID')
        return value

    def parse_output(self, stdout, stderr, log_text):
        self._model_usage = None
        self.cli_fields = {}
        try:
            obj = json.loads(stdout.decode('utf-8'), object_pairs_hook=_unique_pairs,
                             parse_constant=_reject_constant)
        except (UnicodeError, ValueError, TypeError, RecursionError):
            # A quota stderr with non-JSON stdout gives the fixed quota-fallback
            # result; validate_status maps it to CLAUDE_QUOTA_EXHAUSTED (#2158).
            # No stderr text is stored or returned.
            if _QUOTA.search(stderr.decode('utf-8', 'replace')):
                return _quota_fallback()
            raise RouteError('CLAUDE_OUTPUT_INVALID') from None
        # #2275: kept before validation, so a result object that is refused
        # (CLAUDE_OUTPUT_INVALID) still gives its well-typed fields.
        self.cli_fields = cli_run_fields(obj)
        if (type(obj) is not dict or obj.get('type') != 'result' or
                type(obj.get('is_error')) is not bool or type(obj.get('subtype')) is not str or
                not _is_canonical_uuid(obj.get('session_id'))):
            raise RouteError('CLAUDE_OUTPUT_INVALID')
        model_usage = obj.get('modelUsage')
        self._model_usage = model_usage if type(model_usage) is dict else None
        usage = self._normalized_usage(obj.get('usage'))
        result = obj.get('result')
        summary = result[:4000] if type(result) is str else ''
        return {'status': ('DONE' if not obj['is_error'] and obj['subtype'] == 'success'
                           else 'error'),
                'summary': summary, 'session_id': obj['session_id'], 'usage': usage,
                'raw': obj}

    @staticmethod
    def _normalized_usage(value):
        if type(value) is not dict:
            return None
        names = {'input': 'input_tokens', 'output': 'output_tokens',
                 'cache_read': 'cache_read_input_tokens',
                 'cache_creation': 'cache_creation_input_tokens'}
        counts = {}
        for key, field in names.items():
            if field in value:
                count = value[field]
                if type(count) is not int or count < 0:
                    return None
                counts[key] = count
        if 'input' not in counts or 'output' not in counts:
            return None
        cache_read = counts.get('cache_read', 0)
        cache_creation = counts.get('cache_creation', 0)
        prompt = counts['input'] + cache_read + cache_creation
        normalized = {'prompt_tokens': prompt, 'completion_tokens': counts['output'],
                      'total_tokens': prompt + counts['output']}
        if 'cache_read' in counts:
            normalized['cached_prompt_tokens'] = cache_read
        return normalized

    def verified_model(self, log_text, requested):
        model_usage = getattr(self, '_model_usage', None)
        if type(model_usage) is not dict or not model_usage:
            return None
        if any(type(key) is not str or not key.startswith('claude-')
               for key in model_usage):
            return '\0'
        requested_usage = model_usage.get(requested)
        if (type(requested_usage) is dict and
                type(requested_usage.get('outputTokens')) is int and
                requested_usage['outputTokens'] > 0):
            return requested
        candidates = [(details.get('outputTokens'), key)
                      for key, details in model_usage.items() if type(details) is dict and
                      type(details.get('outputTokens')) is int and
                      details['outputTokens'] > 0]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def validate_status(self, parsed):
        if parsed.get('status') != 'DONE':
            # #2158: the quota-fallback result (quota stderr, non-JSON stdout)
            # is a quota refusal, so worker_availability records it. Only the
            # fixed code is kept.
            if _is_quota_fallback(parsed):
                raise RouteError('CLAUDE_QUOTA_EXHAUSTED')
            raise RouteError('CLAUDE_STATUS_FAILED')

    def provider_basis(self, parsed):
        """CLI_USAGE only for a parsed Claude CLI JSON result reporting token usage."""
        raw = parsed.get('raw')
        if (type(raw) is dict and raw.get('type') == 'result' and
                _is_canonical_uuid(raw.get('session_id')) and
                parsed.get('session_id') == raw['session_id'] and
                usage_reported(parsed.get('usage'))):
            return 'CLI_USAGE'
        return None

    def provider_quota_reported(self, parsed):
        """Only a parsed Claude CLI JSON error result with api_error_status 429.

        Stderr text and result text are never evidence; quota_exhausted still
        classifies them as before. The quota-fallback result (raw {}, no
        session) is never evidence.
        """
        raw = parsed.get('raw')
        return (type(raw) is dict and raw.get('type') == 'result' and
                raw.get('is_error') is True and
                _is_canonical_uuid(raw.get('session_id')) and
                parsed.get('session_id') == raw['session_id'] and
                type(raw.get('api_error_status')) is int and
                raw['api_error_status'] == 429)

    def quota_exhausted(self, parsed, stderr):
        if parsed.get('status') == 'DONE':
            return False
        raw = parsed.get('raw')
        if type(raw) is not dict:
            raw = {}
        status = raw.get('api_error_status')
        return (type(status) is int and status == 429 or
                (type(raw.get('result')) is str and bool(_QUOTA.search(raw['result']))) or
                bool(_QUOTA.search(stderr.decode('utf-8', 'replace'))))
