"""Tests for the bounded gh api I/O used by advisory inline copies."""

import json
import subprocess
import unittest
from unittest import mock

from llmtool import review_inline_io
from llmtool.review_inline import post_inline_copies

ROOT = '/synthetic/repo'
GH = '/synthetic/bin/gh'
ENV = {'PATH': '/synthetic/bin', 'HOME': '/synthetic/home'}
HEAD = 'a' * 40


def completed(stdout=b'', returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout)


class FakeRun:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class ReviewInlineIOTests(unittest.TestCase):
    def call(self, replies, func):
        fake = FakeRun(replies)
        with mock.patch.object(review_inline_io.subprocess, 'run', fake):
            result = func()
        return result, fake

    def make_io(self):
        return review_inline_io.github_io(ROOT, GH, ENV)

    def test_file_lister_reads_bounded_pages(self):
        page1 = [{'filename': 'tools/llmtool/llmtool/a.py'}] * 100
        page2 = [{'filename': 'docs/b.md'}, 'junk']
        file_lister = self.make_io()[0]
        names, fake = self.call(
            [completed(json.dumps(page1).encode('utf-8')),
             completed(json.dumps(page2).encode('utf-8'))],
            lambda: file_lister(9))
        self.assertEqual(len(names), 101)
        self.assertEqual(names[:2], ['tools/llmtool/llmtool/a.py'] * 2)
        self.assertEqual(names[-1], 'docs/b.md')
        argv, kwargs = fake.calls[1]
        self.assertEqual(argv, [GH, 'api',
                                'repos/{owner}/{repo}/pulls/9/files?per_page=100&page=2'])
        self.assertEqual(kwargs['cwd'], ROOT)
        self.assertEqual(kwargs['env'], ENV)
        self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(kwargs['timeout'], review_inline_io.GH_TIMEOUT)
        self.assertNotIn('input', kwargs)

    def test_comment_lister_returns_raw_items(self):
        items = [{'id': 1, 'body': 'first'}, {'id': 2, 'body': 'second'}]
        comment_lister = self.make_io()[1]
        result, fake = self.call(
            [completed(json.dumps(items).encode('utf-8'))],
            lambda: comment_lister(9))
        self.assertEqual(result, items)
        self.assertEqual(fake.calls[0][0],
                         [GH, 'api', 'repos/{owner}/{repo}/pulls/9/comments?per_page=100&page=1'])

    def test_poster_sends_body_on_stdin_only(self):
        payload = {'body': 'synthetic body text', 'path': 'docs/b.md'}
        poster = self.make_io()[2]
        result, fake = self.call([completed(b'not json')], lambda: poster(9, payload))
        self.assertIsNone(result)
        argv, kwargs = fake.calls[0]
        self.assertEqual(argv, [GH, 'api', '--method', 'POST',
                                'repos/{owner}/{repo}/pulls/9/comments', '--input', '-'])
        self.assertEqual(kwargs['input'], json.dumps(payload).encode('utf-8'))
        self.assertNotIn('stdin', kwargs)
        self.assertNotIn('synthetic body text', ' '.join(argv))

    def test_failed_or_bad_pages_raise(self):
        cases = [completed(b'[]', returncode=1), completed(b'{}'), completed(b'\xff'),
                 subprocess.TimeoutExpired('gh', review_inline_io.GH_TIMEOUT),
                 OSError('synthetic command failure')]
        for reply in cases:
            file_lister = self.make_io()[0]
            with self.assertRaises(review_inline_io.InlineIOError):
                self.call([reply], lambda: file_lister(9))

    def test_full_pages_at_max_pages_raise(self):
        full = json.dumps([{'filename': 'docs/x.md'}] * 100).encode('utf-8')
        file_lister = self.make_io()[0]
        fake = FakeRun([completed(full) for _ in range(review_inline_io.MAX_PAGES)])
        with mock.patch.object(review_inline_io.subprocess, 'run', fake):
            with self.assertRaises(review_inline_io.InlineIOError):
                file_lister(9)
        self.assertEqual(len(fake.calls), review_inline_io.MAX_PAGES)

    def test_each_failure_raises_its_fixed_code(self):
        # #1235 C6a 2a: every failure carries one fixed code and nothing else.
        full = json.dumps([{'filename': 'docs/x.md'}] * 100).encode('utf-8')
        cases = [
            ([completed(b'[]', returncode=1)], 'INLINE_IO_COMMAND_FAILED'),
            ([subprocess.TimeoutExpired('gh', 1)], 'INLINE_IO_COMMAND_FAILED'),
            ([OSError('synthetic')], 'INLINE_IO_COMMAND_FAILED'),
            ([completed(b'{}')], 'INLINE_IO_BAD_PAGE'),
            ([completed(b'\xff')], 'INLINE_IO_BAD_PAGE'),
            ([completed(full)] * review_inline_io.MAX_PAGES, 'INLINE_IO_TOO_MANY_PAGES'),
        ]
        for replies, code in cases:
            with self.subTest(code=code, first=replies[0]):
                file_lister = self.make_io()[0]
                with self.assertRaises(review_inline_io.InlineIOError) as caught:
                    self.call(replies, lambda: file_lister(9))
                self.assertEqual(caught.exception.args, (code,))
        with self.assertRaises(review_inline_io.InlineIOError) as caught:
            review_inline_io.github_io(ROOT, '', ENV)
        self.assertEqual(caught.exception.args, ('INLINE_IO_INVALID',))


    def test_invalid_arguments_raise_without_subprocess(self):
        fake = FakeRun([])
        with mock.patch.object(review_inline_io.subprocess, 'run', fake):
            for gh in (None, ''):
                with self.assertRaises(review_inline_io.InlineIOError):
                    review_inline_io.github_io(ROOT, gh, ENV)
            with self.assertRaises(review_inline_io.InlineIOError):
                review_inline_io.github_io(ROOT, GH, None)
            file_lister, comment_lister, poster = self.make_io()
            for pr in (0, -1, '9', True):
                for call in (file_lister, comment_lister):
                    with self.assertRaises(review_inline_io.InlineIOError):
                        call(pr)
                with self.assertRaises(review_inline_io.InlineIOError):
                    poster(pr, {'body': 'x'})
            for comment in (None, 'x', []):
                with self.assertRaises(review_inline_io.InlineIOError):
                    poster(9, comment)
        self.assertEqual(fake.calls, [])

    def test_post_inline_copies_counts_failed_post(self):
        body = 'Finding: F1 [High] tools/llmtool/llmtool/a.py:12 off by one\n'
        files = json.dumps([{'filename': 'tools/llmtool/llmtool/a.py'}]).encode('utf-8')
        comments = json.dumps([]).encode('utf-8')
        file_lister, comment_lister, poster = self.make_io()
        fake = FakeRun([completed(files), completed(comments),
                        completed(b'', returncode=1)])
        with mock.patch.object(review_inline_io.subprocess, 'run', fake):
            result = post_inline_copies(9, HEAD, body, file_lister=file_lister,
                                        comment_lister=comment_lister, poster=poster)
        self.assertEqual(result, {'posted': 0, 'skipped': 0, 'failed': 1})
        self.assertEqual(len(fake.calls), 3)


if __name__ == '__main__':
    unittest.main()
