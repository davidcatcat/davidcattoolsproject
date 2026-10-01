"""Bounded analyze-log suggestion checks. Commands are never executed or rewritten."""

import re
import shlex

from .errors import LLMToolError
from .terminal import contains_unsafe_controls
from .validator import CommandRisk, classify_command, _sensitive_path

OUTPUT_LIMIT = 1024 * 1024
_ERROR = "analyze-log NEXT_COMMANDS failed safety/format validation; output omitted"


def validate_log_suggestions(content):
    """Accept a final NEXT_COMMANDS section with <=3 single-line bullet commands.

    This guards obvious credential display and known risky operations, not all
    shell semantics or factual claims. Unsupported formatting fails closed.
    """
    if not isinstance(content, str):
        raise LLMToolError(_ERROR)
    try:
        if len(content) > OUTPUT_LIMIT or len(content.encode("utf-8")) > OUTPUT_LIMIT:
            raise LLMToolError(_ERROR)
    except UnicodeError:
        raise LLMToolError(_ERROR) from None
    if contains_unsafe_controls(content):
        raise LLMToolError(_ERROR)
    lines = content.splitlines()
    headings = [i for i, line in enumerate(lines)
                if re.fullmatch(r"\s*(?:#{1,6}\s+)?(?:\*\*)?NEXT_COMMANDS:?(?:\*\*)?\s*", line)]
    if len(headings) != 1:
        raise LLMToolError(_ERROR)
    suggestions = [line.strip() for line in lines[headings[0] + 1:] if line.strip()]
    if suggestions == ["(none)"]:
        return
    if not 1 <= len(suggestions) <= 3:
        raise LLMToolError(_ERROR)
    for suggestion in suggestions:
        match = re.fullmatch(r"(?:[-*] |[1-3][.)] )(.+)", suggestion)
        if match is None:
            raise LLMToolError(_ERROR)
        command = match.group(1)
        if command.startswith('`') and command.endswith('`'):
            command = command[1:-1]
        if len(command.encode('utf-8')) > 8 * 1024:
            raise LLMToolError(_ERROR)
        # Do not attempt to interpret embedded programs, expansions or escaped
        # continuations. Placeholders should be plain uppercase identifiers.
        if any(c in command for c in ('`', '$', '\\')):
            raise LLMToolError(_ERROR)
        assessment = classify_command(command)
        if assessment.risk in (CommandRisk.SENSITIVE_READ, CommandRisk.MUTATING,
                               CommandRisk.DESTRUCTIVE):
            raise LLMToolError(_ERROR)
        try:
            tokens = shlex.split(command)
        except ValueError:
            raise LLMToolError(_ERROR) from None
        if not tokens or any(t.rsplit('/', 1)[-1] in ('sh', 'bash', 'zsh', 'fish', 'python',
                                                     'python3', 'perl', 'ruby') for t in tokens):
            raise LLMToolError(_ERROR)
        display_tools = ('cat', 'grep', 'head', 'tail', 'sed', 'awk', 'less', 'more',
                         'base64', 'hexdump', 'xxd', 'strings')
        if (any(t.rsplit('/', 1)[-1] in display_tools for t in tokens)
                and any(_sensitive_path(t) for t in tokens)):
            raise LLMToolError(_ERROR)
        # Flags/wrappers/pipes cannot hide a literal Secret resource selection.
        for index, token in enumerate(tokens):
            if token.rsplit('/', 1)[-1] == 'kubectl' and 'get' in tokens[index + 1:]:
                after_get = tokens[index + 1:].index('get') + index + 2
                if any(part.split('/', 1)[0].split('.', 1)[0].lower() in ('secret', 'secrets')
                       for item in tokens[after_get:] for part in item.split(',')):
                    raise LLMToolError(_ERROR)
