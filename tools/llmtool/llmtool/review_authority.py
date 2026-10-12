"""B1 host-only production authority adapters; public wiring stays unavailable.

No default paths, installation, ledger creation, provider factory or env grants.
Static host policy is not a second store. Same-UID host code is trusted; these
objects do not establish OS isolation or authenticate caller declarations.
"""
import hashlib
import os
import stat
import time
import re
import threading
import secrets
from dataclasses import dataclass, replace
from pathlib import Path

from .action_contract import ContractValue, _hex, _integer, _path
from .authorization_policy import (HostIssuer,IssuerClass,Decision,ProviderReviewBinding,
    ProviderObservedState,decide_provider_review)
from .authorization_ledger import ReceiptLedger,LedgerError
from .task_review import (HostServices,PairApproval,ReviewPacket,ReviewCallBinding,Admission,
    ConsumeToken,CallOutcome,LaunchResult,UnavailableReviewAuthority,load_json,canonical,
    digest,reject,safe_id,make_packet,PACKET_LIMIT,screen)
from .task_lifecycle import utc_time,inspect_task,Role
from .task_review_collector import physical_directory,collect_local_facts,Observation


def _reference(value):
    if type(value) is not str or not re.fullmatch(
            r'https://github\.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-[0-9]+',value):
        reject('REVIEW_HOST_POLICY_INVALID')


def _keys(value,expected):
    if type(value) is not dict or set(value) != set(expected):
        reject('REVIEW_HOST_POLICY_INVALID')


@dataclass(frozen=True)
class ProviderRoutePolicy(ContractValue):
    policy_id: str
    version: int
    provider: str
    profile: str
    model: str
    data_classes: tuple
    max_packet_bytes: int
    call_timeout_seconds: int
    run_timeout_seconds: int
    max_attempts: int
    retry_count: int
    fallback_count: int
    per_call_cap: int
    per_day_cap: int
    unknown_cost_accepted: bool
    not_before: str
    not_after: str
    authorizer_ref: str

    def __post_init__(self):
        safe_id(self.policy_id);_integer(self.version,minimum=1)
        safe_id(self.profile);safe_id(self.model)
        if (self.provider != 'claude-cli' or self.data_classes != ('DIFF','REVIEW_METADATA','SOURCE') or
                self.unknown_cost_accepted is not True or type(self.unknown_cost_accepted) is not bool):
            reject('REVIEW_ROUTE_NOT_AUTHORIZED')
        for name,limit in (('max_packet_bytes',PACKET_LIMIT),('call_timeout_seconds',120),
                           ('run_timeout_seconds',180),('per_day_cap',1000)):
            _integer(getattr(self,name),minimum=1,maximum=limit)
        for name,value in (('max_attempts',1),('retry_count',0),('fallback_count',0),('per_call_cap',1)):
            if type(getattr(self,name)) is not int or getattr(self,name) != value:
                reject('REVIEW_ROUTE_NOT_AUTHORIZED')
        if utc_time(self.not_before) >= utc_time(self.not_after): reject('REVIEW_HOST_POLICY_INVALID')
        _reference(self.authorizer_ref)

    @property
    def digest(self):
        return digest(self.to_dict(),'llmtool.provider-route-policy')


@dataclass(frozen=True)
class RegisteredAgent:
    agent_id: str
    root: str
    device: int
    inode: int
    common_dir: str
    origin: str
    branch_prefix: str
    venv_prefix: str

    def __post_init__(self):
        safe_id(self.agent_id)
        for name in ('root','venv_prefix'): _path(getattr(self,name),absolute=True)
        if not self.common_dir.endswith('/.git'): reject('REVIEW_REGISTRATION_INVALID')
        _path(self.common_dir[:-5],absolute=True)
        _integer(self.device);_integer(self.inode,minimum=1)
        _path(self.branch_prefix.rstrip('/'))
        if (self.origin != 'git@github.com:davidcatcat/davidcattoolsproject.git' or
                self.branch_prefix in ('main','automation/ai-loop') or
                self.venv_prefix != self.root+'/tools/llmtool/.venv'):
            reject('REVIEW_REGISTRATION_INVALID')


@dataclass(frozen=True)
class HostPin:
    """Independent Integrator-published pins; caller JSON/env cannot create it."""
    path: str
    host_digest: str
    authorizer_ref: str
    pair_digest: str
    packet_input_digest: str
    repository_roots: tuple

    def __post_init__(self):
        _path(self.path,absolute=True);_reference(self.authorizer_ref)
        for value in (self.host_digest,self.pair_digest,self.packet_input_digest): _hex(value)
        if type(self.repository_roots) is not tuple or not 1 <= len(self.repository_roots) <= 32:
            reject('REVIEW_HOST_POLICY_INVALID')
        for root in self.repository_roots: _path(root,absolute=True)


@dataclass(frozen=True)
class HostRegistration:
    agents: tuple
    reviewer_role: str
    reviewer_executable: str
    reviewer_auth_home: str
    route_policies: tuple  # (policy, revoked) tuples
    ledger_path: str
    ledger_device: int
    ledger_inode: int
    ledger_nonce: str
    capability_id: str
    authorizer_ref: str

    def route(self,route_digest):
        matches=[(p,r) for p,r in self.route_policies if p.digest == route_digest]
        if len(matches) != 1 or matches[0][1]: reject('REVIEW_ROUTE_NOT_AUTHORIZED')
        return matches[0][0]


def _decode_host(raw):
    v=load_json(raw,64*1024)
    _keys(v,('schema_version','agents','reviewer','route_policies','ledger','issuer'))
    if type(v['schema_version']) is not int or v['schema_version'] != 1: reject('REVIEW_HOST_POLICY_INVALID')
    if type(v['agents']) is not list or not 1 <= len(v['agents']) <= 32: reject('REVIEW_REGISTRATION_INVALID')
    agents=[]
    for a in v['agents']:
        _keys(a,RegisteredAgent.__dataclass_fields__);agents.append(RegisteredAgent(**a))
    if len({a.agent_id for a in agents}) != len(agents) or len({a.root for a in agents}) != len(agents):
        reject('REVIEW_REGISTRATION_INVALID')
    reviewer=v['reviewer'];_keys(reviewer,('role','executable','auth_home'))
    if reviewer['role'] != 'llmtool-review/claude-cli' or reviewer['role'] in {a.agent_id for a in agents}:
        reject('REVIEW_REGISTRATION_INVALID')
    for key in ('executable','auth_home'): _path(reviewer[key],absolute=True)
    entries=v['route_policies']
    if type(entries) is not list or not 1 <= len(entries) <= 32: reject('REVIEW_HOST_POLICY_INVALID')
    policies=[]
    for entry in entries:
        _keys(entry,('policy','revoked'));_keys(entry['policy'],ProviderRoutePolicy.__dataclass_fields__)
        if type(entry['revoked']) is not bool: reject('REVIEW_HOST_POLICY_INVALID')
        p=dict(entry['policy']);p['data_classes']=tuple(p['data_classes'])
        policies.append((ProviderRoutePolicy(**p),entry['revoked']))
    if len({p.policy_id for p,r in policies}) != len(policies): reject('REVIEW_HOST_POLICY_INVALID')
    ledger=v['ledger'];_keys(ledger,('path','device','inode','creation_nonce'))
    _path(ledger['path'],absolute=True);_hex(ledger['creation_nonce'])
    _integer(ledger['device']);_integer(ledger['inode'],minimum=1)
    issuer=v['issuer'];_keys(issuer,('capability_id','authorizer_ref'))
    safe_id(issuer['capability_id']);_reference(issuer['authorizer_ref'])
    return HostRegistration(tuple(agents),reviewer['role'],reviewer['executable'],reviewer['auth_home'],
        tuple(policies),ledger['path'],ledger['device'],ledger['inode'],ledger['creation_nonce'],
        issuer['capability_id'],issuer['authorizer_ref'])


def read_host_file(path):
    """Read strict static policy only; returns no authority, creates nothing."""
    path=Path(path);physical_directory(path.parent)
    if stat.S_IMODE(path.parent.stat().st_mode) != 0o700: reject('REVIEW_HOST_FILE_UNSAFE')
    fd=None
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|getattr(os,'O_CLOEXEC',0))
        st=os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1 or
                stat.S_IMODE(st.st_mode) != 0o600 or not 0 < st.st_size <= 64*1024):
            reject('REVIEW_HOST_FILE_UNSAFE')
        raw=os.read(fd,64*1024+1);after=os.fstat(fd);named=os.lstat(path)
        fields=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if fields(st) != fields(after) or fields(st) != fields(named) or len(raw) != st.st_size:
            reject('REVIEW_HOST_FILE_UNSAFE')
        reg=_decode_host(raw.decode('utf-8'))
        excluded=[*(a.root for a in reg.agents),*(a.common_dir for a in reg.agents)]
        for candidate in (path,Path(reg.ledger_path)):
            if any(candidate == Path(root) or Path(root) in candidate.parents for root in excluded):
                reject('REVIEW_HOST_FILE_UNSAFE')
        return reg,hashlib.sha256(raw).hexdigest()
    except OSError: reject('REVIEW_HOST_FILE_UNAVAILABLE')
    finally:
        if fd is not None: os.close(fd)


def load_host_registration(pin):
    if type(pin) is not HostPin: reject('REVIEW_HOST_NOT_PINNED')
    reg,sha=read_host_file(pin.path)
    if sha != pin.host_digest or reg.authorizer_ref != pin.authorizer_ref:
        reject('REVIEW_HOST_NOT_PINNED')
    for candidate in (Path(pin.path),Path(reg.ledger_path)):
        if any(candidate == Path(r) or Path(r) in candidate.parents for r in pin.repository_roots):
            reject('REVIEW_HOST_FILE_UNSAFE')
    for agent in reg.agents:
        st=physical_directory(agent.root);physical_directory(agent.common_dir);physical_directory(agent.venv_prefix)
        if (st.st_dev,st.st_ino) != (agent.device,agent.inode): reject('REVIEW_REGISTRATION_INVALID')
    physical_directory(Path(reg.ledger_path).parent)
    st=os.lstat(reg.ledger_path)
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1 or
            stat.S_IMODE(st.st_mode) != 0o600 or (st.st_dev,st.st_ino) != (reg.ledger_device,reg.ledger_inode)):
        reject('REVIEW_LEDGER_UNSAFE')
    executable=Path(reg.reviewer_executable)
    physical_directory(executable.parent);physical_directory(reg.reviewer_auth_home)
    st=executable.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid not in (0,os.geteuid()) or st.st_mode & 0o022 or not st.st_mode & 0o111:
        reject('REVIEW_RUNTIME_INVALID')
    return reg


class HostClock:
    def utc(self): return int(time.time())
    def monotonic(self): return time.monotonic()


class HostAuthorshipObserver:
    """Actual own-root facts plus independently held host baseline/writer gates.

    Missing host gates fail closed. These callbacks are trusted host code, not
    caller claims or PID attribution. B1 does not install production gates.
    """
    def __init__(self,pin,expect,events,clock,*,baseline_check,writers_check):
        self.pin,self.expect,self.events,self.clock=pin,expect,events,clock
        self.baseline_check,self.writers_check=baseline_check,writers_check
        self.samples={}

    def _facts(self,root): return collect_local_facts(root)

    def observe(self):
        reg=load_host_registration(self.pin)
        registered={a.agent_id:a for a in reg.agents}
        facts=[]
        for a in self.expect.authored_by:
            if a.agent_id not in registered: reject('REVIEW_IDENTITY_NOT_ATTESTED')
            r=registered[a.agent_id]
            if (a.role is not Role.DEVELOPER or a.root != r.root or a.common_dir != r.common_dir or
                    not a.branch.startswith(r.branch_prefix) or a.branch in ('main','automation/ai-loop')):
                reject('REVIEW_IDENTITY_NOT_ATTESTED')
            f=self._facts(Path(r.root))
            if (not f.clean_tracked or f.root != r.root or f.branch != a.branch or
                    f.common_dir != r.common_dir or f.source_sha != self.expect.source_sha):
                reject('REVIEW_SOURCE_DRIFT')
            if (not callable(self.baseline_check) or not callable(self.writers_check) or
                    self.baseline_check(r,f) is not True or self.writers_check(r,f) is not True):
                reject('REVIEW_IDENTITY_NOT_ATTESTED')
            facts.append(f.digest)
        if not facts: reject('REVIEW_IDENTITY_NOT_ATTESTED')
        root=self.expect.authored_by[0].root
        sampled=datetime_utc(self.clock.utc())
        o=Observation('davidcatcat/davidcattoolsproject',self.expect.agents,self.expect.authored_by,
            self.expect.source_sha,self.expect.task,self.expect.epoch,self.events,sampled,self.clock.monotonic(),
            (root+'/tools/llmtool/.venv/bin/python',root+'/tools/llmtool/.venv',root+'/tools/llmtool/llmtool/__init__.py'),
            digest((self.pin.host_digest,facts),'llmtool.host-observation-state.v1'))
        self.samples[o.digest]=o
        while len(self.samples)>8: self.samples.pop(next(iter(self.samples)))
        return o


def datetime_utc(seconds):
    from datetime import datetime,timezone
    _integer(seconds,maximum=253402300799)
    return datetime.fromtimestamp(seconds,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


@dataclass(frozen=True)
class ReviewContext:
    """Independent host-held task/pair/original-input pins, not caller files."""
    ready: object
    final: object
    expect: object
    events: tuple
    approval: PairApproval
    packet_input: bytes


class ProductionReviewIssuer(HostIssuer):
    def __init__(self,pin,clock,observer,context):
        if type(pin) is not HostPin or type(context) is not ReviewContext:
            reject('REVIEW_HOST_NOT_PINNED')
        self.pin,self.clock,self.observer,self.context=pin,clock,observer,context
        self._capability=load_host_registration(pin).capability_id
        # Allocated by host code before admission; no caller launch identity.
        self.reviewer_launch_nonce=secrets.token_hex(16)
        self.redeem_run_id=secrets.token_hex(16)

    @property
    def issuer_class(self): return IssuerClass.PRODUCTION_REVIEW
    @property
    def capability_id(self): return self._capability

    def policy(self,route_digest):
        reg=load_host_registration(self.pin)
        if reg.capability_id != self.capability_id: reject('REVIEW_HOST_NOT_PINNED')
        p=reg.route(route_digest);now=self.clock.utc()
        if not int(utc_time(p.not_before).timestamp()) <= now < int(utc_time(p.not_after).timestamp()):
            reject('REVIEW_ROUTE_NOT_AUTHORIZED')
        return p

    def vouches_for(self,policy_digest):
        try: self.policy(policy_digest);return True
        except Exception: return False

    def daily_cap(self,route_digest): return self.policy(route_digest).per_day_cap

    def matches(self,call,observed):
        c=self.context;p=self.policy(call.route_digest)
        if (pair_pin(c.approval) != self.pin.pair_digest or
                hashlib.sha256(c.packet_input).hexdigest() != self.pin.packet_input_digest): return False
        # Reconstruct the packet from host originals and recorded authentic
        # pre-observation. New current observations cannot renew its digest.
        before=self.observer.samples.get(call.observation_digest)
        if before is None: return False
        now=datetime_utc(self.clock.utc());mono=self.clock.monotonic()
        before.validate(c.expect,c.events,now,mono)
        current=self.observer.observe();current.validate(c.expect,c.events,now,mono)
        if current.state_digest != before.state_digest: return False
        c.approval.validate(c.ready,c.final,c.expect,now)
        snapshot=inspect_task(c.ready,c.expect,c.events,now)
        packet=make_packet(c.ready,c.final,c.expect,snapshot,c.approval,before,
                           load_json(c.packet_input.decode(),1024*1024),expected_route=p.policy_id)
        scope=digest({'data_classes':('SOURCE','DIFF','REVIEW_METADATA'),
            'path_list_digest':digest([s['path'] for s in packet.to_dict()['sources']],'llmtool.review-path-list.v1')})
        return (type(call) is ReviewCallBinding and call.ready_task == c.approval.ready_task and
            call.acceptance_task == c.approval.acceptance_task and call.criterion_map == c.approval.criterion_map and
            call.task_id == c.expect.task.task_id and call.run_id == c.expect.task.run_id and
            call.source_sha == c.expect.source_sha and call.epoch == c.expect.epoch and
            call.sequence == snapshot.last_sequence and call.packet_digest == packet.digest and
            call.author_agents == tuple(sorted(a.agent_id for a in c.expect.authored_by)) and
            call.reviewer_role == 'llmtool-review/claude-cli' and call.reviewer_role not in call.author_agents and
            call.reviewer_launch_nonce == self.reviewer_launch_nonce and call.redeem_run_id == self.redeem_run_id and
            (call.route_id,call.route_version,call.provider,call.profile,call.model) ==
                (p.policy_id,p.version,p.provider,p.profile,p.model) and
            call.export_scope_digest == scope and len(packet.data) <= p.max_packet_bytes and
            utc_time(call.deadline) <= min(utc_time(c.approval.expires_at),utc_time(p.not_after)))


def pair_pin(approval):
    # Public stable helper for the host's independently approved pair manifest.
    values=dict(approval.__dict__)
    for name in ('ready_task','acceptance_task'): values[name]=values[name].to_dict()
    return digest(values,'llmtool.host-pair-pin.v1')


class ProductionReviewAuthority:
    test_only=False
    def __init__(self,issuer,ledger):
        if not isinstance(issuer,ProductionReviewIssuer) or type(ledger) is not ReceiptLedger or ledger.kind != 'PROVIDER_REVIEW':
            reject('REVIEW_AUTHORITY_UNAVAILABLE')
        reg=load_host_registration(issuer.pin)
        if (ledger._path,ledger._identity,ledger.creation_nonce) != (
                reg.ledger_path,(reg.ledger_device,reg.ledger_inode),reg.ledger_nonce):
            reject('REVIEW_LEDGER_UNSAFE')
        self.issuer,self.ledger=issuer,ledger
        self.receipts={}

    def _observed(self,binding):
        b=ProviderReviewBinding(binding)
        return ProviderObservedState(b.digest,binding.source_sha,binding.observation_digest,True)

    def admit(self,binding):
        try:
            b=ProviderReviewBinding(binding)
            decision=decide_provider_review(b,issuer=self.issuer,observed=self._observed(binding))
            if decision.decision is not Decision.ALLOW_WITH_TRUSTED_RECEIPT:
                return Admission(binding,False,'REVIEW_GRANT_NOT_AUTHORIZED',None,self.test_only)
            receipt=self.ledger.issue_provider(decision,ttl_seconds=120)
            self.receipts[receipt.nonce]=receipt
            return Admission(binding,True,'REVIEW_ADMITTED',receipt.nonce,self.test_only)
        except LedgerError as exc: reject('REVIEW_'+exc.code.value)

    def consume(self,admission):
        receipt=self.receipts.get(admission.grant_ref)
        if (type(admission) is not Admission or receipt is None or not admission.admitted or
                receipt.binding.call != admission.binding or admission.test_only is not self.test_only):
            reject('REVIEW_GRANT_NOT_AUTHORIZED')
        try:
            committed=self.ledger.consume_provider(receipt,issuer=self.issuer,
                observed=self._observed(admission.binding))
        except LedgerError as exc: reject('REVIEW_'+exc.code.value)
        c=admission.binding
        return ConsumeToken(c.digest,c.reviewer_launch_nonce,receipt.nonce,committed,self.test_only)

    def receipt_for(self,token):
        receipt=self.receipts.get(token.grant_ref) if type(token) is ConsumeToken else None
        if (receipt is None or not token.committed or token.test_only is not self.test_only or
                token.binding_digest != receipt.binding.call.digest or
                token.reviewer_launch_nonce != receipt.binding.call.reviewer_launch_nonce):
            reject('REVIEW_GRANT_NOT_AUTHORIZED')
        return receipt

    def finalize(self,token,outcome):
        receipt=self.receipt_for(token)
        if type(outcome) is not CallOutcome or outcome.binding_digest != token.binding_digest:
            reject('REVIEW_FINALIZE_UNKNOWN')
        try: done=self.ledger.finalize_provider(receipt,outcome.status)
        except LedgerError: reject('REVIEW_FINALIZE_UNKNOWN')
        if not done: reject('REVIEW_FINALIZE_UNKNOWN')


class ProductionReviewerLauncher:
    test_only=False
    def __init__(self,authority):
        self.authority=authority
        self._attempted=set()
        self._attempt_lock=threading.Lock()

    def launch(self,packet,token):
        receipt=self.authority.receipt_for(token)
        c=receipt.binding.call
        p=self.authority.issuer.policy(c.route_digest)
        if (self.authority.ledger.status(receipt.nonce) != 'CONSUMED' or
                ReviewPacket(packet).digest != c.packet_digest or len(packet)>p.max_packet_bytes):
            reject('REVIEW_GRANT_NOT_AUTHORIZED')
        reg=load_host_registration(self.authority.issuer.pin)
        with self._attempt_lock:
            if token.grant_ref in self._attempted: reject('REVIEW_GRANT_NOT_AUTHORIZED')
            self._attempted.add(token.grant_ref)
        # Use the existing provider argv, pipe limits and owned-child lifecycle.
        from .providers.claude_cli import ClaudeCLIProvider
        class TrackedProvider(ClaudeCLIProvider):
            provenance=None
            @staticmethod
            def _child_environment():
                env=ClaudeCLIProvider._child_environment();env['HOME']=reg.reviewer_auth_home
                return env
            def _collect(inner,process,input_bytes,lifecycle):
                if lifecycle.process is not process or process.returncode is not None or os.getpgid(process.pid) != process.pid:
                    reject('REVIEW_LAUNCH_NOT_ATTESTED')
                inner.provenance=digest({'nonce':c.reviewer_launch_nonce,'owned_pid':process.pid,
                    'owned_pgid':os.getpgid(process.pid),'host_pid':os.getpid(),'started':time.monotonic(),
                    'executable':reg.reviewer_executable},'llmtool.review-owned-child.v1')
                return super()._collect(process,input_bytes,lifecycle)
        provider=TrackedProvider({'model':p.model,'timeout_seconds':p.call_timeout_seconds},None)
        content,elapsed=provider._invoke(reg.reviewer_executable,provider._prompt(ReviewPacket(packet).messages()))
        if provider.provenance is None: reject('REVIEW_LAUNCH_NOT_ATTESTED')
        return LaunchResult(content,canonical({'elapsed_seconds':elapsed}),c.reviewer_launch_nonce,
                            provider.provenance,True,self.test_only)


def review_authority_factory():
    """No production Host path/route/ledger authorization has been installed."""
    return UnavailableReviewAuthority()


def compose_host_services(issuer,ledger,*,budget):
    """Explicit trusted host composition, not public wiring or a grant flag."""
    from .run_budget import RunBudget
    if not isinstance(issuer,ProductionReviewIssuer) or type(budget) is not RunBudget:
        reject('REVIEW_HOST_SERVICES_INVALID')
    c=issuer.context
    raw=load_json(c.packet_input.decode(),1024*1024)
    reg=load_host_registration(issuer.pin)
    policies=[p for p,revoked in reg.route_policies if not revoked and p.policy_id == raw.get('route')]
    if len(policies) != 1: reject('REVIEW_ROUTE_NOT_AUTHORIZED')
    policy=issuer.policy(policies[0].digest)
    if budget.wall_clock_ms > policy.run_timeout_seconds*1000: reject('REVIEW_BUDGET_BLOCKED')
    authority=ProductionReviewAuthority(issuer,ledger)
    return HostServices(authority,ProductionReviewerLauncher(authority),issuer.observer,issuer.clock,
        c.approval,c.packet_input,dict(id=policy.policy_id,version=policy.version,digest=policy.digest,
        profile=policy.profile,model=policy.model),issuer.reviewer_launch_nonce,issuer.capability_id,
        issuer.redeem_run_id,budget,False,policy.unknown_cost_accepted)
