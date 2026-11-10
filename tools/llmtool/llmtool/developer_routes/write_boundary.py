"""Operating-system write boundaries for direct Developer CLI processes.

Apple deprecates ``sandbox-exec``; a real Mac acceptance run remains required.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile


class WriteBoundary(ABC):
    @abstractmethod
    def prepare(self, root, write_paths, state_dirs=()):
        """Return wrapper argv prefix and the digest of its write policy."""

    @abstractmethod
    def verify(self, root, write_paths, state_dirs=(), *, runner):
        """Probe the actual wrapper and return bounded verification evidence."""


def _safe_relative(path):
    if (type(path) is not str or not path or path.startswith('/') or '\\' in path or
            any(ord(char) < 32 or ord(char) == 127 for char in path) or
            any(part in ('', '.', '..') for part in path.split('/'))):
        raise ValueError('invalid relative path')
    return path


def _sbpl_quote(value):
    if type(value) is not str or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('invalid profile path')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


class UnsupportedBoundary(WriteBoundary):
    def prepare(self, root, write_paths, state_dirs=()):
        return [], None

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        return {'verified': False, 'policy_sha256': None, 'probes': []}


class SandboxExecBoundary(WriteBoundary):
    """macOS sandbox-exec wrapper restricted to configured writable paths."""

    def __init__(self, *, executable='/usr/bin/sandbox-exec', denied_dirs=(),
                 tmpdir=None, allow_gw_agent_tmp=False, allow_claude_tmp=False):
        self.executable = executable
        self.denied_dirs = tuple(denied_dirs)
        self.tmpdir = tmpdir
        if type(allow_gw_agent_tmp) is not bool:
            raise ValueError('invalid gw-agent temporary allowance')
        self.allow_gw_agent_tmp = allow_gw_agent_tmp
        if type(allow_claude_tmp) is not bool:
            raise ValueError('invalid Claude temporary allowance')
        self.allow_claude_tmp = allow_claude_tmp
        self._prepared = None

    def with_gw_agent_tmp(self):
        """Return an unprepared copy that allows the gateway's exact temp names."""
        return SandboxExecBoundary(executable=self.executable, denied_dirs=self.denied_dirs,
                                   tmpdir=self.tmpdir, allow_gw_agent_tmp=True)

    def _policy(self, root, write_paths, state_dirs):
        root = Path(root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('invalid root')
        if type(write_paths) not in (list, tuple) or type(state_dirs) not in (list, tuple):
            raise ValueError('invalid path collection')
        git_pointer = root / '.git'
        if git_pointer.is_symlink():
            git_path = (git_pointer.parent / os.readlink(git_pointer)).resolve(strict=True)
        elif git_pointer.exists():
            git_path = git_pointer.resolve(strict=True)
        else:
            raise ValueError('missing git pointer')
        # Git's common directory is normally an ancestor/peer of the task root.
        # The tasks directory is an ancestor of this D6 root, so its presence
        # must not accidentally deny the authorized root subtree. Relative
        # paths cannot name a sibling task; Git and configured paths are denied.
        denied = [git_path]
        try:
            common = Path(os.fsdecode(__import__(
                'subprocess').check_output(['/usr/bin/git', '-C', str(root),
                    'rev-parse', '--path-format=absolute', '--git-common-dir'],
                    stdin=__import__('subprocess').DEVNULL, stderr=__import__('subprocess').DEVNULL,
                    env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1',
                         'GIT_CONFIG_GLOBAL': '/dev/null'})).strip()).resolve(strict=True)
            denied.append(common)
        except Exception:
            # A worktree whose Git metadata cannot be read cannot be isolated.
            raise ValueError('git common directory unavailable') from None
        home = Path.home().resolve(strict=True)
        for item in self.denied_dirs:
            if (type(item) is not str or not item or Path(item).is_absolute() or
                    any(part in ('', '.', '..') for part in item.split('/')) or
                    '\\' in item or any(ord(char) < 32 or ord(char) == 127 for char in item)):
                raise ValueError('invalid denied directory')
            denied.append((home / item).resolve(strict=False))
        allowed = []
        for item in write_paths:
            rel = _safe_relative(item)
            # Refuse write paths with any .git component regardless of case.
            if any(part.casefold() == '.git' for part in rel.split('/')):
                raise ValueError('writable path intersects a denied path')
            target = root.joinpath(*rel.split('/'))
            # Existing symlinks can escape before sandbox evaluation; reject all.
            current = root
            for part in rel.split('/'):
                current = current / part
                if current.is_symlink():
                    raise ValueError('symlinked writable path')
            absolute = target.resolve(strict=False)
            if absolute == root or any(absolute == blocked or blocked in absolute.parents
                                       for blocked in denied):
                raise ValueError('writable path intersects a denied path')
            allowed.append(str(absolute))
        states = []
        for item in state_dirs:
            path = Path(item)
            if not path.is_absolute():
                raise ValueError('state path must be absolute')
            path = path.resolve(strict=False)
            temp_root = Path(self.tmpdir or os.environ.get('TMPDIR') or tempfile.gettempdir()).resolve()
            if path == temp_root or temp_root not in path.parents:
                raise ValueError('state path outside private temporary directory')
            if any(path == blocked or blocked in path.parents or path in blocked.parents
                   for blocked in denied):
                raise ValueError('state path intersects a denied path')
            info = path.stat()
            if info.st_mode & 0o077:
                raise ValueError('state directory must be private')
            states.append(str(path))
        return root, sorted(set(allowed)), sorted(set(states))

    @staticmethod
    def _profile(paths, state_paths=(), gw_tmp_parents=(), claude_tmp_paths=()):
        rows = [
            '(version 1)',
            '(deny default)',
            '(allow process*)',
            '(allow sysctl-read)',
            '(allow mach*)',
            '(allow ipc*)',
            '(allow signal)',
            '(allow network*)',
            '(allow file-read*)',
            '(deny file-write*)',
        ]
        rows.extend('(allow file-write* (literal ' + _sbpl_quote(path) + '))' for path in paths)
        rows.extend('(allow file-write* (subpath ' + _sbpl_quote(path) + '))'
                    for path in state_paths)
        for parent in gw_tmp_parents:
            literal_parent = ''.join(
                char if char.isalnum() or char in '/_-' else '[' + char + ']'
                for char in parent.rstrip('/') + '/')
            suffix = '.gw-agent-tmp-' + '[0-9a-f]' * 16
            pattern = '^' + literal_parent + suffix + '$'
            rows.append('(allow file-write* (regex #' + _sbpl_quote(pattern) + '))')
        for path in claude_tmp_paths:
            escaped = ''.join(
                char if char.isalnum() or char in '/_-' else '[' + char + ']'
                for char in path)
            suffix = '[.]tmp[.][0-9]+[.]' + '[0-9a-f]' * 12
            pattern = '^' + escaped + suffix + '$'
            # An abandoned CLI temp file remains outside write_paths and blocks the run.
            rows.append('(allow file-write* (regex #' + _sbpl_quote(pattern) + '))')
        rows.extend('(allow file-write* (literal ' + _sbpl_quote(path) + '))'
                    for path in ('/dev/null', '/dev/tty'))
        return '\n'.join(rows)

    @staticmethod
    def _gw_tmp_parents(root, write_paths):
        root = Path(root).resolve(strict=True)
        return sorted({str((root / Path(path).parent).resolve(strict=False))
                       for path in write_paths})

    def prepare(self, root, write_paths, state_dirs=()):
        if not os.path.isfile(self.executable) or not os.access(self.executable, os.X_OK):
            raise OSError('sandbox-exec unavailable')
        root, paths, states = self._policy(root, write_paths, state_dirs)
        gw_tmp_parents = (self._gw_tmp_parents(root, write_paths)
                          if self.allow_gw_agent_tmp else ())
        claude_tmp_paths = tuple(paths) if self.allow_claude_tmp else ()
        profile = self._profile(paths, states, gw_tmp_parents, claude_tmp_paths)
        digest = hashlib.sha256(profile.encode('utf-8')).hexdigest()
        self._prepared = (str(root), tuple(write_paths), tuple(state_dirs), paths, states, profile, digest)
        return [self.executable, '-p', profile], digest

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        if self._prepared is None or not callable(runner):
            return {'verified': False, 'policy_sha256': None, 'probes': []}
        prepared_root, prepared_paths, prepared_states, paths, states, profile, digest = self._prepared
        try:
            current_root, current_paths, current_states = self._policy(root, write_paths, state_dirs)
        except (OSError, ValueError, RuntimeError):
            return {'verified': False, 'policy_sha256': digest, 'probes': []}
        if (str(current_root), tuple(write_paths), tuple(state_dirs), current_paths,
                current_states) != (prepared_root, prepared_paths, prepared_states, paths, states):
            return {'verified': False, 'policy_sha256': digest, 'probes': []}
        gw_tmp_parents = (self._gw_tmp_parents(root, write_paths)
                          if self.allow_gw_agent_tmp else ())
        claude_tmp_paths = tuple(paths) if self.allow_claude_tmp else ()
        # The same kernel wrapper must allow a write at an authorized target
        # and deny root, Git, sibling-task, configured denied locations, and
        # hard links from outside the boundary into an allowed literal path.
        probes = []
        targets = [str(Path(root).resolve() / item) for item in write_paths]
        denied = [str(Path(root).resolve() / '.llmtool-boundary-probe')]
        git = Path(root).resolve() / '.git'
        if git.is_file():
            denied.append(str(git.resolve().parent / '.llmtool-git-probe'))
        elif git.is_dir():
            denied.append(str(git / '.llmtool-boundary-probe'))
        denied.append(str(Path(root).resolve().parent / '.llmtool-task-parent-probe'))
        denied.extend(str((Path.home() / item).resolve(strict=False) / '.llmtool-denied-probe')
                      for item in self.denied_dirs)
        python = shutil.which('python3') or '/usr/bin/python3'
        for path, expected in [(p, True) for p in targets[:1]] + [(p, False) for p in denied]:
            program = ('from pathlib import Path\n'
                       'p=Path(' + repr(path) + ')\n'
                       'old=p.read_bytes() if p.exists() else None\n'
                       'try:\n p.write_bytes(b"probe")\nfinally:\n'
                       ' if old is not None: p.write_bytes(old)\n'
                       ' else: p.unlink(missing_ok=True)\n')
            code = 'import sys\n' + program
            # The probe itself is the only shell payload and is constant apart
            # from safely repr-encoded local paths.
            try:
                rc, out, err = runner([self.executable, '-p', profile, python, '-c', code],
                                      str(root), {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                                  'PYTHONDONTWRITEBYTECODE': '1'}, 10)
            except Exception:
                probes.append({'expected_write': expected, 'passed': False})
                continue
            passed = (type(rc) is int and ((rc == 0) if expected else (rc != 0)))
            probes.append({'expected_write': expected, 'passed': passed})
        if gw_tmp_parents:
            for parent in gw_tmp_parents:
                allowed_tmp = str(Path(parent) / '.gw-agent-tmp-0123456789abcdef')
                denied_tmp = str(Path(parent) / '.gw-agent-tmp-not-hex')
                for path, expected in ((allowed_tmp, True), (denied_tmp, False)):
                    code = ('from pathlib import Path\n'
                            'p=Path(' + repr(path) + ')\n'
                            'p.write_bytes(b"probe")\n'
                            'p.unlink()\n')
                    try:
                        rc, out, err = runner(
                            [self.executable, '-p', profile, python, '-c', code],
                            str(root), {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                        'PYTHONDONTWRITEBYTECODE': '1'}, 10)
                        passed = type(rc) is int and ((rc == 0) if expected else (rc != 0))
                    except Exception:
                        passed = False
                    probes.append({'expected_write': expected, 'operation': 'gw_agent_tmp',
                                   'passed': passed})
        if claude_tmp_paths:
            for target in claude_tmp_paths:
                parent = str(Path(target).parent)
                allowed_tmp = target + '.tmp.123.0123456789ab'
                allowed_program = (
                    'import os,stat\nfrom pathlib import Path\n'
                    'p=Path(' + repr(target) + ')\n'
                    't=Path(' + repr(allowed_tmp) + ')\n'
                    'old=p.read_bytes() if p.exists() else None\n'
                    'mode=stat.S_IMODE(p.stat().st_mode) if old is not None else None\n'
                    'try:\n t.write_bytes(b"probe")\n os.replace(t,p)\n'
                    'finally:\n'
                    ' if t.exists(): t.unlink()\n'
                    ' if old is not None: p.write_bytes(old); p.chmod(mode)\n'
                    ' elif p.exists(): p.unlink()\n')
                try:
                    rc, out, err = runner([self.executable, '-p', profile, python, '-c',
                                           allowed_program], str(root),
                                          {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                           'PYTHONDONTWRITEBYTECODE': '1'}, 10)
                    passed = type(rc) is int and rc == 0
                except Exception:
                    passed = False
                probes.append({'expected_write': True, 'operation': 'claude_tmp_rename',
                               'path': target, 'passed': passed})
                # The real macOS probe showed its case-insensitive match cannot deny an uppercase suffix.
                invalid = (target + '.tmp.123.0123456789a',
                           target + '.tmp.x.0123456789ab',
                           target + '.tmp.123.0123456789ab.extra',
                           str(Path(parent) / '.llmtool-other.tmp.123.0123456789ab'),
                           str(Path(root).resolve() / '.llmtool-other.tmp.123.0123456789ab'))
                for path in invalid:
                    code = ('import sys\nfrom pathlib import Path\n'
                            'p=Path(' + repr(path) + ')\n'
                            'try:\n p.write_bytes(b"probe")\n'
                            'except PermissionError:\n sys.exit(3)\n'
                            'except Exception:\n sys.exit(4)\n'
                            'try:\n p.unlink(missing_ok=True)\n'
                            'except Exception:\n sys.exit(4)\n'
                            'sys.exit(0)\n')
                    try:
                        rc, out, err = runner([self.executable, '-p', profile, python, '-c', code],
                                              str(root), {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                                          'PYTHONDONTWRITEBYTECODE': '1'}, 10)
                        passed = type(rc) is int and rc == 3
                    except Exception:
                        passed = False
                    probes.append({'expected_write': False, 'operation': 'claude_tmp_name',
                                   'path': path, 'passed': passed})
                source = None
                hardlink_tmp = target + '.tmp.123.0123456789ab'
                try:
                    fd, source = tempfile.mkstemp(
                        prefix='llmtool-claude-hardlink-',
                        dir=str(Path(root).resolve().parent))
                    os.write(fd, b'probe-source')
                    os.close(fd)
                    program = ('import os,sys\nfrom pathlib import Path\n'
                               's=Path(' + repr(source) + ')\n'
                               'p=Path(' + repr(hardlink_tmp) + ')\n'
                               'try:\n os.link(s,p)\n'
                               'except PermissionError:\n sys.exit(3)\n'
                               'except OSError:\n sys.exit(4)\n'
                               'try:\n p.unlink()\n'
                               'except OSError:\n sys.exit(4)\n'
                               'sys.exit(7)\n')
                    rc, out, err = runner([self.executable, '-p', profile, python, '-c', program],
                                          str(root), {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                                      'PYTHONDONTWRITEBYTECODE': '1'}, 10)
                    passed = type(rc) is int and rc == 3
                except Exception:
                    passed = False
                finally:
                    if source is not None:
                        try:
                            os.unlink(source)
                        except OSError:
                            pass
                probes.append({'expected_write': False, 'operation': 'claude_tmp_hardlink',
                               'path': hardlink_tmp, 'passed': passed})
        if targets:
            target = targets[0]
            source = None
            try:
                fd, source = tempfile.mkstemp(prefix='llmtool-hardlink-probe-')
                os.write(fd, b'probe-source')
                os.close(fd)
                program = (
                    'import os,stat,sys\n'
                    'from pathlib import Path\n'
                    'p=Path(' + repr(target) + ')\n'
                    's=Path(' + repr(source) + ')\n'
                    'old=p.read_bytes() if p.is_file() else None\n'
                    'mode=stat.S_IMODE(p.stat().st_mode) if old is not None else None\n'
                    'if old is not None: p.unlink()\n'
                    'linked=False\n'
                    'try:\n os.link(s,p); linked=True\n'
                    'except OSError:\n pass\n'
                    'finally:\n'
                    ' if p.exists(): p.unlink()\n'
                    ' if old is not None:\n  p.write_bytes(old); p.chmod(mode)\n'
                    'sys.exit(7 if linked else 0)\n')
                rc, out, err = runner([self.executable, '-p', profile, python, '-c', program],
                                      str(root), {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                                  'PYTHONDONTWRITEBYTECODE': '1'}, 10)
                probes.append({'expected_write': False, 'operation': 'hardlink',
                               'passed': type(rc) is int and rc == 0})
            except Exception:
                probes.append({'expected_write': False, 'operation': 'hardlink', 'passed': False})
            finally:
                if source is not None:
                    try:
                        os.unlink(source)
                    except OSError:
                        pass
        code = 'from pathlib import Path\nPath("/dev/null").write_bytes(b"probe")\n'
        try:
            rc, out, err = runner([self.executable, '-p', profile, python, '-c', code],
                                  str(root), {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                              'PYTHONDONTWRITEBYTECODE': '1'}, 10)
            probes.append({'expected_write': True, 'passed': type(rc) is int and rc == 0})
        except Exception:
            probes.append({'expected_write': True, 'passed': False})
        return {'verified': bool(probes) and all(probe['passed'] for probe in probes),
                'policy_sha256': digest, 'probes': probes}


def for_platform(platform=None, **kwargs):
    platform = sys.platform if platform is None else platform
    if platform == 'darwin':
        return SandboxExecBoundary(**kwargs)
    return UnsupportedBoundary()
