"""Synthetic-only Step0 census; never opens an actual CLI rollout."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from llmtool import rollout_census as c


def row(ordinal, kind, payload):
    return json.dumps(dict(timestamp='2026-10-15T00:00:00Z', ordinal=ordinal,
                           type=kind, payload=payload)).encode() + b'\n'


class CensusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve(); self.home.chmod(0o700)
        self.path = self.home/'sessions/2026/10/15/rollout-2026-10-15T00-00-00-reviewer.jsonl'
        self.path.parent.mkdir(parents=True, mode=0o700)
        for part in (self.home/'sessions', self.home/'sessions/2026', self.home/'sessions/2026/10'):
            part.chmod(0o700)
        self.meta = row(12, 'session_meta', dict(id='reviewer', session_id='session', history_mode='paginated'))
        self.path.write_bytes(self.meta + row(13, 'event_msg', dict(type='task_started')))
        self.path.chmod(0o600)

    def run_census(self, **kw):
        st = self.path.lstat()
        p = c.InputPin(str(self.home), str(self.path), 'session', 'reviewer',
                       st.st_uid, st.st_dev, st.st_ino, 0o600)
        return c.census(p, **kw)

    def test_clean_structure_not_native_authority(self):
        result = self.run_census()
        self.assertEqual(result['status'], 'FORMAT_OBSERVED_SUPPORTED')
        self.assertTrue(result['ordinal_summary']['meta_plus_one'])
        self.assertEqual(result['resource_counts']['records'], 1)
        self.assertTrue(result['closed'])
        self.assertNotIn('reviewer', json.dumps(result))

    def write(self, body, meta=None):
        self.path.write_bytes((self.meta if meta is None else meta) + body)

    def event(self, ordinal, kind='task_started', **kw):
        return row(ordinal, 'event_msg', dict(type=kind, **kw))

    def test_upstream_golden_projection_privacy(self):
        # Codex 0.162.1 / 092d3acd: protocol.rs SessionMeta and AgentMessage,
        # rollout writer serialized event_msg/item_completed; see PR155 pins.
        body = self.event(13) + self.event(14, 'item_completed', item={
            'type': 'UserMessage', 'id': 'CANARY_ITEM', 'client_id': 'CANARY_CLIENT',
            'content': [{'type': 'text', 'text': 'CANARY_USER', 'text_elements': []}]})
        for ordinal, phase in ((15, 'commentary'), (16, 'final_answer')):
            body += self.event(ordinal, 'item_completed', item={
                'type': 'AgentMessage', 'phase': phase,
                'content': [{'type': 'Text', 'text': 'CANARY_AGENT'}]})
        body += self.event(17, 'item_completed', item={'type':'Reasoning','content':'CANARY_REASONING'})
        body += self.event(18, 'task_complete')
        meta = row(12, 'session_meta', dict(id='reviewer', session_id='session',
            history_mode='paginated', base_instructions='CANARY_META', dynamic_tools=['CANARY_TOOL']))
        self.write(body, meta)
        r = self.run_census()
        self.assertEqual(r['status'], 'FORMAT_OBSERVED_SUPPORTED')
        self.assertEqual(r['shape_counts'], dict(user_text=1,agent_text=2,unknown=0,unsupported=0,pickup_rejected=0))
        self.assertEqual(r['fixed_histograms']['error']['ABSENT'], 1)
        self.assertNotIn('CANARY', json.dumps(r))
        self.assertNotIn(str(self.path), json.dumps(r))
        self.assertEqual(r['resource_counts']['records'], 6)

    def test_history_presence_null_value_and_legacy(self):
        for key in c.HISTORY:
            for value in (None, 'CANARY_HISTORY'):
                meta = dict(id='reviewer',session_id='session',history_mode='paginated')
                meta[key] = value
                self.write(self.event(13),row(12,'session_meta',meta))
                r = self.run_census()
                self.assertEqual(r['status'],'UNSUPPORTED')
                self.assertTrue(r['metadata_summary']['history_presence'][key])
                self.assertNotIn('CANARY',json.dumps(r))
        for mode in ('legacy','future',None):
            self.write(self.event(13),row(12,'session_meta',dict(id='reviewer',session_id='session',history_mode=mode)))
            r=self.run_census();self.assertIn('LEGACY_HISTORY',r['reasons'])
            self.assertEqual(r['resource_counts']['records'],1)

    def test_metadata_overlimit_and_invalid_do_not_project_history(self):
        for meta,code in ((b'x'*(c.LINE+1)+b'\n','META_OVER_LIMIT'),
                          (b'{"broken":\n','META_INVALID'),(b'{}','META_INVALID')):
            self.write(self.event(13),meta)
            r=self.run_census();self.assertIn(code,r['reasons'])
            self.assertEqual(r['resource_counts']['records'],0)
            self.assertTrue(all(x is None for x in r['metadata_summary']['history_presence'].values()))
            self.assertLessEqual(r['resource_counts']['bytes'],c.LINE+1)

    def test_metadata_exact_line_boundary(self):
        payload=dict(id='reviewer',session_id='session',history_mode='paginated',padding='')
        base=row(12,'session_meta',payload)
        for delta in (-1,0,1):
            payload['padding']='x'*(c.LINE-len(base)+delta)
            self.write(self.event(13),row(12,'session_meta',payload))
            r=self.run_census()
            self.assertEqual('META_OVER_LIMIT' in r['reasons'],delta==1)
            payload['padding']=''

    def test_identity_mismatch_before_history_projection(self):
        self.write(self.event(13),row(12,'session_meta',dict(id='foreign',session_id='session',history_mode='paginated')))
        r=self.run_census();self.assertIn('IDENTITY_MISMATCH',r['reasons']);self.assertEqual(r['resource_counts']['records'],0)

    def test_ordinals_invalid_and_gaps(self):
        for ordinal in (True,-1,2**64,1.0,None):
            self.write(self.event(ordinal));r=self.run_census()
            self.assertIn('ORDINAL_INVALID',r['reasons']);self.assertFalse(r['ordinal_summary']['contiguous'])
        for ords in ((14,), (13,13), (13,12), (13,15)):
            self.write(b''.join(self.event(x) for x in ords));r=self.run_census()
            self.assertIn('ORDINAL_GAP',r['reasons']);self.assertFalse(r['sample_clean'])
        self.write(b'');r=self.run_census();self.assertIsNone(r['ordinal_summary']['meta_plus_one'])

    def test_strict_json_unicode_and_unknown_canaries(self):
        for data in (b'\xff', b'{', b'{"ordinal":13,"ordinal":13}',
                     b'{"ordinal":NaN}', b'{"x":"\\ud800"}'):
            self.write(data+b'\n');r=self.run_census()
            self.assertIn('INVALID_RECORD',r['reasons'])
        self.write(row(13,'CANARY_UNKNOWN',dict(type='CANARY_TYPE')))
        r=self.run_census();self.assertEqual(r['status'],'UNSUPPORTED')
        self.assertEqual(r['shape_counts']['unknown'],1);self.assertNotIn('CANARY',json.dumps(r))

    def test_content_shapes_phase_and_error_enums(self):
        for content in ([],[{'type':'Text','text':'a'},{'type':'Text','text':'b'}],
                        [{'type':'text','text':'a'}],[{'type':'Text','text':4}],None):
            self.write(self.event(13,'item_completed',item=dict(type='AgentMessage',phase='final_answer',content=content)))
            r=self.run_census();self.assertIn('UNSUPPORTED_SHAPE',r['reasons'])
        for phase in (None, 'CANARY_PHASE'):
            self.write(self.event(13,'item_completed',item=dict(type='AgentMessage',phase=phase,content=[{'type':'Text','text':'x'}])))
            r=self.run_census();self.assertEqual(r['fixed_histograms']['phase']['UNKNOWN'],1)
            self.assertNotIn('CANARY',json.dumps(r))
        for error,label in ((None,'NULL'),({},'OBJECT'),('CANARY_ERROR','OTHER')):
            self.write(self.event(13,'task_complete',error=error));r=self.run_census()
            self.assertEqual(r['fixed_histograms']['error'][label],1);self.assertEqual(r['status'],'FORMAT_OBSERVED_SUPPORTED' if label=='OBJECT' else 'UNSUPPORTED')

    def test_record_bound_exact_and_plus_one_preserves_counts(self):
        for count in (127,128,129):
            self.write(b''.join(self.event(i) for i in range(13,13+count)))
            r=self.run_census();self.assertEqual(r['resource_counts']['records'],min(count,128))
            self.assertEqual('RECORD_LIMIT' in r['reasons'],count==129)

    def test_tail_bounds_alignment_nonzero_origin_and_sample_clean(self):
        # Few large but legal rows exercise byte windows instead of record cap.
        for delta in (-1,0,1):
            first=self.event(13, padding='x'*50000)
            rows=b''.join(self.event(i,padding='x'*50000) for i in range(14,22))
            padding=c.TAIL+delta-len(first+rows)
            self.assertGreater(padding,0)
            body=self.event(13,padding='x'*(50000+padding))+rows
            self.assertEqual(len(body),c.TAIL+delta)
            self.write(body);r=self.run_census()
            self.assertEqual(r['status'],'INCOMPLETE' if delta==1 else 'FORMAT_OBSERVED_SUPPORTED')
            self.assertLessEqual(r['resource_counts']['bytes'],c.TOTAL)
            self.assertTrue(r['sample_clean'])
            if delta==1:
                self.assertEqual(r['ordinal_summary']['origin'],'WINDOW_DERIVED')
                self.assertIsNone(r['ordinal_summary']['meta_plus_one'])
        self.write(b''.join(self.event(i,padding='\u4e2d'*10000) for i in range(13,34)))
        r=self.run_census();self.assertEqual(r['reasons'],['BOUNDED_TAIL_SAMPLE'])
        self.assertTrue(r['sample_clean']);self.assertGreater(r['resource_counts']['records'],0)

    def test_partial_last_alignment_unavailable_and_long_line(self):
        self.write(self.event(13)+b'\xe4\xb8');r=self.run_census()
        self.assertIn('PARTIAL_LAST_LINE',r['reasons']);self.assertEqual(r['resource_counts']['records'],1)
        self.assertFalse(r['sample_clean'])
        self.write(b'x'*(c.TAIL+1));r=self.run_census()
        self.assertIn('TAIL_ALIGNMENT_UNAVAILABLE',r['reasons'])
        self.write(self.event(13,padding='x'*c.LINE));r=self.run_census()
        self.assertIn('LINE_OVER_LIMIT',r['reasons']);self.assertFalse(r['sample_clean'])

    def test_actual_short_reads_reach_read_limit(self):
        from unittest.mock import patch
        original=c.Budget.read
        def short(budget,fd,count):
            return original(budget,fd,count if budget.reads==0 else min(count,20))
        self.write(b''.join(self.event(i) for i in range(13,100)))
        with patch.object(c.Budget,'read',short):r=self.run_census()
        self.assertEqual(r['resource_counts']['reads'],128)
        self.assertIn('READ_LIMIT',r['reasons']);self.assertGreater(r['resource_counts']['records'],0)

    def test_preopen_symlink_fifo_device_and_link_rejected(self):
        from unittest.mock import patch
        original=c.os.open
        for mode in ('symlink','fifo','hardlink'):
            self.path.unlink()
            if mode=='symlink':self.path.symlink_to('/dev/null')
            elif mode=='fifo':os.mkfifo(self.path,0o600)
            else:
                other=self.home/'other';other.write_bytes(self.meta);other.chmod(0o600);os.link(other,self.path)
            with patch.object(c.os,'open',wraps=original) as opened:
                r=self.run_census();self.assertEqual(r['status'],'BLOCKED');opened.assert_not_called()
            self.path.unlink()
            self.path.write_bytes(self.meta);self.path.chmod(0o600)
        st=self.path.lstat()
        p=c.InputPin(str(self.home),str(self.path),'session','reviewer',st.st_uid,st.st_dev,st.st_ino,0o600)
        device=os.stat('/dev/null')
        with patch.object(c,'_physical',return_value=device),patch.object(c.os,'open') as opened:
            r=c.census(p);self.assertEqual(r['status'],'BLOCKED');opened.assert_not_called()

    def test_parent_symlink_and_private_permissions(self):
        st=self.path.stat()
        link=self.home/'alias';link.symlink_to(self.home/'sessions',target_is_directory=True)
        p=c.InputPin(str(self.home),str(link/'2026/10/15'/self.path.name),'session','reviewer',st.st_uid,st.st_dev,st.st_ino,0o600)
        self.assertEqual(c.census(p)['status'],'BLOCKED')
        self.path.chmod(0o644);self.assertEqual(self.run_census()['status'],'BLOCKED')

    def test_growth_shrink_replacement_and_mode_drift(self):
        from unittest.mock import patch
        original=c.os.read
        for mode in ('grow','shrink','replace','chmod'):
            self.write(self.event(13,padding='x'*500));done=[]
            def read(fd,count):
                data=original(fd,count)
                if not done:
                    done.append(1)
                    if mode=='grow':
                        with self.path.open('ab') as f:f.write(self.event(14))
                    elif mode=='shrink':
                        with self.path.open('r+b') as f:f.truncate(len(self.meta)+20)
                    elif mode=='replace':
                        self.path.rename(self.home/'old');self.path.write_bytes(self.meta);self.path.chmod(0o600)
                    else:self.path.chmod(0o644)
                return data
            with patch.object(c.os,'read',side_effect=read):r=self.run_census()
            self.assertIn('SOURCE_GREW' if mode=='grow' else 'SOURCE_CHANGED',r['reasons'])
            self.assertFalse(r['sample_clean']);self.path.chmod(0o600)
            if (self.home/'old').exists():(self.home/'old').unlink()

    def test_close_deadline_and_operation_failure(self):
        from unittest.mock import patch
        close=c.os.close
        def failed(fd):close(fd);raise OSError('CANARY_EXCEPTION')
        with patch.object(c.os,'close',side_effect=failed):r=self.run_census()
        self.assertIn('CLOSE_FAILED',r['reasons']);self.assertNotIn('CANARY',json.dumps(r))
        b=c.Budget();b.deadline=b.start
        r=self.run_census(_budget=b);self.assertIn('DEADLINE',r['reasons'])
        b=c.Budget();ticks=iter((b.start,b.start,b.start+6))
        b.clock=lambda:next(ticks)
        with self.assertRaises(c.Stop) as exc:b.op(lambda:None)
        self.assertEqual(exc.exception.code,'DEADLINE')

    def test_byte_limit_preserves_projection(self):
        from unittest.mock import patch
        original=c.Budget.read
        def charged(budget,fd,count):
            if budget.reads>=2:budget.bytes=c.TOTAL
            return original(budget,fd,count)
        self.write(b''.join(self.event(i,padding='x'*1000) for i in range(13,60)))
        with patch.object(c.Budget,'read',charged):r=self.run_census()
        self.assertIn('BYTE_LIMIT',r['reasons']);self.assertGreater(r['resource_counts']['records'],0)

    def pin_document(self):
        import hashlib
        import sys
        root=Path(c.__file__).absolute().parents[3]
        st=self.path.stat()
        pin=dict(kind='llmtool.native-step0-census-pin',schema_version=1,root=str(root),
            source_sha='a'*40,code_hashes={k:hashlib.sha256((root/v).read_bytes()).hexdigest() for k,v in c.SOURCE_FILES.items()},
            runtime=dict(executable=sys.executable,prefix=sys.prefix,version=list(sys.version_info[:3]),
                         interpreter_sha256=hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest()),
            input=dict(codex_home=str(self.home),path=str(self.path),session_id='session',thread_id='reviewer',
                       uid=st.st_uid,dev=st.st_dev,inode=st.st_ino,mode=0o600),
            bounds=dict(line=c.LINE,total=c.TOTAL,tail=c.TAIL,records=128,reads=128,operation_seconds=5,total_seconds=30))
        return pin

    def argv(self,pin):
        import hashlib
        file=self.home/'pin.json';data=json.dumps(pin).encode();file.write_bytes(data);file.chmod(0o600)
        return ['--pin-file',str(file),'--pin-sha256',hashlib.sha256(data).hexdigest(),'--json']

    def entry(self,argv,output=None,main_file="DRIVER",*,flag_overrides=None):
        import sys
        from unittest.mock import patch
        import io
        root=Path(c.__file__).absolute().parents[3]
        output=output if output is not None else io.StringIO()
        # CI's ordinary unittest runner is not an isolated Host. This private
        # fixture models the required flags; actual CLI subprocesses use real
        # -B -I and production has no flags override or bypass.
        actual_flags=sys.flags
        expected_flags=dict(isolated=1,dont_write_bytecode=1)
        expected_flags.update(flag_overrides or {})
        class FixtureFlags:
            def __getattr__(self,key):
                return expected_flags[key] if key in expected_flags else getattr(actual_flags,key)
        with patch.object(sys,'flags',FixtureFlags()), patch.object(sys.modules["__main__"],"__file__",str(root/c.SOURCE_FILES["driver"]) if main_file=="DRIVER" else main_file,create=True):
            code=c._entry(argv,_root=root,_stdout=output, _runtime=self._runtime())
        return code,output

    def _runtime(self):
        import sys
        return (sys.executable,sys.prefix)

    def test_driver_valid_pins_and_safe_json(self):
        code,out=self.entry(self.argv(self.pin_document()))
        self.assertEqual(code,0,out.getvalue())
        r=json.loads(out.getvalue());self.assertEqual(r['status'],'FORMAT_OBSERVED_SUPPORTED')
        self.assertLess(len(out.getvalue()),16384);self.assertNotIn(str(self.path),out.getvalue())

    def test_driver_admission_before_input_read(self):
        from unittest.mock import patch
        import io
        pin=self.pin_document()
        for mutate in (lambda p:p.update(source_sha='CANARY'),
                       lambda p:p['code_hashes'].update(decoder='0'*64),
                       lambda p:p['runtime'].update(executable='/CANARY'),
                       lambda p:p.update(bounds={}),lambda p:p.update(root='/CANARY')):
            pin=self.pin_document();mutate(pin)
            with patch.object(c,'census') as read:
                code,out=self.entry(self.argv(pin));self.assertEqual(code,1);read.assert_not_called()
                self.assertNotIn('CANARY',out.getvalue())
        for argv in ([],['--pin-file','CANARY'],['--help'],['--pin-file','CANARY','--pin-sha256','bad','--json']):
            with patch.object(c,'census') as read:
                code,out=self.entry(argv);self.assertEqual(code,1);read.assert_not_called()
                self.assertNotIn('CANARY',out.getvalue())

    def test_pin_permissions_hash_size_symlink_and_duplicate_json(self):
        import hashlib
        from unittest.mock import patch
        argv=self.argv(self.pin_document());file=Path(argv[1])
        variants=[('mode',None),('hash',None),('size',b'x'*16385),('duplicate',b'{"x":1,"x":2}')]
        for mode,data in variants:
            argv=self.argv(self.pin_document())
            if mode=='mode':file.chmod(0o644)
            elif mode=='hash':argv[3]='0'*64
            else:file.write_bytes(data);argv[3]=hashlib.sha256(data).hexdigest()
            with patch.object(c,'census') as read:
                code,out=self.entry(argv);self.assertEqual(code,1);read.assert_not_called()
        file.unlink();file.symlink_to(self.path)
        with patch.object(c,'census') as read:
            code,out=self.entry(argv);self.assertEqual(code,1);read.assert_not_called()

    def test_output_failure_nonzero_and_no_private_traceback(self):
        class Bad:
            def write(self,value):raise OSError('CANARY_OUTPUT')
            def flush(self):raise AssertionError()
        code,_=self.entry(self.argv(self.pin_document()),Bad());self.assertEqual(code,1)

    def test_signal_handler_fail_closed_and_restored(self):
        import signal
        from unittest.mock import patch
        before=signal.getsignal(signal.SIGTERM)
        def signal_read(*args,**kw):
            os.kill(os.getpid(),signal.SIGTERM)
        with patch.object(c,'census',side_effect=signal_read):
            code,out=self.entry(self.argv(self.pin_document()))
        self.assertEqual(code,1);self.assertIn('INTERRUPTED',out.getvalue())
        self.assertEqual(signal.getsignal(signal.SIGTERM),before)

    def test_import_no_task_effects_and_no_pickup_calls(self):
        import ast
        import importlib
        from unittest.mock import patch
        from llmtool import codex_pickup_native as n
        with patch.object(c.os,'open',side_effect=AssertionError('task open')), \
             patch.object(c.os,'lstat',side_effect=AssertionError('task probe')), \
             patch.object(n,'feed_rollout_record',side_effect=AssertionError('feed')), \
             patch.object(n.EventCoverage,'seal',side_effect=AssertionError('seal')):
            importlib.reload(c)
        with patch.object(n,'feed_rollout_record',side_effect=AssertionError('feed')), \
             patch.object(n.EventCoverage,'seal',side_effect=AssertionError('seal')), \
             patch.object(n,'ComposedPickup',side_effect=AssertionError('compose')):
            self.assertEqual(self.run_census()['status'],'FORMAT_OBSERVED_SUPPORTED')
        tree=ast.parse(Path(c.__file__).read_text())
        calls={node.func.id for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)}
        self.assertFalse(calls & {'feed_rollout_record','ComposedPickup','QueueCodec','EventCoverage'})

    def test_synthetic_subprocess_json_exit_and_no_public_bypass(self):
        import subprocess
        import sys
        root=Path(c.__file__).absolute().parents[3]
        argv=self.argv(self.pin_document())
        # Private seam only in a test-owned -c wrapper, never a production flag.
        script='from llmtool import rollout_census as c;import sys;sys.modules[\"__main__\"].__file__='+repr(str(root/c.SOURCE_FILES['driver']))+';raise SystemExit(c._entry(sys.argv[1:],_root='+repr(str(root))+',_runtime=(sys.executable,sys.prefix)))'
        proc=subprocess.run([sys.executable,'-B','-I','-c',script]+argv,capture_output=True,timeout=10)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertEqual(proc.stderr,b'');self.assertEqual(json.loads(proc.stdout)['status'],'FORMAT_OBSERVED_SUPPORTED')
        actual=subprocess.run([sys.executable,'-B','-I',str(root/c.SOURCE_FILES['driver']),'--test-root',str(root)],capture_output=True,timeout=10)
        self.assertEqual(actual.returncode,1);self.assertNotIn(str(root).encode(),actual.stdout+actual.stderr)
        self.assertIn(b'PIN_INVALID',actual.stdout)

    def test_actual_cumulative_byte_boundary(self):
        payload=dict(id='reviewer',session_id='session',history_mode='paginated',padding='')
        payload['padding']='x'*(c.LINE-len(row(12,'session_meta',payload)))
        meta=row(12,'session_meta',payload)
        self.assertEqual(len(meta),c.LINE)
        for delta in (-1,0,1):
            lines=[]
            for i in range(7):
                target=c.LINE+(delta if i==6 else 0)
                # Above line bound is unsupported rather than a tail bound:
                # place the +1 in a short separate final record instead.
                if delta==1:target=c.LINE
                base=self.event(13+i,padding='')
                lines.append(self.event(13+i,padding='x'*(target-len(base))))
            body=b''.join(lines)
            if delta==1:body+=b'\n'
            self.write(body,meta);r=self.run_census()
            self.assertLessEqual(r['resource_counts']['bytes'],c.TOTAL)
            if delta<=0:
                self.assertEqual(r['resource_counts']['bytes'],c.TOTAL+delta)
                self.assertEqual(r['status'],'FORMAT_OBSERVED_SUPPORTED')
            else:
                self.assertEqual(r['resource_counts']['bytes'],c.TOTAL)
                self.assertEqual(r['status'],'BLOCKED') # last empty row invalid

    def test_utf8_cut_alignment_and_bounded_partial_not_clean(self):
        def utfrow(i):
            return json.dumps(dict(timestamp='x',ordinal=i,type='event_msg',
                payload=dict(type='task_started',padding='\u4e2d'*16000)),ensure_ascii=False).encode()+b'\n'
        self.write(b''.join(utfrow(i) for i in range(13,30)))
        r=self.run_census();self.assertEqual(r['reasons'],['BOUNDED_TAIL_SAMPLE'])
        self.assertTrue(r['sample_clean'])
        with self.path.open('ab') as f:f.write(b'{"incomplete":"\xe4')
        r=self.run_census();self.assertIn('PARTIAL_LAST_LINE',r['reasons']);self.assertFalse(r['sample_clean'])

    def test_no_feed_seal_network_spawn_thread_or_provider_effects(self):
        import socket
        import subprocess
        import threading
        from unittest.mock import patch
        with patch.object(socket,'socket',side_effect=AssertionError('socket')), \
             patch.object(subprocess,'Popen',side_effect=AssertionError('spawn')), \
             patch.object(threading.Thread,'start',side_effect=AssertionError('thread')):
            r=self.run_census();self.assertEqual(r['status'],'FORMAT_OBSERVED_SUPPORTED')

    def test_actual_production_cli_only_pinned_runtime(self):
        import subprocess
        import sys
        root=Path(c.__file__).absolute().parents[3]
        argv=self.argv(self.pin_document())
        proc=subprocess.run([sys.executable,'-B','-I',str(root/c.SOURCE_FILES['driver'])]+argv,
            capture_output=True,timeout=10)
        allowed=(str(root) in (c.CANONICAL,c.CODEX) and sys.executable==str(root/'tools/llmtool/.venv/bin/python'))
        self.assertEqual(proc.returncode,0 if allowed else 1,proc.stderr)
        self.assertEqual(proc.stderr,b'')
        r=json.loads(proc.stdout);self.assertEqual(r['status'],'FORMAT_OBSERVED_SUPPORTED' if allowed else 'BLOCKED')
        if not allowed:self.assertIn('RUNTIME_MISMATCH',r['reasons'])

    def test_python39_syntax(self):
        import ast
        root=Path(c.__file__).absolute().parents[3]
        for rel in c.SOURCE_FILES.values():
            if rel.endswith('codex_pickup_native.py'):continue
            ast.parse((root/rel).read_text(),feature_version=(3,9))

    def test_pin_and_hash_close_even_after_expired_deadline(self):
        from unittest.mock import patch
        import hashlib
        for kind in ('pin','hash'):
            file=self.home/'private';data=b'{}';file.write_bytes(data);file.chmod(0o600)
            b=c.Budget();now=[b.start];b.clock=lambda:now[0]
            original_read=c.os.read;original_close=c.os.close
            def expired_read(fd,count):
                data=original_read(fd,count);now[0]=b.work_deadline+1;return data
            with patch.object(c.os,'read',side_effect=expired_read), \
                 patch.object(c.os,'close',wraps=original_close) as closed:
                with self.assertRaises(c.Stop) as error:
                    if kind=='pin':c._read_pin(str(file),hashlib.sha256(data).hexdigest(),b)
                    else:c._hash_file(file,b)
                self.assertEqual(error.exception.code,'DEADLINE');self.assertEqual(closed.call_count,1)

    def test_shrink_while_unread_size_still_above_offset(self):
        from unittest.mock import patch
        self.write(b''.join(self.event(i,padding='x'*1500) for i in range(13,80)))
        original=c.os.read;calls=[]
        def shrinking(fd,count):
            data=original(fd,count);calls.append(len(data))
            if len(calls)==2:
                offset=os.lseek(fd,0,os.SEEK_CUR)
                newsize=self.path.stat().st_size-100
                self.assertGreater(newsize,offset)
                with self.path.open('r+b') as f:f.truncate(newsize)
            return data
        with patch.object(c.os,'read',side_effect=shrinking):r=self.run_census()
        self.assertIn('SOURCE_CHANGED',r['reasons']);self.assertEqual(r['status'],'BLOCKED')

    def test_invalid_metadata_never_continues_history_reads(self):
        from unittest.mock import patch
        self.write(b'CANARY_HISTORY'*10000,b'{"invalid":true}\n')
        original=c.os.read
        with patch.object(c.os,'read',wraps=original) as read:r=self.run_census()
        self.assertEqual(read.call_count,1);self.assertIn('META_INVALID',r['reasons'])
        self.assertEqual(r['resource_counts']['records'],0)

    def test_fresh_import_and_forbidden_pickup_effects(self):
        import subprocess
        import sys
        from unittest.mock import patch
        from llmtool import codex_pickup_native as n
        with patch.object(n.QueueCodec,'delivery',side_effect=AssertionError('delivery')), \
             patch.object(n.EventCoverage,'feed',side_effect=AssertionError('feed')):
            self.assertEqual(self.run_census()['status'],'FORMAT_OBSERVED_SUPPORTED')
        script='''import socket,subprocess,threading,os
from unittest.mock import patch
with patch.object(socket,'socket',side_effect=AssertionError('network')), patch.object(subprocess,'Popen',side_effect=AssertionError('spawn')), patch.object(threading.Thread,'start',side_effect=AssertionError('thread')), patch.object(os,'open',side_effect=AssertionError('taskopen')), patch.object(os,'lstat',side_effect=AssertionError('taskprobe')):
    import llmtool.rollout_census
'''
        result=subprocess.run([sys.executable,'-B','-I','-c',script],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout,b'')

    def test_python39_parents_without_slice_support(self):
        # pathlib parents gained slicing in3.10; preserve3.9 runtime semantics.
        from unittest.mock import patch
        original=Path.parents
        class Parents39:
            def __init__(self,value):self.value=value
            def __iter__(self):return iter(self.value)
            def __getitem__(self,key):
                if isinstance(key,slice):raise TypeError('parents slicing unavailable on3.9')
                return self.value[key]
        with patch.object(Path,'parents',property(lambda value:Parents39(original.fget(value)))):
            r=self.run_census()
        self.assertEqual(r['status'],'FORMAT_OBSERVED_SUPPORTED')

    def test_repair_n1_paginated_supported_writer_golden(self):
        # Pinned092d3acd: protocol/src/{items,models}.rs + rollout/src/policy.rs.
        # Historical IDs below are DATA; census never correlates/publishes them.
        body=self.event(13,turn_id='turn')
        body+=row(14,'response_item',dict(type='message',role='user',id='raw-user',content=[dict(type='input_text',text='CANARY')]))
        body+=row(15,'response_item',dict(type='message',role='assistant',content=[dict(type='output_text',text='CANARY')]))
        body+=row(16,'response_item',dict(type='reasoning',summary=[dict(type='summary_text',text='CANARY')]))
        body+=row(17,'turn_context',dict(turn_id='turn',base_instructions='CANARY'))
        body+=self.event(18,'token_count',info=dict(total_token_usage=dict(total_tokens=4)))
        body+=self.event(19,'item_completed',thread_id='reviewer',turn_id='turn',item=dict(type='Plan',id='plan',text='CANARY'))
        body+=self.event(20,'task_complete',turn_id='turn',error=dict(message='CANARY_FAILURE'))
        self.write(body);r=self.run_census()
        self.assertEqual(r['status'],'FORMAT_OBSERVED_SUPPORTED')
        self.assertEqual(r['shape_counts']['pickup_rejected'],0)
        self.assertEqual(r['fixed_histograms']['response_item']['message'],2)
        self.assertEqual(r['fixed_histograms']['event']['token_count'],1)
        self.assertEqual(r['fixed_histograms']['item']['Plan'],1)
        self.assertEqual(r['fixed_histograms']['terminal']['Failed'],1)
        self.assertNotIn('CANARY',json.dumps(r))

    def test_repair_n1_known_pickup_rejected_separate_from_unknown(self):
        top=('compacted','thread_settings_applied','thread_goal_updated','token_usage_record','world_state')
        responses=('additional_tools','agent_message','local_shell_call','function_call','tool_search_call','function_call_output',
            'custom_tool_call','custom_tool_call_output','tool_search_output','web_search_call','image_generation_call',
            'compaction','configuration_update','compaction_trigger','context_compaction')
        items=('FunctionCallOutput','HookPrompt','CommandExecution','DynamicToolCall','CollabAgentToolCall',
            'SubAgentActivity','WebSearch','ImageView','Extension','ImageGeneration','EnteredReviewMode',
            'ExitedReviewMode','FileChange','McpToolCall','ContextCompaction')
        values=[row(13,k,{}) for k in top]
        values += [row(13,'response_item',dict(type=k)) for k in responses]
        values += [self.event(13,'item_completed',item=dict(type=k)) for k in items]
        values += [row(13,'response_item',dict(type='message',role=k)) for k in ('developer','system')]
        for value in values:
            with self.subTest(value=value[:90]):
                self.write(value);r=self.run_census()
                self.assertEqual(r['status'],'UNSUPPORTED')
                self.assertIn('PICKUP_REJECTED_RECORD',r['reasons'])
                self.assertNotIn('UNKNOWN_TYPE',r['reasons'])
                self.assertEqual(r['shape_counts']['pickup_rejected'],1)
        for value in (row(13,'CANARY',{}),row(13,'response_item',dict(type='CANARY')),
                      self.event(13,'item_completed',item=dict(type='CANARY'))):
            self.write(value);r=self.run_census();self.assertIn('UNKNOWN_TYPE',r['reasons'])
            self.assertNotIn('CANARY',json.dumps(r))

    def test_repair_n1_phase_missing_partial_unknown_distinct(self):
        for value,label in (('ABSENT','ABSENT'),('partial_answer','partial_answer'),(None,'UNKNOWN'),('CANARY','UNKNOWN')):
            item=dict(type='AgentMessage',id='agent',content=[dict(type='Text',text='CANARY')])
            if value!='ABSENT':item['phase']=value
            self.write(self.event(13,'item_completed',item=item));r=self.run_census()
            self.assertEqual(r['status'],'UNSUPPORTED')
            self.assertEqual(r['fixed_histograms']['phase'][label],1)
            self.assertFalse(r['sample_clean']);self.assertNotIn('CANARY',json.dumps(r))

    def test_repair_n1_failed_and_interrupted_terminal_shapes(self):
        for fields,supported in ((dict(error={}),True),({},True),(dict(error=None),False),
                                  (dict(error='CANARY'),False)):
            self.write(self.event(13,'task_complete',**fields));r=self.run_census()
            self.assertEqual(r['status'],'FORMAT_OBSERVED_SUPPORTED' if supported else 'UNSUPPORTED')
        for reason in ('interrupted','CANARY',None):
            self.write(self.event(13,'turn_aborted',turn_id='turn',reason=reason));r=self.run_census()
            self.assertEqual(r['status'],'FORMAT_OBSERVED_SUPPORTED' if reason=='interrupted' else 'UNSUPPORTED')

    def test_repair_n2_no_complete_sample_cannot_be_supported(self):
        for body in (b'',b'{"partial":"\xe4',b'not-terminated'):
            self.write(body);r=self.run_census()
            self.assertEqual(r['status'],'INCOMPLETE');self.assertIn('NO_RECORDS',r['reasons'])
            self.assertFalse(r['sample_clean']);self.assertIsNone(r['ordinal_summary']['meta_plus_one'])
        self.path.write_bytes(b'');r=self.run_census()
        self.assertEqual(r['status'],'BLOCKED');self.assertIn('META_INVALID',r['reasons'])
        meta=row(12,'session_meta',dict(id='reviewer',session_id='session',history_mode='legacy'))
        self.write(b'',meta);r=self.run_census();self.assertEqual(r['status'],'UNSUPPORTED')

    def test_repair_n3_task_review_hash_required_and_loaded_path_bound(self):
        from unittest.mock import patch
        from llmtool import task_review
        pin=self.pin_document();pin['code_hashes'].pop('task_review',None)
        with patch.object(c,'census') as read:
            code,out=self.entry(self.argv(pin));self.assertEqual(code,1);read.assert_not_called()
        pin=self.pin_document()
        with patch.object(task_review,'__file__','/CANARY/task_review.py'),patch.object(c,'census') as read:
            code,out=self.entry(self.argv(pin));self.assertEqual(code,1);read.assert_not_called()
            self.assertIn('RUNTIME_MISMATCH',out.getvalue());self.assertNotIn('CANARY',out.getvalue())
        pin=self.pin_document();pin['code_hashes']['task_review']='0'*64
        with patch.object(c,'census') as read:
            code,out=self.entry(self.argv(pin));self.assertEqual(code,1);read.assert_not_called()

    def test_repair_n3_isolated_flags_and_main_binding_before_read(self):
        import sys
        from unittest.mock import patch
        for key in ('isolated','dont_write_bytecode'):
            with patch.object(c,'census') as read:
                code,out=self.entry(self.argv(self.pin_document()),flag_overrides={key:0})
                self.assertEqual(code,1);self.assertIn('RUNTIME_MISMATCH',out.getvalue());read.assert_not_called()
        for main_file in (None,'/CANARY/wrong.py'):
            with patch.object(c,'census') as read:
                code,out=self.entry(self.argv(self.pin_document()),main_file=main_file)
                self.assertEqual(code,1);self.assertIn('RUNTIME_MISMATCH',out.getvalue());read.assert_not_called()
                self.assertNotIn('CANARY',out.getvalue())

    def test_repair_n3_driver_import_error_fixed_json_no_traceback(self):
        import subprocess
        import sys
        root=Path(c.__file__).absolute().parents[3]
        script='''import builtins,runpy,sys
original=builtins.__import__
def blocked(name,*a,**kw):
    if name=='llmtool.rollout_census':raise ImportError('CANARY_PRIVATE_PATH')
    return original(name,*a,**kw)
builtins.__import__=blocked
runpy.run_path(sys.argv[1],run_name='__main__')
'''
        r=subprocess.run([sys.executable,'-B','-I','-c',script,str(root/c.SOURCE_FILES['driver'])],capture_output=True,timeout=10)
        self.assertEqual(r.returncode,1);self.assertEqual(r.stderr,b'')
        result=json.loads(r.stdout);self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['reasons'],['RUNTIME_MISMATCH'])
        self.assertNotIn(b'CANARY',r.stdout+r.stderr);self.assertNotIn(str(root).encode(),r.stdout+r.stderr)

    def test_repair_n3_driver_rejects_flags_before_dependency_import(self):
        import subprocess
        import sys
        root=Path(c.__file__).absolute().parents[3]
        for flag in ('isolated','dont_write_bytecode'):
            script='''import builtins,runpy,sys
realflags=sys.flags
class Flags:
    def __getattr__(self,key):return 0 if key==sys.argv[2] else getattr(realflags,key)
sys.flags=Flags()
original=builtins.__import__
def blocked(name,*a,**kw):
    if name=='llmtool.rollout_census':
        print('UNEXPECTED_IMPORT_CANARY',file=sys.stderr)
        raise AssertionError('dependency import before flags')
    return original(name,*a,**kw)
builtins.__import__=blocked
runpy.run_path(sys.argv[1],run_name='__main__')
'''
            r=subprocess.run([sys.executable,'-B','-I','-c',script,str(root/c.SOURCE_FILES['driver']),flag],
                capture_output=True,timeout=10)
            self.assertEqual(r.returncode,1);self.assertEqual(r.stderr,b'')
            self.assertEqual(json.loads(r.stdout)['reasons'],['RUNTIME_MISMATCH'])

    def test_repair_fixture_runner_flags_do_not_bypass_production(self):
        import io
        import sys
        from unittest.mock import patch
        actual=sys.flags
        class NonisolatedRunner:
            def __getattr__(self,key):return 0 if key=='isolated' else getattr(actual,key)
        root=Path(c.__file__).absolute().parents[3]
        argv=self.argv(self.pin_document())
        with patch.object(sys,'flags',NonisolatedRunner()):
            output=io.StringIO()
            with patch.object(sys.modules['__main__'],'__file__',str(root/c.SOURCE_FILES['driver']),create=True), \
                 patch.object(c,'census') as read:
                code=c._entry(argv,_root=root,_stdout=output,_runtime=self._runtime())
                self.assertEqual(code,1);self.assertIn('RUNTIME_MISMATCH',output.getvalue());read.assert_not_called()
            # A unit fixture models the isolated Host; it is not production.
            code,output=self.entry(argv)
            self.assertEqual(code,0,output.getvalue())
