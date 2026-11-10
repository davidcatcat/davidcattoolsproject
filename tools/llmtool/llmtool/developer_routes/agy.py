"""AGY CLI adapter for the provider-neutral Developer route contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re

from .base import DeveloperRoute, RouteError
from .. import github_identity

_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z')
_EFFORT = re.compile(r'(?:minimal|low|medium|high)\Z')
_CONVERSATION = re.compile(r'[A-Za-z0-9_-]{8,128}\Z')
MAX_PROMPT_BYTES = 512 * 1024


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


class AgyRoute(DeveloperRoute):
    name = 'agy'
    worker_key = 'agy'

    @property
    def family(self):
        # Route identity is not the model family; review separation uses the
        # actual model recorded on the Developer Task.
        return 'agy'

    def validate_profile(self, model, effort):
        if (type(model) is not str or _MODEL.fullmatch(model) is None or
                type(effort) is not str or _EFFORT.fullmatch(effort) is None):
            raise RouteError('AGY_PROFILE_INVALID')
        return model, effort

    def build_argv(self, executable, request):
        return [executable, '-p', request['prompt'], '--model', request['model'],
                '--effort', request['effort'], '--sandbox', '--output-format', 'json',
                '--print-timeout', '0', '--log-file', str(request['log_path'])]

    def build_env(self, tmpdir):
        env = {key: os.environ[key] for key in (
            'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE',
            'AGY_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if key in os.environ}
        for key in ('HOME', 'AGY_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR'):
            if key in env and (not os.path.isabs(env[key]) or
                               any(ord(char) < 32 for char in env[key])):
                raise RouteError('AGY_ENV_INVALID')
        env['TMPDIR'] = tmpdir
        home = env.get('HOME') or str(Path.home())
        return github_identity.ai_github_env(env, home)

    def validate_request(self, request):
        value = super().validate_request(request)
        prompt = value.get('prompt')
        log_path = value.get('log_path')
        if (type(prompt) is not str or len(prompt.encode('utf-8')) > MAX_PROMPT_BYTES or
                type(log_path) is not str or not log_path or os.path.isabs(log_path) or
                '\\' in log_path or any(part in ('', '.', '..') for part in log_path.split('/')) or
                any(ord(char) < 32 or ord(char) == 127 for char in log_path)):
            raise RouteError('AGY_REQUEST_INVALID')
        if not isinstance(self.config.get('executable'), str):
            raise RouteError('AGY_CONFIG_INVALID')
        value['log_path'] = log_path
        return value

    def parse_output(self, stdout, stderr, log_text):
        try:
            obj = json.loads(stdout.decode('utf-8'), object_pairs_hook=_unique_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (UnicodeError, ValueError, TypeError, RecursionError):
            if re.search(rb'quota|RESOURCE_EXHAUSTED|rate\s*limit', stderr, re.IGNORECASE):
                return {'status': 'error', 'summary': '', 'session_id': None,
                        'usage': None, 'raw': {}}
            raise RouteError('AGY_OUTPUT_INVALID') from None
        if (type(obj) is not dict or type(obj.get('conversation_id')) is not str or
                _CONVERSATION.fullmatch(obj['conversation_id']) is None or
                type(obj.get('status')) is not str):
            raise RouteError('AGY_OUTPUT_INVALID')
        usage_value = obj.get('usage')
        usage = ({key: item for key, item in usage_value.items()
                  if type(key) is str and type(item) is int}
                 if type(usage_value) is dict else {})
        summary = obj.get('response', obj.get('result', ''))
        if type(summary) is not str:
            summary = ''
        return {'status': obj['status'], 'summary': summary[:4000],
                'session_id': obj['conversation_id'], 'usage': usage or None,
                'raw': obj}

    def verified_model(self, log_text, requested):
        names = re.findall(r'model_resolver.*?Resolving model ([^\s]+)', log_text)
        if not names:
            return None
        return names[0] if all(name == names[0] for name in names) else '\0'

    def quota_exhausted(self, parsed, stderr):
        raw = parsed.get('raw', {})
        fields = (raw.get('status'), raw.get('error'), stderr.decode('utf-8', 'replace'))
        return any(type(value) is str and re.search(
            r'quota|RESOURCE_EXHAUSTED|rate\s*limit', value, re.IGNORECASE)
            for value in fields)


def owner_assignment_gate(task_id, owner_ref):
    """Return a gate that re-evaluates the authoritative local dispatch file."""
    from .. import worker_dispatch

    def evaluate():
        decision = worker_dispatch.evaluate('agy', owner_ref, task_id)
        if (getattr(decision, 'allowed', False) is True and
                getattr(decision, 'reason_code', None) in (
                    'OWNER_POLICY_ALLOWED', 'OWNER_ASSIGNMENT_MATCHED')):
            return decision
        return False
    return evaluate
