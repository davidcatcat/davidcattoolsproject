"""Native Delta source tests: synthetic protocol/owned I/O only."""
import hashlib
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from llmtool import codex_pickup_native as n


def raw(x):return json.dumps(x,ensure_ascii=True,separators=(',',':')).encode()


class CodecTests(unittest.TestCase):
    def codec(self):
        c=n.QueueCodec('reviewer','client-1','a'*64);c.add('synthetic packet DATA')
        c.accept(raw({'queuedSubmission':{'id':'submission-1','input':c.input,'clientUserMessageId':'client-1'}}));return c
    def test_exact_id_mapping_no_uuid_substitution(self):
        c=self.codec();x={'threadId':'reviewer','turnId':'turn-1','item':{'type':'userMessage','id':'item-1','clientId':'client-1','content':c.input}}
        self.assertEqual(c.delivery(raw(x)),('submission-1','client-1','item-1','turn-1'))
        self.assertEqual(c.delete(),('thread/queue/delete',dict(threadId='reviewer',queuedSubmissionId='submission-1')))
        self.assertEqual(c.interrupt(),('turn/interrupt',dict(threadId='reviewer',turnId='turn-1')))
        with self.assertRaises(Exception):c.delivery(raw(x))
        with self.assertRaises(Exception):c.add('retry')
    def test_wrong_client_thread_content_and_missing_ids(self):
        for key,value in [('clientId','submission-1'),('id',None),('content',[])]:
            c=self.codec();item={'type':'userMessage','id':'item-1','clientId':'client-1','content':c.input};item[key]=value
            with self.subTest(key=key),self.assertRaises(Exception):c.delivery(raw({'threadId':'reviewer','turnId':'turn-1','item':item}))
        with self.assertRaises(Exception):self.codec().delivery(raw({'threadId':'peer','turnId':'turn-1','item':{}}))
    def test_pagination_empty_only_after_complete_and_limits(self):
        c=self.codec();self.assertFalse(c.list_page(raw({'data':[],'nextCursor':'next'})))
        self.assertTrue(c.list_page(raw({'data':[],'nextCursor':None})))
        c=self.codec()
        for i in range(8):c.list_page(raw({'data':[],'nextCursor':'cursor-'+str(i)}))
        with self.assertRaises(Exception):c.list_request()
        c=self.codec();c.list_page(raw({'data':[],'nextCursor':'same'}))
        with self.assertRaises(Exception):c.list_page(raw({'data':[],'nextCursor':'same'}))
    def test_n2_all_echoes_and_ascii_boundaries(self):
        self.assertEqual(n.wire(b'"'+b'a'*(n.LIMIT-2)+b'"'),'a'*(n.LIMIT-2))
        with self.assertRaises(Exception):n.wire(b'"'+b'a'*(n.LIMIT-1)+b'"')
        for text in ('a'*65536,'\U0001f600'*6000,'\u4e2d'*12000):
            c=n.QueueCodec('reviewer','client-1','a'*64)
            with self.subTest(text=text[:1]),self.assertRaises(Exception):c.add(text)
            self.assertFalse(c.added)
        c=self.codec();echo={'id':'submission-1','input':c.input,'clientUserMessageId':'client-1'}
        self.assertFalse(c.list_page(raw({'data':[echo],'nextCursor':None})))
        for value in ({'data':[{'id':'x','input':['x'*65536],'clientUserMessageId':'c'}],'nextCursor':None},
                      {'threadId':'reviewer','turnId':'turn-1','item':{'type':'userMessage','id':'item-1','clientId':'client-1','content':['x'*65536]}}):
            with self.assertRaises(Exception):n.wire(raw(value))
        with self.assertRaises(Exception):n.wire(b'{"data":')
        with self.assertRaises(Exception):n.wire(b'{"x":1,"x":2}')
    def test_input_echo_must_match_not_only_client(self):
        c=n.QueueCodec('r','c','a'*64);c.add('DATA')
        with self.assertRaises(Exception):c.accept(raw({'queuedSubmission':{'id':'s','input':[],'clientUserMessageId':'c'}}))


class CoverageTests(unittest.TestCase):
    def start(self):
        x=n.EventCoverage('reviewer','b'*64);x.feed(sequence=1,source_digest='b'*64,thread_id='reviewer',turn_id='turn-1',kind='started');return x
    def test_q1_every_release_and_unknown_item(self):
        self.assertEqual(len(n.RELEASE_ITEMS),16);self.assertEqual(len(n.NON_TOOL),4)
        for kind in n.RELEASE_ITEMS|{'FutureTool'}:
            with self.subTest(kind=kind):
                x=self.start();call=lambda:x.feed(sequence=2,source_digest='b'*64,thread_id='reviewer',turn_id='turn-1',kind='item',item_type=kind,item_id='item-1')
                if kind in n.NON_TOOL:call()
                else:
                    with self.assertRaises(Exception):call()
                    with self.assertRaises(Exception):x.seal(drained=True,child_exited=True,source_digest='b'*64)
    def test_n3_terminal_status_and_coverage_exit_matrix(self):
        for status in ('Completed','Interrupted','Failed','InProgress'):
            for exited in (True,False):
                for drained in (True,False):
                    with self.subTest(status=status,exited=exited,drained=drained):
                        x=self.start()
                        if status=='InProgress':
                            with self.assertRaises(Exception):x.feed(sequence=2,source_digest='b'*64,thread_id='reviewer',turn_id='turn-1',kind='terminal',status=status)
                        else:x.feed(sequence=2,source_digest='b'*64,thread_id='reviewer',turn_id='turn-1',kind='terminal',status=status)
                        if status!='InProgress' and exited and drained:self.assertEqual(len(x.seal(drained=drained,child_exited=exited,source_digest='b'*64)),64)
                        else:
                            with self.assertRaises(Exception):x.seal(drained=drained,child_exited=exited,source_digest='b'*64)
    def test_gap_wrong_source_other_thread_and_late_tool(self):
        for fields in ({'sequence':3},{'source_digest':'c'*64},{'thread_id':'child-thread'},{'turn_id':'foreign-turn'}):
            x=self.start();args=dict(sequence=2,source_digest='b'*64,thread_id='reviewer',turn_id='turn-1',kind='item',item_type='Plan');args.update(fields)
            with self.subTest(fields=fields),self.assertRaises(Exception):x.feed(**args)
        x=self.start();x.feed(sequence=2,source_digest='b'*64,thread_id='reviewer',turn_id='turn-1',kind='terminal',status='Completed')
        with self.assertRaises(Exception):x.feed(sequence=3,source_digest='b'*64,thread_id='reviewer',turn_id='turn-1',kind='item',item_type='SubAgentActivity')
    def test_original_wire_normalization_and_turn_client_mapping(self):
        c=CodecTests().codec();x=n.EventCoverage('reviewer','b'*64)
        n.feed_notification(x,c,raw({'method':'turn/started','params':{'threadId':'reviewer','turn':{'id':'turn-1','status':'inProgress'}}}),sequence=1,source_digest='b'*64)
        n.feed_notification(x,c,raw({'method':'item/started','params':{'threadId':'reviewer','turnId':'turn-1','item':{'type':'userMessage','id':'item-1','clientId':'client-1','content':c.input}}}),sequence=2,source_digest='b'*64)
        n.feed_notification(x,c,raw({'method':'item/completed','params':{'threadId':'reviewer','turnId':'turn-1','item':{'type':'userMessage','id':'item-1','clientId':'client-1','content':c.input}}}),sequence=3,source_digest='b'*64)
        n.feed_notification(x,c,raw({'method':'turn/completed','params':{'threadId':'reviewer','turn':{'id':'turn-1','status':'completed'}}}),sequence=4,source_digest='b'*64)
        self.assertEqual(len(x.seal(drained=True,child_exited=True,source_digest='b'*64)),64)
        for method in ('thread/queue/changed','thread/started','item/unknown'):
            with self.assertRaises(Exception):n.feed_notification(x,c,raw({'method':method,'params':{'threadId':'reviewer'}}),sequence=4,source_digest='b'*64)


class DomainTests(unittest.TestCase):
    def samples(self):return tuple(n.DomainSample(d,'trusted-host','owner','run-1','g-1',0,('dispatcher',)) for d in n.DOMAINS)
    def test_reviewer_author_inference_separate_from_worker_zero(self):
        a=self.samples();b=(a[0],replace(a[1],counter=1),replace(a[2],counter=1));self.assertEqual(n.validate_domains(a,b)[n.DOMAINS[0]],0)
        for i,changes in ((0,{'counter':1}),(0,{'generation':'g-2'}),(1,{'counter':2}),(2,{'source':'arbitrary'})):
            b=list(a);b[i]=replace(b[i],**changes)
            with self.assertRaises(Exception):n.validate_domains(a,tuple(b))
        with self.assertRaises(Exception):n.validate_domains(a,())
    def test_missing_coverage_and_reset_are_not_zero(self):
        with self.assertRaises(Exception):replace(self.samples()[0],covered_entrypoints=())
        a=self.samples();b=(replace(a[0],counter=1),a[1],a[2])
        with self.assertRaises(Exception):n.validate_domains(b,a)


class OwnedIOTests(unittest.TestCase):
    def test_owned_child_real_exit_and_no_provider_or_native_cli(self):
        out=n._fixture_owned_client('import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())',b'synthetic')
        self.assertEqual(out['stdout'],b'synthetic')
    def test_eof_is_not_exit_and_blocked_stdout_timeout(self):
        for script in ('import os,time; os.close(1);os.close(2);time.sleep(10)',
                       'import time;time.sleep(10)'):
            start=time.monotonic()
            with self.assertRaises(Exception):n._fixture_owned_client(script,timeout=1)
            self.assertLess(time.monotonic()-start,2)
    def test_output_overflow_and_failed_child(self):
        for script in ('import sys;sys.stdout.write("x"*65537)','raise SystemExit(1)'):
            with self.assertRaises(Exception):n._fixture_owned_client(script)
    def test_fixture_socket_bounded_exact_rpc_and_method_denial(self):
        with tempfile.TemporaryDirectory(prefix='llmtool-pickup-fixture-') as tmp:
            root=Path(tmp).resolve();root.chmod(0o700);path=root/'rpc.sock'
            server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);server.bind(str(path));path.chmod(0o600);server.listen(1);server.settimeout(3)
            errors=[]
            def respond():
                try:
                    with server.accept()[0] as conn:
                        data=bytearray()
                        while b'\n' not in data:data.extend(conn.recv(8192))
                        x=json.loads(data);conn.sendall(raw({'jsonrpc':'2.0','id':x['id'],'result':{'data':[],'nextCursor':None}})+b'\n')
                except Exception as e:errors.append(type(e).__name__)
            thread=threading.Thread(target=respond);thread.start()
            try:
                rpc=n.FixtureSocketRPC(path,root)
                with self.assertRaises(Exception):rpc.request('thread/start',{})
                self.assertEqual(rpc.request('thread/queue/list',{'threadId':'reviewer'}),{'data':[],'nextCursor':None})
            finally:thread.join(timeout=4);server.close()
            self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
    def test_public_entry_zero_callbacks(self):
        with patch('socket.socket',side_effect=AssertionError('native socket')),patch('subprocess.Popen',side_effect=AssertionError('native child')):
            self.assertIsNone(n.native_services_factory());self.assertEqual(n.run_native_pickup()['code'],'NATIVE_PICKUP_NOT_AUTHORIZED')


class ReceiptTests(unittest.TestCase):
    def test_private_record_requires_pinned_owner_and_exact_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();root.chmod(0o700);p=root/'receipt.json'
            pin=n.ReceiptWaiterPin('author','task-1','run-1','a'*40,'notification-1','owned-tool','child-gen',str(p),200)
            value=dict(session=pin.session,task_id=pin.task_id,run_id=pin.run_id,source_sha=pin.source_sha,
                       notification_id=pin.notification_id,owner_tool_id=pin.owner_tool_id,child_generation=pin.child_generation,verdict_digest='b'*64)
            p.write_bytes(raw(value));p.chmod(0o600)
            self.assertEqual(n.read_receipt(pin,owner_digest=pin.digest,verdict_digest='b'*64,now=100),hashlib.sha256(p.read_bytes()).hexdigest())
            for changes in ({'now':200},{'owner_digest':'c'*64},{'verdict_digest':'c'*64}):
                kwargs=dict(owner_digest=pin.digest,verdict_digest='b'*64,now=100);kwargs.update(changes)
                with self.assertRaises(Exception):n.read_receipt(pin,**kwargs)
            p.chmod(0o644)
            with self.assertRaises(Exception):n.read_receipt(pin,owner_digest=pin.digest,verdict_digest='b'*64,now=100)
    def test_host_record_is_static_data_not_session_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();root.chmod(0o700);path=root/'host.json'
            value=dict(session='reviewer',root=str(root),branch='main',checkout_sha='a'*40,runtime_digest='b'*64,generation=1)
            path.write_bytes(raw(value));path.chmod(0o600)
            pin=n.HostRecordPin(str(root),str(path),hashlib.sha256(path.read_bytes()).hexdigest(),'reviewer','main','a'*40,'b'*64,1)
            self.assertEqual(n.collect_host_record(pin)["record_digest"],pin.metadata_digest)
            path.write_bytes(raw({**value,'generation':2}))
            with self.assertRaises(Exception):n.collect_host_record(pin)


class AdvisoryCoverageRegressions(unittest.TestCase):
    def test_rejected_original_notification_poison_is_sticky(self):
        c=CodecTests().codec();x=CoverageTests().start()
        x.feed(sequence=2,source_digest='b'*64,thread_id='reviewer',turn_id='turn-1',kind='terminal',status='Completed')
        with self.assertRaises(Exception):n.feed_notification(x,c,raw({'method':'unknown/new','params':{'threadId':'reviewer'}}),sequence=3,source_digest='b'*64)
        with self.assertRaises(Exception):x.seal(drained=True,child_exited=True,source_digest='b'*64)
    def test_every_allowed_raw_item_requires_original_id(self):
        for kind in n.NON_TOOL-{'UserMessage'}:
            c=CodecTests().codec();x=CoverageTests().start()
            value={'method':'item/started','params':{'threadId':'reviewer','turnId':'turn-1','item':{'type':kind[0].lower()+kind[1:]}}}
            with self.subTest(kind=kind),self.assertRaises(Exception):n.feed_notification(x,c,raw(value),sequence=2,source_digest='b'*64)


class ProtocolJourneyTests(unittest.TestCase):
    def test_complete_raw_protocol_into_existing_handoff_and_receipt_ledger(self):
        from test_codex_handoff_transport import DeltaTrial,RPC
        from llmtool import codex_handoff_transport as c,task_review as r
        class ProtocolRPC(RPC):
            def __init__(self,t):
                super().__init__(t);self.codec=n.QueueCodec(t.binding.reviewer_session,'client-1',t.binding.packet_digest)
                self.coverage=n.EventCoverage(t.binding.reviewer_session,t.session.event_source_digest)
            def queue(self,request):
                t=self.t;self.calls.append('queue');self.queued=True
                row=t.ledger.notification_record(t.binding.object_key)
                assert row['status']=='CONSUMED' and row['pending']==1
                method,params=self.codec.add('DATA\n'+r.canonical(t.j.packet.to_dict()).decode())
                assert method=='thread/queue/add'
                self.codec.accept(raw({'queuedSubmission':{'id':'message-1','input':params['input'],'clientUserMessageId':'client-1'}}))
                original=[{'method':'turn/started','params':{'threadId':self.codec.thread,'turn':{'id':'turn-1','status':'inProgress'}}},
                          {'method':'item/started','params':{'threadId':self.codec.thread,'turnId':'turn-1','item':{'type':'userMessage','id':'original-item-1','clientId':'client-1','content':self.codec.input}}},
                          {'method':'item/completed','params':{'threadId':self.codec.thread,'turnId':'turn-1','item':{'type':'userMessage','id':'original-item-1','clientId':'client-1','content':self.codec.input}}},
                          {'method':'turn/completed','params':{'threadId':self.codec.thread,'turn':{'id':'turn-1','status':'completed'}}}]
                for i,event in enumerate(original,1):n.feed_notification(self.coverage,self.codec,raw(event),sequence=i,source_digest=t.session.event_source_digest)
                self.proof=self.coverage.seal(drained=True,child_exited=True,source_digest=t.session.event_source_digest)
                t.event('DELIVERY');t.event('TURN_STARTED',self.codec.turn);t.event('TURN_COMPLETED',self.codec.turn)
                return c.QueueAck(self.codec.submission,t.binding.reviewer_session,t.binding.packet_digest,t.session.server_digest,True,True)
        t=DeltaTrial();self.addCleanup(t.close);t.rpc=ProtocolRPC(t)
        result=t.run();self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(set(result['dimensions'].values()),{'PASS'})
        self.assertEqual(t.rpc.calls.count('queue'),1);self.assertEqual(t.rpc.calls.count('author_receipt'),1)
        self.assertEqual((t.rpc.codec.submission,t.rpc.codec.client,t.rpc.codec.item,t.rpc.codec.turn),('message-1','client-1','original-item-1','turn-1'))
        self.assertFalse(result['authorized']);self.assertEqual(result['runtime_evidence_status'],'NOT_VERIFIED')
    def test_nonadjacent_cursor_replay_and_multiple_echo_cap(self):
        c=CodecTests().codec();c.list_page(raw({'data':[],'nextCursor':'a'}));c.list_page(raw({'data':[],'nextCursor':'b'}))
        with self.assertRaises(Exception):c.list_page(raw({'data':[],'nextCursor':'a'}))
        c=CodecTests().codec()
        echoes=[{'id':'s'+str(i),'clientUserMessageId':'c'+str(i),'input':[{'type':'text','text':'a'*33000}]} for i in range(2)]
        with self.assertRaises(Exception):c.list_page(raw({'data':echoes,'nextCursor':None}))
    def test_started_item_without_complete_or_mismatched_id_cannot_seal(self):
        c=CodecTests().codec();x=CoverageTests().start()
        n.feed_notification(x,c,raw({'method':'item/started','params':{'threadId':'reviewer','turnId':'turn-1','item':{'type':'agentMessage','id':'item-1','text':'DATA'}}}),sequence=2,source_digest='b'*64)
        x.feed(sequence=3,source_digest='b'*64,thread_id='reviewer',turn_id='turn-1',kind='terminal',status='Completed')
        with self.assertRaises(Exception):x.seal(drained=True,child_exited=True,source_digest='b'*64)
        with self.assertRaises(Exception):n.feed_notification(x,c,raw({'method':'item/completed','params':{'threadId':'reviewer','turnId':'turn-1','item':{'type':'agentMessage','id':'other-item','text':'DATA'}}}),sequence=4,source_digest='b'*64)
