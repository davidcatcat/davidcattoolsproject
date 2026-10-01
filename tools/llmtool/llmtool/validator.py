"""Deterministic text/data assessment. Structural support is not factual truth."""

import json
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .context import ContextPacket, parse_context_packet
from .errors import LLMToolError

COMMAND_LIMIT = 8 * 1024
PACKET_ITEM_LIMIT = 1024


class CommandRisk(str, Enum):
    SAFE_READ = 'SAFE_READ'
    SENSITIVE_READ = 'SENSITIVE_READ'
    MUTATING = 'MUTATING'
    DESTRUCTIVE = 'DESTRUCTIVE'
    UNKNOWN = 'UNKNOWN'


class Severity(str, Enum):
    INFO = 'INFO'
    WARNING = 'WARNING'
    ERROR = 'ERROR'


@dataclass(frozen=True)
class CommandAssessment:
    command: str
    risk: CommandRisk
    reason: str

    def to_dict(self):
        return {'command': self.command, 'risk': self.risk.value, 'reason': self.reason}

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=True, indent=2)


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    severity: Severity
    message: str
    category: str
    locator: Optional[str] = None

    def to_dict(self):
        return {'code': self.code, 'severity': self.severity.value,
                'message': self.message, 'category': self.category, 'locator': self.locator}


@dataclass(frozen=True)
class ValidationResult:
    findings: tuple[ValidationFinding, ...] = ()
    command_assessments: tuple[CommandAssessment, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'findings', tuple(self.findings))
        object.__setattr__(self, 'command_assessments', tuple(self.command_assessments))

    @property
    def valid(self):
        return not any(f.severity == Severity.ERROR for f in self.findings)

    def to_dict(self):
        return {'valid': self.valid, 'findings': [f.to_dict() for f in self.findings],
                'command_assessments': [a.to_dict() for a in self.command_assessments]}

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=True, indent=2)


# A risk floor for compounds, not a promise that unknown components are safe.
RISK_ORDER = {CommandRisk.SAFE_READ: 0, CommandRisk.UNKNOWN: 1,
              CommandRisk.SENSITIVE_READ: 2, CommandRisk.MUTATING: 3, CommandRisk.DESTRUCTIVE: 4}
REASONS = {
    CommandRisk.SAFE_READ: 'recognized narrow read operation; no execution permission is granted',
    CommandRisk.SENSITIVE_READ: 'operation may expose credentials, private configuration or sensitive content',
    CommandRisk.MUTATING: 'operation can change files, repository state or running services',
    CommandRisk.DESTRUCTIVE: 'operation can delete data or disrupt systems or processes',
    CommandRisk.UNKNOWN: 'unsupported or ambiguous operation/syntax; safety is not established',
}


def requires_approval(risk):
    """Conservative future policy only. No approval mechanism is activated."""
    return risk != CommandRisk.SAFE_READ


def _segments(text):
    """Split only unquoted shell separators; retain quoting for shlex tokens."""
    segments, operators, start = [], [], 0
    quote = None
    escaped = False
    i = 0
    while i < len(text):
        char = text[i]
        if escaped:
            escaped = False
        elif char == '\\' and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in '|&;<>':
            segments.append(text[start:i])
            j = i + 1
            while j < len(text) and text[j] == char:
                j += 1
            operators.append(text[i:j])
            start = j
            i = j - 1
        i += 1
    segments.append(text[start:])
    return segments, operators, bool(quote or escaped)


KUBECTL_CONNECTION_OPTIONS = frozenset((
    '-s', '--server', '--token', '--kubeconfig', '--kuberc', '--as',
    '--as-group', '--as-uid', '--as-user-extra', '--client-certificate',
    '--client-key', '--certificate-authority', '--username', '--password',
    '--insecure-skip-tls-verify', '--tls-server-name', '--proxy-url',
    '--cluster', '--user',
))

def sensitive_path(value):
    path = value.lower()
    basename = path.rsplit('/', 1)[-1]
    return (any(word in path for word in ('credential', 'secret', 'token', 'password', 'api_key', 'api-key', 'config', 'kubeconfig',
                                         '.ssh', '.aws/', '.kube/', 'keychain', 'keystore',
                                         '.password-store', '.netrc', '.npmrc', '.pypirc',
                                         'private-key', 'private_key', 'id_rsa', 'id_ed25519', 'id_dsa', 'id_ecdsa'))
            or basename.startswith('.env') or basename.endswith(('.pem', '.key', '.p12', '.pfx', '.conf'))
            or basename in ('application.yml', 'application.yaml', 'application.properties')
            or ('config' in basename and (path.startswith('/etc/') or basename.endswith(('.yaml', '.yml', '.json', '.ini', '.conf')))))


def connection_override(tokens):
    """Detect explicit Kubernetes destination/identity overrides; no policy."""
    return any(a.split('=', 1)[0] in KUBECTL_CONNECTION_OPTIONS
               or (a.startswith('-s') and not a.startswith('--')) for a in tokens)


def secret_resource(value):
    """Recognize literal Secret resources and raw API path components."""
    return any(part.lower().split('.', 1)[0] in ('secret', 'secrets')
               for item in value.split(',') for part in item.split('/'))


def credential_display(command, include_connection=True):
    """Bounded literal exposure facts shared by A6 and suggestion filtering.

    No expansions, aliases, shell interpretation or authorization are performed.
    """
    if not isinstance(command, str) or len(command) > COMMAND_LIMIT:
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    names = [t.rsplit('/', 1)[-1] for t in tokens]
    displays = ('cat', 'grep', 'head', 'tail', 'sed', 'awk', 'less', 'more',
                'base64', 'hexdump', 'xxd', 'strings', 'od')
    if any(n in displays for n in names) and any(sensitive_path(t) for t in tokens):
        return True
    if 'printenv' in names:
        return True
    for i, name in enumerate(names):
        tail = tokens[i + 1:]
        if name == 'env' and not tail:
            return True
        if name in ('kubectl', 'oc'):
            if include_connection and connection_override(tail):
                return True
            if 'xargs' in names[:i] and 'get' in tail:
                return True  # Runtime-supplied resource selections are opaque.
            if 'get' in tail and any(secret_resource(t) for t in tail):
                return True
            if ('get' in tail and any('jsonpath' in t or 'template' in t for t in tail)
                    and any('env' in t.lower() or 'data' in t.lower() for t in tail)):
                return True
        if name == 'aws' and tail[:2] == ['secretsmanager', 'get-secret-value']:
            return True
        if name == 'vault' and tail[:2] == ['kv', 'get']:
            return True
        if name == 'helm' and tail[:2] == ['get', 'values']:
            return True
        if name == 'git' and 'config' in tail and any(t in ('--list', '-l') for t in tail):
            return True
    return False


def _simple(text):
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return CommandRisk.UNKNOWN
    if not tokens:
        return CommandRisk.UNKNOWN
    # No expansions, substitutions, scripts, wrappers or interpreter analysis.
    if any(c in text for c in ('$','`','(',')')):
        return CommandRisk.UNKNOWN
    if any(c in text for c in ('{', '}')):
        # A literal numeric stash reference is Git data, not shell brace syntax.
        git_args = tokens[1:]
        while len(git_args) >= 2 and git_args[0] in ('-C', '-c'):
            git_args = git_args[2:]
        if (tokens[0] not in ('git', '/usr/bin/git', '/bin/git')
                or git_args[:2] != ['stash', 'drop'] or len(git_args) != 3
                or not re.fullmatch(r'stash@\{[0-9]+\}', git_args[2])):
            return CommandRisk.UNKNOWN
    name, args = tokens[0], tokens[1:]
    if name.rsplit('/', 1)[-1] == 'sudo':
        if not args or args[0].startswith('-'):
            return CommandRisk.UNKNOWN
        inner = _simple(shlex.join(args))
        return max((CommandRisk.UNKNOWN, inner), key=RISK_ORDER.__getitem__)
    # Only known conventional absolute binary locations; arbitrary aliases stay unknown.
    if name.rsplit('/', 1)[0] in ('/bin', '/usr/bin', '/usr/sbin', '/sbin'):
        name = name.rsplit('/', 1)[-1]
    if name.lower() == 'drop' and args and args[0].lower() in ('database', 'table'):
        return CommandRisk.DESTRUCTIVE
    if name in ('rm', 'truncate', 'shutdown', 'reboot', 'halt', 'poweroff', 'mkfs', 'newfs', 'format') or name.startswith('mkfs.'):
        return CommandRisk.DESTRUCTIVE
    if name == 'kill':
        return CommandRisk.DESTRUCTIVE if any(a in ('-9', '-KILL', '-SIGKILL') for a in args) and any(not a.startswith('-') for a in args) else CommandRisk.UNKNOWN
    if name in ('touch', 'mkdir', 'cp', 'mv', 'chmod', 'chown', 'chgrp', 'ln', 'install', 'tee', 'ansible-playbook'):
        return CommandRisk.MUTATING
    if name in ('rmdir', 'unlink', 'shred'):
        return CommandRisk.DESTRUCTIVE
    if name == 'dd':
        return CommandRisk.DESTRUCTIVE if any(a.startswith('of=') for a in args) else CommandRisk.UNKNOWN
    if name == 'find':
        return CommandRisk.DESTRUCTIVE if '-delete' in args else CommandRisk.UNKNOWN
    if name == 'docker' and args and args[0] == 'rm':
        return CommandRisk.DESTRUCTIVE
    if name == 'sed' and any(a == '-i' or a == '--in-place' or a.startswith('--in-place=') or (a.startswith('-') and not a.startswith('--') and 'i' in a[1:]) for a in args):
        return CommandRisk.MUTATING
    if name == 'env' and args:
        return CommandRisk.UNKNOWN
    if name in ('env', 'printenv'):
        return CommandRisk.SENSITIVE_READ
    if name in ('cat', 'head', 'tail', 'less', 'more', 'grep', 'rg', 'sed', 'awk', 'strings'):
        return CommandRisk.SENSITIVE_READ if any(sensitive_path(a) for a in args) else CommandRisk.UNKNOWN
    if name == 'security' and args and args[0] in ('find-generic-password', 'find-internet-password', 'dump-keychain'):
        return CommandRisk.SENSITIVE_READ
    if name == 'aws' and args[:2] == ['configure', 'export-credentials']:
        return CommandRisk.SENSITIVE_READ
    if name == 'git':
        # A5's fixed-argv Git Intelligence is hardened separately. Arbitrary
        # shell-level Git commands remain approval-requiring, including reads.
        if not args:
            return CommandRisk.UNKNOWN
        while args and args[0] in ('-C', '-c'):
            if len(args) < 2:
                return CommandRisk.UNKNOWN
            args = args[2:]
        if not args or args[0].startswith('-'):
            return CommandRisk.UNKNOWN
        operation, options = args[0], args[1:]
        if operation == 'reset':
            if any(len(a) > 2 and a.startswith('--') and '--hard'.startswith(a) for a in options):
                return CommandRisk.DESTRUCTIVE
            return CommandRisk.MUTATING
        if operation == 'clean':
            return CommandRisk.DESTRUCTIVE if any(a.startswith('-') and not a.startswith('--') and 'f' in a for a in options) or any(len(a) > 2 and a.startswith('--') and '--force'.startswith(a) for a in options) else CommandRisk.UNKNOWN
        if operation == 'branch':
            return CommandRisk.DESTRUCTIVE if any(a.startswith('-') and not a.startswith('--') and 'D' in a[1:] for a in options) else CommandRisk.UNKNOWN
        if operation == 'checkout' and '--' in options and options.index('--') < len(options) - 1:
            return CommandRisk.DESTRUCTIVE
        # Only literal flags before the option terminator are options.
        flags = options[:options.index('--')] if '--' in options else options
        if operation == 'checkout' and any(a in ('-f', '--force') for a in flags):
            return CommandRisk.DESTRUCTIVE
        if operation == 'switch' and '--discard-changes' in flags:
            return CommandRisk.DESTRUCTIVE
        if operation == 'restore':
            known = ('--staged', '-S', '--worktree', '-W')
            if any(a.startswith('-') and a not in known for a in flags):
                return CommandRisk.UNKNOWN
            paths = [a for a in flags if a not in known]
            if '--' in options:
                paths += options[options.index('--') + 1:]
            if not paths:
                return CommandRisk.UNKNOWN
            if any(a in ('--worktree', '-W') for a in flags) or not any(a in ('--staged', '-S') for a in flags):
                return CommandRisk.DESTRUCTIVE
            return CommandRisk.MUTATING
        if operation == 'stash' and (options == ['clear'] or options == ['drop']
                or (len(options) == 2 and options[0] == 'drop'
                    and re.fullmatch(r'(?:[0-9]+|stash@\{[0-9]+\})', options[1]))):
            return CommandRisk.DESTRUCTIVE
        if operation in ('add', 'commit', 'push', 'pull', 'fetch', 'merge', 'rebase', 'checkout', 'switch', 'restore', 'stash', 'tag'):
            if operation == 'push' and (any(a == '-f' or a.startswith('--force') for a in options) or any(a.startswith(':') or (a.startswith('+') and len(a) > 1) for a in options)):
                return CommandRisk.DESTRUCTIVE
            return CommandRisk.MUTATING
        if any(a == '--output' or a.startswith('--output=') for a in options):
            return CommandRisk.MUTATING
        if operation == 'diff' and all(a in ('--staged', '--cached', '--stat', '--name-only', '--no-ext-diff', '--no-textconv', '--no-color') for a in options):
            return CommandRisk.UNKNOWN if '--stat' in options or '--name-only' in options else CommandRisk.SENSITIVE_READ
        if operation == 'log' and all(a in ('--oneline', '--no-decorate') or re.fullmatch(r'-[0-9]+|--max-count=[0-9]+', a) for a in options):
            return CommandRisk.UNKNOWN if '--oneline' in options else CommandRisk.SENSITIVE_READ
        if operation == 'show' and (not options or options == ['HEAD']):
            return CommandRisk.SENSITIVE_READ
        return CommandRisk.UNKNOWN
    if name == 'kubectl':
        # Destination, identity and impersonation overrides can forward trusted
        # context credentials elsewhere. Never strip them into SAFE_READ.
        overrides = KUBECTL_CONNECTION_OPTIONS
        if connection_override(args):
            remaining = []
            index = 0
            while index < len(args):
                option = args[index].split('=', 1)[0]
                if option in overrides or option in ('-n', '--namespace', '--context'):
                    if '=' in args[index] or option == '--insecure-skip-tls-verify':
                        index += 1
                    elif index + 1 < len(args):
                        index += 2
                    else:
                        return CommandRisk.UNKNOWN
                elif args[index].startswith('-s') and not args[index].startswith('--'):
                    index += 1
                else:
                    remaining.append(args[index])
                    index += 1
            return max(CommandRisk.UNKNOWN, _simple('kubectl ' + shlex.join(remaining)), key=RISK_ORDER.get)
        while args and args[0] in ('-n', '--namespace', '-s', '--server', '--context'):
            if len(args) < 2:
                return CommandRisk.UNKNOWN
            args = args[2:]
        if args and any(args[0].startswith(prefix + '=') for prefix in ('--namespace', '--server', '--context')):
            args = args[1:]
        if not args:
            return CommandRisk.UNKNOWN
        if args[0] == 'delete':
            return CommandRisk.DESTRUCTIVE
        if args[0] in ('apply', 'patch', 'edit', 'scale', 'create', 'replace', 'set', 'label', 'annotate') or args[:2] == ['rollout', 'restart']:
            return CommandRisk.MUTATING
        if args[0] == 'get' and len(args) >= 2:
            resource = args[1].lower()
            if secret_resource(resource):
                return CommandRisk.SENSITIVE_READ
            if resource in ('pods', 'pod', 'deployments', 'deployment', 'events', 'event'):
                if len(args) == 2 or (len(args) == 3 and re.fullmatch(r'[a-z0-9][a-z0-9.-]*', args[2])):
                    return CommandRisk.SAFE_READ
                if '-o' in args or any(a.startswith('--output') or a.startswith('-o') for a in args[2:]):
                    return CommandRisk.SENSITIVE_READ
        if args[:2] == ['describe', 'pod']:
            return CommandRisk.SENSITIVE_READ
        if args[:2] == ['rollout', 'status'] and len(args) == 3 and re.fullmatch(r'(deployment|daemonset|statefulset)/[a-z0-9][a-z0-9.-]*', args[2]):
            return CommandRisk.SAFE_READ
        return CommandRisk.UNKNOWN
    if name == 'systemctl' and args:
        if args[0] in ('restart', 'stop', 'start', 'reload', 'enable', 'disable'):
            return CommandRisk.MUTATING
        if args[0] == 'status':
            return CommandRisk.SENSITIVE_READ
    if name == 'terraform' and args:
        if args[0] == 'destroy':
            return CommandRisk.DESTRUCTIVE
        if args[0] in ('apply', 'import', 'refresh'):
            return CommandRisk.MUTATING
    if name == 'pwd' and not args:
        return CommandRisk.SAFE_READ
    if name in ('ls', 'stat'):
        allowed = ('-l', '-a', '-la', '-al', '-h', '-lh', '-d', '--') if name == 'ls' else ('--',)
        if all(a in allowed or (not a.startswith('-') and not any(c in a for c in '*?[')) for a in args):
            return CommandRisk.SAFE_READ
    if name == 'ps':
        return CommandRisk.SENSITIVE_READ
    return CommandRisk.UNKNOWN


def classify_command(command):
    """Classify bounded simple argv/compounds as data; never interpret input."""
    if not isinstance(command, str):
        raise LLMToolError('command must be text')
    if len(command) > COMMAND_LIMIT:
        raise LLMToolError('command exceeds 8 KiB')
    try:
        raw = command.encode('utf-8')
    except UnicodeError:
        raise LLMToolError('command must be UTF-8 text') from None
    if len(raw) > COMMAND_LIMIT:
        raise LLMToolError('command exceeds 8 KiB')
    # Terminal controls/bidi text can disguise separators. Replace them with a
    # separator for conservative component analysis and retain UNKNOWN as a floor.
    unsafe_controls = any((not c.isprintable() and c != '\t') or c in '\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069' for c in command)
    analysis = ''.join(';' if ((not c.isprintable() and c != '\t') or c in '\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069') else c for c in command)
    parts, operators, malformed = _segments(analysis)
    risks = []
    for index, part in enumerate(parts):
        if index and operators[index-1] in ('>', '>>', '<'):
            # Redirect target is data, not a second command.
            risks.append(CommandRisk.MUTATING if operators[index-1] != '<' else
                         _simple('cat ' + part))
        else:
            risks.append(_simple(part))
    if malformed or any(op not in ('|', '&&', '||', ';', '>', '>>', '<') for op in operators):
        risks.append(CommandRisk.UNKNOWN)
    if operators and (any(not part.strip() for part in parts) or any(op in ('>', '>>', '<') for op in operators)):
        risks.append(CommandRisk.UNKNOWN)
    risk = max(risks, key=RISK_ORDER.__getitem__)
    if credential_display(command, include_connection=False):
        risk = max((risk, CommandRisk.SENSITIVE_READ), key=RISK_ORDER.__getitem__)
    if unsafe_controls:
        risk = max((risk, CommandRisk.UNKNOWN), key=RISK_ORDER.__getitem__)
    return CommandAssessment(command, risk, REASONS[risk])


def assess_verify_next(packet):
    """Preserve each suggestion; prose and unsupported syntax remain UNKNOWN."""
    _bounded_packet(packet)
    assessments = []
    for text in packet.verify_next:
        try:
            assessments.append(classify_command(text))
        except LLMToolError:
            assessments.append(CommandAssessment(text, CommandRisk.UNKNOWN, REASONS[CommandRisk.UNKNOWN]))
    return tuple(assessments)


def _bounded_packet(packet):
    if type(packet) is not ContextPacket:
        raise LLMToolError('validator requires a ContextPacket')
    categories = (packet.facts, packet.evidence, packet.inferences,
                  packet.hypotheses, packet.unknowns, packet.verify_next)
    if sum(len(items) for items in categories) > PACKET_ITEM_LIMIT:
        raise LLMToolError('validator packet exceeds 1024-item limit')
    # Bound content before allocating a complete serialized copy.
    from .context import PACKET_LIMIT
    size = 0
    for index, items in enumerate(categories):
        for item in items:
            texts = (item.statement, item.source, item.locator or '') if index == 1 else (item,)
            for text in texts:
                if len(text) > PACKET_LIMIT:
                    raise LLMToolError('context packet output exceeds 1 MiB')
                size += len(text.encode('utf-8'))
                if size > PACKET_LIMIT:
                    raise LLMToolError('context packet output exceeds 1 MiB')
    parse_context_packet(packet.to_json())


def validate_context_packet(packet):
    """Check support presence and exact category collisions, not claim truth."""
    # Reuse A4's schema/UTF-8/depth/size contract also for constructed packets.
    _bounded_packet(packet)
    findings = []
    def add(code, severity, message, category, locator=None):
        findings.append(ValidationFinding(code, severity, message, category, locator))
    if not packet.evidence and (packet.facts or packet.inferences):
        add('EVIDENCE_MISSING', Severity.ERROR, 'facts or inferences exist but the packet has no evidence', 'evidence')
    if packet.inferences and not packet.facts and not packet.evidence:
        add('INFERENCE_SUPPORT_MISSING', Severity.ERROR, 'inferences have no supporting fact or evidence available', 'inferences')
    categories = ('facts', 'inferences', 'hypotheses', 'unknowns')
    for i, left in enumerate(categories):
        for right in categories[i+1:]:
            overlap = set(getattr(packet, left)) & set(getattr(packet, right))
            for index, text in enumerate(getattr(packet, left)):
                if text in overlap:
                    add('CATEGORY_COLLISION', Severity.ERROR, 'exact statement occurs in incompatible semantic categories',
                        left + '/' + right, left + '[' + str(index) + ']')
    if packet.unknowns and not packet.verify_next:
        add('VERIFY_NEXT_MISSING', Severity.WARNING, 'unknowns exist without next evidence checks', 'verify_next')
    assessments = assess_verify_next(packet)
    for index, assessment in enumerate(assessments):
        if assessment.risk != CommandRisk.SAFE_READ:
            add('VERIFY_NEXT_' + assessment.risk.value, Severity.WARNING,
                assessment.reason, 'verify_next', 'verify_next[' + str(index) + ']')
    return ValidationResult(findings, assessments)


def format_command_assessment(assessment):
    return 'Risk: ' + assessment.risk.value + '\nReason: ' + assessment.reason


def format_validation_result(result):
    lines = ['VALID' if result.valid else 'INVALID']
    for finding in result.findings:
        lines.append(finding.severity.value + ' ' + finding.code + ': ' + finding.message +
                     (' [' + finding.locator + ']' if finding.locator else ''))
    return '\n'.join(lines)
