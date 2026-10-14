"""Release-pinned pickup primitives. Public native composition stays unavailable.

Private fixtures exercise protocol and real owned I/O, never existing CLI RPC.
Audit evidence is cooperative Host evidence, not OS isolation or authorization.
"""
import hashlib
import json
import math
import os
import selectors
import signal
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from .action_contract import _hex, _identifier, _integer
from .task_review import digest, load_json, screen, reject
from .task_review_collector import physical_directory
from .providers.cli_lifecycle import CLIProcessLifecycle
from .authorization_policy import HostIssuer, IssuerClass, NotificationBinding

PROTOCOL_SHA = '092d3acd6bec3e3a14bdc7e7a2810ab628ab759d'
CLI_VERSION = '0.162.1'
LIMIT = 65536
NON_TOOL = frozenset(('UserMessage','AgentMessage','Reasoning','Plan'))
RELEASE_ITEMS = frozenset(('UserMessage','HookPrompt','AgentMessage','FunctionCallOutput','Plan',
    'Reasoning','CommandExecution','FileChange','McpToolCall','DynamicToolCall','CollabAgentToolCall',
    'SubAgentActivity','ImageView','EnteredReviewMode','ExitedReviewMode','ContextCompaction'))
DOMAINS = ('LLMTOOL_PROVIDER_CALLS','REVIEWER_TURN_INFERENCE','CLAUDE_AUTHOR_INFERENCE')
METHODS = frozenset(('initialize','thread/read','thread/queue/add','thread/queue/list',
                     'thread/queue/delete','turn/interrupt'))
_FIXTURE = object()


def native_services_factory():
    return None


def run_native_pickup(*args, **kwargs):
    return dict(status='BLOCKED',code='NATIVE_PICKUP_NOT_AUTHORIZED',authorized=False,
                runtime_evidence_status='NOT_VERIFIED')


def wire(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= LIMIT:
        reject('PICKUP_WIRE_LIMIT')
    value = load_json(raw.decode('utf-8'), LIMIT)
    # Apply privacy checks to escaped output too. Never print echo or exceptions.
    screen(raw.decode('utf-8')); screen(json.dumps(value,ensure_ascii=True))
    return value


def encoded(value):
    data = json.dumps(value,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode()
    if len(data) > LIMIT: reject('PICKUP_WIRE_LIMIT')
    screen(data.decode()); return data


@dataclass(frozen=True)
class NativeGrant:
    binding: NotificationBinding
    authority_digest: str
    protocol_sha: str
    cli_version: str
    reviewer_route: str
    author_route: str
    reviewer_context_digest: str
    author_context_digest: str
    experimental_api: bool
    post_trial_disposition: str
    synthetic_only: bool
    receipt_registration_digest: str

    def __post_init__(self):
        if type(self.binding) is not NotificationBinding: reject('PICKUP_GRANT_INVALID')
        for v in (self.authority_digest,self.reviewer_context_digest,self.author_context_digest,
                  self.receipt_registration_digest): _hex(v)
        _identifier(self.reviewer_route); _identifier(self.author_route)
        if (self.protocol_sha != PROTOCOL_SHA or self.cli_version != CLI_VERSION or
                self.experimental_api is not True or self.synthetic_only is not True or
                self.post_trial_disposition != 'STOP_PRESERVE_CONTEXT'):
            reject('PICKUP_GRANT_INVALID')


class NativePickupIssuer(HostIssuer):
    """Host-only seam; construction alone cannot authenticate a native grant.

    No shipped registry/production reader issues the sealed acceptance. Private
    fixture acceptance remains test evidence, never a public native route.
    """
    issuer_class = IssuerClass.NOTIFICATION
    def __init__(self, grant, acceptance):
        if type(grant) is not NativeGrant or type(acceptance) is not _AcceptedGrant:
            reject('PICKUP_NOT_AUTHORIZED')
        if acceptance.marker is not _FIXTURE or acceptance.digest != digest(asdict(grant)):
            reject('PICKUP_NOT_AUTHORIZED')
        self.grant,self.acceptance = grant,acceptance
        self._capability = grant.binding.capability_id
    @property
    def capability_id(self):return self._capability
    def vouches_for(self, binding_digest):
        return self.acceptance.marker is _FIXTURE and binding_digest == self.grant.binding.digest


@dataclass(frozen=True)
class _AcceptedGrant:
    digest: str
    marker: object


def _fixture_issuer(grant):
    return NativePickupIssuer(grant,_AcceptedGrant(digest(asdict(grant)),_FIXTURE))


@dataclass(frozen=True)
class DomainSample:
    domain: str
    source: str
    owner: str
    run_id: str
    generation: str
    counter: int
    covered_entrypoints: tuple
    usage: str = 'UNKNOWN'
    def __post_init__(self):
        if self.domain not in DOMAINS: reject('PICKUP_COUNTER_DOMAIN')
        for v in (self.source,self.owner,self.run_id,self.generation): _identifier(v)
        _integer(self.counter)
        if (type(self.covered_entrypoints) is not tuple or not self.covered_entrypoints or
                len(self.covered_entrypoints)>32 or len(set(self.covered_entrypoints))!=len(self.covered_entrypoints)):
            reject('PICKUP_COUNTER_DOMAIN')
        for v in self.covered_entrypoints: _identifier(v)
        if self.usage != 'UNKNOWN': reject('PICKUP_COUNTER_DOMAIN')


def validate_domains(before, after):
    if type(before) is not tuple or type(after) is not tuple or len(before)!=3 or len(after)!=3:
        reject('PICKUP_COUNTER_DOMAIN')
    for domain,a,b in zip(DOMAINS,before,after):
        if type(a) is not DomainSample or type(b) is not DomainSample or a.domain!=domain or b.domain!=domain:
            reject('PICKUP_COUNTER_DOMAIN')
        if (a.source,a.owner,a.run_id,a.generation,a.covered_entrypoints)!=(b.source,b.owner,b.run_id,b.generation,b.covered_entrypoints):
            reject('PICKUP_COUNTER_DOMAIN')
        delta=b.counter-a.counter
        if delta<0 or (domain==DOMAINS[0] and delta!=0) or (domain!=DOMAINS[0] and delta>1):
            reject('PICKUP_COUNTER_DOMAIN')
    return {domain:after[i].counter-before[i].counter for i,domain in enumerate(DOMAINS)}


class QueueCodec:
    """Exact four-ID mapping; no textual/temporal inference or retry."""
    def __init__(self, thread_id, client_id, packet_digest):
        _identifier(thread_id); _identifier(client_id); _hex(packet_digest)
        self.thread,self.client,self.packet = thread_id,client_id,packet_digest
        self.submission=self.item=self.turn=None
        self.input=None;self.added=False;self.cursor=None;self.pages=0;self.items=0;self.seen=set();self.cursors=set()
    def add(self, text):
        if self.added or type(text) is not str: reject('PICKUP_EVENT_REPLAY')
        screen(text);screen(json.dumps(text,ensure_ascii=True))
        value={'threadId':self.thread,'input':[{'type':'text','text':text,'text_elements':[]}],
               'clientUserMessageId':self.client}
        # Reserve bounded ID/envelope headroom for add/list/notification echoes.
        echo={'result':{'queuedSubmission':{'id':'x'*256,'input':value['input'],
              'clientUserMessageId':self.client}},'id':'x'*256}
        notification={'method':'item/started','params':{'threadId':self.thread,'turnId':'x'*256,
            'item':{'type':'userMessage','id':'x'*256,'clientId':self.client,'content':value['input']}}}
        encoded(echo);encoded(notification);encoded({'data':[echo['result']['queuedSubmission']],'nextCursor':None})
        self.input=value['input'];self.added=True
        return 'thread/queue/add',value
    def accept(self, raw):
        if not self.added or self.submission is not None:reject('PICKUP_EVENT_REPLAY')
        x=wire(raw)
        if type(x) is not dict or set(x)!= {'queuedSubmission'}: reject('PICKUP_QUEUE_UNKNOWN')
        q=x['queuedSubmission']
        if type(q) is not dict or set(q)!= {'id','input','clientUserMessageId'} or q['clientUserMessageId']!=self.client or q['input']!=self.input:
            reject('PICKUP_QUEUE_UNKNOWN')
        _identifier(q['id']);self.submission=q['id'];return self.submission
    def list_request(self):
        if self.pages>=8: reject('PICKUP_EVENT_LIMIT')
        return 'thread/queue/list',dict(threadId=self.thread,cursor=self.cursor,limit=16)
    def list_page(self, raw):
        x=wire(raw)
        if type(x) is not dict or set(x)!= {'data','nextCursor'} or type(x['data']) is not list:
            reject('PICKUP_EVENT_UNVERIFIED')
        self.pages+=1;self.items+=len(x['data'])
        if self.pages>8 or self.items>128: reject('PICKUP_EVENT_LIMIT')
        for q in x['data']:
            if type(q) is not dict or set(q)!= {'id','input','clientUserMessageId'} or type(q['input']) is not list: reject('PICKUP_EVENT_UNVERIFIED')
            _identifier(q['id']);_identifier(q['clientUserMessageId'])
            if q['id'] in self.seen: reject('PICKUP_EVENT_REPLAY')
            self.seen.add(q['id'])
        cursor=x['nextCursor']
        if cursor is not None:
            _identifier(cursor)
            if cursor in self.cursors:reject('PICKUP_EVENT_REPLAY')
            self.cursors.add(cursor)
        self.cursor=cursor
        return cursor is None and self.items==0
    def delivery(self, raw):
        x=wire(raw)
        if type(x) is not dict or x.get('threadId')!=self.thread:reject('PICKUP_EVENT_UNVERIFIED')
        item=x.get('item')
        if (type(item) is not dict or item.get('type')!='userMessage' or item.get('clientId')!=self.client or
                item.get('content')!=self.input or self.submission is None):reject('PICKUP_TURN_UNCORRELATED')
        _identifier(item.get('id'));_identifier(x.get('turnId'))
        if self.item is not None:reject('PICKUP_EVENT_REPLAY')
        self.item,self.turn=item['id'],x['turnId'];return self.submission,self.client,self.item,self.turn
    def delete(self):
        if self.submission is None:reject('PICKUP_TURN_UNCORRELATED')
        return 'thread/queue/delete',dict(threadId=self.thread,queuedSubmissionId=self.submission)
    def interrupt(self):
        if self.turn is None:reject('PICKUP_TURN_UNCORRELATED')
        return 'turn/interrupt',dict(threadId=self.thread,turnId=self.turn)


class EventCoverage:
    """Pinned observer window. Provenance is supplied by trusted Host, not prose."""
    def __init__(self, thread, source_digest):
        _identifier(thread);_hex(source_digest)
        self.thread,self.source=thread,source_digest;self.last=0;self.started=False;self.terminal=None
        self.failed=False;self.tool=False;self.turn=None;self.events=[];self.item_ids={}
    def feed(self, *, sequence, source_digest, thread_id, turn_id, kind, item_type=None, status=None, item_id=None, item_phase="started"):
        if self.failed:reject('PICKUP_COVERAGE_UNVERIFIED')
        try:
            if source_digest!=self.source or sequence!=self.last+1 or thread_id!=self.thread:
                reject('PICKUP_COVERAGE_UNVERIFIED')
            if type(sequence) is not int or len(self.events)>=128:reject('PICKUP_EVENT_LIMIT')
            _identifier(turn_id)
            if self.turn is not None and turn_id!=self.turn:reject('PICKUP_TOOL_EFFECT')
            if kind=='started':
                if self.started:reject('PICKUP_EVENT_REPLAY')
                self.started=True;self.turn=turn_id
            elif kind=='item':
                if not self.started or item_type not in NON_TOOL: self.tool=True;reject('PICKUP_TOOL_EFFECT')
                _identifier(item_id)
                prior=self.item_ids.get(item_id)
                if item_phase=='started':
                    if prior is not None:reject('PICKUP_EVENT_REPLAY')
                    self.item_ids[item_id]=(item_type,turn_id,'started')
                elif item_phase=='completed':
                    if prior!=(item_type,turn_id,'started'):reject('PICKUP_COVERAGE_UNVERIFIED')
                    self.item_ids[item_id]=(item_type,turn_id,'completed')
                else:reject('PICKUP_COVERAGE_UNVERIFIED')
            elif kind=='terminal':
                if not self.started or self.terminal is not None or status not in ('Completed','Interrupted','Failed'):
                    reject('PICKUP_COVERAGE_UNVERIFIED')
                self.terminal=status
            else:reject('PICKUP_COVERAGE_UNVERIFIED')
            self.last=sequence;self.events.append((sequence,thread_id,turn_id,kind,item_type,status,item_id,item_phase))
        except Exception:
            self.failed=True;raise
    def seal(self, *, drained, child_exited, source_digest):
        if self.failed or not self.started or self.terminal is None or any(x[2] != "completed" for x in self.item_ids.values()) or drained is not True or child_exited is not True or source_digest!=self.source:
            reject('PICKUP_COVERAGE_UNVERIFIED')
        return digest((self.source,self.events),'llmtool.pickup-coverage.v1')


@dataclass(frozen=True)
class ReceiptWaiterPin:
    session: str
    task_id: str
    run_id: str
    source_sha: str
    notification_id: str
    owner_tool_id: str
    child_generation: str
    channel: str
    expires_at: int
    def __post_init__(self):
        for v in (self.session,self.task_id,self.run_id,self.notification_id,self.owner_tool_id,self.child_generation):_identifier(v)
        _hex(self.source_sha,(40,64));_integer(self.expires_at)
        if type(self.channel) is not str or not os.path.isabs(self.channel):reject('PICKUP_AUTHOR_RECEIPT_UNVERIFIED')
    @property
    def digest(self):return digest(asdict(self),'llmtool.author-waiter.v1')


def read_receipt(pin, *, owner_digest, verdict_digest, now):
    if type(pin) is not ReceiptWaiterPin or pin.digest!=owner_digest or type(now) is not int or now>=pin.expires_at:
        reject('PICKUP_AUTHOR_RECEIPT_UNVERIFIED')
    _hex(verdict_digest)
    path=Path(pin.channel);physical_directory(path.parent)
    if stat.S_IMODE(path.parent.stat().st_mode)!=0o700:reject('PICKUP_AUTHOR_RECEIPT_UNVERIFIED')
    fd=None
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600:
            reject('PICKUP_AUTHOR_RECEIPT_UNVERIFIED')
        raw=os.read(fd,LIMIT+1);after=os.fstat(fd);named=os.lstat(path)
        fields=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_nlink,s.st_uid)
        if fields(st)!=fields(after) or fields(st)!=fields(named) or len(raw)!=st.st_size:reject('PICKUP_AUTHOR_RECEIPT_UNVERIFIED')
        value=wire(raw)
        expected={'session':pin.session,'task_id':pin.task_id,'run_id':pin.run_id,'source_sha':pin.source_sha,
            'notification_id':pin.notification_id,'owner_tool_id':pin.owner_tool_id,'child_generation':pin.child_generation,
            'verdict_digest':verdict_digest}
        if value!=expected:reject('PICKUP_AUTHOR_RECEIPT_UNVERIFIED')
        # This file is only data; independent Host notification/turn evidence is
        # additionally required by the runner before AUTHOR_RECEIPTED passes.
        return hashlib.sha256(raw).hexdigest()
    except OSError:reject('PICKUP_AUTHOR_RECEIPT_UNVERIFIED')
    finally:
        if fd is not None:os.close(fd)


class PickupClientLifecycle(CLIProcessLifecycle):
    """Scoped owned client stop; avoids provider lifecycle's unbounded wait."""
    def __init__(self, deadline):
        super().__init__('codex-pickup-client');self.deadline=deadline
    def stop(self):
        p=self.process
        if p is None or p.returncode is not None:return
        try:os.killpg(p.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        remaining=self.deadline-time.monotonic()
        if remaining<=0:reject('PICKUP_CHILD_EXIT_UNKNOWN')
        try:p.wait(timeout=remaining)
        except subprocess.TimeoutExpired:reject('PICKUP_CHILD_EXIT_UNKNOWN')


def _fixture_owned_client(script, payload=b'', *, timeout=5, output_limit=LIMIT):
    """Only deterministic fixture Python; not a Codex/server/provider launcher."""
    if type(script) is not str or type(payload) is not bytes:reject('PICKUP_CHILD_PROTOCOL_INVALID')
    _integer(timeout,minimum=1,maximum=5);_integer(output_limit,minimum=1,maximum=LIMIT)
    start=time.monotonic();deadline=start+timeout
    # Reserve time for actual exit/owned cleanup inside this same client budget.
    io_deadline=deadline-min(0.5,timeout/2)
    with PickupClientLifecycle(deadline) as lifecycle:
        p=subprocess.Popen([sys.executable,'-I','-B','-c',script],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True,
            env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'})
        lifecycle.own(p);selector=selectors.DefaultSelector();buffers={'stdout':bytearray(),'stderr':bytearray()};offset=0
        try:
            for stream in (p.stdin,p.stdout,p.stderr):os.set_blocking(stream.fileno(),False)
            selector.register(p.stdin,selectors.EVENT_WRITE,'stdin')
            selector.register(p.stdout,selectors.EVENT_READ,'stdout');selector.register(p.stderr,selectors.EVENT_READ,'stderr')
            while selector.get_map():
                lifecycle.check();left=io_deadline-time.monotonic()
                if left<=0:reject('PICKUP_CHILD_TIMEOUT')
                for key,_ in selector.select(min(left,0.05)):
                    if key.data=='stdin':
                        if offset==len(payload):selector.unregister(key.fileobj);key.fileobj.close();continue
                        offset+=os.write(key.fd,payload[offset:offset+8192])
                    else:
                        chunk=os.read(key.fd,8192)
                        if not chunk:selector.unregister(key.fileobj);continue
                        buffers[key.data].extend(chunk)
                        if len(buffers[key.data])>output_limit:reject('PICKUP_CHILD_OUTPUT_LIMIT')
            try:code=p.wait(timeout=max(0.001,io_deadline-time.monotonic()))
            except subprocess.TimeoutExpired:reject('PICKUP_CHILD_TIMEOUT')
            if code!=0:reject('PICKUP_CHILD_FAILED')
            return {k:bytes(v) for k,v in buffers.items()}
        finally:
            selector.close()
            for stream in (p.stdin,p.stdout,p.stderr):
                if not stream.closed:stream.close()


class FixtureSocketRPC:
    """Bounded synthetic endpoint only; existing native endpoints stay denied."""
    def __init__(self, path, fixture_root):
        path=Path(path);root=Path(fixture_root);physical_directory(root)
        if (not root.name.startswith('llmtool-pickup-fixture-') or path.parent!=root or
                stat.S_IMODE(root.stat().st_mode)!=0o700):reject('NATIVE_PICKUP_NOT_AUTHORIZED')
        info=path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
            reject('NATIVE_PICKUP_NOT_AUTHORIZED')
        self.path=str(path);self.identity=(info.st_dev,info.st_ino);self.id=0
    def request(self, method, params, *, timeout=5):
        if method not in METHODS:reject('PICKUP_RPC_METHOD_DENIED')
        _integer(timeout,minimum=1,maximum=5)
        info=os.lstat(self.path)
        if (info.st_dev,info.st_ino)!=self.identity:reject('PICKUP_IDENTITY_DRIFT')
        self.id+=1;data=encoded({'jsonrpc':'2.0','id':self.id,'method':method,'params':params})+b'\n'
        deadline=time.monotonic()+timeout
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.settimeout(max(0.001,deadline-time.monotonic()));connection.connect(self.path)
            connection.settimeout(max(0.001,deadline-time.monotonic()));connection.sendall(data)
            raw=bytearray()
            while b'\n' not in raw:
                left=deadline-time.monotonic()
                if left<=0:reject('PICKUP_READ_TIMEOUT')
                connection.settimeout(left);chunk=connection.recv(min(8192,LIMIT+1-len(raw)))
                if not chunk:reject('PICKUP_CHILD_PROTOCOL_INVALID')
                raw.extend(chunk)
                if len(raw)>LIMIT:reject('PICKUP_WIRE_LIMIT')
            if raw.count(b'\n')!=1 or not raw.endswith(b'\n'):reject('PICKUP_CHILD_PROTOCOL_INVALID')
            result=wire(bytes(raw[:-1]))
            if type(result) is not dict or set(result)!= {'jsonrpc','id','result'} or result['jsonrpc']!='2.0' or type(result['id']) is not int or result['id']!=self.id:
                reject('PICKUP_CHILD_PROTOCOL_INVALID')
            return result['result']


def _feed_notification(coverage, codec, raw, *, sequence, source_digest):
    """Original protocol input, not a reviewer-authored evidence document."""
    x=wire(raw)
    if type(x) is not dict or set(x)!= {'method','params'} or type(x['params']) is not dict:
        reject('PICKUP_COVERAGE_UNVERIFIED')
    p=x['params'];thread=p.get('threadId')
    if thread!=codec.thread:reject('PICKUP_TOOL_EFFECT')
    method=x['method']
    if method in ('turn/started','turn/completed'):
        turn=p.get('turn')
        if type(turn) is not dict:reject('PICKUP_COVERAGE_UNVERIFIED')
        status=turn.get('status');mapping={'completed':'Completed','interrupted':'Interrupted','failed':'Failed'}
        if method=='turn/completed' and status not in mapping:reject('PICKUP_COVERAGE_UNVERIFIED')
        if method=='turn/started' and status!='inProgress':reject('PICKUP_COVERAGE_UNVERIFIED')
        coverage.feed(sequence=sequence,source_digest=source_digest,thread_id=thread,turn_id=turn.get('id'),
                      kind='started' if method=='turn/started' else 'terminal',status=mapping.get(status))
    elif method in ('item/started','item/completed'):
        item=p.get('item')
        if type(item) is not dict or type(item.get('type')) is not str:reject('PICKUP_COVERAGE_UNVERIFIED')
        kind=item['type'][0:1].upper()+item['type'][1:]
        if kind=='UserMessage':
            if codec.item is None:codec.delivery(encoded(p))
            elif (item.get('clientId'),item.get('id'),p.get('turnId'),item.get('content')) != (codec.client,codec.item,codec.turn,codec.input):
                reject('PICKUP_TURN_UNCORRELATED')
        coverage.feed(sequence=sequence,source_digest=source_digest,thread_id=thread,turn_id=p.get('turnId'),
                      kind='item',item_type=kind,item_id=item.get('id'),item_phase='started' if method=='item/started' else 'completed')
    else:reject('PICKUP_COVERAGE_UNVERIFIED')


def feed_notification(coverage, codec, raw, *, sequence, source_digest):
    if type(coverage) is not EventCoverage or type(codec) is not QueueCodec:reject('PICKUP_COVERAGE_UNVERIFIED')
    try:return _feed_notification(coverage,codec,raw,sequence=sequence,source_digest=source_digest)
    except (Exception,KeyboardInterrupt):
        coverage.failed=True
        raise


@dataclass(frozen=True)
class HostRecordPin:
    """External Host pins independent of packet/source claims. No authority."""
    root: str
    metadata_path: str
    metadata_digest: str
    session: str
    branch: str
    checkout_sha: str
    runtime_digest: str
    generation: int
    def __post_init__(self):
        _identifier(self.session);_hex(self.metadata_digest);_hex(self.runtime_digest)
        _hex(self.checkout_sha,(40,64));_integer(self.generation,minimum=1)
        if type(self.branch) is not str or not self.branch:reject('PICKUP_IDENTITY_DRIFT')
        if type(self.root) is not str or not os.path.isabs(self.root) or not os.path.isabs(self.metadata_path):
            reject('PICKUP_IDENTITY_DRIFT')


def collect_host_record(pin):
    """Bounded static facts; native Session ownership still needs trusted Host.

    Allows an explicitly pinned canonical main checkout without weakening the
    existing developer collector. Never runs a peer interpreter or follows PID
    heuristics. A same-UID record/file is not an OS attestation.
    """
    if type(pin) is not HostRecordPin:reject('PICKUP_IDENTITY_DRIFT')
    physical_directory(pin.root)
    path=Path(pin.metadata_path);physical_directory(path.parent)
    fd=None
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600 or not 0<st.st_size<=LIMIT:
            reject('PICKUP_IDENTITY_DRIFT')
        raw=os.read(fd,LIMIT+1);after=os.fstat(fd);named=path.lstat()
        fields=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mode,s.st_mtime_ns,s.st_ctime_ns,s.st_uid,s.st_nlink)
        if fields(st)!=fields(after) or fields(st)!=fields(named) or len(raw)!=st.st_size or hashlib.sha256(raw).hexdigest()!=pin.metadata_digest:
            reject('PICKUP_IDENTITY_DRIFT')
        # Private filesystem metadata is not routed packet DATA. Compare the
        # entire fixed schema to external pins and return digests only.
        value=load_json(raw.decode('utf-8'),LIMIT)
        expected={'session':pin.session,'root':pin.root,'branch':pin.branch,'checkout_sha':pin.checkout_sha,
                  'runtime_digest':pin.runtime_digest,'generation':pin.generation}
        if value!=expected:reject('PICKUP_IDENTITY_DRIFT')
        return {"pin_digest":digest(asdict(pin)),"record_digest":pin.metadata_digest}
    except OSError:reject('PICKUP_IDENTITY_DRIFT')
    finally:
        if fd is not None:os.close(fd)
