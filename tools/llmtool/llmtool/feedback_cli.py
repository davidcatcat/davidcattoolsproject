"""Offline feedback CLI adapter. No provider/config/credential resolution."""

import json

from .errors import LLMToolError
from .feedback import (FailureTag, FeedbackCase, FeedbackStatus, SkillVersionState,
                       feedback_skill_state, transition_feedback, parse_regression_case,
                       evaluate_regression)
from .feedback_files import read_feedback_text, read_feedback_file, write_feedback_file
from .skills import load_skill


def add_feedback_parser(sub):
    feedback = sub.add_parser('feedback', help='Capture, review and evaluate skill feedback offline')
    actions = feedback.add_subparsers(dest='action', required=True)
    capture = actions.add_parser('capture', help='Capture explicitly supplied, redacted failure material')
    capture.add_argument('--skill', required=True)
    for option in ('input-file', 'output-file', 'expected-file'):
        capture.add_argument('--' + option, required=True)
    capture.add_argument('--tag', action='append', default=[], choices=[t.value for t in FailureTag])
    capture.add_argument('--case-file', help='create a new explicit .json case; never overwrite')
    for action in ('show', 'validate'):
        child = actions.add_parser(action, help='Inspect feedback data and packaged skill linkage')
        child.add_argument('--file', required=True)
        child.add_argument('--json', action='store_true')
    review = actions.add_parser('review', help='Record human review notes, atomically replacing the explicit case')
    review.add_argument('--file', required=True)
    review.add_argument('--notes-file', required=True)
    regression = actions.add_parser('regression', help='Evaluate supplied output with fixed declarative assertions')
    regression.add_argument('--case', required=True)
    regression.add_argument('--output-file', required=True)
    regression.add_argument('--json', action='store_true')


def run_feedback(args):
    if args.action == 'capture':
        skill = load_skill(args.skill)
        case = FeedbackCase.capture(skill.name, skill.version, read_feedback_text(args.input_file),
                                    read_feedback_text(args.output_file), read_feedback_text(args.expected_file), args.tag)
        if args.case_file:
            write_feedback_file(args.case_file, case)
        print(case.to_json())
        return 0
    if args.action == 'regression':
        case = parse_regression_case(read_feedback_text(args.case))
        result = evaluate_regression(case, read_feedback_text(args.output_file))
        if args.json:
            print(result.to_json())
        else:
            print('PASS' if result.passed else 'FAIL')
            for item in result.results:
                print(str(item.index) + ' ' + item.type.value + ': ' + item.message)
        return 0 if result.passed else 1
    case = read_feedback_file(args.file)
    state = feedback_skill_state(case)
    if args.action == 'review':
        if state == SkillVersionState.NEWER_THAN_CURRENT:
            raise LLMToolError('feedback references a future packaged skill version')
        reviewed = transition_feedback(case, FeedbackStatus.REVIEWED, read_feedback_text(args.notes_file))
        write_feedback_file(args.file, reviewed, previous=case)
        print(reviewed.to_json())
        return 0
    valid = state != SkillVersionState.NEWER_THAN_CURRENT
    if args.action == 'show':
        if args.json:
            print(case.to_json())
        else:
            print('Case: ' + case.id + '\nSkill: ' + case.skill + ' v' + str(case.skill_version) +
                  '\nVersion: ' + state.value + '\nStatus: ' + case.status.value)
            print('Tags: ' + ', '.join(t.value for t in case.failure_tags))
            for name in ('input', 'actual_output', 'expected_behavior', 'review_notes'):
                print(name.upper() + ':\n' + getattr(case, name))
            print('VALIDATOR_FINDINGS:\n' + json.dumps([f.to_dict() for f in case.validator_findings], ensure_ascii=True, indent=2))
    else:
        data = {'valid': valid, 'skill_version_state': state.value, 'case_id': case.id}
        print(json.dumps(data, indent=2) if args.json else ('VALID' if valid else 'INVALID') + '\nVersion: ' + state.value)
    return 0 if valid else 1
