"""Native read-only collector oracle and adversarial fixed Git primitive tests."""
import hashlib
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from llmtool import task_review_collector as c
from llmtool.errors import LLMToolError
from test_task_review import fixture,FakeHost,NOW


class CollectorTests(unittest.TestCase):
    def test_observation_allows_automation_branch_but_still_rejects_main(self):
        ready,final,expect,events=fixture();observation=FakeHost(ready,final,expect,events).observation
        dev,reviewer=expect.agents
        automation=replace(dev,branch='automation/ai-loop')
        automation_expect=replace(expect,agents=(automation,reviewer),authored_by=(automation,))
        automation_observation=replace(observation,agents=(automation,reviewer),authored_by=(automation,))
        self.assertIsNone(automation_observation.validate(automation_expect,events,NOW,10))
        with self.assertRaises(LLMToolError) as raised:
            replace(dev,branch='main')
        self.assertEqual(raised.exception.lifecycle_code,'TASK_BINDING_MISMATCH')

    def test_fixed_git_reads_reject_arbitrary_argv_before_process(self):
        with patch('subprocess.Popen',side_effect=AssertionError('not allowed')):
            for argv in [('fetch','origin'),('reset','--hard'),('config','--get','arbitrary'),
                         ('diff','HEAD','; curl'),('show','attacker')]:
                with self.subTest(argv=argv),self.assertRaises(LLMToolError): c._LocalGit(Path.cwd()).read(argv)
    def test_read_budget_exhaustion_before_process(self):
        reader=c._LocalGit(Path.cwd()); reader.reads=32
        with patch('subprocess.Popen',side_effect=AssertionError()),self.assertRaises(LLMToolError):
            reader.read(c.git_intel.HEAD)
        reader.reads=0; reader.started-=31
        with patch('subprocess.Popen',side_effect=AssertionError()),self.assertRaises(LLMToolError):
            reader.read(c.git_intel.HEAD)
    def test_physical_symlink_root_and_ancestor_reject(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform=='darwin' else None) as folder:
            root=Path(folder); (root/'actual').mkdir(); (root/'link').symlink_to(root/'actual',target_is_directory=True)
            for path in (root/'link',root/'link'/'child'):
                with self.assertRaises((LLMToolError,OSError)): c.physical_directory(path)
    def test_wrong_import_root_cannot_launch_an_interpreter(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform=='darwin' else None) as folder:
            with patch('subprocess.Popen',side_effect=AssertionError('wrong-root execution')),self.assertRaises(LLMToolError):
                c.collect_local_facts(folder)
    def test_fixture_no_attestation_or_wrong_runtime_is_never_valid(self):
        ready,final,expect,events=fixture();host=FakeHost(ready,final,expect,events)
        for field,value in [('identity_attested',False),('host_record',''),('runtime_paths',('/other/python',)*3)]:
            with self.subTest(field=field),self.assertRaises(LLMToolError):
                replace(host.observation,**{field:value}).validate(expect,events,NOW,10)
    def test_state_digest_covers_session_source_and_events_but_not_sample_time(self):
        ready,final,expect,events=fixture();o=FakeHost(ready,final,expect,events).observation
        self.assertEqual(o.state_digest,replace(o,monotonic_at=11).state_digest)
        for change in ({'source_sha':'3'*40},{'epoch':8},{'host_record':'other'},
                       {'events':events[:-1]},{'agents':(replace(o.agents[0],session_id='other'),o.agents[1])}):
            self.assertNotEqual(o.state_digest,replace(o,**change).state_digest)
    def test_fresh_utc_and_monotonic_both_required(self):
        ready,final,expect,events=fixture();o=FakeHost(ready,final,expect,events).observation
        for change in ({'monotonic_at':4},{'monotonic_at':11},{'observed_at':'2026-10-13T00:00:11Z'},
                       {'observed_at':'2026-10-13T00:00:04Z'}):
            with self.subTest(change=change),self.assertRaises(LLMToolError):
                replace(o,**change).validate(expect,events,NOW,10)



class SourceCollectionTests(unittest.TestCase):
    def setUp(self):
        self.base='2'*40;self.head='1'*40;self.blob='3'*40
        self.calls=[];self.text='def answer():\n    return 42\n'
    def read(self,reader,args):
        self.calls.append(tuple(args))
        if args==c.git_intel.HEAD: return (self.head+'\n').encode()
        if args[:2]==('diff','--name-only'): return b'src/app.py\0'
        if args[0]=='diff': return b'diff --git a/src/app.py b/src/app.py\n+return 42\n'
        if args[0]=='ls-tree': return ('100644 blob '+self.blob+'\tsrc/app.py\0').encode()
        if args[0]=='cat-file': return self.text.encode()
        raise AssertionError(args)
    def collect(self):
        with patch.object(c._LocalGit,'read',autospec=True,side_effect=self.read):
            return c.collect_source_data(Path.cwd(),self.base,self.head,('src',))
    def test_full_base_and_head_originals_sha_and_complete_diff(self):
        diff,sources=self.collect()
        self.assertTrue(diff['complete']);self.assertEqual(diff['changed_paths'],['src/app.py'])
        import json
        source=json.loads(sources[0]['content'])
        self.assertEqual(source['base']['commit'],self.base)
        self.assertEqual(source['source']['commit'],self.head)
        self.assertEqual(source['source']['text'],self.text)
        self.assertEqual(sources[0]['sha256'],hashlib.sha256(sources[0]['content'].encode()).hexdigest())
    def test_binary_sensitive_oversize_blob_rejected(self):
        for text in ('\0binary','api_key=sk-'+('x'*32),'x'*(c.SOURCE_LIMIT+1)):
            self.text=text
            with self.subTest(text=text[:15]),self.assertRaises(LLMToolError): self.collect()
    def test_source_commit_changed_after_read_rejects(self):
        original=self.read;seen=0
        def drift(reader,args):
            nonlocal seen
            if args==c.git_intel.HEAD:
                seen+=1
                if seen>1: return b'4444444444444444444444444444444444444444\n'
            return original(reader,args)
        with patch.object(c._LocalGit,'read',autospec=True,side_effect=drift),self.assertRaises(LLMToolError):
            c.collect_source_data(Path.cwd(),self.base,self.head,('src',))
    def test_symlink_or_submodule_tree_not_exported(self):
        original=self.read
        def submodule(reader,args):
            if args[0]=='ls-tree':return ('160000 commit '+self.blob+'\tsrc/app.py\0').encode()
            return original(reader,args)
        with patch.object(c._LocalGit,'read',autospec=True,side_effect=submodule),self.assertRaises(LLMToolError):
            c.collect_source_data(Path.cwd(),self.base,self.head,('src',))



class NativeGitDataTests(unittest.TestCase):
    """Standalone minimal synthetic Git fixture, NOT a copy of the project."""
    def setUp(self):
        import subprocess
        self.temp=tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform=='darwin' else None)
        self.root=Path(self.temp.name);(self.root/'src').mkdir()
        self.run_git=lambda *a:subprocess.check_output(['/usr/bin/git','-c','core.hooksPath=/dev/null',*a],cwd=self.root,stderr=subprocess.PIPE)
        self.run_git('init','-q');self.run_git('config','user.name','Fixture');self.run_git('config','user.email','fixture@example.invalid')
        self.file=self.root/'src/app.py';self.file.write_text('answer = 1\n')
        self.run_git('add','src/app.py');self.run_git('commit','-qm','base')
        self.base=self.run_git('rev-parse','HEAD').decode().strip()
        self.file.write_text('answer = 42\n');self.run_git('add','src/app.py');self.run_git('commit','-qm','subject')
        self.head=self.run_git('rev-parse','HEAD').decode().strip()
    def tearDown(self):self.temp.cleanup()
    def test_actual_git_exact_sha_originals_and_no_data_write(self):
        before={p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        diff,sources=c.collect_source_data(self.root,self.base,self.head,('src',))
        self.assertIn('+answer = 42',diff['content'])
        import json
        data=json.loads(sources[0]['content']);self.assertEqual(data['base']['text'],'answer = 1\n')
        self.assertEqual(data['source']['text'],'answer = 42\n')
        self.assertEqual(before,{p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
    def test_real_git_wrong_head_source_and_scope_fail_closed(self):
        for head,scope in ((self.base,('src',)),(self.head,('docs',))):
            with self.subTest(head=head,scope=scope),self.assertRaises(LLMToolError):
                c.collect_source_data(self.root,self.base,head,scope)
    def test_config_probe_excludes_hardening_overrides_but_rejects_actual_local_hooks(self):
        query=next(a for a in c.FIXED_READS if '--get-regexp' in a)
        self.assertEqual(c._LocalGit(self.root).read(query),b'')
        self.run_git('config','core.hooksPath','/untrusted/hooks')
        self.assertIn(b'core.hookspath',c._LocalGit(self.root).read(query).lower())
    def test_real_binary_blob_not_exported(self):
        self.file.write_bytes(b'\x00binary');self.run_git('add','src/app.py');self.run_git('commit','-qm','binary')
        head=self.run_git('rev-parse','HEAD').decode().strip()
        with self.assertRaises(LLMToolError): c.collect_source_data(self.root,self.base,head,('src',))


class LocalFactsOriginTests(unittest.TestCase):
    def _collect(self, origin, branch='feat/review-origin'):
        with tempfile.TemporaryDirectory(prefix='review-origin-') as folder:
            root=Path(folder)
            head='1'*40
            common=str(root/'.git');gitdir=str(root/'.git/worktrees/task')
            registration=(f'worktree {root}\nbranch refs/heads/{branch}\nlocked\n').encode()

            class Reader:
                def __init__(self): self.root=root
                def read(self,args):
                    if args==c.git_intel.ROOT:return (str(root)+'\n').encode()
                    if args==c.git_intel.BRANCH:return (branch+'\n').encode()
                    if args==c.git_intel.HEAD:return (head+'\n').encode()
                    if args==('rev-parse','--path-format=absolute','--git-common-dir'):
                        return (common+'\n').encode()
                    if args==('rev-parse','--absolute-git-dir'):return (gitdir+'\n').encode()
                    if args==('config','--get','remote.origin.url'):return (origin+'\n').encode()
                    if args==('worktree','list','--porcelain'):return registration
                    if args==c.git_intel.STATUS:return b''
                    if args in (('ls-files','--stage','-z'),
                                ('ls-files','--others','--exclude-standard','-z'),
                                ('ls-files','--others','--ignored','--exclude-standard','-z')):
                        return b''
                    if len(args)>2 and args[:2]==('config','--local'):
                        return b''
                    raise AssertionError(args)

            reader=Reader()
            with patch.object(c,'physical_directory',return_value=root.stat()), \
                 patch('llmtool.task_lifecycle_cli.read_task_file',return_value=('fixture',None)), \
                 patch.object(sys,'prefix',str(root/'tools/llmtool/.venv')), \
                 patch.object(sys,'executable',str(root/'tools/llmtool/.venv/bin/python')), \
                 patch('llmtool.__file__',str(root/'tools/llmtool/llmtool/__init__.py')):
                return c.collect_local_facts(root,reader=reader)

    def test_accepts_only_exact_d6_origins_and_preserves_repository(self):
        for origin in (
            'git@github.com:davidcatcat/davidcattoolsproject.git',
            'https://github.com/davidcatcat/davidcattoolsproject.git',
        ):
            with self.subTest(origin=origin):
                facts=self._collect(origin)
                self.assertEqual(facts.repository,'davidcatcat/davidcattoolsproject')
        for origin in (
            'git@github.com:davidcatcat/davidcattoolsproject.git/',
            'git@github.com:davidcatcat/davidcattoolsproject',
            'Git@github.com:davidcatcat/davidcattoolsproject.git',
            'git@github.com:someoneelse/davidcattoolsproject.git',
            'ssh://git@github.com/davidcatcat/davidcattoolsproject.git',
            'https://github.com/davidcatcat/davidcattoolsproject.git/',
            'https://github.com/davidcatcat/davidcattoolsproject',
            'HTTPS://github.com/davidcatcat/davidcattoolsproject.git',
            'https://github.com/someoneelse/davidcattoolsproject.git',
        ):
            with self.subTest(origin=origin), self.assertRaises(LLMToolError):
                self._collect(origin)

    def test_automation_branch_is_allowed_but_main_remains_rejected(self):
        facts=self._collect('git@github.com:davidcatcat/davidcattoolsproject.git',
                            branch='automation/ai-loop')
        self.assertEqual(facts.branch,'automation/ai-loop')
        with self.assertRaises(LLMToolError):
            self._collect('git@github.com:davidcatcat/davidcattoolsproject.git',branch='main')

if __name__=='__main__': unittest.main()
