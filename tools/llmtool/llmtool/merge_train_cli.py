"""``llmtool task train`` build, status, merge and close (#1182 T3b2).

``build``, ``merge`` and ``close`` resolve the caller PM session with
``pm_caller.resolve_caller_session`` and require it to own every member Task
before the merge-train library step is called, once. ``build`` checks the
members given on the command line; ``merge`` and ``close`` read the members from
the validated train record. ``close`` also re-checks a member's owner right
before each member advance; a refusal there stops the whole close at once.
``status`` reads through ``merge_train_store.list_records`` and changes nothing.
A stale-source stop before build, merge or close prints the same one refusal.

Malformed input refuses TRAIN_INPUT_INVALID while the command line is parsed,
before the fresh-source check or any call: unknown options at the train or
sub-command level, help flags (the train parsers have no help option), missing
values, and malformed members or train_id.

Every command prints exactly one JSON object with ``train_id`` when known,
``step``, ``codes`` and ``result``. A refusal is ``pm_caller.refusal``-shaped
and carries one code; nothing is called after it. Output that a step prints
itself is discarded. Exit 0 for BUILT, MERGED, CLOSED, SUPERSEDED, WAITING_CI
and status, else 1.

Store, pm_caller, task_advance and step modules are imported only when a train
command runs, so registering the parser costs nothing for other commands.
"""

import argparse
import contextlib
import functools
import io
import json
import os
import re
import sys

TRAIN_INPUT_INVALID = 'TRAIN_INPUT_INVALID'
TRAIN_BUILD_UNAVAILABLE = 'TRAIN_BUILD_UNAVAILABLE'
TRAIN_STEP_FAILED = 'TRAIN_STEP_FAILED'
SOURCE_UNKNOWN = 'LLMTOOL_SOURCE_UNKNOWN'

MIN_MEMBERS = 2
MAX_MEMBERS = 6
STATUS_STEP = 'STATUS'
EXIT_ZERO_STEPS = frozenset(('BUILT', 'MERGED', 'CLOSED', 'SUPERSEDED', 'WAITING_CI',
                             STATUS_STEP))
STATUS_FIELDS = ('train_id', 'state', 'main_sha', 'train_head', 'train_pr', 'members',
                 'excluded', 'merge_sha')

_MEMBER = re.compile(r'([0-9a-f]{32}):([1-9][0-9]{0,6})')
_CODE = re.compile(r'[A-Z][A-Z0-9_]{0,63}')


def _refused(code, train_id=None, task_id=None):
    from . import pm_caller
    value = pm_caller.refusal(task_id, code)
    if train_id is not None:
        value['train_id'] = train_id
    return value


def _render(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                      allow_nan=False)


def _finish(value):
    """Print exactly one JSON object and return the exit status."""
    try:
        rendered = _render(value)
    except (TypeError, ValueError):
        value = _refused(TRAIN_STEP_FAILED, value.get('train_id'))
        rendered = _render(value)
    sys.stdout.write(rendered + '\n')
    sys.stdout.flush()
    return 0 if value.get('step') in EXIT_ZERO_STEPS else 1


def _input_error(message=None):
    # argparse's own message can echo the input; print only the code.
    raise SystemExit(_finish(_refused(TRAIN_INPUT_INVALID)))


class _TrainParser(argparse.ArgumentParser):
    """Train sub-command parser: any malformed input refuses TRAIN_INPUT_INVALID."""

    def error(self, message):
        _input_error(message)

    def parse_known_args(self, args=None, namespace=None):
        namespace, extras = super().parse_known_args(args, namespace)
        if extras:
            self.error('unrecognized arguments')
        return namespace, extras


def _valid_action(namespace):
    """True when the parsed train action carries well-formed members or train_id."""
    action = getattr(namespace, 'train_action', None)
    if action == 'build':
        return _members(getattr(namespace, 'member', None)) is not None
    if action in ('status', 'merge', 'close'):
        return _train_id(getattr(namespace, 'train_id', None)) is not None
    return False


def _strict_train_parse(parse_known_args):
    """Wrap the train parser's own ``parse_known_args``.

    Any unrecognized token at the train or sub-command level, or a parsed action
    with malformed members or train_id, refuses TRAIN_INPUT_INVALID here, while
    the command line is parsed: before the root parser's generic error, the
    fresh-source check, the store or any step.
    """
    @functools.wraps(parse_known_args)
    def parse(args=None, namespace=None):
        namespace, extras = parse_known_args(args, namespace)
        if extras or not _valid_action(namespace):
            _input_error()
        return namespace, extras
    return parse


def add_train_parser(task_actions):
    """Register ``task train build|status|merge|close`` under ``task_actions``.

    The train parser and its sub-command parsers have no help option, so a help
    flag is malformed input like any other unknown option.
    """
    train = task_actions.add_parser(
        'train', add_help=False,
        help='Build, inspect, merge or close one merge train (one step per call)')
    train.error = _input_error
    train.parse_known_args = _strict_train_parse(train.parse_known_args)
    actions = train.add_subparsers(dest='train_action', required=True,
                                   parser_class=_TrainParser)
    build = actions.add_parser('build', add_help=False,
                               help='Build one train from 2 to 6 member Tasks')
    build.add_argument('--member', action='append', metavar='TASK_ID:PR',
                       help='member Task and its PR, in train order (repeat 2 to 6 times)')
    build.add_argument('--pm-session', help='caller PM session (default: runtime session)')
    status = actions.add_parser('status', add_help=False,
                                help='Print one train record; changes nothing')
    status.add_argument('train_id')
    for name, text in (('merge', 'Merge one recorded train once at its exact head'),
                       ('close', 'Close the member Tasks of one merged train')):
        child = actions.add_parser(name, add_help=False, help=text)
        child.add_argument('train_id')
        child.add_argument('--pm-session', help='caller PM session (default: runtime session)')
    return train


def _members(raw):
    """Return the ordered member list, or None for malformed input."""
    if type(raw) is not list or not MIN_MEMBERS <= len(raw) <= MAX_MEMBERS:
        return None
    members, task_ids, prs = [], set(), set()
    for item in raw:
        match = _MEMBER.fullmatch(item) if type(item) is str else None
        if match is None:
            return None
        task_id, pr = match.group(1), int(match.group(2))
        if task_id in task_ids or pr in prs:
            return None
        task_ids.add(task_id)
        prs.add(pr)
        members.append({'task_id': task_id, 'pr': pr})
    return members


def _train_id(value):
    from . import merge_train_store
    if type(value) is str and merge_train_store.TRAIN_ID.fullmatch(value):
        return value
    return None


def _exception_code(exc, default):
    code = getattr(exc, 'merge_train_code', None)
    return code if type(code) is str and _CODE.fullmatch(code) else default


def _read_record(train_id, train_root):
    """Return (record, None) or (None, code) through the validating store read."""
    from . import merge_train_store
    try:
        return merge_train_store.get(train_id, root=train_root), None
    except Exception as exc:
        return None, _exception_code(exc, merge_train_store.TRAIN_RECORD_UNAVAILABLE)


def _status(train_id, train_root):
    """Read through ``list_records`` only: ``get`` may create the record
    directory or change its mode, and status must change nothing."""
    from . import merge_train_store
    try:
        records = merge_train_store.list_records(train_root)
    except Exception as exc:
        return _refused(_exception_code(exc, merge_train_store.TRAIN_RECORD_UNAVAILABLE),
                        train_id)
    found = [record for record in records if record.get('train_id') == train_id]
    if len(found) != 1:
        return _refused(merge_train_store.TRAIN_RECORD_UNAVAILABLE, train_id)
    record = found[0]
    return {'train_id': train_id, 'step': STATUS_STEP, 'codes': [],
            'result': {field: record[field] for field in STATUS_FIELDS}}


def _result_train_id(result):
    for source in (result, result.get('result')):
        if type(source) is dict and _train_id(source.get('train_id')) is not None:
            return source['train_id']
    return None


def _step(train_id, call):
    """Call one library step with its own output discarded; shape its result."""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = call()
    except Exception as exc:
        return _refused(_exception_code(exc, TRAIN_STEP_FAILED), train_id)
    if (type(result) is not dict or type(result.get('step')) is not str or
            type(result.get('codes', [])) is not list or
            not all(type(code) is str for code in result.get('codes', []))):
        return _refused(TRAIN_STEP_FAILED, train_id)
    value = {'step': result['step'], 'codes': list(result.get('codes', [])),
             'result': result.get('result')}
    known = train_id if train_id is not None else _result_train_id(result)
    if known is not None:
        value['train_id'] = known
    return value


class _MemberRefused(BaseException):
    """A member's owner re-check refused inside ``close``; stops the whole close.

    ``merge_train_ops.close_train`` treats an ``Exception`` from one member
    advance as an open member and goes on to the next member, so a
    ``pm_caller.PMCallerRefused`` would not stop it. This BaseException passes
    that handler, and ``run_task_train`` turns it into the pm_caller refusal.
    """

    def __init__(self, task_id, code):
        super().__init__(code)
        self.task_id = task_id
        self.code = code


class _OwnedAdvance:
    """Re-check a member's owner right before its advance; refuse without calling.

    The first refusal is latched: every later call raises it again without
    calling the advance, even when a step swallows the first raise.
    """

    def __init__(self, call, store, caller_key):
        self.call = call
        self.store = store
        self.caller_key = caller_key
        self.refused = None

    def __call__(self, **kwargs):
        if self.refused is None:
            from . import pm_caller
            task_id = kwargs.get('task_id')
            code = pm_caller.owner_refusal_code(self.store, task_id, self.caller_key)
            if code is None:
                return self.call(**kwargs)
            self.refused = (task_id, code)
        raise _MemberRefused(*self.refused)


def source_refusal(args, exc):
    """Print the one refusal for a stale-source stop before a train step; exit 1."""
    code = getattr(exc, 'completion_code', None)
    if type(code) is not str or not _CODE.fullmatch(code):
        code = SOURCE_UNKNOWN
    return _finish(_refused(code, _train_id(getattr(args, 'train_id', None))))


def run_task_train(args, *, store=None, build_call=None, merge_call=None, close_call=None,
                   github_get=None, gh_runner=None, git_runner=None, advance_call=None,
                   train_root=None, root=None):
    """Run one ``task train`` command and print exactly one JSON object."""
    action = getattr(args, 'train_action', None)
    members = train_id = None
    if action == 'build':
        members = _members(getattr(args, 'member', None))
        if members is None:
            return _finish(_refused(TRAIN_INPUT_INVALID))
    elif action in ('status', 'merge', 'close'):
        train_id = _train_id(getattr(args, 'train_id', None))
        if train_id is None:
            return _finish(_refused(TRAIN_INPUT_INVALID))
    else:
        return _finish(_refused(TRAIN_INPUT_INVALID))
    if action == 'status':
        return _finish(_status(train_id, train_root))

    from . import pm_caller
    try:
        caller_key = pm_caller.resolve_caller_session(
            explicit=getattr(args, 'pm_session', None))
    except pm_caller.PMCallerRefused as exc:
        return _finish(_refused(exc.code, train_id))
    if store is None:
        from .task_store import TaskStore
        store = TaskStore()
    if action == 'build':
        task_ids = [member['task_id'] for member in members]
    else:
        record, code = _read_record(train_id, train_root)
        if record is None:
            return _finish(_refused(code, train_id))
        task_ids = [member['task_id'] for member in record['members']]
    for task_id in task_ids:
        code = pm_caller.owner_refusal_code(store, task_id, caller_key)
        if code is not None:
            return _finish(_refused(code, train_id, task_id))

    from . import task_advance, task_completion
    github_get = task_completion._github_get if github_get is None else github_get
    gh_runner = task_advance._publish_run_gh if gh_runner is None else gh_runner
    git_runner = task_advance._publish_run_git if git_runner is None else git_runner
    # The same repository root ``task advance`` falls back to: the current checkout.
    root = os.getcwd() if root is None else root

    if action == 'build':
        if build_call is None:
            try:
                from .merge_train_build import build_train as build_call
            except ImportError:
                return _finish(_refused(TRAIN_BUILD_UNAVAILABLE))
        return _finish(_step(None, lambda: build_call(
            members, store=store, caller_key=caller_key, github_get=github_get,
            git_runner=git_runner, gh_runner=gh_runner, root=root, train_root=train_root)))
    if action == 'merge':
        if merge_call is None:
            from .merge_train_ops import merge_train as merge_call
        return _finish(_step(train_id, lambda: merge_call(
            train_id, store=store, github_get=github_get, gh_runner=gh_runner, root=root,
            train_root=train_root)))
    if close_call is None:
        from .merge_train_ops import close_train as close_call
    if advance_call is None:
        advance_call = functools.partial(task_advance.advance, store=store,
                                         github_get=github_get)
    owned = _OwnedAdvance(advance_call, store, caller_key)
    try:
        value = _step(train_id, lambda: close_call(
            train_id, store=store, advance_call=owned, train_root=train_root))
    except _MemberRefused:
        value = None
    if owned.refused is not None:
        # Also when the step swallowed the raise and returned a result.
        task_id, code = owned.refused
        value = _refused(code, train_id, task_id)
    return _finish(value)
