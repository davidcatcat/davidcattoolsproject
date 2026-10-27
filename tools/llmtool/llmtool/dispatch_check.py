"""Minimal policy decision command for trusted PM scripts."""

import json

from . import worker_dispatch


def add_dispatch_check_parser(subparsers):
    parser = subparsers.add_parser(
        'dispatch-check', help='Check Owner dispatch policy without exposing its contents')
    parser.add_argument('worker')
    parser.add_argument('--task', dest='task_id')
    parser.add_argument('--owner-ref', dest='owner_assignment_ref')
    return parser


def run_dispatch_check(args):
    decision = worker_dispatch.evaluate(
        args.worker, args.owner_assignment_ref, args.task_id)
    print(json.dumps({
        'worker': decision.worker_key,
        'allowed': decision.allowed,
        'reason': decision.reason_code,
        'policy_digest': decision.policy_sha256,
    }, separators=(',', ':'), ensure_ascii=True))
    return 0 if decision.allowed else 3
