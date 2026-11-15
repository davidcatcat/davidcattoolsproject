"""#1230 refusal-code pin: task_review export scope and call binding codes."""
import dataclasses
import unittest

from llmtool import task_review as r
from llmtool.errors import LLMToolError


class ScopeAndBindingCodeTests(unittest.TestCase):
    def code(self, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        return caught.exception.review_code

    def test_credential_parts_and_out_of_scope_paths_are_blocked(self):
        self.assertIsNone(r.scope_path('src/app.py', ('src',)))
        for path in ('src/credentials/key.txt', 'src/auth.json', 'src/Credentials/x', 'docs/app.py', 'srcx/app.py'):
            with self.subTest(path=path):
                self.assertEqual(self.code(lambda: r.scope_path(path, ('src',))), 'REVIEW_EXPORT_SCOPE_BLOCKED')

    def test_call_binding_without_task_bindings_is_binding_invalid(self):
        fields = {f.name: None for f in dataclasses.fields(r.ReviewCallBinding)}
        for name in ('ready_task', 'acceptance_task'):
            with self.subTest(name=name):
                self.assertEqual(self.code(lambda: r.ReviewCallBinding(**dict(fields, **{name: 'not-a-binding'}))),
                                 'REVIEW_BINDING_INVALID')


if __name__ == '__main__':
    unittest.main()
