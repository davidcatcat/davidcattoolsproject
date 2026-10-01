"""Offline measurement and explicit fake-provider boundary tests."""
import ast
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import benchmark as b
from llmtool import benchmark_cli as cli
from llmtool.cli import main
from llmtool.context import ContextPacket, EvidenceItem
from llmtool.errors import LLMToolError
from llmtool.feedback import RegressionCase, RegressionAssertion, AssertionType as A
from llmtool.providers.base import Reply
from llmtool.validator import CommandRisk as R

FIXTURES = Path(__file__).resolve().parents[1] / 'benchmarks'


def case(assertions=None, text='HTTP 500 observed'):
    return RegressionCase.create(['0'*24], 'context-pack', 2, text,
                                 assertions or [RegressionAssertion(A.CONTEXT_VALID)])


def spec(assertions=None, safety=None, text='HTTP 500 observed'):
    return b.BenchmarkSpec(case(assertions, text), 'Synthetic benchmark', ['synthetic'], safety)


def output(facts=('HTTP 500 observed',), commands=('stat /etc/company/app/config.yaml',), evidence=True):
    return ContextPacket(facts, [EvidenceItem('HTTP 500 observed', 'synthetic log')] if evidence else [],
                         [], ['Configuration mismatch possible'], ['Previous exception missing'], commands).to_json()


def metric(result, name):
    return result.quality.to_dict()[name]


class BenchmarkModelTests(unittest.TestCase):
    def test_offline_valid(self):
        result = b.evaluate_benchmark(spec(), output())
        self.assertTrue(result.passed)
        self.assertEqual(metric(result, 'assertion_pass_rate')['ratio'], 1)
        self.assertEqual(result.runtime, b.BenchmarkRuntime())

    def test_reuses_exact_a7_results(self):
        from llmtool.feedback import evaluate_regression
        s = spec([RegressionAssertion(A.FACT_CONTAINS, 'missing')])
        self.assertEqual(b.evaluate_benchmark(s, output()).regression, evaluate_regression(s.regression, output()))

    def test_immutable_detached_models(self):
        tags = ['synthetic']; commands = ['pwd']; risks = [R.MUTATING]
        safety = b.SafetyExpectation(risks, commands)
        s = b.BenchmarkSpec(case(), 'name', tags, safety)
        tags.clear(); commands.clear(); risks.clear()
        self.assertEqual(s.tags, ('synthetic',))
        self.assertEqual(s.safety.preferred_verify_next_any, ('pwd',))
        with self.assertRaises(FrozenInstanceError):
            s.name = 'changed'

    def test_spec_roundtrip_and_deterministic_json(self):
        s = spec(safety=b.SafetyExpectation([R.MUTATING], ['pwd']))
        self.assertEqual(b.parse_benchmark_spec(s.to_json()), s)
        self.assertEqual(b.evaluate_benchmark(s, output()).to_json(), b.evaluate_benchmark(s, output()).to_json())
        self.assertNotIn('HTTP 500 observed', b.evaluate_benchmark(s, output()).to_json())

    def test_strict_fields_and_malformed_case(self):
        data = spec().to_dict()
        for name in data:
            changed = dict(data); del changed[name]
            with self.subTest(name=name), self.assertRaises(LLMToolError):
                b.parse_benchmark_spec(json.dumps(changed))
        for value in ({**data, 'reasoning': 'private'}, {**data, 'regression': {}}, [], {}):
            with self.assertRaises(LLMToolError):
                b.parse_benchmark_spec(json.dumps(value))

    def test_duplicate_constants_depth_and_utf8(self):
        raw = spec().to_json()
        for value in ('{"name":1,"name":2}', 'NaN', 'Infinity', raw.replace('"minimum_skill_version": 2', '"minimum_skill_version": NaN'),
                      '['*1000+']'*1000, '\ud800', 'x'*(b.JSON_LIMIT+1), '日'*(b.JSON_LIMIT//3+1)):
            with self.subTest(value=value[:20]), self.assertRaises(LLMToolError):
                b.parse_benchmark_spec(value)

    def test_spec_text_and_collection_bounds(self):
        for changes in ({'name': ''}, {'name': 'x'*257}, {'tags': ['x']*17}, {'tags': ['x','x']}, {'regression': {}}, {'safety': {}}):
            with self.assertRaises(LLMToolError):
                replace(spec(), **changes)
        for args in ((['UNKNOWN']*6, []), (['PRIVATE'], []), ([], ['pwd']*17), ([], ['pwd','pwd'])):
            with self.assertRaises(LLMToolError):
                b.SafetyExpectation(*args)

    def test_zero_denominator(self):
        result = b.evaluate_benchmark(spec(), output())
        for name in ('fact_retention', 'hallucination_rate', 'finding_recall', 'false_positive_rate'):
            self.assertEqual(metric(result, name), {'numerator':0,'denominator':0,'ratio':None,'assessed':True})

    def test_fact_retention(self):
        s = spec([RegressionAssertion(A.FACT_CONTAINS, 'HTTP 500 observed'), RegressionAssertion(A.FACT_CONTAINS, 'missing')])
        m = metric(b.evaluate_benchmark(s, output()), 'fact_retention')
        self.assertEqual((m['numerator'],m['denominator'],m['ratio']), (1,2,.5))

    def test_curated_hallucination(self):
        s = spec([RegressionAssertion(A.FACT_NOT_CONTAINS, 'invented'), RegressionAssertion(A.FACT_NOT_CONTAINS, 'absent')])
        result = b.evaluate_benchmark(s, output(facts=['invented']))
        self.assertFalse(result.passed)
        self.assertEqual(metric(result, 'hallucination_rate')['ratio'], .5)

    def test_finding_recall(self):
        s = spec([RegressionAssertion(A.FINDING_PRESENT, 'EVIDENCE_MISSING'), RegressionAssertion(A.FINDING_PRESENT, 'CATEGORY_COLLISION')])
        self.assertEqual(metric(b.evaluate_benchmark(s, output(evidence=False)), 'finding_recall')['ratio'], .5)

    def test_curated_false_positive(self):
        s = spec([RegressionAssertion(A.FINDING_ABSENT, 'EVIDENCE_MISSING')])
        self.assertEqual(metric(b.evaluate_benchmark(s, output(evidence=False)), 'false_positive_rate')['ratio'], 1)

    def test_additional_quality(self):
        s = spec([RegressionAssertion(A.HYPOTHESIS_CONTAINS, 'Configuration mismatch possible'),
                  RegressionAssertion(A.UNKNOWN_CONTAINS, 'Previous exception missing'),
                  RegressionAssertion(A.VERIFY_NEXT_CONTAINS, 'stat /etc/company/app/config.yaml')])
        result = b.evaluate_benchmark(s, output())
        for name in ('hypothesis_retention','unknown_retention','verify_next_retention'):
            self.assertEqual(metric(result,name)['ratio'],1)

    def test_command_risk_assertion(self):
        s = spec([RegressionAssertion(A.COMMAND_RISK_EQUALS, 'SENSITIVE_READ', 'cat /etc/company/app/config.yaml')])
        result = b.evaluate_benchmark(s, output(commands=['cat /etc/company/app/config.yaml']))
        self.assertTrue(result.passed)
        self.assertEqual(metric(result,'command_risk_pass_rate')['ratio'],1)

    def test_malformed_candidate_fails_closed_not_observed_hallucination(self):
        s = spec([RegressionAssertion(A.CONTEXT_VALID), RegressionAssertion(A.FACT_NOT_CONTAINS,'invented'),
                  RegressionAssertion(A.FINDING_ABSENT,'EVIDENCE_MISSING')])
        for candidate in ('broken', '{}', '{"facts": [], "facts": []}'):
            result = b.evaluate_benchmark(s,candidate)
            self.assertFalse(result.passed)
            self.assertEqual(metric(result,'assertion_pass_rate')['numerator'],0)
            self.assertEqual(metric(result,'hallucination_rate')['denominator'],1)
            self.assertIsNone(metric(result,'hallucination_rate')['ratio'])
            self.assertFalse(metric(result,'false_positive_rate')['assessed'])

    def test_context_validation_failure(self):
        result = b.evaluate_benchmark(spec(), output(evidence=False))
        self.assertFalse(result.passed)
        self.assertEqual(metric(result,'context_validity')['ratio'],0)

    def test_compression_utf8_bytes(self):
        s = spec([RegressionAssertion(A.TEXT_CONTAINS,'日')], text='日本語')
        result = b.evaluate_benchmark(s, '日')
        self.assertEqual(result.efficiency.to_dict(), {'input_bytes':9,'output_bytes':3,'compression_ratio':1/3})

    def test_invalid_candidate_size_type_controls(self):
        for value in (None, [], 'x'*(b.JSON_LIMIT+1), '\ud800', '\x1b[31m'):
            with self.assertRaises(LLMToolError):
                b.evaluate_benchmark(spec(),value)

    def test_numeric_models_reject_negative_bool_nonfinite(self):
        for value in (-1, True, float('nan'), float('inf'), '1', b.TOKEN_LIMIT+1):
            with self.assertRaises(LLMToolError):
                b.BenchmarkRuntime(input_tokens=value,output_tokens=0,total_tokens=value)
            with self.assertRaises(LLMToolError):
                b.Metric(value,1)
        with self.assertRaises(LLMToolError):
            b.Efficiency(0,1)

    def test_token_openai(self):
        runtime = b.normalize_runtime({'input_tokens':3,'output_tokens':4,'total_tokens':7},10)
        self.assertTrue(runtime.token_usage_available)
        self.assertEqual((runtime.input_tokens,runtime.output_tokens,runtime.total_tokens),(3,4,7))

    def test_token_compatible(self):
        runtime = b.normalize_runtime({'prompt_tokens':3,'completion_tokens':4,'total_tokens':7},10)
        self.assertEqual(runtime.input_tokens,3)
        self.assertTrue(runtime.token_usage_available)

    def test_tokens_unavailable_cli_elapsed_only(self):
        runtime = b.normalize_runtime({'elapsed_seconds':.25},300)
        self.assertEqual(runtime.provider_latency_ms,250)
        self.assertEqual(runtime.wall_latency_ms,300)
        self.assertEqual((runtime.input_tokens,runtime.output_tokens,runtime.total_tokens),(None,None,None))
        self.assertFalse(runtime.token_usage_available)

    def test_malformed_usage_deterministically_unavailable(self):
        for data in ({'input_tokens':1}, {'input_tokens':-1,'output_tokens':1,'total_tokens':0},
                     {'input_tokens':True,'output_tokens':1,'total_tokens':2},
                     {'input_tokens':1,'output_tokens':2,'total_tokens':8},
                     {'input_tokens':float('inf'),'output_tokens':1,'total_tokens':2}, {'total_tokens':0}):
            self.assertFalse(b.normalize_runtime(data,0).token_usage_available)

    def test_zero_token_counts_are_available_not_missing(self):
        self.assertTrue(b.normalize_runtime({'input_tokens':0,'output_tokens':0,'total_tokens':0},0).token_usage_available)

    def test_metadata_allowlist_no_leak(self):
        runtime = b.normalize_runtime({'model':'model-1','Authorization':'PRIVATE','base_url':'PRIVATE','raw_response':'PRIVATE'},1)
        self.assertNotIn('PRIVATE',json.dumps(runtime.to_dict()))
        self.assertEqual(runtime.model,'model-1')
        self.assertIsNone(b.normalize_runtime({'model':'PRIVATE\x1b'},0).model)

    def test_latency_bounds(self):
        for value in (-1,True,float('nan'),b.LATENCY_LIMIT+1):
            with self.assertRaises(LLMToolError):
                b.BenchmarkRuntime(wall_latency_ms=value)
        for value in (-1, True, float('nan'),float('inf'),100000,10**1000):
            self.assertIsNone(b.normalize_runtime({'elapsed_seconds':value},0).provider_latency_ms)

    def test_result_models_reject_arbitrary_objects(self):
        result = b.evaluate_benchmark(spec(),output())
        for changes in ({'quality': {}}, {'runtime': {}}, {'case_id': 'bad'}, {'regression': {}}):
            with self.assertRaises(LLMToolError):
                replace(result, **changes)
        with self.assertRaises(LLMToolError):
            b.BenchmarkMetrics([{}]*len(b.QUALITY_NAMES))

    def test_suite_serialized_result_size_bound(self):
        # Per-case names/results remain bounded; the full suite is bounded separately.
        result = b.evaluate_benchmark(spec(),output())
        from llmtool.feedback import RegressionResult, AssertionResult
        results=[]
        for i in range(b.SUITE_LIMIT):
            regression=RegressionResult(format(i,'024x'),tuple(AssertionResult(j,A.TEXT_CONTAINS,True,'日'*85) for j in range(64)))
            results.append(replace(result,case_id=regression.case_id,regression=regression))
        with self.assertRaisesRegex(LLMToolError,'1 MiB'):
            b.BenchmarkSuiteResult(results)

    def test_runtime_strict_roundtrip(self):
        runtime = b.BenchmarkRuntime(10,8,'model',1,2,3)
        raw = json.dumps({f.name:getattr(runtime,f.name) for f in b.fields(runtime)})
        self.assertEqual(b.parse_benchmark_runtime(raw),runtime)
        with self.assertRaises(LLMToolError):
            b.parse_benchmark_runtime(json.dumps(runtime.to_dict()))
        with self.assertRaises(LLMToolError):
            b.BenchmarkRuntime(input_tokens=1)

    def test_suite_aggregate_raw_counts(self):
        first = b.evaluate_benchmark(spec([RegressionAssertion(A.FACT_CONTAINS,'HTTP 500 observed')]),output())
        second = b.evaluate_benchmark(spec([RegressionAssertion(A.FACT_CONTAINS,v) for v in ('a','b','c')], text='other'),output())
        suite = b.BenchmarkSuiteResult([first,second])
        m = suite.to_dict()['quality']['fact_retention']
        self.assertEqual((m['numerator'],m['denominator'],m['ratio']),(1,4,.25))
        self.assertEqual([r.case_id for r in suite.results],[first.case_id,second.case_id])

    def test_suite_runtime_and_efficiency_aggregate(self):
        first = b.evaluate_benchmark(spec(text='one'),output(),b.BenchmarkRuntime(10,9,None,1,2,3))
        second = b.evaluate_benchmark(spec(text='two'),output(),b.BenchmarkRuntime(30))
        data = b.BenchmarkSuiteResult([first,second]).to_dict()
        self.assertEqual(data['runtime']['wall_latency']['mean_ms'],20)
        self.assertEqual(data['runtime']['token_sample_count'],1)
        self.assertEqual(data['runtime']['total_tokens'],3)
        self.assertEqual(data['efficiency']['input_bytes'],6)

    def test_suite_unknown_metric_not_silently_excluded(self):
        assertions=[RegressionAssertion(A.FACT_NOT_CONTAINS,'bad')]
        first=b.evaluate_benchmark(spec(assertions,text='one'),output())
        second=b.evaluate_benchmark(spec(assertions,text='two'),'broken')
        self.assertIsNone(b.BenchmarkSuiteResult([first,second]).to_dict()['quality']['hallucination_rate']['ratio'])

    def test_duplicate_case_ids_rejected(self):
        result=b.evaluate_benchmark(spec(),output())
        with self.assertRaisesRegex(LLMToolError,'duplicate'):
            b.BenchmarkSuiteResult([result,result])

    def test_manifest_roundtrip_limits_strict_fields(self):
        suite=b.BenchmarkSuite([b.SuiteEntry('case.json','output.json')])
        self.assertEqual(b.parse_benchmark_suite(suite.to_json()),suite)
        for data in ({'entries':[]},{'entries':[{}]},{'entries':[],'glob':'*'},
                     {'entries':[b.SuiteEntry('a','b').to_dict()]*(b.SUITE_LIMIT+1)}):
            with self.assertRaises(LLMToolError):
                b.parse_benchmark_suite(json.dumps(data))

    def test_safety_preferred_evidence(self):
        s=spec(safety=b.SafetyExpectation([R.MUTATING,R.DESTRUCTIVE],['stat /etc/company/app/config.yaml']))
        self.assertTrue(b.evaluate_benchmark(s,output()).passed)
        self.assertFalse(b.evaluate_benchmark(s,output(commands=['cat /etc/company/app/config.yaml'])).passed)

    def test_mutating_safety_failure(self):
        s=spec(safety=b.SafetyExpectation([R.MUTATING]))
        result=b.evaluate_benchmark(s,output(commands=['touch marker']))
        self.assertFalse(result.passed)
        self.assertEqual(result.safety.to_dict()['MUTATING'],1)

    def test_destructive_safety_failure(self):
        s=spec(safety=b.SafetyExpectation([R.DESTRUCTIVE]))
        result=b.evaluate_benchmark(s,output(commands=['rm -rf marker']))
        self.assertFalse(result.passed)
        self.assertEqual(result.safety.to_dict()['DESTRUCTIVE'],1)

    def test_sensitive_read_is_data_not_automatic_failure(self):
        s=spec(safety=b.SafetyExpectation([R.MUTATING,R.DESTRUCTIVE]))
        result=b.evaluate_benchmark(s,output(commands=['cat /etc/company/app/config.yaml']))
        self.assertTrue(result.passed)
        self.assertEqual(result.safety.to_dict()['SENSITIVE_READ'],1)

    def test_prose_unknown_and_bounded_command_fallback(self):
        result=b.evaluate_benchmark(spec(),output(commands=['Investigate permissions','x'*8193]))
        self.assertEqual(result.safety.to_dict()['UNKNOWN'],2)

    def test_malformed_packet_safety_fails(self):
        s=spec([RegressionAssertion(A.TEXT_CONTAINS,'broken')],b.SafetyExpectation())
        self.assertFalse(b.evaluate_benchmark(s,'broken').passed)

    def test_permission_denied_fixture(self):
        s=b.parse_benchmark_spec((FIXTURES/'permission-denied.spec.json').read_text())
        result=b.evaluate_benchmark(s,(FIXTURES/'permission-denied.candidate.json').read_text())
        self.assertTrue(result.passed)
        self.assertEqual(result.safety.to_dict()['SENSITIVE_READ'],1)
        self.assertTrue(result.safety.preferred_evidence_present)

    def test_no_execution_ast(self):
        forbidden={'eval','exec','system','Popen','run','call','check_call','check_output'}
        for module in (b,cli):
            tree=ast.parse(Path(module.__file__).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Call):
                    name=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else ''
                    self.assertNotIn(name,forbidden)
                if isinstance(node,(ast.Import,ast.ImportFrom)):
                    self.assertNotIn('subprocess',getattr(node,'module','') or '')


class BenchmarkCLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.casefile=self.root/'case.json'; self.casefile.write_text(case().to_json())
        self.candidate=self.root/'output.json'; self.candidate.write_text(output())

    def invoke(self,args):
        stdout=io.StringIO(); stderr=io.StringIO()
        with redirect_stdout(stdout),redirect_stderr(stderr):
            code=main(['benchmark']+args)
        return code,stdout.getvalue(),stderr.getvalue()

    def test_evaluate_no_provider_or_credentials_empty_home(self):
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,{'HOME':str(self.root)},clear=True))
            for target in ('llmtool.config.resolve_config','llmtool.config.api_key','llmtool.providers.create_client',
                           'llmtool.cli.resolve_config','llmtool.cli.api_key','llmtool.cli.create_client'):
                stack.enter_context(patch(target,side_effect=AssertionError('offline boundary')))
            code,raw,error=self.invoke(['evaluate','--case',str(self.casefile),'--output-file',str(self.candidate),'--json'])
        self.assertEqual(code,0); self.assertEqual(error,'')
        self.assertTrue(json.loads(raw)['passed'])

    def test_suite_no_provider_and_relative_paths(self):
        manifest=self.root/'suite.json'
        manifest.write_text(b.BenchmarkSuite([b.SuiteEntry('case.json','output.json')]).to_json())
        with patch('llmtool.config.resolve_config',side_effect=AssertionError('offline')), \
             patch('llmtool.config.api_key',side_effect=AssertionError('offline')), \
             patch('llmtool.providers.create_client',side_effect=AssertionError('offline')):
            code,raw,error=self.invoke(['suite','--manifest',str(manifest),'--json'])
        self.assertEqual((code,error),(0,'')); self.assertTrue(json.loads(raw)['passed'])

    def test_malformed_case_empty_stdout_sanitized(self):
        self.casefile.write_text('PRIVATE invalid')
        code,raw,error=self.invoke(['evaluate','--case',str(self.casefile),'--output-file',str(self.candidate),'--json'])
        self.assertEqual((code,raw),(1,'')); self.assertNotIn('PRIVATE',error); self.assertNotIn('Traceback',error)

    def test_failed_candidate_returns_json_exit_one(self):
        self.candidate.write_text('broken')
        code,raw,error=self.invoke(['evaluate','--case',str(self.casefile),'--output-file',str(self.candidate),'--json'])
        self.assertEqual((code,error),(1,'')); self.assertFalse(json.loads(raw)['passed'])

    def test_suite_no_partial_output_on_later_failure(self):
        manifest=self.root/'suite.json'
        manifest.write_text(b.BenchmarkSuite([b.SuiteEntry('case.json','output.json'),b.SuiteEntry('missing.json','output.json')]).to_json())
        code,raw,error=self.invoke(['suite','--manifest',str(manifest),'--json'])
        self.assertEqual((code,raw),(1,'')); self.assertNotIn('missing.json',error)

    def test_suite_rejects_duplicate_before_output(self):
        manifest=self.root/'suite.json'
        manifest.write_text(b.BenchmarkSuite([b.SuiteEntry('case.json','output.json')]*2).to_json())
        self.assertEqual(self.invoke(['suite','--manifest',str(manifest),'--json'])[:2],(1,''))

    def test_no_glob_or_recursive_discovery(self):
        manifest=self.root/'suite.json'
        manifest.write_text(b.BenchmarkSuite([b.SuiteEntry('*.json','output.json')]).to_json())
        with patch.object(Path,'glob',side_effect=AssertionError('no discovery')),patch.object(Path,'rglob',side_effect=AssertionError('no discovery')):
            self.assertEqual(self.invoke(['suite','--manifest',str(manifest)])[:2],(1,''))

    def test_file_symlink_parent_traversal_and_oversize(self):
        link=self.root/'link.json'; link.symlink_to(self.casefile)
        for filename in (link,self.root/'..'/'case.json'):
            self.assertEqual(self.invoke(['evaluate','--case',str(filename),'--output-file',str(self.candidate)])[:2],(1,''))
        self.candidate.write_text('x'*(b.JSON_LIMIT+1))
        self.assertEqual(self.invoke(['evaluate','--case',str(self.casefile),'--output-file',str(self.candidate)])[:2],(1,''))

    def test_live_calls_once_existing_prompt_and_safe_metadata(self):
        client=Mock(); client.complete.return_value=Reply(output(),{'elapsed_seconds':.1,'Authorization':'PRIVATE'})
        with patch('llmtool.config.resolve_config',return_value={}) as config,patch('llmtool.config.api_key',return_value=None),\
             patch('llmtool.providers.create_client',return_value=client),patch.object(cli.time,'monotonic',side_effect=[10,10.25]):
            code,raw,error=self.invoke(['live','--case',str(self.casefile),'--provider','fake','--json'])
        self.assertEqual((code,error),(0,'')); client.complete.assert_called_once()
        messages=client.complete.call_args[0][0]
        self.assertEqual(messages[1]['content'],case().input)
        self.assertIn('Selected skill: context-pack (version 2)',messages[0]['content'])
        self.assertEqual(config.call_args[0][0]['provider'],'fake')
        self.assertEqual(json.loads(raw)['runtime']['wall_latency_ms'],250)
        self.assertNotIn('PRIVATE',raw)

    def test_live_never_executes_returned_commands(self):
        marker=self.root/'executed'
        candidate=output(commands=['touch '+str(marker)])
        s=spec(safety=b.SafetyExpectation([R.MUTATING,R.DESTRUCTIVE])); path=self.root/'spec.json'; path.write_text(s.to_json())
        client=Mock(); client.complete.return_value=Reply(candidate,{})
        with patch('llmtool.config.resolve_config',return_value={}),patch('llmtool.config.api_key',return_value=None),patch('llmtool.providers.create_client',return_value=client):
            code,raw,error=self.invoke(['live','--spec',str(path),'--json'])
        self.assertEqual(code,1); self.assertFalse(marker.exists()); client.complete.assert_called_once()

    def test_live_precondition_fails_before_provider(self):
        future=case()
        from llmtool.feedback import RegressionCase
        future=RegressionCase.create(['0'*24],'context-pack',2147483647,'input',future.assertions)
        self.casefile.write_text(future.to_json())
        with patch('llmtool.providers.create_client',side_effect=AssertionError('precondition')):
            self.assertEqual(self.invoke(['live','--case',str(self.casefile)])[:2],(1,''))

    def test_human_sections_no_candidate_dump_terminal_safe(self):
        code,raw,error=self.invoke(['evaluate','--case',str(self.casefile),'--output-file',str(self.candidate)])
        self.assertEqual(code,0)
        for section in ('QUALITY','EFFICIENCY','RUNTIME','SAFETY'):
            self.assertIn(section,raw)
        self.assertNotIn('HTTP 500 observed',raw)

    def test_argparse_errors_sanitized(self):
        stderr=io.StringIO()
        with redirect_stderr(stderr),self.assertRaises(SystemExit) as error:
            main(['benchmark','evaluate','--PRIVATE'])
        self.assertEqual(error.exception.code,2); self.assertNotIn('PRIVATE',stderr.getvalue())


if __name__=='__main__':
    unittest.main()
