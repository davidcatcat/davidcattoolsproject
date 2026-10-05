"""Explicit A9 orchestration CLI. Dry-run is structural and fully offline."""

import sys

from .errors import LLMToolError
from .feedback_files import read_feedback_text
from .inputs import read_input
from .orchestration import (
    format_orchestration_result,
    format_plan,
    parse_orchestration_plan,
    run_orchestration_plan,
)
from .terminal import human_output


def add_orchestration_parser(sub):
    parser = sub.add_parser("orchestrate", help="Run a bounded role/profile text pipeline")
    parser.add_argument("--plan", required=True, help="Explicit orchestration plan JSON")
    parser.add_argument("text", nargs="?", help="Direct task text")
    parser.add_argument("--file", help="Read one UTF-8 regular task file")
    parser.add_argument("--json", action="store_true", help="Emit stable result JSON")
    parser.add_argument("--dry-run", action="store_true", help="Validate plan structure only; no config/provider access")


def run_orchestration(args):
    plan = parse_orchestration_plan(read_feedback_text(args.plan, context='orchestration plan'))
    if args.dry_run:
        if args.text is not None or args.file is not None:
            raise LLMToolError("orchestration dry-run accepts the plan only")
        output = plan.to_json() if args.json else human_output(format_plan(plan), sys.stdout)
        print(output)
        return 0
    task = read_input(args.text, args.file, sys.stdin)
    result = run_orchestration_plan(plan, task)
    output = result.to_json() if args.json else human_output(format_orchestration_result(result), sys.stdout)
    print(output)
    return 0
