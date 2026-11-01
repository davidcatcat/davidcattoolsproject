"""S1b A bounded local facts; actual host Session attestation awaits B.

Git reads reuse A5 environment/config hardening. Additional fixed read primitives
are deliberately local-only, bounded and individually tested. A local snapshot
is not an authentic host, registered baseline, Session or production grant.
"""
import hashlib
import os
import selectors
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import git_intel
from .task_lifecycle import Role, utc_time
from .task_review import reject, digest, scope_path, canonical, screen, SOURCE_LIMIT
from .review_origins import REVIEW_ORIGINS
from .providers.cli_lifecycle import terminate

MAX_METADATA = 1024 * 1024
MAX_TOTAL = 2 * MAX_METADATA
FIXED_READS = frozenset((git_intel.ROOT, git_intel.HEAD, git_intel.BRANCH, git_intel.STATUS,
    ('rev-parse','--path-format=absolute','--git-common-dir'),
    ('rev-parse','--absolute-git-dir'), ('config','--get','remote.origin.url'),
    ('worktree','list','--porcelain'), ('ls-files','--stage','-z'),
    ('ls-files','--others','--exclude-standard','-z'),
    ('ls-files','--others','--ignored','--exclude-standard','-z'),
    ('config','--local','--includes','--null','--name-only','--get-regexp',
     r'^(core\.(hookspath|fsmonitor|sshcommand|gitproxy)|filter\.|url\.|include\.|includeif\.|remote\..*\.promisor|extensions\.partialclone)')))


def physical_directory(path):
    path = Path(path)
    if not path.is_absolute() or str(path) != os.path.abspath(path):
        reject('REVIEW_ROOT_INVALID')
    for part in (path,*path.parents):
        info = part.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0,os.getuid()) or
                (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX))):
            reject('REVIEW_ROOT_INVALID')
    return path.stat()


class _LocalGit:
    def __init__(self, root):
        self.root = root
        self.started = time.monotonic()
        self.total = self.reads = 0

    def read(self, args):
        args = tuple(args)
        hex_ref = lambda value: type(value) is str and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',value)
        prefix = ('diff',) + git_intel.DIFF_OPTIONS
        diff = (len(args) == len(prefix)+3 and args[:len(prefix)] == prefix and
                hex_ref(args[-3]) and hex_ref(args[-2]) and args[-1] == '--')
        names = (len(args) == 8 and args[:5] == ('diff','--name-only','-z','--no-ext-diff','--no-textconv') and
                 hex_ref(args[5]) and hex_ref(args[6]) and args[7] == '--')
        blob = (len(args) == 3 and args[:2] == ('cat-file','blob') and hex_ref(args[2]))
        tree = (len(args) == 5 and args[:2] == ('ls-tree','-z') and hex_ref(args[2]) and args[3] == '--')
        # Bundle metadata only: complete, non-renaming path sets and immutable
        # trees. Never accept caller argv or broaden the raw-source export path.
        bundle_names = (len(args) == 9 and args[:6] ==
            ('diff','--name-only','-z','--no-ext-diff','--no-textconv','--no-renames') and
            hex_ref(args[6]) and hex_ref(args[7]) and args[8] == '--')
        bundle_tree = (len(args) == 5 and args[:4] ==
                       ('ls-tree','-r','--full-tree','-z') and hex_ref(args[4]))
        ancestor = (len(args) == 4 and args[:2] == ('merge-base','--is-ancestor') and
                    hex_ref(args[2]) and hex_ref(args[3]))
        if tree:
            scope_path(args[4], (args[4],))
        if (args not in FIXED_READS and not (diff or names or blob or tree or
                bundle_names or bundle_tree or ancestor)) or self.reads >= 32:
            reject('REVIEW_GIT_READ_DENIED')
        self.reads += 1
        remaining = 30 - (time.monotonic()-self.started)
        if remaining <= 0:
            reject('REVIEW_COLLECTOR_TIMEOUT')
        # Fixed system executable; no PATH-selected git, shell or inherited overrides.
        executable = '/usr/bin/git'
        info = os.stat(executable)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            reject('REVIEW_RUNTIME_INVALID')
        env = git_intel._environment()
        env['PATH'] = '/usr/bin:/bin'
        argv = [executable,'--no-pager','--no-replace-objects']
        for item in git_intel.SAFE_CONFIG:
            argv += ['-c',item]
        argv += list(args)
        proc = subprocess.Popen(argv,cwd=self.root,env=env,stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False,close_fds=True,start_new_session=True)
        output = bytearray()
        errors = 0
        selector = selectors.DefaultSelector()
        deadline = time.monotonic()+min(10,remaining)
        try:
            for stream, kind in ((proc.stdout,'out'),(proc.stderr,'err')):
                os.set_blocking(stream.fileno(),False)
                selector.register(stream,selectors.EVENT_READ,kind)
            while selector.get_map():
                left = deadline-time.monotonic()
                if left <= 0:
                    reject('REVIEW_COLLECTOR_TIMEOUT')
                for key,_ in selector.select(min(left,0.1)):
                    chunk = os.read(key.fileobj.fileno(),8192)
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    self.total += len(chunk)
                    if self.total > MAX_TOTAL:
                        reject('REVIEW_COLLECTOR_TOO_LARGE')
                    if key.data == 'out':
                        limit = SOURCE_LIMIT if diff or blob else MAX_METADATA
                        if len(output)+len(chunk) > limit:
                            if diff or blob:
                                reject('REVIEW_PACKET_TOO_LARGE',component='diff_source',limit=SOURCE_LIMIT)
                            reject('REVIEW_COLLECTOR_TOO_LARGE')
                        output.extend(chunk)
                    else:
                        errors += len(chunk)
                        if errors > 64*1024:
                            reject('REVIEW_COLLECTOR_TOO_LARGE')
            left = deadline-time.monotonic()
            if left <= 0:
                reject('REVIEW_COLLECTOR_TIMEOUT')
            code = proc.wait(timeout=left)
            if ancestor and code == 1 and not errors:
                reject('BUNDLE_BASE_NOT_ANCESTOR')
            no_match = args[0] == 'config' and '--get-regexp' in args and code == 1
            if errors or (code and not no_match):
                reject('REVIEW_GIT_READ_FAILED')
            return bytes(output)
        finally:
            selector.close()
            # Only this owned read child's process group, never a peer PID.
            if proc.returncode is None:
                terminate(proc)
            proc.stdout.close(); proc.stderr.close()


@dataclass(frozen=True)
class LocalFacts:
    root: str
    branch: str
    source_sha: str
    common_dir: str
    repository: str
    index_digest: str
    inventory_digest: str
    registration_digest: str
    runtime_digest: str
    clean_tracked: bool

    @property
    def digest(self):
        return digest(self.__dict__)


def collect_local_facts(root, *, reader=None):
    """Observe a physical own import root, without claiming attestation/authority."""
    root = Path(root)
    before = physical_directory(root)
    # Current process imports are observations; a peer-selected executable is never run.
    import llmtool
    imported = Path(llmtool.__file__).absolute()
    prefix = Path(sys.prefix).absolute()
    if (root not in imported.parents or root not in prefix.parents or
            not str(sys.executable).startswith(str(prefix) + '/')):
        reject('REVIEW_RUNTIME_INVALID')
    physical_directory(prefix)
    physical_directory(imported.parent)
    runtime = {'executable':sys.executable,'prefix':sys.prefix,'import':str(imported)}
    from .task_lifecycle_cli import read_task_file
    config,_ = read_task_file(str(prefix/'pyvenv.cfg'))
    if len(config.encode()) > 4096:
        reject('REVIEW_RUNTIME_INVALID')
    runtime['pyvenv_digest'] = hashlib.sha256(config.encode()).hexdigest()
    reader = _LocalGit(root) if reader is None else reader
    if Path(reader.root) != root:
        reject('REVIEW_ROOT_INVALID')
    text = lambda args: reader.read(args).decode('utf-8').rstrip('\n')
    if text(git_intel.ROOT) != str(root):
        reject('REVIEW_ROOT_INVALID')
    if reader.read(('config','--local','--includes','--null','--name-only','--get-regexp',
                   r'^(core\.(hookspath|fsmonitor|sshcommand|gitproxy)|filter\.|url\.|include\.|includeif\.|remote\..*\.promisor|extensions\.partialclone)')):
        reject('REVIEW_CONFIG_UNSAFE')
    branch = text(git_intel.BRANCH)
    if not branch or branch in ('main','automation/ai-loop'):
        reject('REVIEW_BRANCH_INVALID')
    head = text(git_intel.HEAD)
    common = text(('rev-parse','--path-format=absolute','--git-common-dir'))
    physical_directory(common)
    gitdir = text(('rev-parse','--absolute-git-dir'))
    physical_directory(gitdir)
    # Known Git mutation lock paths; no unbounded traversal through shared user data.
    if any(Path(p).exists() for p in (gitdir+'/index.lock',gitdir+'/HEAD.lock',
                                    common+'/packed-refs.lock',common+'/config.lock',
                                    common+'/refs/heads/'+branch+'.lock')):
        reject('REVIEW_GIT_LOCKED')
    origin = text(('config','--get','remote.origin.url'))
    if origin not in REVIEW_ORIGINS:
        reject('REVIEW_REPOSITORY_INVALID')
    registration = reader.read(('worktree','list','--porcelain'))
    blocks = registration.decode().strip().split('\n\n')
    own = [b for b in blocks if b.splitlines()[0] == 'worktree '+str(root)]
    if len(own) != 1 or '\nlocked' not in own[0] or '\nbranch refs/heads/'+branch not in own[0]:
        reject('REVIEW_REGISTRATION_INVALID')
    if sum('\nbranch refs/heads/'+branch+'\n' in b+'\n' for b in blocks) != 1:
        reject('REVIEW_BRANCH_INVALID')
    status_raw = reader.read(git_intel.STATUS)
    status = git_intel.parse_status(status_raw)
    index = reader.read(('ls-files','--stage','-z'))
    untracked = reader.read(('ls-files','--others','--exclude-standard','-z'))
    ignored = reader.read(('ls-files','--others','--ignored','--exclude-standard','-z'))
    if (text(git_intel.HEAD) != head or text(git_intel.BRANCH) != branch or
            reader.read(('ls-files','--stage','-z')) != index or
            reader.read(git_intel.STATUS) != status_raw or
            (root.stat().st_dev,root.stat().st_ino) != (before.st_dev,before.st_ino)):
        reject('REVIEW_SOURCE_DRIFT')
    return LocalFacts(str(root),branch,head,common,'davidcatcat/davidcattoolsproject',
        hashlib.sha256(index).hexdigest(),hashlib.sha256(untracked+b'\0'+ignored).hexdigest(),
        hashlib.sha256(registration).hexdigest(),digest(runtime),
        not (status.staged or status.unstaged or status.conflicted))


def collect_source_data(root, base_sha, source_sha, scope, *, reader=None):
    """Read complete exact-commit diff + original base/head UTF-8 source blobs.

    Local DATA only: this helper neither authenticates caller refs nor exports.
    A future B host must independently pin refs/scope and grant the route.
    """
    physical_directory(root)
    reader = _LocalGit(root) if reader is None else reader
    if Path(reader.root) != Path(root):
        reject('REVIEW_ROOT_INVALID')
    head = lambda: reader.read(git_intel.HEAD).decode().strip()
    if head() != source_sha:
        reject('REVIEW_SOURCE_DRIFT')
    paths = reader.read(('diff','--name-only','-z','--no-ext-diff','--no-textconv',base_sha,source_sha,'--'))
    paths = tuple(p.decode('utf-8') for p in paths.split(b'\0') if p)
    if not 1 <= len(paths) <= 32 or len(set(paths)) != len(paths):
        reject('REVIEW_PACKET_BINDING_INVALID')
    for path in paths:
        scope_path(path,scope)
    diff = reader.read(('diff',)+git_intel.DIFF_OPTIONS+(base_sha,source_sha,'--')).decode('utf-8')
    screen(diff)
    if 'GIT binary patch' in diff or 'Binary files ' in diff:
        reject('REVIEW_PACKET_BINDING_INVALID')
    sources = []
    for path in paths:
        scope_path(path,scope)
        content = {}
        for label,commit in (('base',base_sha),('source',source_sha)):
            raw = reader.read(('ls-tree','-z',commit,'--',path)).split(b'\0')
            entries = [e for e in raw if e]
            if not entries:
                content[label] = None
                continue
            if len(entries) != 1:
                reject('REVIEW_PACKET_BINDING_INVALID')
            metadata, found = entries[0].split(b'\t',1)
            mode,kind,blob = metadata.decode().split()
            if mode not in ('100644','100755') or kind != 'blob' or found.decode() != path:
                reject('REVIEW_PACKET_BINDING_INVALID')
            data = reader.read(('cat-file','blob',blob)).decode('utf-8')
            screen(data)
            content[label] = {'commit':commit,'blob':blob,'text':data,
                              'sha256':hashlib.sha256(data.encode()).hexdigest()}
        text = canonical(content).decode()
        sources.append({'path':path,'content':text,'sha256':hashlib.sha256(text.encode()).hexdigest()})
        if len(diff.encode())+sum(len(s['content'].encode()) for s in sources) > SOURCE_LIMIT:
            reject('REVIEW_PACKET_TOO_LARGE',component='diff_source',limit=SOURCE_LIMIT)
    if head() != source_sha:
        reject('REVIEW_SOURCE_DRIFT')
    return {'content':diff,'sha256':hashlib.sha256(diff.encode()).hexdigest(),
            'changed_paths':list(paths),'complete':True},sources


def collect_bundle_manifest(root, base_sha, source_sha, scope, forbidden=(), *, reader=None):
    """Complete local blob references only; no raw source/diff or export waiver.

    Two bounded tree reads avoid a per-path read budget expansion. The original
    privacy-screened collect_source_data route remains unchanged.
    """
    physical_directory(root)
    reader = _LocalGit(root) if reader is None else reader
    if Path(reader.root) != Path(root):
        reject('REVIEW_ROOT_INVALID')
    for ref in (base_sha, source_sha):
        if type(ref) is not str or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', ref):
            reject('BUNDLE_BASE_INVALID')
    if reader.read(git_intel.HEAD).decode().strip() != source_sha:
        reject('REVIEW_SOURCE_DRIFT')
    reader.read(('merge-base','--is-ancestor',base_sha,source_sha))
    raw = reader.read(('diff','--name-only','-z','--no-ext-diff','--no-textconv',
                       '--no-renames',base_sha,source_sha,'--'))
    paths = [p.decode('utf-8') for p in raw.split(b'\0') if p]
    if not 1 <= len(paths) <= 32 or len(set(paths)) != len(paths):
        reject('BUNDLE_SCOPE_INVALID')
    for path in paths:
        scope_path(path, scope)
        if any(path == f or path.startswith(f+'/') for f in forbidden):
            reject('BUNDLE_SCOPE_INVALID')
    trees = []
    for commit in (base_sha, source_sha):
        entries = {}
        for entry in reader.read(('ls-tree','-r','--full-tree','-z',commit)).split(b'\0'):
            if not entry:
                continue
            meta, name = entry.split(b'\t',1)
            path = name.decode('utf-8')
            if path in entries:
                reject('BUNDLE_SCOPE_INVALID')
            mode, kind, blob = meta.decode('ascii').split()
            entries[path] = (mode, kind, blob)
        tree = {}
        for path in paths:
            if path not in entries:
                tree[path] = None
            else:
                mode, kind, blob = entries[path]
                if (mode not in ('100644','100755') or kind != 'blob' or
                        not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',blob)):
                    reject('BUNDLE_SCOPE_INVALID')
                tree[path] = {'mode':mode, 'blob':blob}
        trees.append(tree)
    if reader.read(git_intel.HEAD).decode().strip() != source_sha:
        reject('REVIEW_SOURCE_DRIFT')
    return [{'path':p,'base':trees[0][p],'source':trees[1][p]} for p in sorted(paths)]


def verify_bundle_facts(facts, reader):
    """Recheck local observations with the SAME 32-read/30s budget."""
    text = lambda args: reader.read(args).decode('utf-8').rstrip('\n')
    index = reader.read(('ls-files','--stage','-z'))
    untracked = reader.read(('ls-files','--others','--exclude-standard','-z'))
    ignored = reader.read(('ls-files','--others','--ignored','--exclude-standard','-z'))
    registration = reader.read(('worktree','list','--porcelain'))
    status = git_intel.parse_status(reader.read(git_intel.STATUS))
    if (text(git_intel.HEAD) != facts.source_sha or text(git_intel.BRANCH) != facts.branch or
            hashlib.sha256(index).hexdigest() != facts.index_digest or
            hashlib.sha256(untracked+b'\0'+ignored).hexdigest() != facts.inventory_digest or
            hashlib.sha256(registration).hexdigest() != facts.registration_digest or
            status.staged or status.unstaged or status.conflicted):
        reject('REVIEW_SOURCE_DRIFT')


@dataclass(frozen=True)
class Observation:
    """Host observation data; constructor alone supplies no attestation."""
    repository: str
    agents: tuple
    authored_by: tuple
    source_sha: str
    task: object
    epoch: int
    events: tuple
    observed_at: str
    monotonic_at: float
    runtime_paths: tuple  # Own-root executable/prefix/import provenance fixture.
    host_record: str
    identity_attested: bool = True
    writers_clean: bool = True
    locks_clear: bool = True
    baseline_valid: bool = True
    config_safe: bool = True
    registered_locked: bool = True
    authority_valid: bool = True

    @property
    def state_digest(self):
        return digest({'repository':self.repository,'agents':[a.to_dict() for a in self.agents],
            'authors':[a.to_dict() for a in self.authored_by],'sha':self.source_sha,
            'task':self.task.to_dict(),'epoch':self.epoch,'events':[e.to_dict() for e in self.events],
            'runtime':self.runtime_paths,'host_record':self.host_record,
            'guards':[self.identity_attested,self.writers_clean,self.locks_clear,self.baseline_valid,
                      self.config_safe,self.registered_locked,self.authority_valid]})

    @property
    def digest(self):
        return digest({'state_digest':self.state_digest,'observed_at':self.observed_at,
                       'monotonic_at':self.monotonic_at})

    def validate(self, expect, events, now, mono):
        if self.authority_valid is not True:
            reject('REVIEW_GRANT_NOT_AUTHORIZED')
        if (self.agents != expect.agents or self.authored_by != expect.authored_by or
                self.source_sha != expect.source_sha or self.task != expect.task or
                self.epoch != expect.epoch or self.events != events or
                self.repository != 'davidcatcat/davidcattoolsproject' or not self.host_record or
                any(value is not True for value in (self.identity_attested,self.writers_clean,
                    self.locks_clear,self.baseline_valid,self.config_safe,self.registered_locked))):
            reject('REVIEW_IDENTITY_NOT_ATTESTED')
        developers = [a for a in self.agents if a.role is Role.DEVELOPER]
        if len({a.branch for a in developers}) != len(developers) or any(
                a.branch in ('main','automation/ai-loop') or a.branch == r.branch
                for a in developers for r in self.agents if r.role is Role.REVIEWER):
            reject('REVIEW_BRANCH_INVALID')
        if not developers or len(self.runtime_paths) != 3 or any(
                not p.startswith(developers[0].root+'/') for p in self.runtime_paths):
            reject('REVIEW_RUNTIME_INVALID')
        age = (utc_time(now)-utc_time(self.observed_at)).total_seconds()
        if not 0 <= age <= 5 or not 0 <= mono-self.monotonic_at <= 5:
            reject('REVIEW_OBSERVATION_STALE')
