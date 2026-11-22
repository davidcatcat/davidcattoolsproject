"""Read a finished Claude Reviewer's unposted verdict from its transcript (#1568)."""
import glob
import json
import os
import re
import shutil
import subprocess

from . import review_result, role_config, task_completion
from .errors import LLMToolError

_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
_REVIEWER = re.compile(r'reviewer-[0-9a-f]{20}\Z')
_MARKER = '<!-- activity: type=review_verdict actor='
_ACCEPT = 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT'


def session_state(session_id, config_dir, *, run=subprocess.run, claude=None):
    claude = claude or shutil.which('claude')
    try:
        out = run([claude, 'agents', '--json', '--all'], stdin=subprocess.DEVNULL,
                  capture_output=True, timeout=30, check=False,
                  env=dict(os.environ, CLAUDE_CONFIG_DIR=config_dir))
        rows = json.loads(out.stdout) if claude and out.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        rows = []
    row = next((r for r in rows if type(r) is dict and r.get('sessionId') == session_id),
               None) if type(rows) is list else None
    if row is None:
        return 'UNKNOWN'
    if row.get('state') in ('done', 'stopped'):
        return 'ENDED'
    # #2552: a background session that answered waits in 'blocked'; the caller
    # decides from the transcript whether the turn really finished.
    return 'BLOCKED' if row.get('state') == 'blocked' else 'RUNNING'


def _transcript_path(session_id, config_dir, reviewer_task):
    paths = glob.glob(os.path.join(glob.escape(config_dir), 'projects',
                                   '*' + reviewer_task, session_id + '.jsonl'))
    if (len(paths) != 1 or os.path.islink(paths[0]) or not os.path.isfile(paths[0]) or
            os.path.getsize(paths[0]) > 64 * 1024 * 1024):
        return None
    return paths[0]


def turn_complete(session_id, config_dir, reviewer_task):
    """#2552: True only when the transcript proves the reviewer's turn finished.

    A background reviewer that answered sits in `claude agents` state 'blocked'
    (waiting for the next input), which also covers waiting on a tool approval.
    The turn counts as finished only when the last assistant message stopped with
    end_turn and every tool_use has a tool_result. Any doubt returns False.
    """
    path = _transcript_path(session_id, config_dir, reviewer_task)
    if path is None:
        return False
    last_stop, pending = None, set()
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if type(entry) is not dict:
                    continue
                message = entry.get('message')
                content = message.get('content') if type(message) is dict else None
                items = content if type(content) is list else ()
                if entry.get('type') == 'assistant' and type(message) is dict:
                    last_stop = message.get('stop_reason')
                    pending |= {item.get('id') for item in items
                                if type(item) is dict and item.get('type') == 'tool_use'}
                elif entry.get('type') == 'user':
                    pending -= {item.get('tool_use_id') for item in items
                                if type(item) is dict and item.get('type') == 'tool_result'}
    except OSError:
        return False
    return last_stop == 'end_turn' and not pending


def verdict_blocks(session_id, config_dir, reviewer_task):
    path = _transcript_path(session_id, config_dir, reviewer_task)
    if path is None:
        return None
    blocks = []
    with open(path, encoding='utf-8', errors='replace') as handle:
        for line in handle:
            try:
                entry = json.loads(line)
                content = list(entry['message']['content']
                               if entry['type'] == 'assistant' else ())
            except Exception:
                continue
            for item in content:
                text = item.get('text') if type(item) is dict else None
                if type(text) is str and item.get('type') == 'text':
                    blocks += [_verdict_lines(_MARKER + part) for part in text.split(_MARKER)[1:]]
    return blocks


def _verdict_lines(text):
    # Only the verdict is ever posted: stop at a code fence, or after the binding
    # line (and an ACCEPT line right after it). Prose after it is never kept.
    lines = []
    for line in text.split('\n'):
        if line.lstrip().startswith('```') or (line != _ACCEPT and any(
                kept.startswith('LLMTOOL-REVIEW-BINDING ') for kept in lines)):
            break
        lines.append(line)
    return '\n'.join(lines).rstrip()


def select_verdict(blocks, *, task, head, version):
    if not blocks:
        return 'REVIEWER_VERDICT_MISSING', None
    binding = task_completion._review_binding_line(
        task['task_id'], task['run_id'], task['task_binding']['contract_digest'], head)
    stamp = '1970-01-01T00:00:00Z'
    named = [block for block in blocks if binding in block.split('\n')]
    # Exactly marker, Verdict line, binding and at most one ACCEPT line; else refuse.
    bound = [block for block in named
             if len(block.split('\n')) == len(set(block.split('\n')[2:])) + 2 and
             set(block.split('\n')[2:]) in ({binding}, {binding, _ACCEPT}) and
             task_completion._VERDICT_MARKER.fullmatch(block.split('\n')[0]) and
             review_result._comment_verdict(
                 {'id': 0, 'body': block, 'created_at': stamp, 'updated_at': stamp},
                 head=head, version=version, task=task) is not None]
    if len(bound) != 1:
        return ('REVIEWER_VERDICT_AMBIGUOUS' if len(bound) > 1 else
                'REVIEWER_VERDICT_NOT_EXACT' if named else
                'REVIEWER_VERDICT_BINDING_MISMATCH'), None
    return None, bound[0]


def unposted_verdict(claim, task, *, config_dir=None, run=subprocess.run, claude=None):
    """Return (state, code, block); the caller posts the block unedited."""
    session, reviewer = claim.get('native_session_id'), claim.get('reviewer_task')
    if (type(session) is not str or not _UUID.fullmatch(session) or
            type(reviewer) is not str or not _REVIEWER.fullmatch(reviewer)):
        return 'NOT_APPLICABLE', None, None
    if config_dir is None:
        role = role_config.resolve_role('reviewer')
        if claim.get('account') not in (None, role.get('account')):
            return 'UNKNOWN', 'REVIEWER_ACCOUNT_CHANGED', None
        config_dir = role.get('config_dir')
    if not config_dir:
        return 'UNKNOWN', 'REVIEWER_ACCOUNT_UNCONFIGURED', None
    state = session_state(session, config_dir, run=run, claude=claude)
    if state == 'BLOCKED':
        if not turn_complete(session, config_dir, reviewer):
            # Waiting on a tool approval, or the turn cannot be proven over:
            # a definite stop, never "ended without a verdict".
            return 'BLOCKED', 'REVIEWER_WAITING_APPROVAL', None
        state = 'ENDED'
    if state != 'ENDED':
        return state, ('REVIEWER_SESSION_UNKNOWN' if state == 'UNKNOWN' else None), None
    blocks = verdict_blocks(session, config_dir, reviewer)
    if blocks is None:
        return state, 'REVIEWER_TRANSCRIPT_UNAVAILABLE', None
    code, block = select_verdict(blocks, task=task, head=claim.get('head_sha'),
                                 version=claim.get('version'))
    if block is not None:
        from .task_submission_cli import _screen_text
        try:
            _screen_text(block, 65536)
        except LLMToolError:
            return state, 'REVIEWER_VERDICT_WITHHELD', None
    return state, code, block
