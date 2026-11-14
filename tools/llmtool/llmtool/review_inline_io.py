"""GitHub I/O for advisory inline copies of verdict findings (#1235 C6a, piece 2a).

review_inline stays free of network and subprocess code. This module builds the
three callables that review_inline.post_inline_copies takes, each one a bounded
``gh api`` call. Every failure raises, so post_inline_copies counts it and the
formal verdict is never blocked. Comment bodies go on stdin, never in argv.
"""

import json
import subprocess

GH_TIMEOUT = 30
MAX_PAGES = 10


class InlineIOError(Exception):
    """A fixed inline-copy I/O failure; never output, a body or a credential."""


def github_io(root, gh, env):
    """Return (file_lister, comment_lister, poster) bound to ``gh api`` run in ``root``.

    ``gh`` is the gh executable path and ``env`` the minimal gh environment the
    caller already uses for the verdict post. Lists read at most MAX_PAGES pages.
    """
    if type(gh) is not str or not gh or type(env) is not dict:
        raise InlineIOError('INLINE_IO_INVALID')

    def run(args, payload=None):
        stdin = {'input': payload} if payload is not None else {'stdin': subprocess.DEVNULL}
        try:
            result = subprocess.run([gh, 'api'] + args, cwd=str(root), env=dict(env),
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    check=False, timeout=GH_TIMEOUT, **stdin)
        except (OSError, subprocess.TimeoutExpired):
            raise InlineIOError('INLINE_IO_COMMAND_FAILED') from None
        if result.returncode:
            raise InlineIOError('INLINE_IO_COMMAND_FAILED')
        return result.stdout

    def pages(path, pr_number):
        if type(pr_number) is not int or pr_number < 1:
            raise InlineIOError('INLINE_IO_INVALID')
        items = []
        for page in range(1, MAX_PAGES + 1):
            raw = run(['repos/{owner}/{repo}/pulls/%d/%s?per_page=100&page=%d'
                       % (pr_number, path, page)])
            try:
                batch = json.loads(raw.decode('utf-8'))
            except (AttributeError, UnicodeDecodeError, ValueError, RecursionError):
                raise InlineIOError('INLINE_IO_BAD_PAGE') from None
            if type(batch) is not list:
                raise InlineIOError('INLINE_IO_BAD_PAGE')
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise InlineIOError('INLINE_IO_TOO_MANY_PAGES')

    def file_lister(pr_number):
        return [item.get('filename') for item in pages('files', pr_number) if type(item) is dict]

    def comment_lister(pr_number):
        return pages('comments', pr_number)

    def poster(pr_number, comment):
        if type(pr_number) is not int or pr_number < 1 or type(comment) is not dict:
            raise InlineIOError('INLINE_IO_INVALID')
        run(['--method', 'POST', 'repos/{owner}/{repo}/pulls/%d/comments' % pr_number,
             '--input', '-'], json.dumps(comment).encode('utf-8'))

    return file_lister, comment_lister, poster
