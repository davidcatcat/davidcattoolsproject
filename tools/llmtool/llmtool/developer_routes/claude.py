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
# #2275: bounds of the typed CLI result fields a failed run may keep.
_SUBTYPE = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
MAX_CLI_TURNS = 1_000_000
MAX_CLI_DURATION_MS = 86_400_000


def cli_result_fields(obj):
    """#2275: typed CLI result fields of a parsed JSON object, else None.

    Returns {subtype, is_error, num_turns, duration_ms, result}. A field of
    the wrong type or out of bounds is None. ``subtype`` and ``result`` are
    provider text (``result`` is the raw result text); the caller must screen
    each before keeping any of it.
    """
    if type(obj) is not dict:
        return None
    subtype = obj.get('subtype')
    is_error = obj.get('is_error')
    turns = obj.get('num_turns')
    duration = obj.get('duration_ms')
    result = obj.get('result')
    return {
        'subtype': (subtype if type(subtype) is str and _SUBTYPE.match(subtype)
                    else None),
        'is_error': is_error if type(is_error) is bool else None,
        'num_turns': (turns if type(turns) is int and 0 <= turns <= MAX_CLI_TURNS
                      else None),
        'duration_ms': (duration if type(duration) is int and
                        0 <= duration <= MAX_CLI_DURATION_MS else None),
        'result': result if type(result) is str else None,
    }


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
    # #2275: cli_result_fields of the last parsed JSON object, even when the
    # output is then refused; None when stdout was not JSON.
    parsed_cli_fields = None

    @property
    def family(self):
        return 'claude'

    def validate_profile(self, model, effort):
        if (type(model) is not str or _MODEL.fullmatch(model) is None or
                type(effort) is not str or effort not in _EFFORTS):
            raise RouteError('CLAUDE_PROFILE_INVALID')
        return model, effort

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
        self.parsed_cli_fields = None
        try:
            obj = json.loads(stdout.decode('utf-8'), object_pairs_hook=_unique_pairs,
                             parse_constant=_reject_constant)
            self.parsed_cli_fields = cli_result_fields(obj)
        except (UnicodeError, ValueError, TypeError, RecursionError):
            # A quota stderr with non-JSON stdout gives the fixed quota-fallback
            # result; validate_status maps it to CLAUDE_QUOTA_EXHAUSTED (#2158).
            # No stderr text is stored or returned.
            if _QUOTA.search(stderr.decode('utf-8', 'replace')):
                return _quota_fallback()
            raise RouteError('CLAUDE_OUTPUT_INVALID') from None
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
