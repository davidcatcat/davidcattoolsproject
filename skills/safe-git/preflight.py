"""Daily read-only repository gate with optional private evidence outputs."""
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
        return [file_record(root, n) for n in sorted(set(names)) if n]
    entries = []
    for raw in index.split(b"\0"):
        if raw:
            meta, separator, name = raw.partition(b"\t")
            require(separator == b"\t")
            mode, blob, stage = meta.decode("ascii").split()
            entries.append([os.fsdecode(name), mode, blob, stage])
    return {"index": entries, "tracked": records(tracked),
            "untracked": records(untracked.split(b"\0")),
            "ignored": records(ignored.split(b"\0"))}


def validate_inventory(data):
    require(isinstance(data, dict) and set(data) == {"index", "tracked", "untracked", "ignored"})
    for category, entries in data.items():
        require(isinstance(entries, list))
        seen = set()
        for entry in entries:
            require(isinstance(entry, list) and len(entry) == (4 if category == "index" else 3))
            name = entry[0]
            require(isinstance(name, str) and bool(name) and "\0" not in name)
            path = Path(name)
            require(not path.is_absolute() and ".." not in path.parts)
            if category == "index":
                require(all(isinstance(value, str) for value in entry[1:]))
                require(bool(re.fullmatch(r"[0-7]{6}", entry[1])))
                require(bool(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", entry[2])))
                require(entry[3] in ("0", "1", "2", "3"))
                key = (name, entry[3])
            else:
                require(type(entry[1]) is int and isinstance(entry[2], str))
                require(bool(re.fullmatch(r"[0-9a-f]{64}", entry[2])))
                key = name
            require(key not in seen)
            seen.add(key)


def private_write(path, value, root, common, uid):
    path = Path(path)
    require(path.is_absolute() and path == Path(os.path.abspath(path)))
    protected = [root, common,
                 Path("/Users/davidsun/git/davidcataiporject/davidcattoolsproject"),
                 Path("/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject")]
    require(all(path != parent and parent not in path.parents for parent in protected))
    directory(path.parent, uid, 0o700)
    data = (json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")
    # Existing files/symlinks are errors. Preserve partial files on write failure.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        info = os.fstat(stream.fileno())
        require(info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o600)
        stream.write(data)
        stream.flush()
        require(path.lstat().st_ino == info.st_ino)


def delta(expected, observed):
    result = {}
    for category in expected:
        def grouped(entries):
            values = {}
            for entry in entries:
                values.setdefault(entry[0], []).append(entry[1:])
            return values
        old, new = grouped(expected[category]), grouped(observed[category])
        result[category] = {"added": sorted(new.keys() - old.keys()),
                            "removed": sorted(old.keys() - new.keys()),
                            "changed": sorted(name for name in old.keys() & new.keys()
                                              if old[name] != new[name])}
    return result


def repository_identity(root):
    commands = [("rev-parse", "--show-toplevel"), ("branch", "--show-current"),
                ("rev-parse", "HEAD"),
                ("rev-parse", "--path-format=absolute", "--git-common-dir"),
                ("remote", "get-url", "origin")]
    return [git(root, *command).decode().strip() for command in commands]


def git_directory(root):
    return Path(os.fsdecode(git(
        root, "rev-parse", "--path-format=absolute", "--absolute-git-dir"
    ).strip()))


def pin_git_directory(root, uid):
    path = git_directory(root)
    directory(path, uid)
    return path


def verify_git_directory(root, pinned, uid):
    current = git_directory(root)
    require(current == pinned)
    directory(current, uid)


def check(args):
    root = Path(args.root)
    require(args.uid == os.getuid())
    directory(root, args.uid, int(args.root_mode, 8))
    directory(root.parent, args.uid, int(args.parent_mode, 8))
    expected_identity = [str(root), args.branch, args.head, args.common_dir, args.origin]
    require(repository_identity(root) == expected_identity)
    common = Path(args.common_dir)
    directory(common, args.uid)
    pinned_git_dir = pin_git_directory(root, args.uid)
    if args.write_candidate:
        observed = inventory(root)
        require(inventory(root) == observed)
        require(repository_identity(root) == expected_identity)
        private_write(args.write_candidate, observed, root, common, args.uid)
        verify_git_directory(root, pinned_git_dir, args.uid)
        return "CANDIDATE: review required"
    baseline = Path(args.baseline)
    directory(baseline.parent, args.uid, 0o700)
    info = baseline.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == args.uid
            and stat.S_IMODE(info.st_mode) == 0o600)
    with os.fdopen(os.open(baseline, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        require(identity(os.fstat(stream.fileno())) == identity(info))
        expected = json.load(stream)
        require(identity(os.fstat(stream.fileno())) == identity(info))
    require(identity(baseline.lstat()) == identity(info))
    validate_inventory(expected)
    observed = inventory(root)
    if expected != observed:
        if args.report:
            private_write(args.report, delta(expected, observed), root, common, args.uid)
        raise Rejected()
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
    after = inventory(root)
    if after != observed:
        if args.report:
            private_write(args.report, delta(expected, after), root, common, args.uid)
        raise Rejected()
    require(repository_identity(root) == expected_identity)
    directory(root, args.uid, int(args.root_mode, 8))
    directory(root.parent, args.uid, int(args.parent_mode, 8))
    directory(common, args.uid)
    if args.report:
        private_write(args.report, delta(expected, observed), root, common, args.uid)
    verify_git_directory(root, pinned_git_dir, args.uid)
    return "PASS: read-only preflight"


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "STOP: invalid preflight arguments\n")


def main(argv=None):
    parser = Parser(description=__doc__)
    for name in ("root", "branch", "common-dir", "origin", "head",
                 "root-mode", "parent-mode"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--uid", type=int, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--baseline")
    mode.add_argument("--write-candidate")
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    if args.write_candidate and args.report:
        parser.error("report requires baseline")
    try:
        message = check(args)
    except (Rejected, OSError, ValueError, KeyError, TypeError, AssertionError,
            subprocess.SubprocessError):
        print("STOP: preflight rejected", file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
