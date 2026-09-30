"""CLI wiring only: parse, resolve, read, request, and print."""

import argparse
import json
import sys

from .config import api_key, init_config, resolve_config, show_config, user_config_path
from .configure import run_configure
from .chat import ChatSession, run_chat
from .context import parse_context_packet, format_context_packet
from .errors import LLMToolError
from .inputs import read_input
from .git_intel import collect_git_snapshot, format_git_snapshot
from .prompts import TEMPLATES, messages_for
from .skills import discover_skills, load_skill
from .providers import PROVIDER_REGISTRY, create_client, get_provider_capabilities


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
        if command == "context-pack":
            child.add_argument("--json", action="store_true", help="output a validated Context Packet as JSON")
        add_options(child)
    chat = sub.add_parser("chat", help="Start a memory-only interactive chat")
    chat.add_argument("--skill", help="explicit runtime skill name")
    add_options(chat)
    git = sub.add_parser("git", help="Collect a read-only local Git snapshot offline")
    git.add_argument("--json", action="store_true", help="output snapshot as JSON")
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


def main(argv=None):
    args = parser_for_cli().parse_args(argv)
    try:
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
        if args.command == "context-pack":
            packet = parse_context_packet(result.content)
            output = packet.to_json() if args.json else format_context_packet(packet)
        sys.stdout.write(output + "\n")
        if args.verbose:
            for key, value in result.metadata.items():
                print(f"{key}={value}", file=sys.stderr)
        return 0
    except LLMToolError as exc:
        print("llmtool: " + str(exc), file=sys.stderr)
        return 1
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
