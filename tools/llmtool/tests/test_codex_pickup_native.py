"""Native Delta source tests: synthetic protocol/owned I/O only."""
import hashlib
import json
import os
import socket
import sys
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
    def test_rollout_mode_requires_real_completed_only_and_distinct_seal(self):
        c=n.EventCoverage('reviewer','a'*64,mode='HOST_ROLLOUT')
        c.feed(sequence=1,source_digest='a'*64,thread_id='reviewer',turn_id='turn',kind='started')
        c.feed(sequence=2,source_digest='a'*64,thread_id='reviewer',turn_id='turn',kind='item',
               item_type='AgentMessage',item_id='final',item_phase='completed_only')
        c.feed(sequence=3,source_digest='a'*64,thread_id='reviewer',turn_id='turn',kind='terminal',status='Completed')
        self.assertNotEqual(c.seal(drained=True,child_exited=True,source_digest='a'*64),
                            n.digest((c.source,c.events),'llmtool.pickup-coverage.v1'))
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


class CompositionSourceRedTests(unittest.TestCase):
    def test_queue_snapshot_can_restart_without_replay_reset_of_effects(self):
        c=CodecTests().codec()
        row={'id':'s','input':[],'clientUserMessageId':'c'}
        c.list_page(raw({'data':[row],'nextCursor':None}))
        c.new_snapshot()
        self.assertFalse(c.list_page(raw({'data':[row],'nextCursor':None})))
        with self.assertRaises(Exception):c.accept(raw({'queuedSubmission':{'id':'other','input':c.input,'clientUserMessageId':'client-1'}}))


class CompositionTrial:
    """Owned socketpair server setup occurs outside the composition window."""
    def __init__(self, *, mode='complete', stream_fault=None, rollout=False):
        from test_codex_handoff_transport import DeltaTrial
        from llmtool import codex_handoff_transport as c,task_review as r,authorization_policy as p
        self.t=DeltaTrial();t=self.t
        old_utc=t.j.clock.utc;t.j.clock.utc=lambda:int(old_utc())
        self.inferences=0;self.author_calls=0;self.errors=[];self.requests=[]
        self.channel,self.server=n.SocketPairChannel.fixture_pair();self.server.settimeout(3)
        self.rollout_fixture=None
        if rollout:
            self.rollout_fixture=RolloutFixture(thread=t.binding.reviewer_session,session=t.session.session_id)
            t.session=replace(t.session,event_source='HOST_ROLLOUT',event_source_digest=self.rollout_fixture.pin.digest)
            t.grant=replace(t.grant,session=t.session)
        else:
            # This fixture emits app-server notifications, not rollout JSONL.
            t.session=replace(t.session,event_source='APP_SERVER_EVENTS')
            t.grant=replace(t.grant,session=t.session)
        t.binding=replace(t.binding,host_pin=r.digest((t.j.host.observe().pin,t.session.digest)))
        original_domains=t.host.domains
        def domains():
            samples=original_domains()
            return samples[0],replace(samples[1],counter=self.inferences),replace(samples[2],counter=self.author_calls)
        t.host.domains=domains
        d=domains();pins=tuple(n.DomainPin(x.domain,x.source,x.owner,x.run_id,x.generation,x.covered_entrypoints) for x in d)
        waiter=n.ReceiptWaiterPin(t.binding.author_session,t.binding.task.task_id,t.binding.task.run_id,
            t.binding.source_sha,t.binding.event_id,'owned-author-tool','author-gen',str(Path(t.tmp.name)/'ack.json'),t.binding.expires_at)
        self.pins=n.CompositionPins(t.session.digest,t.binding.host_pin,t.binding.packet_digest,t.binding.route_digest,
            '1'*64,'2'*64,'3'*64,t.binding.risk_acceptance_digest,t.binding.coverage_digest,t.session.event_source_digest,
            t.binding.reviewer_session,waiter,pins,True)
        if rollout:self.pins=replace(self.pins,rollout=self.rollout_fixture.pin)
        t.binding=replace(t.binding,topology='IN_PROCESS_ZERO_CHILD',composition_digest=self.pins.digest)
        t.grant=replace(t.grant,binding=t.binding,poll_seconds=1);t.issuer.binding=t.binding
        t.host.attest=lambda:n.CompositionAttestation(self.pins.digest,t.session.digest,t.binding.host_pin,
            waiter.digest,t.binding.route_digest,t.binding.not_before,t.binding.expires_at)
        self.ack=None;self.notify_count=0;self.poll_count=0;self.waiter_change=lambda x:x;self.receipt_missing=False
        trial=self
        class Waiter:
            def notify(self,pin,message,turn,result):
                trial.notify_count+=1;trial.author_calls=1
                trial.ack=n.AuthorAck(pin.digest,pin.session,pin.task_id,pin.run_id,pin.source_sha,pin.notification_id,
                    pin.owner_tool_id,pin.child_generation,message,turn,result,t.j.clock.utc())
            def poll(self):
                trial.poll_count+=1
                return None if trial.receipt_missing else trial.waiter_change(trial.ack)
        t.rpc.mode='blocked' if mode=='blocked' else 'complete'
        if mode=='changes':t.rpc.verdict_mutate=lambda v:{**v,'functional':'CHANGES_REQUIRED'}
        verdict=t.rpc.verdict('turn-1')
        self.comp=n.ComposedPickup(self.pins,t.host,Waiter(),self.channel,t.j.clock,t.rpc.read())
        def serve():
            try:
                data=bytearray()
                while True:
                    while b'\n' not in data:
                        chunk=self.server.recv(8192)
                        if not chunk:return
                        data.extend(chunk)
                    line,_,rest=data.partition(b'\n');data=bytearray(rest);x=json.loads(line)
                    method=x['method'];self.requests.append(method)
                    if method=='initialized':continue
                    reply={'jsonrpc':'2.0','id':x['id'],'result':{}}
                    if method=='thread/queue/list':reply['result']={'data':[],'nextCursor':None}
                    if method=='thread/queue/add':
                        self.inferences=1
                        params=x['params'];reply['result']={'queuedSubmission':{'id':'submission-1','input':params['input'],'clientUserMessageId':params['clientUserMessageId']}}
                        if stream_fault=='wrong-id':reply['id']+=1
                        if stream_fault=='malformed':self.server.sendall(b'{broken}\n');return
                        if stream_fault=='partial':self.server.sendall(b'{"id":');return
                        if stream_fault=='disconnect':return
                        if rollout:
                            f=self.rollout_fixture
                            f.append(f.journey(params['input'],params['clientUserMessageId'],verdict,mode))
                    self.server.sendall(raw(reply)+b'\n')
                    if method=='thread/queue/add':
                        if rollout:continue  # Server stays alive; local drain must not await EOF.
                        thread=self.pins.thread_id
                        item={'type':'userMessage','id':'user-item','clientId':params['clientUserMessageId'],'content':params['input']}
                        agent={'type':'agentMessage','id':'verdict-item','text':verdict}
                        terminal='failed' if mode=='failed' else 'interrupted' if mode=='interrupted' else 'completed'
                        values=[('turn/started',{'threadId':thread,'turn':{'id':'turn-1','status':'inProgress'}}),
                                ('item/started',{'threadId':thread,'turnId':'turn-1','item':item}),
                                ('item/completed',{'threadId':thread,'turnId':'turn-1','item':item}),
                                ('item/started',{'threadId':thread,'turnId':'turn-1','item':agent}),
                                ('item/completed',{'threadId':thread,'turnId':'turn-1','item':agent}),
                                ('turn/completed',{'threadId':thread,'turn':{'id':'turn-1','status':terminal}})]
                        if stream_fault=='tool':values[3][1]['item']={'type':'commandExecution','id':'bad-tool'}
                        if stream_fault=='flood':values=values[:1]*129
                        if stream_fault=='duplicate-response':self.server.sendall(raw(reply)+b'\n')
                        if stream_fault=='missing-terminal':values=values[:-1]
                        if stream_fault=='other-thread':values[1][1]['threadId']='child-thread'
                        for method,params in values:
                            packet=raw({'jsonrpc':'2.0','method':method,'params':params})+b'\n'
                            # Fragmented byte delivery exercises the actual owned stream.
                            self.server.sendall(packet[:13]);self.server.sendall(packet[13:])
                        return
            except (OSError,ValueError) as exc:self.errors.append(type(exc).__name__)
            finally:self.server.close()
        self.thread=threading.Thread(target=serve);self.thread.start()
    def run(self):
        from llmtool import codex_handoff_transport as c,authorization_policy as p,task_review as r
        t=self.t;b=t.binding
        obs=p.NotificationObserved(b.digest,b.host_pin,True,True,True,True,False,True,True,True,b.risk_acceptance_digest,b.topology,b.composition_digest)
        services=c._fixture_services(t.grant,self.comp,self.comp,t.issuer,t.ledger,t.j.clock,
            r.canonical(t.j.review_fixture.data),admission=p.decide_notification(b,issuer=t.issuer,observed=obs),composition=self.comp)
        return c._run_fixture_composition(t.j.ready,t.j.final,t.j.expect,services)
    def close(self):
        self.channel.close();self.server.close();self.thread.join(timeout=4)
        assert not self.thread.is_alive()
        self.t.close()
        if self.rollout_fixture:self.rollout_fixture.close()


class CompositionAcceptanceTests(unittest.TestCase):
    def trial(self,**kwargs):
        t=CompositionTrial(**kwargs);self.addCleanup(t.close);return t
    def test_full_zero_child_stream_coverage_ledger_and_receipt(self):
        t=self.trial()
        with patch('subprocess.Popen',side_effect=AssertionError('spawn forbidden')) as spawn:
            result=t.run()
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(set(result['dimensions'].values()),{'PASS'})
        self.assertEqual(t.requests.count('initialize'),1);self.assertEqual(t.requests.count('initialized'),1)
        self.assertGreater(t.requests.count('thread/queue/list'),1)
        self.assertEqual(t.requests.count('thread/queue/add'),1);self.assertEqual(t.notify_count,1)
        self.assertEqual(spawn.call_count,0)
        proof=result['composition_evidence'];self.assertEqual(proof['spawn_count'],0)
        self.assertTrue(proof['stream_drained']);self.assertTrue(proof['stream_closed'])
        self.assertEqual(proof['terminal_coverage_digest'],t.comp.coverage.seal(drained=True,child_exited=True,source_digest=t.pins.event_source_digest))
        self.assertFalse(result['authorized']);self.assertEqual(result['runtime_evidence_status'],'NOT_VERIFIED')
        self.assertFalse(t.t.ledger.notification_pending(t.t.binding.reviewer_session))
    def test_all_strict_verdicts_and_terminal_states(self):
        for mode in ('blocked','changes','failed','interrupted'):
            with self.subTest(mode=mode):
                t=self.trial(mode=mode);result=t.run()
                self.assertEqual(result['status'],'CHANGES_REQUIRED' if mode=='changes' else 'BLOCKED',result)
                if mode=='blocked':
                    self.assertEqual(result['dimensions']['REVIEW_COMPLETED'],'NOT_VERIFIED')
                    self.assertEqual(result['dimensions']['AUTHOR_RECEIPTED'],'PASS')
                if mode in ('failed','interrupted'):self.assertEqual(t.notify_count,0)
                self.assertFalse(t.t.ledger.notification_pending(t.t.binding.reviewer_session))
    def test_k3_each_transport_fault_remains_sticky(self):
        for fault in ('wrong-id','malformed','partial','disconnect','flood','duplicate-response','missing-terminal','tool','other-thread'):
            with self.subTest(fault=fault):
                t=self.trial(stream_fault=fault);result=t.run()
                self.assertEqual(result['status'],'BLOCKED',result)
                self.assertTrue(t.comp.coverage.failed)
                self.assertTrue(t.t.ledger.notification_pending(t.t.binding.reviewer_session))
                with self.assertRaises(Exception):t.comp.coverage.seal(drained=True,child_exited=True,source_digest=t.pins.event_source_digest)
                with self.assertRaises(Exception):t.comp.coverage.feed(sequence=t.comp.coverage.last+1,source_digest=t.pins.event_source_digest,
                    thread_id=t.pins.thread_id,turn_id='turn-1',kind='terminal',status='Completed')
    def test_missing_or_wrong_author_receipt_is_partial_not_closed_loop(self):
        for wrong in (False,True):
            t=self.trial();t.t.grant=replace(t.t.grant,receipt_seconds=2)
            if wrong:t.waiter_change=lambda ack:replace(ack,session='wrong-author')
            else:t.receipt_missing=True
            result=t.run();self.assertEqual(result['status'],'PARTIAL',result)
            self.assertNotEqual(result['dimensions']['AUTHOR_RECEIPTED'],'PASS')
            self.assertEqual(t.notify_count,1)
    def test_external_counter_pins_and_attestation_cannot_be_self_report(self):
        for change in ('source','coverage','owner','revoked','topology'):
            with self.subTest(change=change):
                t=self.trial();old=t.t.host.domains
                if change in ('source','coverage','owner'):
                    field='covered_entrypoints' if change=='coverage' else change
                    def altered():
                        samples=list(old());samples[1]=replace(samples[1],**{field:('uncovered',) if change=='coverage' else 'fake-source'});return tuple(samples)
                    t.t.host.domains=altered
                else:
                    old_a=t.t.host.attest;t.t.host.attest=lambda:replace(old_a(),**({'revoked':True} if change=='revoked' else {'topology':'OWNED_CHILD'}))
                result=t.run();self.assertEqual(result['status'],'BLOCKED',result)
                self.assertNotIn('thread/queue/add',t.requests)
    def test_attempted_spawn_poison_even_if_callback_swallows_exception(self):
        t=self.trial();old=t.t.host.attest
        def spawn():
            try:os.fork()
            except Exception:pass
            return old()
        t.t.host.attest=spawn;result=t.run()
        self.assertEqual(result['status'],'BLOCKED',result)
        self.assertTrue(t.comp.coverage.failed)
        self.assertEqual(result['composition_evidence']['spawn_count'],0)
        self.assertGreater(result['composition_evidence']['spawn_attempts'],0)
        self.assertNotIn('thread/queue/add',t.requests)
    def test_close_failure_never_persists_completion(self):
        t=self.trial();t.channel.close=lambda:False
        result=t.run();self.assertEqual(result['status'],'BLOCKED',result)
        self.assertNotEqual(result['dimensions']['REVIEW_COMPLETED'],'PASS')
        self.assertTrue(t.t.ledger.notification_pending(t.t.binding.reviewer_session))
        # Restore ownership cleanup for the test only.
        t.channel.close=lambda:(t.channel.socket.close() or True)


class CompositionBoundaryTests(unittest.TestCase):
    def test_transport_timeout_is_fixed_and_sticky(self):
        channel,server=n.SocketPairChannel.fixture_pair();self.addCleanup(channel.close);self.addCleanup(server.close)
        coverage=n.EventCoverage('thread','a'*64);stream=n.PersistentRPC(channel,coverage,time.monotonic,time.monotonic()+10)
        with patch.object(channel,'read',side_effect=TimeoutError):
            with self.assertRaises(Exception) as caught:stream.initialize()
        self.assertEqual(caught.exception.review_code,'PICKUP_READ_TIMEOUT')
        self.assertTrue(coverage.failed)
        with self.assertRaises(Exception):stream.initialize()
        with self.assertRaises(Exception):coverage.feed(sequence=1,source_digest='a'*64,thread_id='thread',turn_id='turn',kind='started')
    def test_zero_child_guard_catches_each_api_and_restores_originals(self):
        APIs=[(os,'fork'),(os,'posix_spawn'),(os,'posix_spawnp')]
        import subprocess
        APIs.append((subprocess,'Popen'))
        for module,name in APIs:
            if not hasattr(module,name):continue
            original=getattr(module,name);coverage=n.EventCoverage('thread','a'*64)
            with n.ZeroChildWindow(coverage) as window:
                with self.assertRaises(Exception):getattr(module,name)('forbidden')
                self.assertEqual(window.spawn_count,0);self.assertEqual(window.attempts,1)
                self.assertTrue(coverage.failed)
            self.assertIs(getattr(module,name),original)
    def test_attested_spawn_and_topology_poison_permanently(self):
        t=CompositionTrial();self.addCleanup(t.close);old=t.t.host.attest
        t.t.host.attest=lambda:replace(old(),spawn_count=1)
        result=t.run();self.assertEqual(result['status'],'BLOCKED',result)
        self.assertTrue(t.comp.coverage.failed)
        t.t.host.attest=old
        with self.assertRaises(Exception):t.comp.check()
    def test_untrusted_issuer_or_grant_digest_never_initializes(self):
        for change in ('issuer','binding'):
            t=CompositionTrial();self.addCleanup(t.close)
            if change=='issuer':t.t.issuer.revoked=True
            else:t.t.grant=replace(t.t.grant,binding=replace(t.t.binding,composition_digest='f'*64))
            result=t.run();self.assertEqual(result['status'],'BLOCKED',result)
            self.assertNotIn('initialize',t.requests)
    def test_missing_close_or_coverage_cannot_call_durable_finish(self):
        for fault in ('missing-terminal','tool'):
            t=CompositionTrial(stream_fault=fault);self.addCleanup(t.close)
            with patch.object(t.t.ledger,'finish_notification_turn',wraps=t.t.ledger.finish_notification_turn) as finish:
                result=t.run()
            self.assertEqual(result['status'],'BLOCKED',result);self.assertEqual(finish.call_count,0)
    def test_head_or_session_drift_and_late_callback_fail_before_dispatch(self):
        for change in ('session','head','late'):
            t=CompositionTrial();self.addCleanup(t.close)
            if change=='late':
                old=t.t.host.attest
                def late():t.t.j.clock.wait(6);return old()
                t.t.host.attest=late
            else:
                old=t.t.host.observe
                t.t.host.observe=lambda:replace(old(),session=replace(t.t.session,**({'session_id':'other'} if change=='session' else {'checkout_sha':'f'*40})))
            result=t.run();self.assertEqual(result['status'],'BLOCKED',result)
            self.assertNotIn('thread/queue/add',t.requests)


class AdvisoryCompositionRegressions(unittest.TestCase):
    def trial(self):
        t=CompositionTrial();self.addCleanup(t.close);return t
    def test_other_thread_cannot_be_vouched_as_original_session(self):
        t=self.trial();t.comp.pins=replace(t.pins,thread_id='wrong-thread')
        t.t.binding=replace(t.t.binding,composition_digest=t.comp.pins.digest)
        t.t.grant=replace(t.t.grant,binding=t.t.binding);t.t.issuer.binding=t.t.binding
        old=t.t.host.attest
        t.t.host.attest=lambda:replace(old(),pins_digest=t.comp.pins.digest)
        result=t.run();self.assertEqual(result['status'],'BLOCKED',result)
        self.assertNotIn('initialize',t.requests)
    def test_queue_uses_only_validated_packet_and_expiry(self):
        from llmtool import task_review as r
        for field in ('review_packet','expires_at','instructions'):
            t=self.trial();old=t.comp.queue
            def injected(request):
                request=dict(request)
                request[field]=(r.ReviewPacket(r.canonical({'wrong':'packet'})) if field=='review_packet' else
                                t.t.binding.expires_at+1 if field=='expires_at' else 'unapproved instructions')
                return old(request)
            t.comp.queue=injected
            result=t.run();self.assertEqual(result['status'],'BLOCKED',result)
            self.assertNotIn('thread/queue/add',t.requests)
    def test_rejected_admission_never_calls_host_or_issuer(self):
        from llmtool import codex_handoff_transport as c,task_review as r
        t=self.trial()
        services=c._fixture_services(t.t.grant,t.comp,t.comp,t.t.issuer,t.t.ledger,t.t.j.clock,
            r.canonical(t.t.j.review_fixture.data),admission=None,composition=t.comp)
        with patch.object(t.t.host,'attest',side_effect=AssertionError('rejected callback')) as attest:
            result=c._run_fixture_composition(t.t.j.ready,t.t.j.final,t.t.j.expect,services)
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual(attest.call_count,0)
        self.assertNotIn('initialize',t.requests)
    def test_rejected_component_never_calls_evidence_or_escapes(self):
        from llmtool import codex_handoff_transport as c,task_review as r
        t=self.trial()
        class Invalid:
            def evidence(self):raise AssertionError('rejected callback')
        services=c._fixture_services(t.t.grant,t.comp,t.comp,t.t.issuer,t.t.ledger,t.t.j.clock,
            r.canonical(t.t.j.review_fixture.data),composition=Invalid())
        result=c._run_fixture_pickup(t.t.j.ready,t.t.j.final,t.t.j.expect,services)
        self.assertEqual(result['status'],'BLOCKED')
    def test_original_start_timestamp_is_not_renewed_by_late_delivery(self):
        t=self.trial();comp=t.comp;comp.grant=t.t.grant
        c=comp.codec;c.add('DATA');c.accept(raw({'queuedSubmission':{'id':'s','input':c.input,'clientUserMessageId':c.client}}))
        def feed(method,params):
            comp.stream.notifications=[raw({'method':method,'params':params})];comp.stream.notification_times=[t.t.j.clock.utc()];comp._notifications()
        feed('turn/started',{'threadId':t.pins.thread_id,'turn':{'id':'turn-1','status':'inProgress'}})
        original=t.t.j.clock.utc();t.t.j.clock.wait(100)
        feed('item/started',{'threadId':t.pins.thread_id,'turnId':'turn-1','item':{'type':'userMessage','id':'u','clientId':c.client,'content':c.input}})
        starts=[e for e in comp.events if e.kind=='TURN_STARTED']
        self.assertEqual(starts[0].timestamp,original)
    def test_cleanup_transition_does_not_reuse_normal_deadline(self):
        t=self.trial();result=t.run();self.assertEqual(result['status'],'PASS',result)
        comp=t.comp;t.t.j.clock.wait(int(comp.normal_deadline-t.t.j.clock.monotonic())+1)
        comp.begin_cleanup();comp.stream.check()
        self.assertEqual(comp.stream.deadline,comp.overall_deadline)


class AggregateDeadlineRegressions(unittest.TestCase):
    def test_snapshot_pages_share_single_five_second_operation(self):
        t=CompositionTrial();self.addCleanup(t.close)
        comp=t.comp;comp.grant=t.t.grant;comp.window=n.ZeroChildWindow(comp.coverage)
        comp.issuer=t.t.issuer;comp.normal_deadline=t.t.j.clock.monotonic()+100;comp.overall_deadline=comp.normal_deadline+20;comp.stream.deadline=comp.normal_deadline
        pages=iter([{'data':[],'nextCursor':'next'},{'data':[],'nextCursor':None}])
        def slow(method,params,**kwargs):
            t.t.j.clock.wait(4);return next(pages)
        comp.stream.request=slow
        with self.assertRaises(Exception):comp._snapshot()
        self.assertTrue(comp.coverage.failed)
    def test_delayed_demux_does_not_refresh_original_start_receipt(self):
        t=CompositionTrial();self.addCleanup(t.close);comp=t.comp;comp.grant=t.t.grant
        c=comp.codec;c.add('DATA');c.accept(raw({'queuedSubmission':{'id':'s','input':c.input,'clientUserMessageId':c.client}}))
        original=t.t.j.clock.utc()
        comp.stream._notification({'jsonrpc':'2.0','method':'turn/started','params':{'threadId':t.pins.thread_id,'turn':{'id':'turn-1','status':'inProgress'}}})
        t.t.j.clock.wait(4);comp._notifications()
        self.assertEqual(comp.original_start,original)


class LogicalReadDeadlineTests(unittest.TestCase):
    def test_observe_aggregates_host_and_snapshot_time(self):
        t=CompositionTrial();self.addCleanup(t.close);old=t.t.host.observe
        def slow_host():t.t.j.clock.wait(4);return old()
        def slow_snapshot(operation=None):t.t.j.clock.wait(4);return True
        t.t.host.observe=slow_host;t.comp._snapshot=slow_snapshot
        result=t.run();self.assertEqual(result['status'],'BLOCKED',result)
        self.assertNotIn('thread/queue/add',t.requests)
    def test_close_return_time_cannot_renew_seal_deadline(self):
        t=CompositionTrial();self.addCleanup(t.close);old=t.channel.close
        def late_close():t.t.j.clock.wait(6);return old()
        t.channel.close=late_close
        with patch.object(t.t.ledger,'finish_notification_turn',wraps=t.t.ledger.finish_notification_turn) as finish:
            result=t.run()
        self.assertEqual(result['status'],'BLOCKED',result);self.assertEqual(finish.call_count,0)
        self.assertTrue(t.comp.coverage.failed)


class RolloutFixture:
    """Only synthetic JSONL, never a real Codex HOME/session file."""
    def __init__(self, *, thread='reviewer',session='reviewer',next_ordinal=7):
        self.tmp=tempfile.TemporaryDirectory(prefix='llmtool-rollout-fixture-',dir='/private/tmp' if sys.platform=='darwin' else None)
        self.home=Path(self.tmp.name);os.chmod(self.home,0o700)
        self.path=self.home/'sessions/2026/10/15'/('rollout-2026-10-15T01-02-03-'+thread+'.jsonl')
        self.path.parent.mkdir(parents=True,mode=0o700)
        self.meta=self.record(0,'session_meta',{'id':thread,'session_id':session,'history_mode':'paginated','forked_from_id':None})
        self.path.write_bytes(raw(self.meta)+b'\n');self.path.chmod(0o600);st=self.path.stat()
        self.pin=n.RolloutPin(str(self.home),str(self.path),st.st_uid,st.st_dev,st.st_ino,session,thread,self.path.stat().st_size,next_ordinal)
        self.clock_value=0
    @staticmethod
    def record(ordinal,kind,payload):return dict(timestamp='2026-10-15T01:02:03Z',ordinal=ordinal,type=kind,payload=payload)
    def journey(self,content=None,client='client-1',verdict='{}',mode='complete'):
        thread=self.pin.thread_id
        payloads=[{'type':'task_started','turn_id':'turn-1'},
            {'type':'item_completed','thread_id':thread,'turn_id':'turn-1','item':{
                'type':'UserMessage','id':'user-item','client_id':client,'content':content}},
            {'type':'item_completed','thread_id':thread,'turn_id':'turn-1','item':{
                'type':'AgentMessage','id':'comment','phase':'commentary','text':'CANARY_PRIVATE_COMMENTARY'}},
            {'type':'item_completed','thread_id':thread,'turn_id':'turn-1','item':{
                'type':'AgentMessage','id':'verdict-item','phase':'final_answer','text':verdict}},
            {'type':'task_complete','turn_id':'turn-1'}]
        if mode=='failed':payloads[-1]['error']={'message':'CANARY_PRIVATE_FAILURE'}
        if mode=='interrupted':payloads[-1]={'type':'turn_aborted','turn_id':'turn-1','reason':'interrupted'}
        return [self.record(self.pin.next_ordinal+i,'event_msg',p) for i,p in enumerate(payloads)]
    def append(self,records):
        with self.path.open('ab') as f:
            for x in records:f.write(raw(x)+b'\n')
    def reader(self,**limits):
        pin=replace(self.pin,**limits);coverage=n.EventCoverage(pin.thread_id,pin.digest,mode='HOST_ROLLOUT')
        return n.RolloutWindow(pin,coverage,lambda:self.clock_value,100)
    def codec(self):
        c=n.QueueCodec(self.pin.thread_id,'client-1','a'*64);c.add('CANARY_PRIVATE_INPUT')
        c.accept(raw({'queuedSubmission':{'id':'submission-1','input':c.input,'clientUserMessageId':c.client}}));return c
    def close(self):self.tmp.cleanup()


class RolloutAcceptanceTests(unittest.TestCase):
    def fixture(self,**kwargs):
        f=RolloutFixture(**kwargs);self.addCleanup(f.close);return f
    def poison(self,r,call,code=None):
        with self.assertRaises(Exception) as caught:call()
        if code:self.assertEqual(getattr(caught.exception,'review_code',None),code)
        self.assertTrue(r.coverage.failed)
        with self.assertRaises(Exception):r.coverage.seal(drained=True,child_exited=True,source_digest=r.pin.digest)
        with self.assertRaises(Exception):r.poll()
        self.assertNotIn('CANARY',str(caught.exception));self.assertNotIn(str(r.pin.rollout_path),str(caught.exception))
    def feed(self,r,c):
        texts=[]
        for value in r.poll():texts.append(n.feed_rollout_record(r.coverage,c,value,source_digest=r.pin.digest)[2])
        return texts
    def test_full_writer_journey_nonzero_ordinal_real_drain_close_and_privacy(self):
        f=self.fixture();c=f.codec();f.append(f.journey(c.input));r=f.reader()
        original=f.path.read_bytes()
        with patch('subprocess.Popen',side_effect=AssertionError('no spawn')) as spawn:
            texts=self.feed(r,c);r.poll(final=True)
        self.assertEqual(spawn.call_count,0);self.assertEqual(f.path.read_bytes(),original)
        self.assertEqual([x for x in texts if x is not None],['{}'])
        self.assertEqual(r.next_ordinal,12);self.assertTrue(r.closed and r.drained)
        self.assertEqual((c.submission,c.client,c.item,c.turn),('submission-1','client-1','user-item','turn-1'))
        self.assertEqual(r.coverage.terminal,'Completed')
        self.assertNotIn('started',[x[-1] for x in r.coverage.events if x[3]=='item'])
        self.assertNotIn('CANARY',repr(r.coverage.events)+repr(r.coverage.record_digests))
        self.assertEqual(len(r.coverage.seal(drained=True,child_exited=True,source_digest=r.pin.digest)),64)
    def test_partial_suffix_reread_counts_io_consumed_once(self):
        f=self.fixture();c=f.codec();values=f.journey(c.input);r=f.reader()
        line=raw(values[0])+b'\n'
        with f.path.open('ab') as out:out.write(line[:25])
        self.assertEqual(r.poll(),[]);self.assertTrue(r.partial);self.assertEqual(r.next_ordinal,7)
        before=r.bytes
        with f.path.open('ab') as out:out.write(line[25:])
        self.assertEqual(len(r.poll()),1);self.assertGreater(r.bytes,before+len(line)-25)
        self.assertFalse(r.partial);self.assertEqual(r.next_ordinal,8)
    def test_failed_and_interrupted_are_not_completed_success(self):
        for mode,terminal in [('failed','Failed'),('interrupted','Interrupted')]:
            f=self.fixture();c=f.codec();f.append(f.journey(c.input,mode=mode));r=f.reader();self.feed(r,c);r.poll(final=True)
            self.assertEqual(r.coverage.terminal,terminal)
    def test_metadata_legacy_fork_identity_missing_ordinal_and_oversize(self):
        for field,value in [('id','other'),('session_id','other'),('history_mode','legacy'),('forked_from_id','parent'),('instructions','x'*n.LIMIT)]:
            f=self.fixture();meta=json.loads(json.dumps(f.meta));meta['payload'][field]=value
            f.path.write_bytes(raw(meta)+b'\n');r=f.reader();self.poison(r,r.poll)
        for ordinal in [None,True,-1,2**63]:
            f=self.fixture();meta=dict(f.meta,ordinal=ordinal);f.path.write_bytes(raw(meta)+b'\n');r=f.reader();self.poison(r,r.poll)
    def test_cursor_not_record_boundary_and_file_permission_or_identity_drift(self):
        for mode in ('cursor','truncate','replace','hardlink','permission','symlink','parent'):
            f=self.fixture();r=f.reader()
            if mode=='cursor':r=f.reader(start_offset=f.pin.start_offset-1)
            elif mode=='truncate':f.path.write_bytes(b'')
            elif mode=='replace':f.path.rename(f.path.with_suffix('.old'));f.path.write_bytes(raw(f.meta)+b'\n');f.path.chmod(0o600)
            elif mode=='hardlink':os.link(f.path,f.path.with_suffix('.link'))
            elif mode=='permission':f.path.chmod(0o622)
            elif mode=='symlink':f.path.rename(f.path.with_suffix('.old'));f.path.symlink_to(f.path.with_suffix('.old'))
            elif mode=='parent':f.path.parent.chmod(0o777)
            self.poison(r,r.poll,'PICKUP_EVENT_SOURCE_INVALID')
            if mode=='parent':f.path.parent.chmod(0o700)
    def test_shrink_after_poll_and_wrong_uid_inode(self):
        f=self.fixture();r=f.reader();r.poll();f.path.write_bytes(b'x');self.poison(r,r.poll)
        for field in ('uid','inode','device'):
            f=self.fixture();r=f.reader(**{field:getattr(f.pin,field)+1});self.poison(r,r.poll)
    def test_path_compressed_reverted_and_traversal_are_not_admitted(self):
        f=self.fixture()
        for path in [str(f.path)+'.zst',str(f.path).replace('.jsonl','_reverted.jsonl'),str(f.home/'sessions/../bad.jsonl')]:
            with self.assertRaises(Exception):replace(f.pin,rollout_path=path)
        with self.assertRaises(Exception):replace(f.pin,history_mode='Legacy')
    def test_true_line_boundary_and_partial_limit(self):
        for suffix in (b'\n',b''):
            f=self.fixture();r=f.reader();r.poll()
            f.append([f.record(7,'event_msg',{'type':'task_started','turn_id':'turn-1'})])
            r.poll()
            # Exactly 64KiB LF record succeeds at reader level, then +1 rejects.
            prefix=b'{"timestamp":"t","ordinal":8,"type":"turn_context","payload":{"pad":"';tail=b'"}}\n'
            line=prefix+b'x'*(n.LIMIT-len(prefix)-len(tail))+tail
            with f.path.open('ab') as out:out.write(line if suffix else b'x'*(n.LIMIT-1))
            self.assertEqual(len(r.poll()),1 if suffix else 0)
            with f.path.open('ab') as out:out.write(b'x'*(n.LIMIT+1) if suffix else b'x')
            self.poison(r,r.poll,'PICKUP_EVENT_LIMIT')
    def test_window_record_poll_limits_reached_without_early_replay(self):
        f=self.fixture();r=f.reader();r.poll()
        # Distinct contiguous benign records fill the actual default record cap.
        f.append([f.record(7+i,'turn_context',{}) for i in range(127)])
        self.assertEqual(len(r.poll()),127)
        f.append([f.record(134,'turn_context',{})]);self.poison(r,r.poll,'PICKUP_EVENT_LIMIT')
        f=self.fixture();r=f.reader()
        for _ in range(128):r.poll()
        self.poison(r,r.poll,'PICKUP_EVENT_LIMIT')
        f=self.fixture();r=f.reader();r.poll()
        # Repeated partial reads consume the actual 512KiB I/O budget.
        with f.path.open('ab') as out:out.write(b'x'*60000)
        while r.bytes+60000<=512*1024:r.poll()
        self.poison(r,r.poll,'PICKUP_EVENT_LIMIT')
    def test_invalid_utf8_json_unicode_depth_duplicate_keys_and_nan(self):
        for bad in [b'\xff',b'{bad}',b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":"\\ud800"}',b'{"a":'+b'['*17+b'0'+b']'*17+b'}']:
            f=self.fixture();r=f.reader()
            with f.path.open('ab') as out:out.write(bad+b'\n')
            self.poison(r,r.poll,'PICKUP_COVERAGE_UNVERIFIED')
    def test_gap_duplicate_decrease_bool_missing_and_overflow_ordinals(self):
        for ordinal in [8,6,True,None,2**63]:
            f=self.fixture();r=f.reader();f.append([f.record(ordinal,'turn_context',{})]);self.poison(r,r.poll)
        f=self.fixture();r=f.reader();f.append([f.record(7,'turn_context',{}),f.record(7,'turn_context',{})]);self.poison(r,r.poll)
    def test_deadline_partial_and_late_close_are_sticky(self):
        f=self.fixture();r=f.reader();r.poll();f.clock_value=100;self.poison(r,r.poll,'PICKUP_READ_TIMEOUT')
        f=self.fixture();r=f.reader();close=os.close
        def late(fd):close(fd);f.clock_value=6
        with patch.object(n.os,'close',side_effect=late):self.poison(r,r.poll,'PICKUP_READ_TIMEOUT')
    def test_all_wire_rejections_poison_and_cannot_seal(self):
        cases=[]
        for tag in ('turn_started','turn_complete','compacted','unknown','thread_rolled_back','token_usage_record',
                    'thread_settings_applied','thread_goal_updated','retained_context','security_risk_score','configuration_update','additional_tools'):
            cases.append(('event_msg',{'type':tag,'turn_id':'turn-1'}))
        cases += [('session_meta',{}),('world_state',{}),('compacted',{}),('response_item',{'type':'agent_message'})]
        for typ in ('function_call','local_shell_call','custom_tool_call','tool_search_call','web_search_call','image_generation_call','function_call_output','unknown'):
            cases.append(('response_item',{'type':typ}))
        for item in n.RELEASE_ITEMS-n.NON_TOOL|{'unknown'}:
            cases.append(('event_msg',{'type':'item_completed','thread_id':'reviewer','turn_id':'turn-1','item':{'type':item,'id':'bad'}}))
        for reason in ('replaced','review_ended','budget_limited',None):
            cases.append(('event_msg',{'type':'turn_aborted','turn_id':'turn-1','reason':reason}))
        for kind,payload in cases:
            with self.subTest(kind=kind,payload=payload):
                f=self.fixture();c=f.codec();r=f.reader();f.append([f.journey(c.input)[0],f.record(8,kind,payload)])
                self.poison(r,lambda:self.feed(r,c))
    def test_phase_error_null_client_image_cross_turn_and_postterminal_rejections(self):
        for fault in ('null','scalar-error','partial','missing-phase','duplicate-final','image','client','thread','turn','item-replay','second-start','postterminal','abort-null','no-final','pascal-alias'):
            with self.subTest(fault=fault):
                f=self.fixture();c=f.codec();values=f.journey(c.input)
                if fault=='null':values[-1]['payload']['error']=None
                elif fault=='scalar-error':values[-1]['payload']['error']='bad'
                elif fault=='partial':values[3]['payload']['item']['phase']='partial_answer'
                elif fault=='missing-phase':del values[3]['payload']['item']['phase']
                elif fault=='duplicate-final':values.insert(4,json.loads(json.dumps(values[3])))
                elif fault=='image':values[1]['payload']['item']['content']=[{'type':'image','image_url':'private'}]
                elif fault=='client':values[1]['payload']['item']['client_id']='wrong'
                elif fault=='thread':values[1]['payload']['thread_id']='wrong'
                elif fault=='turn':values[1]['payload']['turn_id']='other'
                elif fault=='item-replay':values[3]['payload']['item']['id']='comment'
                elif fault=='second-start':values.insert(2,json.loads(json.dumps(values[0])))
                elif fault=='postterminal':values.append(f.record(12,'turn_context',{}))
                elif fault=='abort-null':values[-1]['payload']={'type':'turn_aborted','turn_id':None,'reason':'interrupted'}
                elif fault=='no-final':values.pop(3)
                elif fault=='pascal-alias':values[1]['payload']['item']['type']='userMessage'
                for i,x in enumerate(values):x['ordinal']=7+i
                f.append(values);r=f.reader();self.poison(r,lambda:self.feed(r,c))
    def test_benign_records_are_counted_and_bound_with_digest(self):
        f=self.fixture();c=f.codec();values=f.journey(c.input)
        values[1:1]=[f.record(0,'turn_context',{'turn_id':'turn-1'}),f.record(0,'response_item',{'type':'reasoning'}),
                     f.record(0,'response_item',{'type':'message','role':'assistant'}),f.record(0,'event_msg',{'type':'token_count'})]
        for i,x in enumerate(values):x['ordinal']=7+i
        f.append(values);r=f.reader();self.feed(r,c);r.poll(final=True)
        self.assertEqual(len(r.coverage.record_digests),9)
    def test_source_mode_mismatch_and_bounds_bool_reject(self):
        f=self.fixture()
        for field in ('max_line_bytes','max_window_bytes','max_records','max_polls','next_ordinal'):
            with self.assertRaises(Exception):replace(f.pin,**{field:True})
        c=n.EventCoverage(f.pin.thread_id,'a'*64)
        with self.assertRaises(Exception):n.RolloutWindow(f.pin,c,lambda:0,10)
        self.assertTrue(c.failed)
    def test_benign_wrong_thread_session_turn_and_overflow_are_sticky(self):
        for key,value in [('thread_id','child-thread'),('session_id','other-session'),('turn_id','other-turn')]:
            f=self.fixture();c=f.codec();r=f.reader()
            f.append([f.journey(c.input)[0],f.record(8,'turn_context',{key:value})])
            self.poison(r,lambda:self.feed(r,c))
        f=self.fixture(next_ordinal=2**63-1);c=f.codec();r=f.reader()
        f.append([f.record(2**63-1,'event_msg',{'type':'task_started','turn_id':'turn-1'})]);self.feed(r,c)
        f.append([f.record(2**63,'turn_context',{})]);self.poison(r,r.poll)
    def test_final_snapshot_partial_or_late_append_and_mode_drift_reject(self):
        for suffix in [b'partial',raw(RolloutFixture.record(12,'turn_context',{}))+b'\n']:
            f=self.fixture();c=f.codec();r=f.reader();f.append(f.journey(c.input));self.feed(r,c)
            with f.path.open('ab') as out:out.write(suffix)
            self.poison(r,lambda:r.poll(final=True))
        f=self.fixture();r=f.reader();r.poll();f.path.chmod(0o400);self.poison(r,r.poll)
    def test_missing_explicit_lifecycle_turn_never_inherits_prior_turn(self):
        for index in (0,1,3,4):
            f=self.fixture();c=f.codec();r=f.reader();values=f.journey(c.input)
            del values[index]['payload']['turn_id'];f.append(values)
            self.poison(r,lambda:self.feed(r,c))
    def test_malformed_schema_lowered_true_budget_and_parent_symlink(self):
        for bad in [[],{'ordinal':7},dict(RolloutFixture.record(7,'event_msg',{}),timestamp=1)]:
            f=self.fixture();c=f.codec();r=f.reader();f.append([bad]);self.poison(r,lambda:self.feed(r,c))
        f=self.fixture();c=f.codec();r=f.reader(max_window_bytes=16);self.poison(r,r.poll,'PICKUP_EVENT_LIMIT')
        f=self.fixture();r=f.reader();parent=f.path.parent;parent.rename(parent.with_name('moved'));parent.symlink_to(parent.with_name('moved'))
        self.poison(r,r.poll,'PICKUP_EVENT_SOURCE_INVALID')
        parent.unlink();parent.with_name('moved').rename(parent)


class RolloutCompositionTests(unittest.TestCase):
    def trial(self,**kwargs):
        t=CompositionTrial(rollout=True,**kwargs);self.addCleanup(t.close);return t
    def test_full_existing_runner_ledger_receipt_and_no_server_eof(self):
        t=self.trial();result=t.run()
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(set(result['dimensions'].values()),{'PASS'})
        self.assertTrue(t.comp.rollout.drained and t.comp.rollout.closed)
        self.assertTrue(t.comp.stream.drained and t.comp.stream.closed)
        self.assertEqual(t.comp.window.spawn_count,0);self.assertEqual(t.comp.window.attempts,0)
        self.assertEqual(t.requests.count('thread/queue/add'),1);self.assertEqual(t.notify_count,1)
        self.assertFalse(t.t.ledger.notification_pending(t.t.binding.reviewer_session))
        self.assertFalse(result['authorized']);self.assertEqual(result['runtime_evidence_status'],'NOT_VERIFIED')
        self.assertNotIn('CANARY',json.dumps(result))
    def test_failed_interrupted_blocked_and_changes(self):
        for mode in ('failed','interrupted','blocked','changes'):
            t=self.trial(mode=mode);result=t.run()
            self.assertEqual(result['status'],'CHANGES_REQUIRED' if mode=='changes' else 'BLOCKED',result)
            if mode in ('failed','interrupted'):self.assertEqual(t.notify_count,0)
            if mode=='blocked':self.assertEqual(result['dimensions']['AUTHOR_RECEIPTED'],'PASS')
    def test_rpc_fault_queue_ack_and_late_tool_keep_unknown_pending(self):
        for fault in ('wrong-id','malformed','partial','disconnect','ack'):
            t=self.trial(stream_fault=None if fault=='ack' else fault)
            if fault=='ack':t.comp.codec.accept=lambda x:(_ for _ in ()).throw(ValueError('CANARY'))
            result=t.run();self.assertEqual(result['status'],'BLOCKED',result)
            self.assertTrue(t.comp.coverage.failed);self.assertEqual(t.notify_count,0)
            self.assertTrue(t.t.ledger.notification_pending(t.t.binding.reviewer_session))
            self.assertEqual(result['dimensions']['REVIEW_COMPLETED'],'NOT_VERIFIED')
    def test_cleanup_late_tool_source_mismatch_and_prequeue_data_fail_closed(self):
        t=self.trial();old=t.comp.clean
        def late():
            f=t.rollout_fixture
            f.append([f.record(12,'response_item',{'type':'function_call','call_id':'late-tool'})])
            return old()
        t.comp.clean=late;result=t.run()
        self.assertEqual(result['status'],'BLOCKED',result)
        self.assertTrue(t.comp.coverage.failed)
        self.assertTrue(t.t.ledger.notification_pending(t.t.binding.reviewer_session))
        self.assertEqual(result['dimensions']['REVIEW_COMPLETED'],'FAIL')
        self.assertEqual(result['dimensions']['AUTHOR_RECEIPTED'],'FAIL')
        t=self.trial();t.rollout_fixture.append([t.rollout_fixture.journey()[0]])
        result=t.run();self.assertEqual(result['status'],'BLOCKED',result);self.assertEqual(t.requests.count('thread/queue/add'),0)
    def test_missing_receipt_is_partial_and_close_timeout_never_finishes(self):
        t=self.trial();t.receipt_missing=True;result=t.run()
        self.assertEqual(result['status'],'PARTIAL',result);self.assertEqual(t.notify_count,1)
        t=self.trial();close=t.channel.close
        def late():
            result=close();t.t.j.clock.wait(6);return result
        t.channel.close=late;result=t.run()
        self.assertEqual(result['status'],'BLOCKED',result)
        self.assertTrue(t.comp.coverage.failed)
        self.assertNotEqual(result['dimensions']['REVIEW_COMPLETED'],'PASS')
