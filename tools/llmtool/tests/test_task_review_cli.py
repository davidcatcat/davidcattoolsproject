"""Public task review cannot activate fake/real authority or load configuration."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from llmtool.cli import main
from llmtool.task_review_collector import LocalFacts
from test_task_review import fixture


class ReviewCLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform=='darwin' else None)
        self.root=Path(self.temp.name)
        ready,final,expect,events=fixture(); now=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        events=tuple(replace(e,timestamp=now) for e in events)
        self.expect=expect
        self.files={'contract':ready.to_json(),'acceptance-contract':final.to_json(),
                    'expect':expect.to_json(),'events':'\n'.join(e.to_json() for e in events)}
        for name,text in self.files.items(): (self.root/name).write_text(text)
        self.args=['task','review']
        for name in self.files: self.args+=['--'+name,str(self.root/name)]
        self.args+=['--json']
    def tearDown(self): self.temp.cleanup()
    def cli(self,args=None):
        out=io.StringIO();err=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err): code=main(args or self.args)
        return code,json.loads(out.getvalue()),err.getvalue()
    def facts(self):
        return LocalFacts('/repo/codex','feat/a',self.expect.source_sha,'/repo/main/.git',
                          'davidcatcat/davidcattoolsproject','a'*64,'b'*64,'c'*64,'d'*64,True)
    def test_valid_inputs_still_block_b_before_provider_config_or_network(self):
        with patch('llmtool.task_review_cli.collect_local_facts',return_value=self.facts()),\
             patch('llmtool.cli.resolve_config',side_effect=AssertionError('no config')),\
             patch('subprocess.Popen',side_effect=AssertionError('no provider')),\
             patch('socket.socket',side_effect=AssertionError('no egress')):
            code,result,err=self.cli()
        self.assertEqual(code,1);self.assertEqual(result['code'],'REVIEW_AUTHORITY_UNAVAILABLE')
        self.assertEqual(result['collector_status'],'LOCAL_FACTS_ONLY')
        self.assertEqual(result['provider_calls'],0);self.assertFalse(result['test_only'])
        self.assertFalse(result['authorized']);self.assertEqual(err,'')
        self.assertNotIn('writer-session',json.dumps(result));self.assertNotIn('/private/root',json.dumps(result))
    def test_caller_env_or_extra_json_cannot_wire_fake_authority(self):
        with patch.dict(os.environ,{'LLMTOOL_FAKE_GRANT':'ALLOW','LLMTOOL_REVIEW_PROVIDER':'claude-cli'}),\
             patch('llmtool.task_review_cli.collect_local_facts',return_value=self.facts()):
            code,result,err=self.cli(); self.assertEqual(code,1);self.assertEqual(result['provider_calls'],0)
        data=json.loads(self.files['expect']);data['production_grant']='ALLOW'
        (self.root/'expect').write_text(json.dumps(data))
        code,result,err=self.cli();self.assertEqual(code,2);self.assertEqual(result['provider_calls'],0)
    def test_no_fake_or_provider_cli_flags(self):
        for option in ('--fake-provider','--grant','--attest','--provider'):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                main(self.args+[option,'ALLOW'])
    def test_unsafe_inputs_have_fixed_sanitized_codes_and_no_provider(self):
        (self.root/'expect').unlink(); (self.root/'expect').symlink_to(self.root/'contract')
        with patch('subprocess.Popen',side_effect=AssertionError()):
            code,result,err=self.cli()
        self.assertEqual(code,2); self.assertNotIn(str(self.root),json.dumps(result)); self.assertEqual(result['provider_calls'],0)
    def test_alias_all_four_inputs_rejected(self):
        args=self.args.copy();args[args.index('--acceptance-contract')+1]=str(self.root/'contract')
        code,result,err=self.cli(args);self.assertEqual(code,2);self.assertEqual(result['code'],'REVIEW_INPUT_ALIAS')
    def test_non_ready_rejected_without_collector_or_writes(self):
        (self.root/'events').write_text('')
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        with patch('llmtool.task_review_cli.collect_local_facts',side_effect=AssertionError()):
            code,result,err=self.cli()
        self.assertEqual(code,1);self.assertEqual(result['code'],'REVIEW_NOT_READY')
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})
    def test_head_drift_blocks_before_real_path(self):
        with patch('llmtool.task_review_cli.collect_local_facts',return_value=replace(self.facts(),source_sha='3'*40)):
            code,result,err=self.cli()
        self.assertEqual(code,1);self.assertEqual(result['code'],'REVIEW_SOURCE_DRIFT')
    def test_actual_root_branch_common_dir_mismatch_denies_identity(self):
        for change in ({'root':'/wrong/root'},{'branch':'feat/peer'},{'common_dir':'/wrong/.git'}):
            with patch('llmtool.task_review_cli.collect_local_facts',return_value=replace(self.facts(),**change)):
                code,result,err=self.cli()
            self.assertEqual(code,1);self.assertEqual(result['code'],'REVIEW_IDENTITY_NOT_ATTESTED')
    def test_deep_json_checked_before_parse(self):
        (self.root/'expect').write_text('['*17+']'*17)
        code,result,err=self.cli();self.assertEqual(code,2);self.assertEqual(result['code'],'REVIEW_DEPTH_INVALID')

if __name__=='__main__': unittest.main()
