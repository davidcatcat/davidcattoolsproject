"""One foreground OpenCode acceptance composition; import ONLY by the active Integrator.

No command-line/file/environment approval reader. Call run() only AFTER the
Integrator independently accepts the exact task contract, source/route/privacy
grants, runtime and budget. Same-UID native permissions are cooperative, not an
OS sandbox. No scheduler, Git writes, tests, subagents, durable queue or
next-stage launch. One invocation; the returned result remains untrusted DATA.

Every environment-specific constant (task root, TaskContract digest, screened
file allowance, executable pin, shared common-dir and budget) is caller-supplied
DATA. None is a grant: the active Integrator constructs them only from its own
independently accepted, current records. Python objects authenticate nothing.
"""
import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import time
import urllib.request

from .action_contract import TaskBinding
from .run_budget import UsageLedger
from .task_lifecycle import AgentBinding, Role
from .task_review import canonical
from . import opencode_worker as w

TASK_ID = 'a11c-opencode-foreground-193'
AGENT_ID = 'opencode-foreground-worker'
PINNED_VERSION = '2.0.32'


def fail():
    raise w.WorkerError('OPENCODE_HOST_ADMISSION_UNKNOWN')


def model_identity(entry):
    """Fail-closed identity of one untrusted native model record.

    Only a nonempty string providerID plus nonempty string id form a name. Any
    other shape (absent, None, empty, non-string, non-mapping) yields None, so
    the caller's existing admission comparisons fail() instead of raising an
    unhandled TypeError. This never grants: a None identity cannot equal the
    contracted model name.
    """
    if type(entry) is not dict:
        return None
    provider = entry.get('providerID')
    name = entry.get('id')
    if type(provider) is not str or not provider or type(name) is not str or not name:
        return None
    return provider + '/' + name


@dataclass(frozen=True)
class HostPolicy:
    """Integrator-supplied, already-accepted composition constants (data, not authority).

    root: registered sole-writer task root of this job.
    contract: exact accepted TaskContract digest.
    files: exact screened read/write file allowance, relative to root.
    Construct only from the active Integrator's independent current grants; a
    policy object is not an attestation and confers no authority by itself.
    """
    root: str
    contract: str
    files: tuple


@dataclass(frozen=True)
class Admission:
    """Trusted active-host data, NOT a token issuer/authentication mechanism.

    Integrator constructs this only from its independent accepted current grants.
    Nonempty references point to that evidence; constructor alone proves nothing.
    Unique runtime directory required for every phase, retained on uncertainty.
    """
    job_digest: str
    current_session: str
    c2_c3_reference: str
    source_route_privacy_reference: str
    runtime_policy_reference: str
    expected_branch: str
    expected_head: str
    runtime_hash: str
    _used: bool = field(default=False, init=False, repr=False)


def git(root, *args):
    # Fixed read-only commands. No fetch, hooks, pager or external diff/textconv.
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/var/empty', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'}
    return subprocess.check_output(['/usr/bin/git', '--no-pager', '-C', root, *args],
        env=env, stderr=subprocess.DEVNULL, timeout=10)


def snapshot(root):
    names = git(root, 'ls-files', '-z').split(b'\0')
    # Full tracked source before/after; no contents leave this process.
    files = {}
    for raw in names:
        if not raw:
            continue
        name = os.fsdecode(raw)
        path = Path(root) / name
        if not path.exists() and not path.is_symlink():
            files[name] = ('absent',)
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            files[name] = ('symlink', os.readlink(path))
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * w.LIMIT:
            fail()
        files[name] = (stat.S_IMODE(info.st_mode), hashlib.sha256(path.read_bytes()).hexdigest())
    return (git(root, 'rev-parse', 'HEAD').decode().strip(),
            git(root, 'branch', '--show-current').decode().strip(),
            git(root, 'ls-files', '--stage', '-z'),
            git(root, 'ls-files', '--others', '--exclude-standard', '-z'), files)


def no_implicit_instructions(job):
    # V2 read can inject nested AGENTS after an allowed file read even when
    # initial project discovery is disabled. Metadata only: never read them.
    root = Path(job.agent.root)
    for name in set(job.read_paths + job.write_paths):
        parent = (root / name).parent
        while parent != root:
            if os.path.lexists(parent / 'AGENTS.md'):
                fail()
            if root not in parent.parents:
                fail()
            parent = parent.parent


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        fail()


class Host:
    test_only = False

    def __init__(self, policy, approval, credential):
        self.policy = policy
        self.approval = approval
        self.credential = credential
        self.before = None
        self.token = None
        self.job_digest = None
        self.consumed = False
        self.final = None

    def admit(self, job, config_digest):
        a = self.approval
        root = self.policy.root
        if (type(a) is not Admission or a._used or a.job_digest != job.digest or
            any(type(x) is not str or not x.strip() for x in (a.current_session,
                a.c2_c3_reference, a.source_route_privacy_reference, a.runtime_policy_reference)) or
            job.agent.root != root or job.task.contract_digest != self.policy.contract or
            job.source_sha != a.expected_head or
            job.runtime.executable_sha256 != a.runtime_hash or
            not set(job.read_paths) <= set(self.policy.files) or
            not set(job.write_paths) <= set(self.policy.files) or
            config_digest != hashlib.sha256(canonical(w.configuration(job))).hexdigest()):
            fail()
        no_implicit_instructions(job)
        # Before server starts: private state was independently provisioned empty.
        # Only our disabled-service config may preexist; no copied history/plugins.
        state = Path(job.runtime.state_root)
        for name in ('home', 'cache', 'data', 'state', 'tmp'):
            if list((state / name).iterdir()):
                fail()
        allowed = state / 'config/service.json'
        if sorted((state / 'config').iterdir()) != [allowed] or allowed.is_symlink():
            fail()
        if json.loads(allowed.read_text()) != {'disabled': True}:
            fail()
        self.before = snapshot(root)
        if self.before[:2] != (a.expected_head, a.expected_branch) or self.before[3]:
            fail()
        if git(root, 'rev-parse', '--show-toplevel').decode().strip() != root:
            fail()
        if git(root, 'rev-parse', '--git-common-dir').decode().strip() != job.agent.common_dir:
            fail()
        self.job_digest = job.digest
        self.token = object()
        return self.token

    def consume(self, token, job):
        if token is not self.token or self.consumed or self.approval._used:
            fail()
        object.__setattr__(self.approval, '_used', True)
        self.consumed = True

    def check(self, token, job, stage):
        if token is not self.token:
            fail()
        no_implicit_instructions(job)
        now = snapshot(self.policy.root)
        if now[:4] != self.before[:4]:
            fail()
        changed = {p for p in now[4] if now[4][p] != self.before[4].get(p)}
        if stage != 'after' and changed:
            fail()
        if not changed <= set(job.write_paths):
            fail()
        # Source files must remain regular, same mode and within exact allowance.
        if any(len(now[4][p]) != 2 or now[4][p][0] != self.before[4][p][0] for p in changed):
            fail()

    def prepare_server(self, token, job, url, password, deadline):
        if token is not self.token or not self.consumed:
            fail()
        root = self.policy.root
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        auth = 'Basic ' + base64.b64encode(('opencode:' + password).encode()).decode()

        def request(route, body=None, secret=False):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail()
            req = urllib.request.Request(url + route,
                data=None if body is None else canonical(body),
                headers={'Authorization': auth, 'Content-Type': 'application/json'})
            with opener.open(req, timeout=min(10, remaining)) as response:
                # Credential response contains secrets: discard without reading.
                if secret:
                    return None
                data = response.read(w.LIMIT + 1)
                if len(data) > w.LIMIT:
                    fail()
                return json.loads(data)

        models = request('/api/model')['data']  # native plugin readiness barrier
        selected = [m for m in models if model_identity(m) == job.model]
        if len(selected) != 1 or selected[0].get('enabled') is not True:
            fail()
        prices = selected[0].get('cost')
        if not prices or any(p.get('input') != 0 or p.get('output') != 0 or
            p.get('cache', {}).get('read') != 0 or p.get('cache', {}).get('write') != 0 for p in prices):
            fail()
        agents = request('/api/agent')['data']
        agents = [a for a in agents if a.get('id') == w.AGENT]
        if len(agents) != 1:
            fail()
        agent = agents[0]
        model = agent.get('model', {})
        if (model_identity(model) != job.model or
            agent.get('steps') != job.max_steps):
            fail()
        rules = agent.get('permissions', [])
        positions = [i for i, r in enumerate(rules)
                     if r == dict(action='*', resource='*', effect='deny')]
        if not positions:
            fail()
        tail = rules[positions[-1] + 1:]
        expected = w.configuration(job)['permissions'][1:]
        if [r for r in tail if r.get('effect') != 'deny'] != expected:
            fail()
        # Native Permission.ask additionally confirms actual effective decisions.
        session = request('/api/session', dict(title='llmtool admission no inference', agent=w.AGENT,
            model={'providerID': 'opencode', 'id': job.model.split('/', 1)[1]},
            location={'directory': root}))['data']['id']
        for action, resource, effect in ([('read', p, 'allow') for p in job.read_paths] +
            [('edit', p, 'allow') for p in job.write_paths] +
            [('shell', '*', 'deny'), ('subagent', '*', 'deny'), ('execute', '*', 'deny'),
             ('grep', 'synthetic-deny-probe', 'deny'), ('glob', '**/*', 'deny'),
             ('read', '.', 'deny'),  # V2 read also supports directory enumeration.
             ('list', '*', 'deny'), ('search', '*', 'deny'),  # Extra policy-name probes.
             ('webfetch', 'https://example.invalid/', 'deny'),
             ('websearch', 'synthetic-deny-probe', 'deny'),
             ('external_directory', '*', 'deny'), ('read', 'UNAPPROVED.txt', 'deny'),
             ('read', '.env', 'deny'), ('read', '.git/config', 'deny')]):
            result = request('/api/session/' + session + '/permission',
                dict(action=action, resources=[resource], agent=w.AGENT))
            if result.get('data', {}).get('effect') != effect:
                fail()
        # Only the Integrator supplies ONE existing native credential in memory.
        # Never reads credential files or duplicates history. No prompt in this call.
        if type(self.credential) is not dict or not self.credential:
            fail()
        request('/api/credential', {'integrationID': 'opencode', 'value': self.credential,
                                    'activate': True}, secret=True)
        self.credential = None

    def finalize(self, token, code, launched):
        if token is not self.token:
            fail()
        self.credential = None
        self.final = {'code': code, 'launched': launched, 'one_use_consumed': self.consumed,
                      'job_digest': self.job_digest}


def provision_runtime(parent):
    """Integrator-only fresh non-project private state; no credentials/history copy."""
    parent = Path(parent)
    w.physical_directory(parent)
    state = parent / ('opencode-' + secrets.token_hex(16))
    state.mkdir(mode=0o700)
    for name in ('home', 'config', 'cache', 'data', 'state', 'tmp'):
        (state / name).mkdir(mode=0o700)
    service = state / 'config/service.json'
    fd = os.open(service, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'disabled': True}, stream)
    return state


def build(*, policy, run_id=None, assignment_session=None, expected_branch, expected_head,
          executable, executable_sha256, common_dir, private_parent, prompt, phase,
          write_paths, usage, budget, current_integrator_session, c2_c3_reference,
          source_route_privacy_reference, runtime_policy_reference, max_steps=8,
          task_binding=None, agent_binding=None):
    """Concrete constructors for ONE active Integrator invocation (data, not authority).

    run_id: existing 32-hex task run ID (same across authorized phases).
    assignment_session: trusted host assignment ID, not a fabricated backend
    session; the native result carries the separately observed backend session.
    expected_branch/head: actual registered new task root values, refreshed after
    each inspected model delta and host test. No historical checkout fallback.
    executable/executable_sha256: independently accepted actual executable pin,
    NOT a new self-generated grant. usage: cumulative known UsageLedger from prior
    stages; the first stage uses UsageLedger((),0,0) only when no inference has
    occurred. source_route_privacy_reference includes the approved prompt plus the
    actual source/diff screening for THIS invocation; refresh after every model,
    test or source change. Returns (WorkerJob, Admission); performs no launch.
    """
    if type(usage) is not UsageLedger:
        raise TypeError('existing usage ledger required')
    if expected_head != git(policy.root, 'rev-parse', 'HEAD').decode().strip():
        fail()
    if expected_branch != git(policy.root, 'branch', '--show-current').decode().strip():
        fail()
    if (task_binding is None) != (agent_binding is None):
        raise w.WorkerError('bindings must be provided as a pair')
    if task_binding is not None:
        if type(task_binding) is not TaskBinding or type(agent_binding) is not AgentBinding:
            raise w.WorkerError('invalid binding types')
        if task_binding.contract_digest != policy.contract:
            raise w.WorkerError('contract mismatch')
        if agent_binding.root != policy.root or agent_binding.common_dir != common_dir or agent_binding.branch != expected_branch or agent_binding.role is not Role.DEVELOPER:
            raise w.WorkerError('binding mismatch')
        if run_id is not None and run_id != task_binding.run_id:
            raise w.WorkerError('run_id mismatch')
        if assignment_session is not None and assignment_session != agent_binding.session_id:
            raise w.WorkerError('session mismatch')
        task = task_binding
        agent = agent_binding
    else:
        if run_id is None or assignment_session is None:
            raise w.WorkerError('legacy args required')
        task = TaskBinding(TASK_ID, run_id, 1, policy.contract)
        agent = AgentBinding(AGENT_ID, assignment_session, Role.DEVELOPER, policy.root, common_dir, expected_branch)
    state = provision_runtime(private_parent)
    job = w.WorkerJob(task=task, agent=agent, source_sha=expected_head, phase=phase,
        model=w.MODELS[phase], prompt=prompt, read_paths=tuple(sorted(policy.files)),
        write_paths=tuple(sorted(write_paths)),
        runtime=w.RuntimePin(executable, executable_sha256, PINNED_VERSION, str(state)),
        budget=budget, usage=usage, max_steps=max_steps)
    admission = Admission(job.digest, current_integrator_session, c2_c3_reference,
        source_route_privacy_reference, runtime_policy_reference, expected_branch,
        expected_head, executable_sha256)
    return job, admission


def run(job, policy, admission, one_native_credential):
    """Active Integrator invocation: returns (untrusted result, sanitized host facts).

    Construct WorkerJob from existing TaskBinding/AgentBinding/RunBudget/UsageLedger,
    exact prompt/scopes, per-phase fresh runtime (provision_runtime) and executable
    pin. Admission binds job.digest and existing independently accepted references.
    No launch until the caller holds actual grants. Reconcile result, host-fixed
    tests, usage and source before any separate regression/fix/pre-review phase.
    Performs no invocation of the next stage, tests or Git action. No auto-loop.
    """
    host = Host(policy, admission, one_native_credential)
    result = w.run_worker(job, host)
    return result, host.final


def run_with_receipt(job, policy, admission, one_native_credential, *, expect, receipt_store):
    """Active Integrator invocation wired to durable G3 ResultReceipt reconciliation.

    Atomically reserves one-shot launch in ``receipt_store`` via
    ``reserve_host_launch`` BEFORE invoking ``run()`` (refusing invalid ``expect``,
    unsafe/corrupt store, prior terminal replay, or in-flight/``UNKNOWN`` state with
    0 Worker launches). When admitted, executes ``run()`` once, independently
    observes post-run Git HEAD in ``policy.root`` (fail-closed to None on error),
    and calls ``record_host_receipt`` with mandatory ``expect`` (both ``task`` and
    ``agent`` bindings), same-invocation ``host.final`` (carrying ``job_digest``),
    and durable ``receipt_store``. Returns ``(result, host.final, receipt)``. A
    ``SUCCESS`` receipt is NOT acceptance or merge authorization
    (``receipt.accepted`` is False).
    """
    from .opencode_result_receipt import record_host_receipt, reserve_host_launch
    refused = reserve_host_launch(job=job, expect=expect, store_path=receipt_store)
    if refused is not None:
        usage = job.usage if type(getattr(job, 'usage', None)) is UsageLedger else UsageLedger((), 0, 0)
        return (w.WorkerResult('OPENCODE_ATTEMPT_ALREADY_USED', 'BLOCKED', None, None,
                               usage, False, (), None, 'NOT_VERIFIED'),
                None, refused)
    result, final = run(job, policy, admission, one_native_credential)
    try:
        observed_head = git(policy.root, 'rev-parse', 'HEAD').decode().strip()
    except Exception:
        observed_head = None
    receipt = record_host_receipt(job=job, result=result, host_final=final,
                                  observed_head=observed_head, expect=expect,
                                  store_path=receipt_store)
    return result, final, receipt

