"""B3: independently validated ordered metadata; unknown objects never stringify."""
import io
import unittest
from llmtool import diagnostics as d
from llmtool.diagnostics_runtime import RunContext


class PrivateValue:
    def __str__(self):
        raise AssertionError('unknown metadata stringified')
    __repr__ = __str__


class MetadataRegressions(unittest.TestCase):
    def test_b3_bad_model_preserves_valid_duration_tokens_and_order(self):
        for model in ('claude-sonnet-é', 'my-token:latest', 'bad\nmodel', 'sk-' + 'a' * 40):
            with self.subTest(model=model):
                run = RunContext(d.DiagnosticMode.VERBOSE)
                expected = {'elapsed_seconds': 0.1, 'prompt_tokens': 1, 'total_tokens': 3}
                run.response({'elapsed_seconds': 0.1, 'model': model, 'prompt_tokens': 1, 'total_tokens': 3})
                self.assertEqual(run._metadata, expected)
                self.assertEqual(run.dropped_event_count, 1)
                output = io.StringIO()
                run.display(output)
                self.assertEqual(output.getvalue().splitlines()[:3],
                                 ['elapsed_seconds=0.1', 'prompt_tokens=1', 'total_tokens=3'])
                self.assertNotIn(model, output.getvalue())
                response = [e for e in run.events if e.type is d.EventType.PROVIDER_RESPONSE]
                self.assertEqual(response[0].data['duration_ms'], 100)
                self.assertNotIn('model', response[0].data)

    def test_b3_bad_token_preserves_model_and_other_tokens(self):
        run = RunContext()
        run.response({'model': 'qwen/test', 'total_tokens': -1, 'completion_tokens': 2})
        self.assertEqual(run._metadata, {'model': 'qwen/test', 'completion_tokens': 2})
        self.assertEqual(run.dropped_event_count, 1)

    def test_b3_valid_metadata_unknown_values_and_redaction_marker(self):
        for model in ('qwen3.6-35b-a3b', 'llama3:8b-instruct-q4', '[redacted]'):
            with self.subTest(model=model):
                run = RunContext(d.DiagnosticMode.VERBOSE)
                expected = {'total_tokens': 0, 'model': model, 'elapsed_seconds': 0.25, 'input_tokens': None}
                run.response(dict(expected, unknown=PrivateValue(), reasoning_content=PrivateValue()))
                self.assertEqual(run._metadata, expected)
                self.assertEqual(list(run._metadata), list(expected))
                self.assertEqual(run.dropped_event_count, 0)
                run.display(io.StringIO())
