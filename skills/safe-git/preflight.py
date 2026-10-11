"""Read-only daily Git preflight; explicit reviewed expectations, no mutation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


class Rejected(Exception):
    pass


def require(condition):
    if not condition:
        raise Rejected()


def run(argv):
    env = {
        "PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        "XDG_CONFIG_HOME": os.devnull,
    }
    result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=15, check=False)
    require(result.returncode == 0 and not result.stderr)
    return result.stdout


def git(root, *args):
    return run(["/usr/bin/git", "--no-optional-locks", "--no-replace-objects",
                "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                "-c", "gc.auto=0", "-C", str(root), *args])


def directory(path, uid, mode=None):
    """Reject symlinks at every component; no resolving away the evidence."""
    require(path.is_absolute() and path == Path(os.path.abspath(path)))
    for component in [path, *path.parents]:
        info = component.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, uid))
        # A root-owned sticky system temp ancestor is permissible; leaves are not.
        shared_temp = (component != path and info.st_uid == 0
                       and bool(info.st_mode & stat.S_ISVTX))
        require(not (info.st_mode & 0o022) or shared_temp)
        if sys.platform == "darwin":
            acl = run(["/bin/ls", "-lde", str(component)]).splitlines()[1:]
            # macOS home's deny-delete ACL grants no access. Reject every grant
            # or other ACL rather than inferring safety from POSIX mode alone.
            require(all(re.fullmatch(rb"\s*\d+: \S+ deny delete", line) for line in acl))
    info = path.lstat()
    require(info.st_uid == uid)
    if mode is not None:
        require(stat.S_IMODE(info.st_mode) == mode)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(info):
    # Reading may update atime; ownership/content identity must remain stable.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def file_record(root, name):
    relative = Path(os.fsdecode(name))
    require(not relative.is_absolute() and ".." not in relative.parts)
    path = root / relative
    # Never follow a directory symlink to read private content outside the root.
    for parent in list(path.parents):
        if parent == root:
            break
        require(not parent.is_symlink())
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode))
    if stat.S_ISLNK(before.st_mode):
        data = os.fsencode(os.readlink(path))
    else:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            require(identity(os.fstat(stream.fileno())) == identity(before))
            data = stream.read()
            require(identity(os.fstat(stream.fileno())) == identity(before))
    require(identity(path.lstat()) == identity(before))
    return [os.fsdecode(name), before.st_mode, digest(data)]


def inventory(root):
    """Explicit inventories include authoritative ignored files, not empty dirs."""
    index = git(root, "ls-files", "--stage", "-z")
    tracked = git(root, "ls-files", "-z").split(b"\0")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    ignored = git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    def records(names):
        values = [file_record(root, n) for n in sorted(set(names)) if n]
        return digest(json.dumps(values, ensure_ascii=True,
                                 separators=(",", ":")).encode("ascii"))
    return {"index": digest(index), "tracked": records(tracked),
            "untracked": records(untracked.split(b"\0")),
            "ignored": records(ignored.split(b"\0"))}


def repository_identity(root):
    commands = [("rev-parse", "--show-toplevel"), ("branch", "--show-current"),
                ("rev-parse", "HEAD"),
                ("rev-parse", "--path-format=absolute", "--git-common-dir"),
                ("remote", "get-url", "origin")]
    return [git(root, *command).decode().strip() for command in commands]


def check(args):
    root = Path(args.root)
    require(args.uid == os.getuid())
    directory(root, args.uid, int(args.root_mode, 8))
    directory(root.parent, args.uid, int(args.parent_mode, 8))
    expected_identity = [str(root), args.branch, args.head, args.common_dir, args.origin]
    require(repository_identity(root) == expected_identity)
    common = Path(args.common_dir)
    directory(common, args.uid)
    baseline = Path(args.baseline)
    info = baseline.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == args.uid
            and stat.S_IMODE(info.st_mode) == 0o600)
    with os.fdopen(os.open(baseline, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        require(identity(os.fstat(stream.fileno())) == identity(info))
        expected = json.load(stream)
        require(identity(os.fstat(stream.fileno())) == identity(info))
    require(identity(baseline.lstat()) == identity(info))
    observed = inventory(root)
    require(expected == observed)
    python = root / "tools/llmtool/.venv/bin/python"
    directory(python.parent.parent, args.uid)
    script = ("import json,sys,pathlib; "
              "assert pathlib.Path(sys.prefix)==pathlib.Path(sys.argv[1]); "
              "import llmtool; print(json.dumps([sys.executable,sys.prefix,llmtool.__file__]))")
    executable, prefix, module = json.loads(run([
        str(python), "-B", "-I", "-c", script, str(python.parent.parent)]))
    require(Path(executable) == python and Path(prefix) == python.parent.parent)
    module = Path(module).resolve(strict=True)
    require(root in module.parents)
    require(inventory(root) == observed)
    require(repository_identity(root) == expected_identity)
    directory(root, args.uid, int(args.root_mode, 8))
    directory(root.parent, args.uid, int(args.parent_mode, 8))
    directory(common, args.uid)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "STOP: invalid preflight arguments\n")


def main(argv=None):
    parser = Parser(description=__doc__)
    for name in ("root", "branch", "common-dir", "origin", "head", "baseline",
                 "root-mode", "parent-mode"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--uid", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        check(args)
    except (Rejected, OSError, ValueError, KeyError, TypeError, AssertionError,
            subprocess.SubprocessError):
        print("STOP: preflight rejected", file=sys.stderr)
        return 1
    print("PASS: read-only preflight")
    return 0


if __name__ == "__main__":
    sys.exit(main())
