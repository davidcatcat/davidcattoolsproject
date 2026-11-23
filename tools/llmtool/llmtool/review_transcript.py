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
                    parts = text.split(_MARKER)
                    blocks += [_verdict_lines(_MARKER + part, fenced=_opens_fence(parts[index]))
                               for index, part in enumerate(parts[1:])]
    return blocks


def _opens_fence(before):
    lines = [line for line in before.split('\n') if line.strip()]
    return bool(lines) and lines[-1].lstrip().startswith('```')


def _verdict_lines(text, *, fenced=False):
    # #2536: the verdict is the whole text from its marker: the header, the
    # binding and the findings and evidence that follow it (formal-verdict.md).
    # Only a verdict opened inside a code fence ends at the closing fence.
    lines = []
    for line in text.split('\n'):
        if fenced and line.lstrip().startswith('```'):
            break
        lines.append(line)
    return '\n'.join(lines).rstrip()


def _claim_config_dir(claim):
    """#2602 item i: the config directory a claim binds review evidence to.

    A claim that recorded the reviewer account it ran as reads that account's
    config directory through the role configuration (a one-shot account
    override); the session id and reviewer task stay bound to the claim. An
    unconfigured or invalid recorded account is refused by the role
    configuration error and never falls back to another account. A claim
    without a recorded account keeps the reviewer role resolved at
    collection time.
    """
    recorded = claim.get('account')
    if recorded is None:
        return role_config.resolve_role('reviewer').get('config_dir')
    try:
        return role_config.resolve_role('reviewer',
                                        cli={'account': recorded}).get('config_dir')
    except LLMToolError as error:
        # The session id and reviewer task are not re-bound; refuse the claim
        # with the role configuration's definite error code (never another
        # account's directory).
        return ('REFUSED', getattr(error, 'role_config_code', None)
                or 'REVIEWER_ACCOUNT_UNCONFIGURED')


def _exact_header(block, binding):
    """Marker, Verdict line, then the binding and at most one ACCEPT line, each once."""
    lines = block.split('\n')
    if (len(lines) < 3 or not task_completion._VERDICT_MARKER.fullmatch(lines[0]) or
            sum(line.startswith('Verdict:') for line in lines) != 1 or
            not lines[1].startswith('Verdict:') or
            sum(line.startswith('LLMTOOL-REVIEW-BINDING') for line in lines) != 1 or
            sum(line == _ACCEPT for line in lines) > 1):
        return False
    header = []
    for line in lines[2:4]:
        if line not in (binding, _ACCEPT) or line in header:
            break
        header.append(line)
    return binding in header and (_ACCEPT not in lines or _ACCEPT in header)


def select_verdict(blocks, *, task, head, version):
    if not blocks:
        return 'REVIEWER_VERDICT_MISSING', None
    binding = task_completion._review_binding_line(
        task['task_id'], task['run_id'], task['task_binding']['contract_digest'], head)
    stamp = '1970-01-01T00:00:00Z'
    named = [block for block in blocks if binding in block.split('\n')]
    # An exact header (marker, Verdict line, binding and at most one ACCEPT line,
    # nothing between them), then the findings and evidence; else refuse.
    bound = [block for block in named
             if _exact_header(block, binding) and
             review_result._comment_verdict(
                 {'id': 0, 'body': block, 'created_at': stamp, 'updated_at': stamp},
                 head=head, version=version, task=task) is not None]
    if len(bound) != 1:
        return ('REVIEWER_VERDICT_AMBIGUOUS' if len(bound) > 1 else
                'REVIEWER_VERDICT_NOT_EXACT' if named else
                'REVIEWER_VERDICT_BINDING_MISMATCH'), None
    block = bound[0]
    if (block.split('\n')[1].startswith('Verdict: FAIL') and
            not any(line.strip().startswith('Finding') for line in block.split('\n'))):
        # #2536: a FAIL without its findings cannot drive a repair; never post one.
        return 'REVIEWER_VERDICT_NO_FINDINGS', None
    return None, block


def unposted_verdict(claim, task, *, config_dir=None, run=subprocess.run, claude=None):
    """Return (state, code, block); the caller posts the block unedited."""
    if 'purpose' in claim:
        return 'NOT_APPLICABLE', None, None
    session, reviewer = claim.get('native_session_id'), claim.get('reviewer_task')
    if (type(session) is not str or not _UUID.fullmatch(session) or
            type(reviewer) is not str or not _REVIEWER.fullmatch(reviewer)):
        return 'NOT_APPLICABLE', None, None
    if config_dir is None:
        resolved = _claim_config_dir(claim)
        if isinstance(resolved, tuple):
            return 'UNKNOWN', resolved[1], None
        config_dir = resolved
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


def _legacy_header(block):
    """The text the pre-#2536 relay kept: up to the binding (and an ACCEPT right after it)."""
    lines = []
    for line in block.split('\n'):
        if line.lstrip().startswith('```') or (line != _ACCEPT and any(
                kept.startswith('LLMTOOL-REVIEW-BINDING ') for kept in lines)):
            break
        lines.append(line)
    return '\n'.join(lines).rstrip()


def truncated_fail_repost(claim, task, comments, *, config_dir=None):
    """#2536: (code, full_block, replaced_comment_id) for a FAIL relayed without findings.

    Every condition must hold, else a refusal code and nothing to post: the
    claim names a Claude Reviewer session; its transcript yields exactly one
    verdict bound to this Task/run/contract/HEAD/version and it is a FAIL with
    findings; the PR holds exactly one verdict comment for that HEAD and
    version, and it is exactly the pre-#2536 truncated header of that verdict.
    A PASS is always refused. The block is the transcript text, unedited.
    """
    session, reviewer = claim.get('native_session_id'), claim.get('reviewer_task')
    if (type(session) is not str or not _UUID.fullmatch(session) or
            type(reviewer) is not str or not _REVIEWER.fullmatch(reviewer)):
        return 'REPOST_NOT_CLAUDE_REVIEWER', None, None
    if config_dir is None:
        resolved = _claim_config_dir(claim)
        if isinstance(resolved, tuple):
            return resolved[1], None, None
        config_dir = resolved
    if not config_dir:
        return 'REVIEWER_ACCOUNT_UNCONFIGURED', None, None
    blocks = verdict_blocks(session, config_dir, reviewer)
    if blocks is None:
        return 'REVIEWER_TRANSCRIPT_UNAVAILABLE', None, None
    head, version = claim.get('head_sha'), claim.get('version')
    code, block = select_verdict(blocks, task=task, head=head, version=version)
    if code is not None:
        return code, None, None
    if not block.split('\n')[1].startswith('Verdict: FAIL'):
        return 'REPOST_ONLY_FAIL', None, None
    if type(comments) is not list:
        return 'REVIEW_RESULT_COMMENTS_INVALID', None, None
    posted = []
    for comment in comments:
        try:
            found = review_result._comment_verdict(comment, head=head, version=version,
                                                   task=task) if type(comment) is dict else None
        except Exception:
            found = None
        if found is not None:
            posted.append(comment)
    if len(posted) != 1:
        return 'REPOST_POSTED_VERDICT_NOT_SINGLE', None, None
    body = task_completion._canonical_verdict_body(posted[0].get('body'))
    if body == block or body != _legacy_header(block):
        return 'REPOST_NOT_TRUNCATED_PREFIX', None, None
    from .task_submission_cli import _screen_text
    try:
        _screen_text(block, 65536)
    except LLMToolError:
        return 'REVIEWER_VERDICT_WITHHELD', None, None
    return None, block, posted[0].get('id')
