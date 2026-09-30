"""Offline human-review workflow and supplied-output regression boundaries."""
import ast
import copy
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import feedback as f
from llmtool import feedback_files as files
from llmtool.cli import main
from llmtool.errors import LLMToolError
from llmtool.skills import load_skill
from llmtool.validator import CommandRisk, Severity, ValidationFinding

FIXTURES = Path(__file__).resolve().parents[1] / 'feedback'
PACKET = {'facts': ['HTTP 500 observed'], 'evidence': [{'statement': 'HTTP 500 observed', 'source': 'supplied log', 'locator': 'line 1'}],
          'inferences': [], 'hypotheses': ['Configuration mismatch is possible'], 'unknowns': ['Previous exception missing'],
          'verify_next': ['stat /etc/company/app/config.yaml']}


def feedback(**changes):
    return f.FeedbackCase.capture(**{**dict(skill='context-pack', skill_version=2, input='  HTTP 500 observed\n',
        actual_output='invalid output', expected_behavior='Preserve observations and uncertainty',
        failure_tags=[f.FailureTag.FORMAT_INVALID]), **changes})


def regression(assertions, **changes):
    return f.RegressionCase.create(**{**dict(source_feedback_ids=[feedback().id], skill='context-pack',
        minimum_skill_version=2, input='HTTP 500 observed', assertions=assertions), **changes})


class FeedbackModelTests(unittest.TestCase):
    def test_valid_immutable_detached(self):
        tags = [f.FailureTag.OTHER]
        finding = ValidationFinding('EVIDENCE_MISSING', Severity.ERROR, 'Evidence absent', 'evidence')
        findings = [finding]
        case = feedback(failure_tags=tags, validator_findings=findings)
        tags.clear(); findings.clear()
        self.assertEqual(case.failure_tags, (f.FailureTag.OTHER,))
        self.assertEqual(case.validator_findings, (finding,))
        with self.assertRaises(FrozenInstanceError):
            case.input = 'changed'
        data = case.to_dict(); data['validator_findings'][0]['message'] = 'changed'
        self.assertEqual(case.validator_findings[0].message, 'Evidence absent')

    def test_deterministic_id_algorithm(self):
        case = feedback()
        canonical = json.dumps(case.identifying_fields(), ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)
        self.assertEqual(case.id, hashlib.sha256(canonical.encode()).hexdigest()[:24])
        self.assertEqual(case, feedback())
        self.assertNotEqual(case.id, feedback(input='different').id)
        self.assertNotEqual(case.id, feedback(skill_version=1).id)
        self.assertNotEqual(case.id, feedback(failure_tags=[f.FailureTag.OTHER]).id)

    def test_id_covers_every_identifying_field(self):
        base = feedback()
        variants = [feedback(skill='analyze-log'), feedback(actual_output='changed'), feedback(expected_behavior='changed'),
                    feedback(validator_findings=[ValidationFinding('X', Severity.INFO, 'note', 'category')])]
        self.assertTrue(all(item.id != base.id for item in variants))

    def test_json_roundtrip_exact_unicode_empty_output(self):
        case = feedback(input='  證據\t500\n ', actual_output='')
        self.assertEqual(f.parse_feedback_case(case.to_json()), case)
        self.assertEqual(case.to_json(), feedback(input=case.input, actual_output='').to_json())
        self.assertEqual(json.loads(case.to_json())['input'], case.input)

    def test_missing_fields(self):
        data = feedback().to_dict()
        for key in data:
            value = copy.deepcopy(data); del value[key]
            with self.subTest(key=key), self.assertRaises(LLMToolError):
                f.parse_feedback_case(json.dumps(value))

    def test_extra_fields(self):
        for name in ('reasoning', 'api_key', 'command', 'hook', 'PRIVATE'):
            with self.subTest(name=name), self.assertRaises(LLMToolError) as error:
                f.parse_feedback_case(json.dumps({**feedback().to_dict(), name: 'PRIVATE'}))
            self.assertNotIn('PRIVATE', str(error.exception))

    def test_invalid_names_versions_types_tags_status(self):
        data = feedback().to_dict()
        invalid = [('skill', '../escape'), ('skill', '/absolute'), ('skill_version', True), ('skill_version', 0),
            ('skill_version', 2**40), ('failure_tags', ['PRIVATE']), ('failure_tags', ['OTHER', 'OTHER']),
            ('failure_tags', 'OTHER'), ('status', 'PRIVATE'), ('validator_findings', [{}]),
            ('input', ''), ('actual_output', None), ('review_notes', None), ('id', '0'*24)]
        for key, value in invalid:
            with self.subTest(key=key, value=value), self.assertRaises(LLMToolError):
                f.parse_feedback_case(json.dumps({**data, key: value}))

    def test_malformed_duplicate_constants_depth(self):
        raw = feedback().to_json()
        invalid = ['', 'PRIVATE malformed', '{}', '[]', raw + 'extra', raw.replace('"id":', '"id":"x", "id":', 1),
                   raw.replace('"skill_version": 2', '"skill_version": NaN'), '['*1000+']'*1000]
        for text in invalid:
            with self.subTest(text=text[:40]), self.assertRaises(LLMToolError) as error:
                f.parse_feedback_case(text)
            self.assertNotIn('PRIVATE', str(error.exception))

    def test_nested_duplicate_findings_and_wrong_types(self):
        case = feedback(validator_findings=[ValidationFinding('X', Severity.INFO, 'note', 'category')])
        raw = case.to_json().replace('"code":', '"code":"other", "code":', 1)
        with self.assertRaisesRegex(LLMToolError, 'duplicate'):
            f.parse_feedback_case(raw)
        for value in (None, 'finding', {}, ValidationFinding('X', 'PRIVATE', 'note', 'category')):
            with self.subTest(value=value), self.assertRaises(LLMToolError):
                feedback(validator_findings=[value])

    def test_text_utf8_controls_and_bounds(self):
        for value in ('PRIVATE\x00', 'PRIVATE\x1b', '\x7f', '\x9b', '\ud800', 'x'*(f.TEXT_LIMIT+1), '日'*(f.TEXT_LIMIT//3+1)):
            with self.subTest(value=value[:20]), self.assertRaises(LLMToolError) as error:
                feedback(input=value)
            self.assertNotIn('PRIVATE', str(error.exception))
        for value in (None, [], {}, 1):
            with self.assertRaises(LLMToolError):
                feedback(input=value)

    def test_json_and_item_bounds(self):
        for text in ('x'*(f.JSON_LIMIT+1), '日'*(f.JSON_LIMIT//3+1)):
            with self.assertRaises(LLMToolError):
                f.parse_feedback_case(text)
        with self.assertRaises(LLMToolError):
            feedback(validator_findings=[ValidationFinding('X', Severity.INFO, 'note', 'category')]*(f.ITEM_LIMIT+1))
        with self.assertRaises(LLMToolError):
            replace(feedback(input='日'*(f.TEXT_LIMIT//3), actual_output='日'*(f.TEXT_LIMIT//3),
                             expected_behavior='日'*(f.TEXT_LIMIT//3)), review_notes='日'*(f.TEXT_LIMIT//3))

    def test_recognizable_credentials_rejected_without_echo(self):
        for text in ('-----BEGIN RSA PRIVATE KEY-----', 'Bearer ' + 'x'*30, 'sk-'+'x'*30, 'AKIA'+'A'*16,
                     'PASSWORD=private-value', '{"api_key": "private-value"}', 'aws_secret_access_key=private-value'):
            with self.subTest(text=text), self.assertRaises(LLMToolError) as error:
                feedback(actual_output=text)
            self.assertNotIn(text, str(error.exception))

    def test_redacted_assignments_and_command_paths_remain_data(self):
        text = 'api_key: <REDACTED>\nPASSWORD=***\ntoken=null\ncat ~/.aws/credentials'
        self.assertEqual(feedback(actual_output=text).actual_output, text)

    def test_all_explicit_tags(self):
        self.assertEqual(len(f.FailureTag), 12)
        case = feedback(failure_tags=list(f.FailureTag))
        self.assertEqual(f.parse_feedback_case(case.to_json()).failure_tags, tuple(f.FailureTag))

    def test_skill_current_older_future_missing(self):
        for version, state in [(2, 'CURRENT'), (1, 'OLDER_THAN_CURRENT'), (3, 'NEWER_THAN_CURRENT')]:
            self.assertEqual(f.feedback_skill_state(feedback(skill_version=version)).value, state)
        with self.assertRaisesRegex(LLMToolError, 'unknown skill'):
            f.feedback_skill_state(feedback(skill='missing'))

    def test_review_forward_lifecycle_id_notes(self):
        case = feedback()
        for status in list(f.FeedbackStatus)[1:]:
            case = f.transition_feedback(case, status, '  Reviewed\n' if status == f.FeedbackStatus.REVIEWED else None)
            self.assertEqual(case.id, feedback().id)
            self.assertEqual(case.review_notes, '  Reviewed\n')
            self.assertEqual(f.parse_feedback_case(case.to_json()), case)
        self.assertEqual(case.status, f.FeedbackStatus.CLOSED)

    def test_review_invalid_transitions(self):
        for status in (f.FeedbackStatus.CAPTURED, f.FeedbackStatus.PATCH_PROPOSED, f.FeedbackStatus.CLOSED, 'PRIVATE'):
            with self.assertRaises(LLMToolError):
                f.transition_feedback(feedback(), status, 'review')
        with self.assertRaises(LLMToolError):
            f.transition_feedback(feedback(), f.FeedbackStatus.REVIEWED, '')
        closed = replace(feedback(), status=f.FeedbackStatus.CLOSED, review_notes='reviewed')
        with self.assertRaises(LLMToolError):
            f.transition_feedback(closed, f.FeedbackStatus.CAPTURED)

    def test_proposal_model_roundtrip_and_no_skill_changes(self):
        skill = load_skill('context-pack')
        reviewed = f.transition_feedback(feedback(), f.FeedbackStatus.REVIEWED, 'human review')
        proposal = f.propose_skill_patch(reviewed, 'Keep uncertainty', ['Keep unknowns'], ['Separate facts from hypotheses'])
        self.assertEqual((proposal.from_version, proposal.proposed_version), (2, 3))
        self.assertEqual(proposal.linked_feedback_case_ids, (reviewed.id,))
        self.assertEqual(f.parse_skill_patch_proposal(proposal.to_json()), proposal)
        self.assertEqual(load_skill('context-pack'), skill)
        with self.assertRaises(FrozenInstanceError):
            proposal.rationale = 'changed'

    def test_proposal_version_and_review_preconditions(self):
        for target in (1, 2, 4, 0, True):
            with self.assertRaises(LLMToolError):
                f.SkillPatchProposal('context-pack', 2, target, 'rationale', ['rule'], [], [feedback().id])
        for case in (feedback(), f.transition_feedback(feedback(skill_version=1), f.FeedbackStatus.REVIEWED, 'review')):
            with self.assertRaises(LLMToolError):
                f.propose_skill_patch(case, 'rationale', ['rule'])

    def test_proposal_strict_schema_rules_links(self):
        proposal = f.SkillPatchProposal('context-pack', 2, 3, 'rationale', ['rule'], [], [feedback().id])
        for key, value in [('added_rules', []), ('added_rules', ['']), ('linked_feedback_case_ids', []),
                           ('linked_feedback_case_ids', ['../escape']), ('linked_feedback_case_ids', [feedback().id]*2),
                           ('rationale', '')]:
            with self.subTest(key=key), self.assertRaises(LLMToolError):
                f.parse_skill_patch_proposal(json.dumps({**proposal.to_dict(), key: value}))
        with self.assertRaises(LLMToolError):
            f.parse_skill_patch_proposal(json.dumps({**proposal.to_dict(), 'patch': 'PRIVATE'}))


class RegressionTests(unittest.TestCase):
    def test_valid_immutable_deterministic_roundtrip(self):
        assertions = [f.RegressionAssertion('CONTEXT_VALID')]
        case = regression(assertions); assertions.clear()
        self.assertEqual(case, f.parse_regression_case(case.to_json()))
        self.assertEqual(len(case.assertions), 1)
        with self.assertRaises(FrozenInstanceError):
            case.input = 'changed'
        self.assertNotEqual(case.id, regression(case.assertions, input='different').id)

    def test_assertion_allowlist_and_argument_schema(self):
        for kind, value, command in [('PRIVATE', None, None), ('CONTEXT_VALID', 'extra', None),
            ('TEXT_CONTAINS', None, None), ('TEXT_CONTAINS', 'value', 'command'),
            ('COMMAND_RISK_EQUALS', 'PRIVATE', None), ('COMMAND_RISK_EQUALS', 'SAFE_READ', '')]:
            with self.subTest(kind=kind), self.assertRaises(LLMToolError):
                f.RegressionAssertion(kind, value, command)
        self.assertEqual(len(f.AssertionType), 12)

    def test_regression_strict_schema_and_bounds(self):
        case = regression([f.RegressionAssertion('CONTEXT_VALID')])
        for key in case.to_dict():
            data = case.to_dict(); del data[key]
            with self.assertRaises(LLMToolError):
                f.parse_regression_case(json.dumps(data))
        for value in ([], [{}], [{'type':'CONTEXT_VALID','value':None,'command':None,'hook':'PRIVATE'}]):
            with self.assertRaises(LLMToolError):
                f.parse_regression_case(json.dumps({**case.to_dict(), 'assertions': value}))
        with self.assertRaises(LLMToolError):
            regression([f.RegressionAssertion('CONTEXT_VALID')]*(f.ASSERTION_LIMIT+1))

    def test_exact_category_assertions(self):
        assertions = [f.RegressionAssertion('FACT_CONTAINS', 'HTTP 500 observed'),
            f.RegressionAssertion('FACT_NOT_CONTAINS', 'Configuration mismatch is possible'),
            f.RegressionAssertion('HYPOTHESIS_CONTAINS', 'Configuration mismatch is possible'),
            f.RegressionAssertion('HYPOTHESIS_NOT_CONTAINS', 'HTTP 500 observed'),
            f.RegressionAssertion('UNKNOWN_CONTAINS', 'Previous exception missing'),
            f.RegressionAssertion('VERIFY_NEXT_CONTAINS', 'stat /etc/company/app/config.yaml')]
        self.assertTrue(f.evaluate_regression(regression(assertions), json.dumps(PACKET)).passed)
        result = f.evaluate_regression(regression([f.RegressionAssertion('FACT_CONTAINS','HTTP 500')]), json.dumps(PACKET))
        self.assertFalse(result.passed)  # Exact element, not substring/semantic equivalence.

    def test_context_valid_and_invalid_support(self):
        case = regression([f.RegressionAssertion('CONTEXT_VALID')])
        self.assertTrue(f.evaluate_regression(case, json.dumps(PACKET)).passed)
        self.assertFalse(f.evaluate_regression(case, json.dumps({**PACKET, 'evidence':[]})).passed)

    def test_finding_presence_absence(self):
        case = regression([f.RegressionAssertion('FINDING_PRESENT', 'EVIDENCE_MISSING'),
                           f.RegressionAssertion('FINDING_ABSENT', 'CATEGORY_COLLISION')])
        result = f.evaluate_regression(case, json.dumps({**PACKET, 'evidence':[]}))
        self.assertTrue(result.passed)

    def test_text_contains_not_contains(self):
        case = regression([f.RegressionAssertion('TEXT_CONTAINS','answer'), f.RegressionAssertion('TEXT_NOT_CONTAINS','fabricated')])
        self.assertTrue(f.evaluate_regression(case, '  Unicode 日 answer  ').passed)

    def test_command_entire_candidate_uses_a6(self):
        for command, risk in [('cat /etc/company/app/config.yaml','SENSITIVE_READ'), ('stat /etc/company/app/config.yaml','SAFE_READ')]:
            case = regression([f.RegressionAssertion('COMMAND_RISK_EQUALS', risk)])
            self.assertTrue(f.evaluate_regression(case, command+'\n').passed)

    def test_command_selector_presence_exact_and_packet(self):
        command = 'stat /etc/company/app/config.yaml'
        case = regression([f.RegressionAssertion('COMMAND_RISK_EQUALS','SAFE_READ',command)])
        for output in (command+'\n', json.dumps(PACKET)):
            self.assertTrue(f.evaluate_regression(case, output).passed)
        for output in ('No command', command+' extra', 'prefix '+command):
            result = f.evaluate_regression(case, output)
            self.assertFalse(result.passed)
            self.assertIn('absent', result.results[0].message)

    def test_malformed_candidate_negative_assertions_fail_closed(self):
        case = regression([f.RegressionAssertion('CONTEXT_VALID'), f.RegressionAssertion('FACT_NOT_CONTAINS','x'),
                           f.RegressionAssertion('FINDING_ABSENT','EVIDENCE_MISSING')])
        for output in ('PRIVATE_RAW', '', '{}', '{"facts":NaN}'):
            result = f.evaluate_regression(case, output)
            self.assertFalse(result.passed)
            self.assertTrue(all(not r.passed for r in result.results))
            self.assertNotIn('PRIVATE_RAW', result.to_json())

    def test_stable_order_json_result(self):
        case = regression([f.RegressionAssertion('TEXT_CONTAINS','yes'), f.RegressionAssertion('TEXT_NOT_CONTAINS','no')])
        result = f.evaluate_regression(case, 'yes')
        self.assertEqual(result.to_json(), f.evaluate_regression(case, 'yes').to_json())
        self.assertEqual([r.index for r in result.results], [0,1])
        self.assertTrue(result.passed)

    def test_skill_linkage_preconditions(self):
        for skill, version in [('missing', 1), ('context-pack', 3)]:
            with self.assertRaises(LLMToolError):
                f.evaluate_regression(regression([f.RegressionAssertion('TEXT_CONTAINS','x')], skill=skill, minimum_skill_version=version),'x')
        self.assertTrue(f.evaluate_regression(regression([f.RegressionAssertion('TEXT_CONTAINS','x')], minimum_skill_version=1),'x').passed)

    def test_candidate_bounds_and_controls(self):
        case = regression([f.RegressionAssertion('TEXT_CONTAINS','x')])
        for output in ('x'*(f.JSON_LIMIT+1), 'PRIVATE\x00', '\ud800'):
            with self.assertRaises(LLMToolError):
                f.evaluate_regression(case, output)

    def test_historical_and_quality_fixtures(self):
        for name, extension in [('sensitive-read','txt'), ('context-quality','json')]:
            case = f.parse_regression_case((FIXTURES/(name+'.regression.json')).read_text())
            source = f.parse_feedback_case((FIXTURES/(name+'.feedback.json')).read_text())
            self.assertEqual(case.source_feedback_ids, (source.id,))
            self.assertEqual(case.skill, source.skill)
            self.assertEqual(case.minimum_skill_version, source.skill_version)
            output = (FIXTURES/(name+'.candidate.'+extension)).read_text()
            self.assertTrue(f.evaluate_regression(case, output).passed)
        proposal = f.parse_skill_patch_proposal((FIXTURES/'sensitive-read.proposal.json').read_text())
        self.assertEqual(proposal.proposed_version, proposal.from_version+1)

    def test_hostile_candidate_no_execution(self):
        marker = Path(tempfile.gettempdir())/'llmtool-feedback-must-not-exist-marker'
        output = 'touch '+str(marker)+'; git reset --hard; echo "$(whoami)"'
        case = regression([f.RegressionAssertion('COMMAND_RISK_EQUALS','DESTRUCTIVE')])
        with patch('subprocess.run', side_effect=AssertionError), patch('subprocess.Popen', side_effect=AssertionError), patch('os.system', side_effect=AssertionError):
            self.assertTrue(f.evaluate_regression(case, output).passed)
            self.assertEqual(feedback(actual_output=output).actual_output, output)
        self.assertFalse(marker.exists())


class FeedbackFileCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.case_file = self.root/'case.json'
        self.case_file.write_text(feedback().to_json(), encoding='utf-8')
        self.notes = self.root/'notes.txt'; self.notes.write_text('  Human review\n', encoding='utf-8')
        for name, text in [('input.txt','  incident 日\n'), ('output.txt','failed output'), ('expected.txt','Preserve facts')]:
            (self.root/name).write_text(text,encoding='utf-8')

    def cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            for target in ('llmtool.cli.resolve_config', 'llmtool.cli.api_key', 'llmtool.cli.create_client',
                           'llmtool.cli.collect_git_snapshot', 'subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket'):
                stack.enter_context(patch(target, side_effect=AssertionError('offline boundary violated')))
            stack.enter_context(redirect_stdout(out)); stack.enter_context(redirect_stderr(err))
            code = main(['feedback']+args)
        return code, out.getvalue(), err.getvalue()

    def capture_args(self):
        return ['capture','--skill','context-pack','--input-file',str(self.root/'input.txt'),
            '--output-file',str(self.root/'output.txt'),'--expected-file',str(self.root/'expected.txt'),'--tag','FORMAT_INVALID']

    def test_capture_json_stdout_no_implicit_file(self):
        before = sorted(self.root.iterdir())
        code,out,err = self.cli(self.capture_args())
        self.assertEqual((code,err),(0,''))
        case = f.parse_feedback_case(out)
        self.assertEqual((case.skill,case.skill_version,case.input),('context-pack',2,'  incident 日\n'))
        self.assertEqual(sorted(self.root.iterdir()),before)

    def test_capture_explicit_atomic_create_0600_no_overwrite(self):
        target = self.root/'captured.json'
        code,out,err = self.cli(self.capture_args()+['--case-file',str(target)])
        self.assertEqual((code,err),(0,''))
        self.assertEqual(target.read_text(),out)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o600)
        original = target.read_bytes()
        code,out,err = self.cli(self.capture_args()+['--case-file',str(target)])
        self.assertEqual((code,out),(1,'')); self.assertEqual(target.read_bytes(),original)
        self.assertFalse(list(self.root.glob('.llmtool-feedback-*')))

    def test_show_human_json_and_validate(self):
        for action in ('show','validate'):
            for json_flag in ([],['--json']):
                code,out,err = self.cli([action,'--file',str(self.case_file)]+json_flag)
                self.assertEqual((code,err),(0,'')); self.assertIn('CURRENT',out) if action=='validate' or not json_flag else None
                if action=='show' and json_flag:
                    self.assertEqual(f.parse_feedback_case(out),feedback())

    def test_version_old_future_missing(self):
        for version,code,state in [(1,0,'OLDER_THAN_CURRENT'),(3,1,'NEWER_THAN_CURRENT')]:
            self.case_file.write_text(feedback(skill_version=version).to_json())
            status,out,err = self.cli(['validate','--file',str(self.case_file),'--json'])
            self.assertEqual((status,err),(code,'')); self.assertEqual(json.loads(out)['skill_version_state'],state)
        self.case_file.write_text(feedback(skill='missing').to_json())
        self.assertEqual(self.cli(['show','--file',str(self.case_file)])[0],1)

    def test_review_atomic_preserves_notes_permissions_id(self):
        self.case_file.chmod(0o640)
        code,out,err = self.cli(['review','--file',str(self.case_file),'--notes-file',str(self.notes)])
        self.assertEqual((code,err),(0,''))
        case = f.parse_feedback_case(out)
        self.assertEqual((case.id,case.review_notes,case.status),(feedback().id,'  Human review\n',f.FeedbackStatus.REVIEWED))
        self.assertEqual(files.read_feedback_file(self.case_file),case)
        self.assertEqual(stat.S_IMODE(self.case_file.stat().st_mode),0o640)
        self.assertFalse(list(self.root.glob('.llmtool-feedback-*')))

    def test_review_invalid_and_future_preserve_file(self):
        for case in (f.transition_feedback(feedback(),f.FeedbackStatus.REVIEWED,'reviewed'),feedback(skill_version=3)):
            self.case_file.write_text(case.to_json())
            original = self.case_file.read_bytes()
            code,out,err = self.cli(['review','--file',str(self.case_file),'--notes-file',str(self.notes)])
            self.assertEqual((code,out),(1,'')); self.assertEqual(self.case_file.read_bytes(),original)

    def test_regression_cli_human_json_pass_fail(self):
        for name,extension in [('sensitive-read','txt'),('context-quality','json')]:
            args=['regression','--case',str(FIXTURES/(name+'.regression.json')),'--output-file',str(FIXTURES/(name+'.candidate.'+extension))]
            for flag in ([],['--json']):
                code,out,err = self.cli(args+flag)
                self.assertEqual((code,err),(0,''))
                self.assertTrue(json.loads(out)['passed']) if flag else self.assertTrue(out.startswith('PASS'))
        candidate=self.root/'bad.txt'; candidate.write_text('PRIVATE_MALFORMED')
        code,out,err=self.cli(['regression','--case',str(FIXTURES/'context-quality.regression.json'),'--output-file',str(candidate),'--json'])
        self.assertEqual((code,err),(1,'')); self.assertFalse(json.loads(out)['passed']); self.assertNotIn('PRIVATE_MALFORMED',out)

    def test_regular_file_bounds_utf8_errors_sanitized(self):
        invalid=[self.root/'missing',self.root]
        for name,raw in [('bad.json',b'PRIVATE malformed'),('binary.json',b'\xff'),('large.json',b'x'*(f.JSON_LIMIT+1))]:
            path=self.root/name; path.write_bytes(raw); invalid.append(path)
        fifo=self.root/'fifo';os.mkfifo(fifo);invalid.append(fifo)
        for path in invalid:
            with self.subTest(path=path):
                code,out,err=self.cli(['show','--file',str(path)])
                self.assertEqual((code,out),(1,''));self.assertNotIn('PRIVATE',err);self.assertNotIn('Traceback',err)

    def test_symlink_file_parent_and_traversal_rejected(self):
        link=self.root/'link.json';link.symlink_to(self.case_file)
        directory=self.root/'linked';directory.symlink_to(self.root,target_is_directory=True)
        for path in (link,directory/'case.json',self.root/'x'/'..'/'case.json'):
            code,out,err=self.cli(['show','--file',str(path)])
            self.assertEqual((code,out),(1,''))
        before=self.case_file.read_bytes()
        self.assertEqual(self.cli(self.capture_args()+['--case-file',str(link)])[0],1)
        self.assertEqual(self.case_file.read_bytes(),before)

    def test_no_skill_md_or_nonjson_write(self):
        target=self.root/'SKILL.md';target.write_text('original skill')
        code,out,err=self.cli(self.capture_args()+['--case-file',str(target)])
        self.assertEqual((code,out),(1,''));self.assertEqual(target.read_text(),'original skill')

    def test_review_intervening_change_and_atomic_failure(self):
        original=feedback();reviewed=f.transition_feedback(original,f.FeedbackStatus.REVIEWED,'review')
        self.case_file.write_text(feedback(input='changed').to_json())
        with self.assertRaises(LLMToolError):
            files.write_feedback_file(self.case_file,reviewed,previous=original)
        self.case_file.write_text(original.to_json());before=self.case_file.read_bytes()
        with patch('llmtool.feedback_files.os.replace',side_effect=OSError('PRIVATE')):
            with self.assertRaises(LLMToolError):
                files.write_feedback_file(self.case_file,reviewed,previous=original)
        self.assertEqual(self.case_file.read_bytes(),before)
        self.assertFalse(list(self.root.glob('.llmtool-feedback-*')))

    def test_security_source_no_execution_imports_calls(self):
        root=Path(__file__).resolve().parents[1]/'llmtool'
        for name in ('feedback.py','feedback_files.py','feedback_cli.py'):
            tree=ast.parse((root/name).read_text())
            imports=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
            imports += [alias.name for node in ast.walk(tree) if isinstance(node,ast.Import) for alias in node.names]
            self.assertFalse(any(x and ('provider' in x or x in ('subprocess','socket','config')) for x in imports))
            for node in ast.walk(tree):
                if isinstance(node,ast.Call):
                    if isinstance(node.func,ast.Name):self.assertNotIn(node.func.id,('eval','exec','compile','__import__'))
                    if isinstance(node.func,ast.Attribute):self.assertNotIn(node.func.attr,('system','popen','Popen'))


if __name__=='__main__':
    unittest.main()
