"""Explicit A10 invocation; complete validation before any stdout."""

import sys

from .retrieval import retrieve_context, format_retrieval_result
from .terminal import human_output


def add_retrieval_parser(sub):
    parser = sub.add_parser("retrieve-context", help="Retrieve scoped evidence with exactly two text requests")
    parser.add_argument("--root", required=True, help="Explicit physical directory root")
    parser.add_argument("--scope", required=True, action="append", help="Authorized relative file/directory; repeatable")
    parser.add_argument("--planner-provider", required=True, help="Persisted planner profile")
    parser.add_argument("--synthesizer-provider", required=True, help="Persisted synthesizer profile")
    parser.add_argument("text", nargs="?", help="Direct task text")
    parser.add_argument("--file", help="Explicit UTF-8 task file")
    parser.add_argument("--json", action="store_true", help="Bounded result JSON; no raw document contents")


def run_retrieval(args):
    result = retrieve_context(args.root, args.scope, args.planner_provider,
                              args.synthesizer_provider, args.text, args.file, sys.stdin)
    output = result.to_json() if args.json else human_output(format_retrieval_result(result), sys.stdout)
    print(output)
    return 0
