"""CLI wiring only: parse, resolve, read, request, and print."""

import argparse
import json
import os
import stat
import sys

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


def _validation_packet(filename):
    """Read only the explicit regular file, with A4's byte bound."""
    descriptor = os.open(filename, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise LLMToolError('validation --file requires a regular file')
        raw = stream.read(PACKET_LIMIT + 1)
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
    chat = sub.add_parser("chat", help="Start a memory-only interactive chat")
    chat.add_argument("--skill", help="explicit runtime skill name")
    add_options(chat)
    git = sub.add_parser("git", help="Collect a read-only local Git snapshot offline")
    git.add_argument("--json", action="store_true", help="output snapshot as JSON")
    excerpts = sub.add_parser("test-excerpts", help="Select bounded captured-test evidence offline; never run tests")
    excerpts.add_argument("--file", required=True, help="one explicit captured UTF-8 regular file")
    excerpts.add_argument("--exit-code", required=True, type=int, help="captured test exit (-127 through 255)")
    excerpts.add_argument("--json", action="store_true", required=True, help="output version-1 evidence JSON")
    excerpts.add_argument("--max-excerpt-bytes", type=int, default=2048, help="128 through 2048 original bytes per excerpt")
    excerpts.add_argument("--max-excerpts", type=int, default=32, help="1 through 32 excerpts; total capped at 16 KiB")
    add_feedback_parser(sub)
    add_benchmark_parser(sub, add_options)
    add_orchestration_parser(sub)
    add_retrieval_parser(sub)
    add_diagnostics_parser(sub)
    add_action_contract_parsers(sub)
    add_task_parser(sub)
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

    def observe(method, *values):
        if run is None:
            return None
        try:
            return getattr(run, method)(*values)
        except Exception:
            try:
                run.drop()
            except Exception:
                pass
            return None

    def store_warning():
        if requested_without_observer:
            # Observer construction itself can fail; explicit opt-in stays honest.
            try:
                from .diagnostics_store import StoreWarning
                print(StoreWarning(d.ErrorCode.DIAGNOSTICS_STORE_UNAVAILABLE).line(), file=sys.stderr)
            except Exception:
                pass
        else:
            observe('display_store_warning', sys.stderr)

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
        # Preserve the primary safe message even if secondary observation fails.
        print('llmtool: ' + str(exc), file=sys.stderr)
        mapped = observe('error', exc, code, component) or code
        observe('finish', 'incomplete' if mapped is d.ErrorCode.MODEL_OUTPUT_INCOMPLETE else 'error')
        try:
            line = observe('failure_line', mapped)
            print(line or ('llmtool: code=' + mapped.value +
                          ' run_id=unavailable trace=' +
                          ('unavailable' if requested_without_observer else 'not-saved')), file=sys.stderr)
        except Exception:
            observe('drop')
        store_warning()
        observe('display', sys.stderr)
        return 1

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
                print('llmtool: analyze-log suppressed ' + str(suppressed) +
                      ' unsafe or unsupported NEXT_COMMANDS suggestion(s)', file=sys.stderr)
        output = human_output(output, sys.stdout)
        sys.stdout.write(output + '\n')
        # Detect a buffered EPIPE before recording successful delivery.
        sys.stdout.flush()
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
        observe('close')


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


def main(argv=None):
    try:
        args = parser_for_cli().parse_args(argv)
        if args.command == 'task':
            return run_task_inspect(args)
        if args.command == 'test-excerpts':
            return _run_test_excerpts(args)
        if args.command in ('action', 'approval'):
            return run_action_contract(args)
        if args.command == 'ask':
            return _run_ask(args)
        if args.command == 'doctor':
            from .doctor import run_doctor
            return run_doctor()
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
            return run_chat(ChatSession(config, selected_skill))
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
