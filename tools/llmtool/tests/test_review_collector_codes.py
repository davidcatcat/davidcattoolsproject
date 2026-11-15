"""#1230 refusal-code pin: task_review_collector fixed-read and bundle manifest codes."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import task_review_collector as c
from llmtool.errors import LLMToolError
import review_trust_fixture


def setUpModule(): review_trust_fixture.start_module()
def tearDownModule(): review_trust_fixture.stop_module()


BASE, SOURCE = '1'*40, '2'*40


class FakeReader:
    def __init__(self, root, head=SOURCE, names=b'src/app.py\0', tree=b''):
        self.root, self.head, self.names, self.tree = root, head, names, tree

    def read(self, args):
        if args == c.git_intel.HEAD:
            return (self.head+'\n').encode()
        if args[0] == 'merge-base':
            return b''
        if args[0] == 'diff':
            return self.names
        return self.tree


class CollectorCodeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.root.chmod(0o700)

    def code(self, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        return caught.exception.review_code

    def manifest(self, reader, base=BASE, forbidden=()):
        return lambda: c.collect_bundle_manifest(self.root, base, SOURCE, ('src',), forbidden, reader=reader)

    def test_fixed_read_denied_and_timeout_before_process(self):
        reader = c._LocalGit(self.root)
        with patch('subprocess.Popen', side_effect=AssertionError('no process')):
            self.assertEqual(self.code(lambda: reader.read(('fetch', 'origin'))), 'REVIEW_GIT_READ_DENIED')
            reader.reads = 32
            self.assertEqual(self.code(lambda: reader.read(c.git_intel.HEAD)), 'REVIEW_GIT_READ_DENIED')
            reader.reads = 0
            reader.started -= 31
            self.assertEqual(self.code(lambda: reader.read(c.git_intel.HEAD)), 'REVIEW_COLLECTOR_TIMEOUT')

    def test_root_codes(self):
        self.assertEqual(self.code(lambda: c.physical_directory('relative/root')), 'REVIEW_ROOT_INVALID')
        other = FakeReader(self.root/'other')
        self.assertEqual(self.code(self.manifest(other)), 'REVIEW_ROOT_INVALID')

    def test_manifest_base_drift_and_scope_codes(self):
        self.assertEqual(self.code(self.manifest(FakeReader(self.root), base='main')), 'BUNDLE_BASE_INVALID')
        self.assertEqual(self.code(self.manifest(FakeReader(self.root, head='3'*40))), 'REVIEW_SOURCE_DRIFT')
        symlink = b'120000 blob ' + b'4'*40 + b'\tsrc/app.py\0'
        for label, reader, forbidden in (
                ('empty', FakeReader(self.root, names=b''), ()),
                ('duplicate', FakeReader(self.root, names=b'src/app.py\0src/app.py\0'), ()),
                ('forbidden', FakeReader(self.root), ('src/app.py',)),
                ('symlink', FakeReader(self.root, tree=symlink), ())):
            with self.subTest(label=label):
                self.assertEqual(self.code(self.manifest(reader, forbidden=forbidden)), 'BUNDLE_SCOPE_INVALID')

    def test_git_non_ancestor_and_failed_read_codes(self):
        def git(*args):
            return subprocess.check_output(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null',
                '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', *args],
                cwd=self.root, stderr=subprocess.PIPE)
        git('init', '-q')
        (self.root/'f').write_text('a\n'); git('add', 'f'); git('commit', '-qm', 'one')
        first = git('rev-parse', 'HEAD').decode().strip()
        (self.root/'f').write_text('b\n'); git('commit', '-qam', 'two')
        second = git('rev-parse', 'HEAD').decode().strip()
        reader = c._LocalGit(self.root)
        self.assertEqual(self.code(lambda: reader.read(('merge-base', '--is-ancestor', second, first))),
                         'BUNDLE_BASE_NOT_ANCESTOR')
        self.assertEqual(self.code(lambda: reader.read(('cat-file', 'blob', '0'*40))), 'REVIEW_GIT_READ_FAILED')


if __name__ == '__main__':
    unittest.main()
