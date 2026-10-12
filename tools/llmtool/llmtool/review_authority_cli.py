"""Read-only host-file syntax/safety inspection; never installation or grant."""
import json
from .review_authority import read_host_file
from .task_review import envelope


def add_review_authority_parser(sub):
    parser=sub.add_parser('review-authority',help='Inspect static host-file safety; grants nothing')
    action=parser.add_subparsers(dest='action',required=True)
    check=action.add_parser('check')
    check.add_argument('--host',required=True)
    check.add_argument('--json',action='store_true',required=True)


def run_review_authority_check(args):
    try:
        reg,sha=read_host_file(args.host)
        result=envelope('REVIEW_HOST_STRUCTURE_VALID',structural_validation='SUCCESS',
                        host_digest=sha,published_pin_validation='NOT_VERIFIED')
        code=0  # Structure only; never effect/provider permission.
    except Exception as exc:
        result=envelope(getattr(exc,'review_code','REVIEW_HOST_POLICY_INVALID'))
        code=2
    print(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False))
    return code
