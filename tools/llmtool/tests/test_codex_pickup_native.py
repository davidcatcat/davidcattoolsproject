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
    def __init__(self, *, mode='complete', stream_fault=None):
        from test_codex_handoff_transport import DeltaTrial
        from llmtool import codex_handoff_transport as c,task_review as r,authorization_policy as p
        self.t=DeltaTrial();t=self.t
        old_utc=t.j.clock.utc;t.j.clock.utc=lambda:int(old_utc())
        self.inferences=0;self.author_calls=0;self.errors=[];self.requests=[]
        self.channel,self.server=n.SocketPairChannel.fixture_pair();self.server.settimeout(3)
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
                    self.server.sendall(raw(reply)+b'\n')
                    if method=='thread/queue/add':
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
