"""Explicit benchmark file adapter; provider resolution is confined to live mode."""

import time
import sys
from pathlib import Path

from .benchmark import (BenchmarkSpec, BenchmarkSuiteResult, evaluate_benchmark,
                        parse_benchmark_spec, parse_benchmark_runtime,
                        parse_benchmark_suite, normalize_runtime, format_benchmark)
from .feedback import parse_regression_case, JSON_LIMIT
from .feedback_files import read_feedback_text
from .errors import LLMToolError
from .terminal import human_output


def add_benchmark_parser(sub, add_options):
    parser = sub.add_parser('benchmark', help='Measure explicit outputs; never execute suggestions')
    actions = parser.add_subparsers(dest='action', required=True)
    for action in ('evaluate', 'live'):
        child = actions.add_parser(action, help='Offline supplied output' if action == 'evaluate' else 'Explicit single provider request')
        source = child.add_mutually_exclusive_group(required=True)
        source.add_argument('--case', help='Explicit A7 RegressionCase file')
        source.add_argument('--spec', help='Explicit benchmark specification file')
        child.add_argument('--json', action='store_true')
        if action == 'evaluate':
            child.add_argument('--output-file', required=True)
            child.add_argument('--runtime-file', help='Explicit trusted normalized runtime data')
        else:
            add_options(child)
    suite = actions.add_parser('suite', help='Offline explicit bounded manifest')
    suite.add_argument('--manifest', required=True)
    suite.add_argument('--json', action='store_true')


def _spec(filename, is_spec=False):
    text = read_feedback_text(filename)
    if is_spec:
        return parse_benchmark_spec(text)
    # Manifests permit either exact schema; malformed data still fails closed.
    from .feedback import _parse
    data = _parse(text)
    if isinstance(data, dict) and 'regression' in data:
        return parse_benchmark_spec(text)
    case = parse_regression_case(text)
    return BenchmarkSpec(case, case.id)


def collect_live_benchmark(spec, options):
    # No offline call reaches config, credential lookup or client construction.
    from .config import resolve_config, api_key
    from .providers import create_client
    from .prompts import messages_for
    from .skills import load_skill
    skill = load_skill(spec.regression.skill)
    if skill.version < spec.regression.minimum_skill_version:
        raise LLMToolError('regression requires a newer packaged skill version')
    messages = messages_for('ask', spec.regression.input, skill)
    config = resolve_config(options)
    client = create_client(config, api_key(config))
    started = time.monotonic()
    reply = client.complete(messages)
    elapsed = round((time.monotonic() - started) * 1000)
    from .providers.base import Reply
    if type(reply) is not Reply:
        raise LLMToolError('benchmark provider returned unsupported reply data')
    runtime = normalize_runtime(reply.metadata, elapsed)
    return evaluate_benchmark(spec, reply.content, runtime)


def run_benchmark(args):
    if args.action == 'suite':
        suite = parse_benchmark_suite(read_feedback_text(args.manifest))
        base = Path(args.manifest).absolute().parent
        results = []
        ids = set()
        size = 0
        for entry in suite.entries:
            spec = _spec(base / entry.case_file)
            if spec.regression.id in ids:
                raise LLMToolError('benchmark suite contains duplicate case identifiers')
            ids.add(spec.regression.id)
            result = evaluate_benchmark(spec, read_feedback_text(base / entry.output_file), entry.runtime)
            size += len(result.to_json().encode('utf-8'))
            if size > JSON_LIMIT:
                raise LLMToolError('benchmark suite result exceeds 1 MiB')
            results.append(result)
        result = BenchmarkSuiteResult(results)
    else:
        spec = _spec(args.spec if args.spec else args.case, bool(args.spec))
        if args.action == 'live':
            result = collect_live_benchmark(spec, vars(args))
        else:
            runtime = parse_benchmark_runtime(read_feedback_text(args.runtime_file)) if args.runtime_file else None
            result = evaluate_benchmark(spec, read_feedback_text(args.output_file), runtime)
    # Complete validation/serialization before touching stdout, including suite failures.
    output = result.to_json() if args.json else human_output(format_benchmark(result), sys.stdout)
    print(output)
    return 0 if result.passed else 1
