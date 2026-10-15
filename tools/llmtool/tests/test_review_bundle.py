"""Review preparation is local evidence, never execution/approval authority."""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_bundle as b
from llmtool import task_lifecycle as t, task_verification as v
from llmtool.errors import LLMToolError
from llmtool.task_review_collector import LocalFacts
from llmtool import task_review_collector as c


def records(root, head='1'*40, branch='feat/bundle'):
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    contract = v.TaskContract(1, 'bundle-test', 1, None, 'Review evidence', ('src',), (),
        (v.AcceptanceCriterion('artifact', v.CriterionKind.ARTIFACT, 'Inspect capture'),))
    dev = t.AgentBinding('developer', 'developer-session', t.Role.DEVELOPER,
                         str(root), str(root.parent/'main/.git'), branch)
    reviewer = t.AgentBinding('reviewer', 'reviewer-session', t.Role.REVIEWER,
                              str(root.parent/'review'), dev.common_dir, 'review/bundle')
    expect = t.Expectations(1, contract.binding('a'*32), head, 1, t.State.QUEUED, 0,
                            (dev, reviewer), (dev,), 300, 'c'*64, ('d'*64,))
    evidence = v.EvidenceRecord(1, 'capture', 'artifact', expect.task, head,
        v.EvidenceSource.ARTIFACT, v.EvidenceOutcome.PASSED, None, None, None, ('d'*64,), True)
    events = []
    for i, state in enumerate((t.State.QUEUED, t.State.STARTING, t.State.RUNNING,
                                t.State.WAITING_REVIEW), 1):
        handoff = t.HandoffRecord(expect.task, head, 1, dev, reviewer, expect.check_digest,
                                  expect.artifact_digests, 'e3', ()) if i == 4 else None
        events.append(t.TaskEvent(1, 'e'+str(i), i, 1, expect.task, head, dev, 'STATE', state,
            'e'+str(i-1) if i > 1 else None, events[-1].state if events else None,
            now, 'f'*64, (evidence,) if i == 3 else (), handoff))
    return contract, expect, tuple(events)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None)
        self.folder = Path(self.tmp.name); self.folder.chmod(0o700)
        self.root = self.folder/'repo'; self.root.mkdir(mode=0o700)
        self.evidence_tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None)
        self.private = Path(self.evidence_tmp.name); self.private.chmod(0o700)
        self.contract, self.expect, self.events = records(self.root)
        self.args = SimpleNamespace(contract=str(self.private/'contract.json'),
            expect=str(self.private/'expect.json'), events=str(self.private/'events.jsonl'),
            base='2'*40, evidence=str(self.private/'evidence.json'),
            output=str(self.private/'bundle.json'), json=True)
        self.write_inputs()
        self.facts = LocalFacts(str(self.root), 'feat/bundle', '1'*40,
            self.expect.agents[0].common_dir, 'davidcatcat/davidcattoolsproject',
            'a'*64, 'b'*64, 'c'*64, 'd'*64, True)
        self.manifest = [{'path':'src/app.py', 'base':None,
                          'source':{'mode':'100644', 'blob':'3'*40}}]

    def tearDown(self):
        self.evidence_tmp.cleanup()
        self.tmp.cleanup()

    def private_file(self, path, data):
        Path(path).write_text(data); Path(path).chmod(0o600)

    def write_inputs(self):
        self.private_file(self.args.contract, self.contract.to_json())
        self.private_file(self.args.expect, self.expect.to_json())
        self.private_file(self.args.events, '\n'.join(e.to_json() for e in self.events)+'\n')
        capture = self.private/'capture.json'
        self.private_file(capture, '{"exit":0,"private":"not copied into bundle"}')
        self.index = {'schema_version':1, 'source_sha':self.expect.source_sha, 'artifacts':[
            {'artifact_id':'tests', 'kind':'TEST', 'source_sha':self.expect.source_sha,
             'path':str(capture), 'sha256':hashlib.sha256(capture.read_bytes()).hexdigest(),
             'metadata':{'exit_code':0, 'argv_sha256':'e'*64}}]}
        self.private_file(self.args.evidence, json.dumps(self.index))

    def prepare(self, environment=None):
        from llmtool.git_intel import HEAD
        reader = SimpleNamespace(root=self.root, read=lambda a:
            (self.expect.source_sha+'\n').encode() if a == HEAD else b'')
        with patch.object(b.Path, 'cwd', return_value=self.root), \
             patch.object(b, '_LocalGit', return_value=reader), \
             patch.object(b, 'collect_local_facts', return_value=self.facts), \
             patch.object(b, 'collect_bundle_manifest', return_value=self.manifest), \
             patch.object(b, 'verify_bundle_facts'), \
             patch.dict(os.environ, environment or {}, clear=True):
            return b.prepare_bundle(self.args)

    def test_private_bundle_complete_manifest_and_truthful_public_summary(self):
        with patch('socket.create_connection', side_effect=AssertionError('network')):
            result = self.prepare()
        output = Path(self.args.output)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result['status'], 'PREPARED')
        self.assertFalse(result['authorized'])
        self.assertEqual(result['runtime_evidence_status'], 'NOT_VERIFIED')
        self.assertNotIn(str(self.private), json.dumps(result))
        data = json.loads(output.read_text())
        self.assertEqual(data['manifest'], self.manifest)
        self.assertEqual(data['artifacts'][0]['provenance'], 'CALLER_REPORTED')
        self.assertNotIn('not copied into bundle', output.read_text())
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), result['bundle_sha256'])
        self.assertIn('Security', data['review_request'])

    def test_missing_null_stale_or_not_waiting_never_prepares(self):
        for variant in ('stale', 'not-waiting', 'missing-handoff', 'wrong-source', 'wrong-run'):
            with self.subTest(variant=variant):
                events = self.events
                if variant == 'stale':
                    events = tuple(replace(e, timestamp='2020-01-01T00:00:00Z') for e in events)
                elif variant == 'not-waiting': events = events[:3]
                elif variant == 'missing-handoff': events = events[:3]+(replace(events[3], handoff=None),)
                elif variant == 'wrong-source': events = tuple(replace(e, source_sha='5'*40) for e in events)
                else: events = tuple(replace(e, task=self.contract.binding('b'*32)) for e in events)
                self.private_file(self.args.events, '\n'.join(e.to_json() for e in events))
                with self.assertRaises(LLMToolError): self.prepare()
                self.assertFalse(Path(self.args.output).exists())

    def test_local_identity_dirty_head_branch_and_root_mismatch(self):
        for field, value in (('clean_tracked',False), ('source_sha','9'*40),
                             ('branch','feat/wrong'), ('root',str(self.folder/'wrong')),
                             ('common_dir',str(self.folder/'other/.git'))):
            with self.subTest(field=field):
                original = self.facts; self.facts = replace(original, **{field:value})
                with self.assertRaises(LLMToolError): self.prepare()
                self.facts = original

    def test_artifact_digest_source_alias_and_schema_rejected(self):
        for kind in ('digest','source','alias','duplicate','unknown','boolean-exit','bad-url'):
            with self.subTest(kind=kind):
                index = json.loads(json.dumps(self.index)); item = index['artifacts'][0]
                if kind == 'digest': item['sha256'] = '0'*64
                elif kind == 'source': item['source_sha'] = '0'*40
                elif kind == 'alias': index['artifacts'].append(dict(item, artifact_id='other'))
                elif kind == 'duplicate': index['artifacts'].append(item)
                elif kind == 'unknown': item['execute'] = 'never'
                elif kind == 'boolean-exit': item['metadata']['exit_code'] = True
                else:
                    item['kind'] = 'CI'; item['metadata'] = {'url':'https://evil.invalid/',
                        'head_sha':self.expect.source_sha, 'conclusion':'SUCCESS'}
                self.private_file(self.args.evidence, json.dumps(index))
                with self.assertRaises(LLMToolError): self.prepare()

    def test_output_existing_symlink_and_worktree_reject_without_overwrite(self):
        original = self.args.output
        Path(original).write_text('preserve')
        with self.assertRaises((LLMToolError,OSError)): self.prepare()
        self.assertEqual(Path(original).read_text(), 'preserve')
        self.args.output = str(self.private/'link.json'); Path(self.args.output).symlink_to(original)
        with self.assertRaises((LLMToolError,OSError)): self.prepare()
        self.args.output = str(self.root/'bundle.json')
        with self.assertRaises(LLMToolError): self.prepare()

    def test_artifact_private_mode_hardlink_symlink_fifo_or_secret_path_rejected(self):
        original = self.index['artifacts'][0]['path']
        for kind in ('public','hardlink','symlink','fifo','credential'):
            with self.subTest(kind=kind):
                path = self.private/(kind+'.json')
                if kind == 'public': self.private_file(path, '{}'); path.chmod(0o644)
                elif kind == 'hardlink': os.link(original,path)
                elif kind == 'symlink': path.symlink_to(original)
                elif kind == 'fifo': os.mkfifo(path,0o600)
                else: path = self.private/'auth.json'; self.private_file(path,'{}')
                self.index['artifacts'][0]['path'] = str(path)
                self.private_file(self.args.evidence,json.dumps(self.index))
                with self.assertRaises((LLMToolError,OSError)): self.prepare()
                path.unlink()
                self.index['artifacts'][0]['path'] = original

    def test_cli_error_is_sanitized_without_provider_resolution(self):
        from llmtool.cli import main
        out, err = io.StringIO(), io.StringIO()
        with patch('sys.stdout',out), patch('sys.stderr',err), \
             patch('llmtool.cli.resolve_config',side_effect=AssertionError('provider config')):
            code = main(['task','review-bundle','--contract',str(self.private/'missing-secret'),
                '--expect',self.args.expect,'--events',self.args.events,'--base',self.args.base,
                '--evidence',self.args.evidence,'--output',self.args.output,'--json'])
        self.assertNotEqual(code,0)
        self.assertEqual(json.loads(out.getvalue())['status'],'BLOCKED')
        self.assertNotIn(str(self.private),out.getvalue()+err.getvalue())

    def test_run_session_environment_is_observation_not_attestation(self):
        with self.assertRaises(LLMToolError):
            self.prepare({'CODEX_THREAD_ID':'changed-session'})

    def test_ci_is_exact_repo_head_and_stays_caller_reported(self):
        item = self.index['artifacts'][0]
        item['kind'] = 'CI'; item['metadata'] = {
            'url':'https://github.com/davidcatcat/davidcattoolsproject/actions/runs/123',
            'head_sha':self.expect.source_sha,'conclusion':'SUCCESS'}
        self.private_file(self.args.evidence,json.dumps(self.index))
        self.prepare()
        data = json.loads(Path(self.args.output).read_text())
        self.assertIn(item['metadata']['url'],data['review_request'])
        self.assertFalse(data['artifacts'][0]['execution_verified'])

    def test_duplicate_depth_unicode_and_index_limit_fail_before_git(self):
        for raw in ('{"schema_version":1,"schema_version":1}', '['*17+']'*17,
                    '{"bad":"\\ud800"}', 'x'*(b.FILE_LIMIT+1)):
            with self.subTest(raw=raw[:24]):
                self.private_file(self.args.evidence,raw)
                with self.assertRaises((LLMToolError,ValueError,UnicodeError)): self.prepare()
        self.private_file(self.args.expect,'{"schema_version":1,"schema_version":1}')
        with patch.object(b,'collect_local_facts',side_effect=AssertionError('not parsed')):
            with self.assertRaises(LLMToolError): self.prepare()

    def test_aggregate_artifact_budget_counts_double_read(self):
        files = []
        for i in range(3):
            path = self.private/('capture'+str(i)+'.txt'); self.private_file(path,'x'*b.FILE_LIMIT)
            files.append(dict(self.index['artifacts'][0], artifact_id='a'+str(i),path=str(path),
                              sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        self.index['artifacts'] = files
        self.private_file(self.args.evidence,json.dumps(self.index))
        with self.assertRaises(LLMToolError): self.prepare()

    def test_input_replacement_and_deadline_block(self):
        inputs = b._Inputs(self.root,b.time.monotonic()+60)
        inputs.read(self.args.expect)
        self.private_file(self.args.expect,'changed')
        with self.assertRaises(LLMToolError): inputs.verify()
        with patch.object(b.time,'monotonic',return_value=100):
            with self.assertRaises(LLMToolError): b._check(99)

    def test_output_failure_preserves_partial_but_not_prepared(self):
        def fail(): raise ValueError('private details never printed')
        with self.assertRaises(ValueError):
            b._write_output(self.args.output,self.root,b'bounded\n',b.time.monotonic()+60,fail)
        self.assertEqual(Path(self.args.output).read_bytes(),b'bounded\n')

    def test_late_output_replacement_or_same_inode_mutation_is_not_prepared(self):
        for kind in ('replace','modify'):
            with self.subTest(kind=kind):
                path = self.private/(kind+'-output.json')
                def change():
                    if kind == 'replace':
                        replacement = self.private/'replacement.json'
                        replacement.write_bytes(b'changed\n'); replacement.replace(path)
                    else:
                        path.write_bytes(b'changed\n')
                with self.assertRaises(LLMToolError):
                    b._write_output(str(path),self.root,b'bounded\n',b.time.monotonic()+60,change)

    def test_late_drift_and_broken_public_receipt_cannot_exit_zero(self):
        out = io.StringIO()
        with patch.object(b,'prepare_bundle',side_effect=LLMToolError('private secret')):
            with patch('sys.stdout',out): self.assertNotEqual(b.run_review_bundle(self.args),0)
        self.assertNotIn('private secret',out.getvalue())
        with patch.object(b,'prepare_bundle',return_value={'status':'PREPARED'}):
            out.close()
            with patch('sys.stdout',out): self.assertNotEqual(b.run_review_bundle(self.args),0)

    def test_late_public_receipt_must_not_exit_zero(self):
        clock = [100.0]
        class LateReceipt(io.StringIO):
            def flush(self): clock[0] = 161.0
        with patch.object(b,'prepare_bundle',return_value={'status':'PREPARED'}), \
             patch.object(b.time,'monotonic',side_effect=lambda:clock[0]), \
             patch('sys.stdout',LateReceipt()):
            self.assertNotEqual(b.run_review_bundle(self.args),0)


class NativeManifestTests(unittest.TestCase):
    """A disposable synthetic repository, never a project clone/worktree."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None)
        self.root = Path(self.tmp.name); self.root.chmod(0o700)
        self.git = lambda *a: subprocess.check_output(['/usr/bin/git','-c','core.hooksPath=/dev/null',
            '-c','user.name=Fixture','-c','user.email=fixture@example.invalid',*a],
            cwd=self.root,stderr=subprocess.PIPE)
        self.git('init','-q'); (self.root/'src').mkdir()
        (self.root/'src/deleted').write_text('old\n')
        (self.root/'src/changed').write_text('old\n')
        self.git('add','src'); self.git('commit','-qm','base')
        self.base = self.git('rev-parse','HEAD').decode().strip()
        (self.root/'src/deleted').unlink(); (self.root/'src/new').write_text('new\n')
        (self.root/'src/changed').write_text('new\n')
        self.git('add','src'); self.git('commit','-qm','source')
        self.head = self.git('rev-parse','HEAD').decode().strip()

    def tearDown(self): self.tmp.cleanup()

    def test_complete_added_deleted_modified_blob_manifest_no_writes(self):
        before = {p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        reader = c._LocalGit(self.root)
        result = c.collect_bundle_manifest(self.root,self.base,self.head,('src',),reader=reader)
        self.assertEqual([x['path'] for x in result],['src/changed','src/deleted','src/new'])
        self.assertIsNone(result[1]['source']); self.assertIsNone(result[2]['base'])
        self.assertEqual(result[0]['source']['blob'],self.git('rev-parse',self.head+':src/changed').decode().strip())
        self.assertLessEqual(reader.reads,6)
        self.assertEqual(before,{p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_scope_forbidden_base_and_exact_sha_reject(self):
        for base, head, scope, forbidden in ((self.head,self.base,('src',),()),
                ('main',self.head,('src',),()),(self.base,self.head,('docs',),()),
                (self.base,self.head,('src',),('src/new',))):
            with self.subTest(base=base,scope=scope), self.assertRaises(LLMToolError):
                c.collect_bundle_manifest(self.root,base,head,scope,forbidden)

    def test_ancestor_read_rejects_nonancestor_and_fixed_argv_is_narrow(self):
        reader = c._LocalGit(self.root)
        with self.assertRaises(LLMToolError): reader.read(('merge-base','--is-ancestor',self.head,self.base))
        for argv in (('merge-base','--is-ancestor','HEAD',self.head),
                     ('ls-tree','-r','--full-tree','-z','HEAD'),('fetch','origin')):
            with patch('subprocess.Popen',side_effect=AssertionError('not allowed')):
                with self.assertRaises(LLMToolError): reader.read(argv)

    def test_symlink_blob_is_not_presented_as_regular_source(self):
        (self.root/'src/link').symlink_to('changed'); self.git('add','src/link'); self.git('commit','-qm','link')
        head = self.git('rev-parse','HEAD').decode().strip()
        with self.assertRaises(LLMToolError): c.collect_bundle_manifest(self.root,self.base,head,('src',))


if __name__ == '__main__': unittest.main()
