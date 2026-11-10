"""Claude CLI adapter for the provider-neutral Developer route contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import uuid

from .base import DeveloperRoute, RouteError
from .. import github_identity

_MODEL = re.compile(r'claude-[a-z0-9][a-z0-9.-]{0,120}\Z')
_EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
_QUOTA = re.compile(r'quota|rate.?limit|usage limit|overloaded', re.IGNORECASE)
MAX_PROMPT_BYTES = 65536


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


class ClaudeRoute(DeveloperRoute):
    name = 'claude'
    worker_key = 'claude-developer'

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
        if prompt_size is None or prompt_size > MAX_PROMPT_BYTES:
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
        try:
            obj = json.loads(stdout.decode('utf-8'), object_pairs_hook=_unique_pairs,
                             parse_constant=_reject_constant)
        except (UnicodeError, ValueError, TypeError, RecursionError):
            if _QUOTA.search(stderr.decode('utf-8', 'replace')):
                return {'status': 'error', 'summary': '', 'session_id': None,
                        'usage': None, 'raw': {}}
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
            raise RouteError('CLAUDE_STATUS_FAILED')

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
