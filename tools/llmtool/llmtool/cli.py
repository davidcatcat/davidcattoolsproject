"""CLI wiring only: parse, resolve, read, request, and print."""

import argparse
import json
import os
import re
import signal
import stat
import sys
import threading
from pathlib import Path

from .config import api_key, init_config, resolve_config, show_config, user_config_path
from .configure import run_configure
from .chat import ChatSession, run_chat
from .context import parse_context_packet, format_context_packet
from .errors import LLMToolError
from .inputs import read_input, validate_input_sources
from .git_intel import collect_git_snapshot, format_git_snapshot
from .prompts import TEMPLATES, messages_for
from .skills import discover_skills, load_skill
from .providers import PROVIDER_REGISTRY, create_client, get_provider_capabilities
from .validator import (classify_command, validate_context_packet,
                        format_command_assessment, format_validation_result)
from .terminal import human_output, silence_broken_pipe
from .context import PACKET_LIMIT
from .feedback_cli import add_feedback_parser, run_feedback
from .benchmark_cli import add_benchmark_parser, run_benchmark
from .orchestration_cli import add_orchestration_parser, run_orchestration
from .retrieval_cli import add_retrieval_parser, run_retrieval
from .diagnostics_cli import add_diagnostics_parser, run_diagnostics
from .action_contract_cli import add_action_contract_parsers, run_action_contract
from .task_lifecycle_cli import add_task_parser, run_task_inspect
from . import task_submission_cli
from .task_review_cli import add_review_parser, run_task_review
from .review_authority_cli import add_review_authority_parser, run_review_authority_check
from .dispatch_check import add_dispatch_check_parser, run_dispatch_check
from .board_check import add_board_check_parser, run_board_check
from .trust_cli import add_trust_parser, run_trust


TASK_ACTIONS = {
    'abandon': ('run_task_abandon', False),
    'ack': ('run_task_ack', False),
    'adopt': ('run_task_adopt', False),
    'advance': ('run_task_advance', False),
    'assign': ('run_task_assign', False),
    'assignments': ('run_task_assignments', False),
    'brief': ('run_task_brief', False),
    'claim': ('run_task_claim', False),
    'claude-bind': ('run_task_claude_bind', True),
    'claude-result': ('run_task_claude_result', False),
    'complete': ('run_task_complete', False),
    'delegate': ('run_task_delegate', True),
    'drive': ('run_task_drive', True),
    'github-events': ('run_task_github_events', False),
    'high-debug': ('run_task_high_debug', True),
    'inbox': ('run_task_inbox', False),
    'record-pm': ('run_task_record_pm', False),
    'remote-test': ('run_task_remote_test', True),
    'repair': ('run_task_repair', True),
    'review-dispatch': ('run_task_review_dispatch', True),
    'review-prompt': ('run_task_review_prompt', False),
    'review-result': ('run_task_review_result', False),
    'show': ('run_task_show', False),
    'status': ('run_task_status', False),
    'submit': ('run_task_submit', False),
}

TASK_REFUSAL_ATTRIBUTES = {
    'high-debug': 'high_debug_code',
    'repair': 'review_repair_code',
    'review-dispatch': 'review_dispatch_code',
    'review-result': 'review_dispatch_code',
}


def _validation_packet(filename):
    """Read only the explicit regular file, with A4's byte bound."""
    descriptor = os.open(filename, os.O_RDONLY | os.O_NONBLOCK)
    primary_error = None
    try:
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise LLMToolError('validation --file requires a regular file')
            raw = stream.read(PACKET_LIMIT + 1)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(descriptor)
        except OSError:
            if primary_error is None:
                raise
    if len(raw) > PACKET_LIMIT:
        raise LLMToolError('context packet output exceeds 1 MiB')
    return parse_context_packet(raw.decode('utf-8'))


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error can echo arbitrary input or supplied secrets.
        self.exit(2, "llmtool: invalid command or options; use --help\n")


def add_options(parser):
    parser.add_argument("--provider", help="named provider profile from user config")
    parser.add_argument("--base-url", help="provider origin (without /v1)")
    parser.add_argument("--model", help="provider route/model")
    parser.add_argument("--timeout", help="positive socket timeout in seconds")
    parser.add_argument("--max-tokens", help="positive completion token limit")
    parser.add_argument("--temperature", help="generation temperature, 0 through 2")


def parser_for_cli():
    parser = SafeParser(description="Read-only, provider-agnostic LLM CLI foundation.")
    parser.add_argument("--version", action="version", version="llmtool 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)
    descriptions = {
        "ask": "Ask an evidence-aware question",
        "analyze-log": "Analyze supplied logs without inventing causes",
        "review-diff": "Review only a supplied diff; never invoke Git",
        "context-pack": "Compress supplied incident evidence",
    }
    for command in TEMPLATES:
        child = sub.add_parser(command, help=descriptions[command])
        child.add_argument("text", nargs="?", help="direct text (quote multiword input)")
        child.add_argument("--file", help="read one UTF-8 regular file")
        child.add_argument("--verbose", action="store_true", help="write safe metadata to stderr")
        if command == "ask":
            child.add_argument("--skill", help="explicit runtime skill name")
            child.add_argument("--debug", action="store_true", help="write safe event summaries; does not enable saving")
            persistence = child.add_mutually_exclusive_group()
            persistence.add_argument("--save-diagnostics", dest="persist_metadata", action="store_true",
                                     help="save bounded diagnostics metadata only")
            persistence.add_argument("--no-save-diagnostics", dest="persist_metadata", action="store_false",
                                     help="disable diagnostics saving, including user opt-in")
            child.set_defaults(persist_metadata=None)
        if command == "context-pack":
            child.add_argument("--json", action="store_true", help="output a validated Context Packet as JSON")
        add_options(child)
    chat = sub.add_parser("chat", help="Start an interactive chat with private local history")
    chat.add_argument("--skill", help="explicit runtime skill name")
    chat.add_argument("--no-history", action="store_true", help="disable saved history; keep it in memory only")
    chat.add_argument("--tui", action="store_true",
                      help="opt-in full-screen terminal UI (needs llmtool[tui]); falls back to line chat")
    chat.add_argument("--dev", action="store_true",
                      help="with --tui: also offer the developer/test Task commands (/task …)")
    add_options(chat)
    git = sub.add_parser("git", help="Collect a read-only local Git snapshot offline")
    git.add_argument("--json", action="store_true", help="output snapshot as JSON")
    excerpts = sub.add_parser("test-excerpts", help="Select bounded captured-test evidence offline; never run tests")
    excerpts.add_argument("--file", required=True, help="one explicit captured UTF-8 regular file")
    excerpts.add_argument("--exit-code", required=True, type=int, help="captured test exit (-127 through 255)")
    excerpts.add_argument("--json", action="store_true", required=True, help="output version-1 evidence JSON")
    excerpts.add_argument("--max-excerpt-bytes", type=int, default=2048, help="128 through 2048 original bytes per excerpt")
    excerpts.add_argument("--max-excerpts", type=int, default=32, help="1 through 32 excerpts; total capped at 16 KiB")
    worker = sub.add_parser("test-triage-worker",
                            help="Advisory company test-triage over test-excerpts; live route BLOCKED")
    worker.add_argument("--file", required=True, help="one explicit captured UTF-8 regular file")
    worker.add_argument("--exit-code", required=True, type=int, help="captured test exit (-127 through 255)")
    worker.add_argument("--profile", required=True, help="explicit openai-compatible profile; no fallback")
    worker.add_argument("--json", action="store_true", required=True, help="output advisory schema-version-2 JSON (request_body is the actual wire digest)")
    worker.add_argument("--phase0-grant", help="private 0600 Phase 0 grant file; requires --phase0-grant-sha256")
    worker.add_argument("--phase0-grant-sha256", help="SHA-256 pin of the grant file from the owner's record")
    add_feedback_parser(sub)
    add_benchmark_parser(sub, add_options)
    add_orchestration_parser(sub)
    add_retrieval_parser(sub)
    add_diagnostics_parser(sub)
    sub.choices['doctor'].add_argument('--strict', action='store_true',
                                      help='exit 1 when overall readiness is not ready')
    add_action_contract_parsers(sub)
    add_task_parser(sub)
    task_actions = sub.choices['task']._subparsers._group_actions[0]
    task_submission_cli.add_task_submission_parsers(task_actions)
    add_review_parser(task_actions)
    from .merge_train_cli import add_train_parser
    add_train_parser(task_actions)
    add_review_authority_parser(sub)
    add_dispatch_check_parser(sub)
    add_board_check_parser(sub)
    from .mac_full_due import add_ci_parser
    add_ci_parser(sub)
    add_trust_parser(sub)
    validate = sub.add_parser("validate", help="Assess commands or Context Packets offline; never execute")
    actions = validate.add_subparsers(dest="action", required=True)
    command = actions.add_parser("command", help="Classify a suggested command as data")
    command.add_argument("text")
    command.add_argument("--json", action="store_true")
    context = actions.add_parser("context", help="Check structural support, not factual truth")
    context.add_argument("--file", required=True)
    context.add_argument("--json", action="store_true")
    sub.add_parser("configure", help="Interactively configure a provider profile")
    providers = sub.add_parser("providers", help="List supported provider capabilities offline")
    providers.add_argument("--json", action="store_true", help="output metadata as JSON")
    skills = sub.add_parser("skills", help="List runtime skills offline")
    skills.add_argument("--json", action="store_true", help="output metadata as JSON")
    skill = sub.add_parser("skill", help="Inspect a runtime skill offline")
    actions = skill.add_subparsers(dest="action", required=True)
    show_skill = actions.add_parser("show", help="Show validated metadata and instructions")
    show_skill.add_argument("name")
    config = sub.add_parser("config", help="Initialize or inspect configuration")
    actions = config.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init", help="Create private user config")
    init.add_argument("--force", action="store_true", help="replace existing user config")
    show = actions.add_parser("show", help="Show resolved config with API key redacted")
    add_options(show)
    actions.add_parser("path", help="Print the user config path")
    return parser


class _AskTermination(BaseException):
    """Private unwind for a TERM/HUP owned by one persisted HTTP ask."""

    def __init__(self, owner, signum, stream=None):
        self.owner = owner
        self.signum = signum
        self.stream = stream


class _AskSignalScope:
    """Temporarily own only default TERM/HUP dispositions for persisted asks."""

    def __init__(self):
        self.signum = None
        self._raised = False
        self._critical = 0
        self._delivery = None
        self._handlers = {}
        self._installing = True
        if os.name != 'posix' or threading.current_thread() is not threading.main_thread():
            self._installing = False
            return
        try:
            for name in ('SIGTERM', 'SIGHUP'):
                number = getattr(signal, name, None)
                if number is None or signal.getsignal(number) is not signal.SIG_DFL:
                    continue
                def handler(received, frame, owner=self):
                    owner._receive(received)
                self._handlers[number] = (signal.SIG_DFL, handler)
                signal.signal(number, handler)
        except Exception:
            self.restore()
        finally:
            self._installing = False

    def _receive(self, signum):
        if self.signum is None:
            self.signum = signum
        if self._critical or self._installing or self._raised:
            return
        self._raised = True
        raise _AskTermination(self, self.signum, self._delivery)

    def _raise_pending(self):
        if self.signum is not None and not self._raised:
            self._raised = True
            raise _AskTermination(self, self.signum)

    def begin_critical(self):
        self._critical += 1

    def end_critical(self):
        self._critical = max(0, self._critical - 1)

    def deliver(self, stream, operation):
        previous = self._delivery
        self._delivery = stream
        try:
            return operation()
        finally:
            self._delivery = previous

    def restore(self):
        for number, (previous, installed) in reversed(tuple(self._handlers.items())):
            try:
                if signal.getsignal(number) is installed:
                    signal.signal(number, previous)
            except Exception:
                pass
        self._handlers.clear()


def _run_ask(args):
    """Observe ask only. Diagnostics never authorize recovery or execution."""
    from . import diagnostics as d
    from .diagnostics_runtime import RunContext
    from .diagnostics_settings import resolve_diagnostics_settings
    from .errors import coded_error
    from .providers.cli_lifecycle import CLIHostTermination

    mode = (d.DiagnosticMode.DEBUG if args.debug else
            d.DiagnosticMode.VERBOSE if args.verbose else d.DiagnosticMode.NORMAL)
    try:
        run = RunContext(mode)
    except Exception:
        run = None  # Even an observer initialization failure is secondary.
    code, component = d.ErrorCode.USER_INPUT_ERROR, 'input'
    requested_without_observer = False
    signal_scope = None
    termination_handled = False

    def deliver(stream, operation):
        if signal_scope is None:
            return operation()
        return signal_scope.deliver(stream, operation)

    def observe(method, *values, check_signal=True):
        if run is None:
            return None
        critical = method in {'enable_persistence', 'persistence_unavailable', 'request',
                             'response', 'error', 'finish', 'drop', 'close'}
        if signal_scope is not None and critical:
            signal_scope.begin_critical()
        try:
            if signal_scope is not None and method in {'display', 'display_store_warning'}:
                stream = values[0] if values else sys.stderr
                result = signal_scope.deliver(stream, lambda: getattr(run, method)(*values))
            else:
                result = getattr(run, method)(*values)
        except Exception:
            try:
                run.drop()
            except Exception:
                pass
            result = None
        finally:
            if signal_scope is not None and critical:
                signal_scope.end_critical()
        if signal_scope is not None and critical and check_signal:
            signal_scope._raise_pending()
        return result

    def store_warning():
        if requested_without_observer:
            # Observer construction itself can fail; explicit opt-in stays honest.
            try:
                from .diagnostics_store import StoreWarning
                deliver(sys.stderr, lambda: print(
                    StoreWarning(d.ErrorCode.DIAGNOSTICS_STORE_UNAVAILABLE).line(), file=sys.stderr))
            except Exception:
                pass
        else:
            observe('display_store_warning', sys.stderr)

    def interrupted(exc):
        nonlocal termination_handled
        if signal_scope is None or exc.owner is not signal_scope:
            raise exc
        termination_handled = True
        if exc.stream is sys.stdout and exc.stream is sys.__stdout__:
            silence_broken_pipe(exc.stream)
            sys.stdout = None
        if exc.stream is sys.stderr and exc.stream is sys.__stderr__:
            silence_broken_pipe(exc.stream)
            sys.stderr = None
        observe('finish', 'interrupted')
        if sys.stderr is not None:
            store_warning()
            observe('display', sys.stderr)
        return 128 + exc.signum

    def setup_persistence(primary_error=False):
        nonlocal requested_without_observer
        try:
            settings = resolve_diagnostics_settings(persist_metadata=args.persist_metadata)
        except Exception:
            # Legacy skill/input errors precede config errors. Invalid settings
            # cannot be used to write, but an explicit save still reports failure.
            if args.persist_metadata is True:
                requested_without_observer = run is None
                observe('persistence_unavailable')
            if not primary_error:
                raise
            return
        requested_without_observer = settings.persist_metadata and run is None
        observe('enable_persistence', settings)

    def failure(exc):
        try:
            # Preserve the primary safe message even if secondary observation fails.
            deliver(sys.stderr, lambda: print('llmtool: ' + str(exc), file=sys.stderr))
            mapped = observe('error', exc, code, component) or code
            observe('finish', 'incomplete' if mapped is d.ErrorCode.MODEL_OUTPUT_INCOMPLETE else 'error')
            try:
                line = observe('failure_line', mapped)
                deliver(sys.stderr, lambda: print(line or ('llmtool: code=' + mapped.value +
                              ' run_id=unavailable trace=' +
                              ('unavailable' if requested_without_observer else 'not-saved')), file=sys.stderr))
            except _AskTermination as termination:
                return interrupted(termination)
            except Exception:
                observe('drop')
            store_warning()
            observe('display', sys.stderr)
            return 1
        except _AskTermination as termination:
            return interrupted(termination)

    try:
        try:
            selected_skill = load_skill(args.skill) if args.skill is not None else None
            validate_input_sources(args.text, args.file, sys.stdin)
        except LLMToolError:
            setup_persistence(primary_error=True)
            raise
        code, component = d.ErrorCode.CONFIG_INVALID, 'config'
        setup_persistence()
        config = resolve_config(vars(args), require_configured=True)
        code, component = d.ErrorCode.USER_INPUT_ERROR, 'input'
        text = read_input(args.text, args.file, sys.stdin)
        code, component = d.ErrorCode.AUTH_CREDENTIAL_MISSING, 'config.auth'
        key = api_key(config)
        code, component = d.ErrorCode.PROVIDER_RUNTIME_ERROR, 'provider'
        client = create_client(config, key)
        provider = {}
        if isinstance(config, dict):
            profiles = config.get('providers', {})
            if isinstance(profiles, dict):
                provider = profiles.get(config.get('active_provider'), {})
        if (run is not None and run.persistence_requested
                and isinstance(provider, dict) and provider.get('type') == 'openai-compatible'):
            signal_scope = _AskSignalScope()
            signal_scope._raise_pending()
        code, component = d.ErrorCode.USER_INPUT_ERROR, 'input'
        messages = messages_for('ask', text, selected_skill)
        code, component = d.ErrorCode.PROVIDER_RUNTIME_ERROR, 'provider'
        observe('request', config)
        result = client.complete(messages)  # Exactly once, including debug/drop cases.
        try:
            metadata = result.metadata
        except Exception:
            observe('drop')
        else:
            observe('response', metadata)
        code, component = d.ErrorCode.MODEL_RESPONSE_INVALID, 'output'
        output = result.content
        if selected_skill is not None and selected_skill.name == 'analyze-log':
            from .log_safety import filter_log_suggestions
            output, suppressed = filter_log_suggestions(output)
            if suppressed:
                deliver(sys.stderr, lambda: print('llmtool: analyze-log suppressed ' + str(suppressed) +
                      ' unsafe or unsupported NEXT_COMMANDS suggestion(s)', file=sys.stderr))
        output = human_output(output, sys.stdout)
        def write_output():
            sys.stdout.write(output + '\n')
            # Detect a buffered EPIPE before recording successful delivery.
            sys.stdout.flush()
        deliver(sys.stdout, write_output)
        observe('finish', 'success')
        store_warning()
        observe('display', sys.stderr)
        return 0
    except LLMToolError as exc:
        return failure(exc)
    except BrokenPipeError:
        silence_broken_pipe(sys.stdout)
        sys.stdout = None
        observe('finish', 'output_closed')
        store_warning()
        return 0
    except (OSError, UnicodeError):
        return failure(coded_error('local I/O failed; details omitted',
                                  'LOCAL_IO_ERROR', 'cli.ask'))
    except _AskTermination as exc:
        return interrupted(exc)
    except EOFError:
        return failure(coded_error('input ended before configuration was complete',
                                  'USER_INPUT_ERROR', 'input'))
    except CLIHostTermination as exc:
        if exc.code in (129, 143):
            observe('finish', 'interrupted')
            store_warning()
            observe('display', sys.stderr)
        raise  # Only tagged owned-CLI cancellation; never normalize SystemExit.
    except KeyboardInterrupt:
        observe('finish', 'interrupted')
        print('llmtool: interrupted', file=sys.stderr)
        store_warning()
        observe('display', sys.stderr)
        return 130
    except Exception:
        # Never stringify an unexpected exception or print a Python traceback.
        return failure(coded_error('internal error; details omitted',
                                  'INTERNAL_UNEXPECTED', 'cli.ask'))
    finally:
        if signal_scope is not None:
            signal_scope.begin_critical()
        try:
            observe('close', check_signal=False)
        finally:
            if signal_scope is not None:
                signal_scope.restore()
                signum = signal_scope.signum
                signal_scope.end_critical()
                if signum is not None and not termination_handled:
                    raise SystemExit(128 + signum)


def _run_test_excerpts(args):
    from .test_output_excerpts import select_test_excerpts
    output = select_test_excerpts(args.file, args.exit_code,
                                  max_excerpt_bytes=args.max_excerpt_bytes,
                                  max_excerpts=args.max_excerpts)
    # Parser delivery success cannot hide a failed captured run, even on EPIPE.
    result = (args.exit_code if args.exit_code > 0 else
              1 if args.exit_code or output['status'] == 'INCOMPLETE'
              or output['test_status'] == 'FAILED' else 0)
    try:
        print(json.dumps(output, ensure_ascii=True, indent=2))
        sys.stdout.flush()
    except BrokenPipeError:
        silence_broken_pipe(sys.stdout)
        sys.stdout = None
    return result


def _run_test_triage_worker(args):
    from .test_triage_worker import exit_status, run_test_triage_worker
    # Profile, selector and packet checks run locally. Without both grant arguments
    # (a pinned Phase 0 grant) the composition stops before any client exists.
    result = run_test_triage_worker(args.file, args.exit_code, profile=args.profile,
                                    phase0_grant=args.phase0_grant,
                                    phase0_grant_sha256=args.phase0_grant_sha256)
    try:
        print(json.dumps(result, ensure_ascii=True, indent=2))
        sys.stdout.flush()
    except BrokenPipeError:
        silence_broken_pipe(sys.stdout)
        sys.stdout = None
    return exit_status(result)


def main(argv=None):
    try:
        args = parser_for_cli().parse_args(argv)
        if args.command == 'review-authority':
            return run_review_authority_check(args)
        if args.command == 'dispatch-check':
            return run_dispatch_check(args)
        if args.command == 'board-check':
            return run_board_check(args)
        if args.command == 'ci':
            from .mac_full_due import run_ci
            return run_ci(args)
        if args.command == 'trust':
            return run_trust(args)
        if args.command == 'task':
            task_action = TASK_ACTIONS.get(args.action)
            if task_action is not None:
                function_name, needs_fresh_source = task_action
                if needs_fresh_source:
                    from .source_freshness import require_fresh_source
                    require_fresh_source()
                return getattr(task_submission_cli, function_name)(args)
            if args.action in ('review-request','review-wait'):
                from .review_exchange import run_exchange
                return run_exchange(args)
            if args.action == 'review-bundle':
                from .review_bundle import run_review_bundle
                return run_review_bundle(args)
            if args.action == 'train':
                from . import merge_train_cli
                if args.train_action in ('build', 'merge', 'close'):
                    from .source_freshness import require_fresh_source
                    try:
                        require_fresh_source()
                    except LLMToolError as exc:
                        return merge_train_cli.source_refusal(args, exc)
                return merge_train_cli.run_task_train(args)
            return run_task_review(args) if args.action == 'review' else run_task_inspect(args)
        if args.command == 'test-excerpts':
            return _run_test_excerpts(args)
        if args.command == 'test-triage-worker':
            return _run_test_triage_worker(args)
        if args.command in ('action', 'approval'):
            return run_action_contract(args)
        if args.command == 'ask':
            return _run_ask(args)
        if args.command == 'doctor':
            from .doctor import run_doctor
            return run_doctor(strict=args.strict)
        if args.command in ('runs', 'trace'):
            return run_diagnostics(args)
        if args.command == "retrieve-context":
            return run_retrieval(args)
        if args.command == "benchmark":
            return run_benchmark(args)
        if args.command == "orchestrate":
            return run_orchestration(args)
        if args.command == "feedback":
            return run_feedback(args)
        if args.command == "validate":
            if args.action == "command":
                assessment = classify_command(args.text)
                output = {"risk": assessment.risk.value, "reason": assessment.reason}
                print(json.dumps(output, indent=2) if args.json else format_command_assessment(assessment))
                return 0
            result = validate_context_packet(_validation_packet(args.file))
            print(result.to_json() if args.json else format_validation_result(result))
            return 0 if result.valid else 1
        if args.command == "git":
            snapshot = collect_git_snapshot()
            print(snapshot.to_json() if args.json else format_git_snapshot(snapshot))
            return 0
        if args.command == "skills":
            metadata = [skill.metadata() for skill in discover_skills()]
            if args.json:
                print(json.dumps(metadata, indent=2))
            else:
                for item in metadata:
                    print(item["name"] + " (version " + str(item["version"]) + "): " + item["description"])
            return 0
        if args.command == "skill":
            skill = load_skill(args.name)
            print(json.dumps(skill.metadata(), indent=2))
            print()
            sys.stdout.write(skill.instructions)
            if not skill.instructions.endswith("\n"):
                print()
            return 0
        selected_skill = load_skill(args.skill) if getattr(args, "skill", None) is not None else None
        if args.command == "providers":
            metadata = {
                name: get_provider_capabilities(name).to_dict()
                for name in PROVIDER_REGISTRY.names()
            }
            if args.json:
                print(json.dumps(metadata, indent=2))
            else:
                for name, capabilities in metadata.items():
                    print("Provider: " + name)
                    for capability, supported in capabilities.items():
                        print("  " + capability + ": " + ("yes" if supported else "no"))
            return 0
        if args.command == "configure":
            run_configure()
            return 0
        if args.command == "config" and args.action == "path":
            print(user_config_path())
            return 0
        if args.command == "config" and args.action == "init":
            init_config(user_config_path(), args.force)
            print("llmtool: user config initialized", file=sys.stderr)
            return 0
        if args.command in TEMPLATES:
            validate_input_sources(args.text, args.file, sys.stdin)
        config = resolve_config(vars(args), require_configured=args.command not in ("config", "chat"))
        if args.command == "config":
            print(show_config(config), end="")
            return 0
        if args.command == "chat":
            history_path = (None if args.no_history else
                            Path.home() / ".local/state/llmtool/chat/history.json")
            session = ChatSession(config, selected_skill, history_path=history_path)
            if args.tui:
                # Lazy import: the default line chat never loads the optional UI.
                from .chat_tui import run_chat_tui
                return run_chat_tui(session, dev=args.dev)
            return run_chat(session)
        text = read_input(args.text, args.file, sys.stdin)
        result = create_client(config, api_key(config)).complete(messages_for(args.command, text, selected_skill))
        # Validation completes before stdout is touched, including truncation checks.
        output = result.content
        if args.command == "analyze-log" or (selected_skill is not None and selected_skill.name == "analyze-log"):
            from .log_safety import filter_log_suggestions
            output, suppressed = filter_log_suggestions(output)
            if suppressed:
                print("llmtool: analyze-log suppressed " + str(suppressed) + " unsafe or unsupported NEXT_COMMANDS suggestion(s)", file=sys.stderr)
        if args.command == "context-pack":
            packet = parse_context_packet(result.content)
            output = packet.to_json() if args.json else format_context_packet(packet)
        if args.command == "context-pack":
            if not args.json:
                output = human_output(output, sys.stdout)
        elif args.command in TEMPLATES:
            output = human_output(output, sys.stdout)
        sys.stdout.write(output + "\n")
        if args.verbose:
            for key, value in result.metadata.items():
                print(f"{key}={value}", file=sys.stderr)
        return 0
    except LLMToolError as exc:
        refusal_code = getattr(exc, 'completion_code', None)
        if (getattr(args, 'command', None) == 'task' and
                getattr(args, 'action', None) in TASK_REFUSAL_ATTRIBUTES):
            refusal_code = getattr(exc, TASK_REFUSAL_ATTRIBUTES[args.action], None)
            refusal_label = args.action
        else:
            refusal_label = 'review-prompt'
        if (getattr(args, 'command', None) == 'task' and
                getattr(args, 'action', None) in ('review-prompt', 'review-dispatch', 'review-result', 'repair', 'high-debug') and
                type(refusal_code) is str and
                re.fullmatch(r'[A-Z0-9_]{1,64}', refusal_code)):
            line = 'llmtool: ' + refusal_label + ' refused: ' + refusal_code
            detail = getattr(exc, 'high_debug_detail', None)
            if (getattr(args, 'action', None) == 'high-debug' and
                    type(detail) is str and len(detail) <= 128 and
                    re.fullmatch(r'[A-Za-z0-9_,= ]+', detail)):
                line += ' (' + detail + ')'
            print(line, file=sys.stderr)
        else:
            print("llmtool: " + str(exc), file=sys.stderr)
        return 1
    except BrokenPipeError:
        # Avoid the interpreter's flush-at-exit traceback / exit 120 noise.
        silence_broken_pipe(sys.stdout)
        sys.stdout = None
        return 0
    except (OSError, UnicodeError):
        print("llmtool: local I/O failed; details omitted", file=sys.stderr)
        return 1
    except EOFError:
        print("llmtool: input ended before configuration was complete", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("llmtool: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
