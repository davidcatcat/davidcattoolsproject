"""Synthetic fixture tests for one-use Git-pinned helper repair. No project checkout."""
import importlib.util
import io
import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('maintenance', Path(__file__).with_name('ai_loop_maintenance.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MaintenanceTests(unittest.TestCase):
    def test_sha_stable(self):
        self.assertEqual(len(m.sha(b'bytes')), 64)
        self.assertEqual(m.sha(b'bytes'), m.sha(b'bytes'))
        self.assertNotEqual(m.sha(b'bytes'), m.sha(b'byte'))

    def test_diff_snapshot_never_overwrites_different_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'state'
            with patch.object(m, 'ROOT', Path(d)):
                saved = m.snapshot(path, b'data', b'diff')
                self.assertEqual(saved.read_bytes(), b'data')
                self.assertEqual(m.snapshot(path, b'data', b'diff'), saved)
                saved.write_bytes(b'corrupted')
                with self.assertRaises(m.Stop):
                    m.snapshot(path, b'data', b'diff')

    def test_snapshot_symlink_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'safe').mkdir(); (root/'alias').symlink_to(root/'safe')
            with self.assertRaises(m.Stop):
                m.snapshot(root/'alias'/'state', b'data', b'diff')

    def test_check_root_rejects_wrong_branch_and_root(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'ROOT', Path(d)), patch.object(m, 'git', return_value='invalid'):
            with self.assertRaises(m.Stop): m.check_root()

    def test_review_invalid_status_or_empty_findings_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root/m.TARGET
            source.parent.mkdir()
            source.write_bytes(b'candidate')
            state = root/'state'; state.mkdir()
            old = Path.cwd()
            try:
                os.chdir(d)
                with patch.object(m, 'ROOT', root), patch.object(m.shutil, 'which', return_value='/bin/claude'):
                    for r in [
                        {'status':'PASS','reviewed_sha256':m.sha(b'other'),'summary':'x','findings':[]},
                        {'status':'FAIL','reviewed_sha256':m.sha(b'candidate'),'summary':'x','findings':[]},
                        {'status':'PASS','reviewed_sha256':m.sha(b'candidate'),'summary':'x','findings':['blocked']}
                    ]:
                        reply=json.dumps({'is_error':False,'subtype':'success','structured_output':r})
                        with patch.object(m, 'call', return_value=reply):
                            with self.assertRaises(m.Stop):
                                m.review_candidate(b'candidate', b'diff',state,'read source')
            finally: os.chdir(old)

    def test_review_good_result_is_accepted_on_exact_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/m.TARGET;source.parent.mkdir();source.write_bytes(b'candidate')
            state=root/'state';state.mkdir();old=Path.cwd()
            try:
                os.chdir(d)
                with patch.object(m, 'ROOT', root), patch.object(m.shutil, 'which', return_value='/bin/claude'):
                    ok={'status':'PASS','reviewed_sha256':m.sha(b'candidate'),'summary':'verified','findings':[]}
                    with patch.object(m, 'call', return_value=json.dumps({'is_error':False,'subtype':'success','structured_output':ok})):
                        self.assertEqual(m.review_candidate(b'candidate',b'diff',state,'contract')['status'],'PASS')
            finally: os.chdir(old)

    def test_review_source_mutation_stops(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/m.TARGET;source.parent.mkdir();source.write_bytes(b'candidate')
            state=root/'state';state.mkdir();old=Path.cwd()
            try:
                os.chdir(d)
                ok={'status':'PASS','reviewed_sha256':m.sha(b'candidate'),'summary':'verified','findings':[]}
                def mutate(*args,**kwargs):
                    source.write_bytes(b'mutated')
                    return json.dumps({'is_error':False,'subtype':'success','structured_output':ok})
                with patch.object(m,'ROOT',root),patch.object(m.shutil,'which',return_value='/bin/claude'),patch.object(m,'call',side_effect=mutate):
                    with self.assertRaisesRegex(m.Stop,'candidate changed'):
                        m.review_candidate(b'candidate',b'diff',state,'contract')
            finally: os.chdir(old)

    def test_repair_requires_change_and_bound(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/m.TARGET;source.parent.mkdir();source.write_bytes(b'candidate')
            state=root/'state';state.mkdir();old=Path.cwd()
            try:
                os.chdir(d)
                with patch.object(m,'ROOT',root),patch.object(m.shutil,'which',return_value='/bin/codex'),patch.object(m,'call',return_value='some text'),patch.object(m,'check_root'):
                    with self.assertRaisesRegex(m.Stop,'did not change'): m.codex_repair(b'candidate',['fix'],'contract',state)
                    with self.assertRaisesRegex(m.Stop,'unbounded'): m.codex_repair(b'candidate',['too much']*13,'contract',state)
            finally: os.chdir(old)

    def test_physical_temp_path_must_exist(self):
        with patch.object(m.Path,'is_dir',return_value=False):
            with self.assertRaises(m.Stop): m.physical_tmpdir()

    def test_verify_ci_requires_two_python_versions(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)
            run={'workflow_runs':[{'head_sha':'a'*40,'name':'ai-loop helper CI','id':7,'status':'completed','conclusion':'success'}]}
            jobs={'jobs':[{'name':'helper (3.9)','conclusion':'success'}]}
            with patch.object(m,'call',side_effect=[json.dumps(run),json.dumps(jobs)]):
                with self.assertRaisesRegex(m.Stop,'3.9/3.14'): m.verify_ci('a'*40,state)

    def test_verify_ci_rejects_red(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)
            run={'workflow_runs':[{'head_sha':'a'*40,'name':'ai-loop helper CI','id':7,'status':'completed','conclusion':'failure'}]}
            with patch.object(m,'call',return_value=json.dumps(run)):
                with self.assertRaisesRegex(m.Stop,'CI failed'): m.verify_ci('a'*40,state)

    def test_verify_ci_accepts_only_exact_head_and_both(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)
            run={'workflow_runs':[{'head_sha':'a'*40,'name':'ai-loop helper CI','id':7,'status':'completed','conclusion':'success'}]}
            jobs={'jobs':[{'name':'helper (3.9)','conclusion':'success'},{'name':'helper (3.14)','conclusion':'success'}]}
            with patch.object(m,'call',side_effect=[json.dumps(run),json.dumps(jobs)]):
                m.verify_ci('a'*40,state)
            self.assertEqual((state/'ci-pass').read_text().strip(),'a'*40)

    def test_published_marker_binds_candidate_commit(self):
        self.assertTrue(m.BASE.startswith('ac03992b'))
        self.assertEqual(m.TARGET,'scripts/test_ai_loop.py')
        self.assertEqual(m.BRANCH,'automation/ai-loop')

if __name__=='__main__':
    unittest.main()
