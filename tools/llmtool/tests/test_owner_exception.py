"""Tests for issue #1253 owner exception verifier."""

import base64
import hashlib
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from llmtool import owner_exception


BASE_SHA = 'a' * 40
HEAD_SHA = 'b' * 40
AUTH_TIME = '2026-11-10T10:00:00Z'
REG_TIME = '2026-11-10T11:00:00Z'
SLUG = 'davidcatcat/davidcattoolsproject'
URL = f"https://github.com/{SLUG}/issues/1253#issuecomment-9999"

FAKE_VALUE = 'fake-host-a'
FAKE_PLACEHOLDER = 'placeholder-a'
REPLACEMENT_MAP = [(FAKE_VALUE, FAKE_PLACEHOLDER)]

BASE_BYTES = b"connect to fake-host-a on port 80\n"
HEAD_BYTES = b"connect to placeholder-a on port 80\n"
HEAD_HASH = hashlib.sha256(HEAD_BYTES).hexdigest()
FILE_PATH = 'config/hosts.txt'


def make_comment_body(
    *,
    marker=owner_exception.REGISTER_MARKER,
    exception_type=owner_exception.EXCEPTION_TYPE,
    slot='S2a',
    pr='#42',
    base=BASE_SHA,
    head=HEAD_SHA,
    files=None,
    extra_lines=None,
):
    if files is None:
        files = [(HEAD_HASH, FILE_PATH)]
    lines = [
        marker,
        f"exception: {exception_type}",
        f"slot: {slot}",
        f"pr: {pr}",
        f"base: {base}",
        f"head: {head}",
    ]
    for h, p in files:
        lines.append(f"file: {h} {p}")
    if extra_lines:
        lines.extend(extra_lines)
    return '\n'.join(lines)


def make_fake_github_get(
    *,
    comment_body=None,
    comment_url=URL,
    comment_created_at=REG_TIME,
    comment_updated_at=REG_TIME,
    comment_user_type='User',
    comment_user_login='ddavidccatssun',
    auth_created_at=AUTH_TIME,
    auth_user_type='User',
    auth_user_login='davidcatcat',
    pr_state='open',
    pr_merged=False,
    pr_base_sha=BASE_SHA,
    pr_head_sha=HEAD_SHA,
    pr_files=None,
    base_file_bytes=BASE_BYTES,
    head_file_bytes=HEAD_BYTES,
    issue_comments=None,
    fail_on=None,
):
    if comment_body is None:
        comment_body = make_comment_body()
    if pr_files is None:
        pr_files = [{'filename': FILE_PATH, 'status': 'modified'}]

    comment_obj = {
        'id': 9999,
        'html_url': comment_url,
        'created_at': comment_created_at,
        'updated_at': comment_updated_at,
        'body': comment_body,
        'user': {'type': comment_user_type, 'login': comment_user_login},
    }

    auth_comment_obj = {
        'id': owner_exception.AUTHORIZATION_COMMENT_ID,
        'html_url': f"https://github.com/{SLUG}/issues/1253#issuecomment-{owner_exception.AUTHORIZATION_COMMENT_ID}",
        'created_at': auth_created_at,
        'updated_at': auth_created_at,
        'body': 'authorization comment body',
        'user': {'type': auth_user_type, 'login': auth_user_login},
    }

    if issue_comments is None:
        issue_comments = [comment_obj]

    def github_get(path):
        if fail_on and fail_on in path:
            raise RuntimeError(f"Simulated failure for {path}")
        if path == 'issues/comments/9999':
            return dict(comment_obj)
        if path == f"issues/comments/{owner_exception.AUTHORIZATION_COMMENT_ID}":
            return dict(auth_comment_obj)
        if path == 'pulls/42':
            return {
                'number': 42,
                'state': pr_state,
                'merged': pr_merged,
                'base': {'sha': pr_base_sha},
                'head': {'sha': pr_head_sha},
            }
        if path.startswith('pulls/42/files'):
            return list(pr_files)
        if path.startswith(f"contents/{FILE_PATH}?ref={pr_base_sha}"):
            return {'content': base64.b64encode(base_file_bytes).decode('ascii')}
        if path.startswith(f"contents/{FILE_PATH}?ref={pr_head_sha}"):
            return {'content': base64.b64encode(head_file_bytes).decode('ascii')}
        if path.startswith(f"issues/{owner_exception.ISSUE}/comments"):
            return list(issue_comments)
        raise AssertionError(f"Unexpected github_get path: {path}")

    return github_get


class OwnerExceptionVerifierTests(unittest.TestCase):
    """Test verify() and default_replacement_map()."""

    def setUp(self):
        self.slug_patch = patch.object(owner_exception, '_trusted_slug', return_value=SLUG)
        self.slug_patch.start()

    def tearDown(self):
        self.slug_patch.stop()

    def test_valid_pass(self):
        gh_get = make_fake_github_get()
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertIsNone(code)
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence['type'], owner_exception.EXCEPTION_TYPE)
        self.assertEqual(evidence['slot'], 'S2a')
        self.assertEqual(evidence['url'], URL)
        self.assertEqual(evidence['pr'], 42)
        self.assertEqual(evidence['base'], BASE_SHA)
        self.assertEqual(evidence['head'], HEAD_SHA)
        self.assertEqual(evidence['files'], [[FILE_PATH, HEAD_HASH, 1]])

    def test_wrong_issue(self):
        wrong_url = f"https://github.com/{SLUG}/issues/1254#issuecomment-9999"
        gh_get = make_fake_github_get()
        code, evidence = owner_exception.verify(wrong_url, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_SCOPE')
        self.assertIsNone(evidence)

    def test_wrong_slot(self):
        body = make_comment_body(slot='S3')
        gh_get = make_fake_github_get(comment_body=body)
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_SCOPE')
        self.assertIsNone(evidence)

    def test_wrong_type(self):
        body = make_comment_body(exception_type='OWNER_EXCEPTION_OTHER')
        gh_get = make_fake_github_get(comment_body=body)
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_TYPE')
        self.assertIsNone(evidence)

    def test_forged_comment_url_mismatch(self):
        gh_get = make_fake_github_get(comment_url="https://github.com/davidcatcat/davidcattoolsproject/issues/1253#issuecomment-8888")
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_REF_INVALID')
        self.assertIsNone(evidence)

    def test_forged_comment_slug_mismatch(self):
        other_url = "https://github.com/otherowner/davidcattoolsproject/issues/1253#issuecomment-9999"
        gh_get = make_fake_github_get()
        code, evidence = owner_exception.verify(other_url, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_REF_INVALID')
        self.assertIsNone(evidence)

    def test_edited_comment(self):
        gh_get = make_fake_github_get(comment_updated_at='2026-11-10T11:05:00Z')
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_EDITED')
        self.assertIsNone(evidence)

    def test_bot_or_unauthorized_comment_author(self):
        for user_type, login in (('Bot', 'ddavidccatssun'), ('User', 'unauthorized-user')):
            with self.subTest(user_type=user_type, login=login):
                gh_get = make_fake_github_get(comment_user_type=user_type, comment_user_login=login)
                code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
                self.assertEqual(code, 'OWNER_EXCEPTION_AUTHOR')
                self.assertIsNone(evidence)

    def test_wrong_marker_comment(self):
        body = make_comment_body(marker='<!-- wrong marker -->')
        gh_get = make_fake_github_get(comment_body=body)
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_MARKER')
        self.assertIsNone(evidence)

    def test_before_authorization_comment(self):
        gh_get = make_fake_github_get(comment_created_at='2026-11-10T09:00:00Z', comment_updated_at='2026-11-10T09:00:00Z')
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_UNAVAILABLE')
        self.assertIsNone(evidence)

    def test_authorization_comment_author_invalid(self):
        gh_get = make_fake_github_get(auth_user_type='Bot')
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_AUTHOR')
        self.assertIsNone(evidence)

    def test_base_head_sha_mismatch(self):
        gh_get_base = make_fake_github_get(pr_base_sha='c' * 40)
        code, evidence = owner_exception.verify(URL, 42, gh_get_base, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_SHA_MISMATCH')
        self.assertIsNone(evidence)

        gh_get_head = make_fake_github_get(pr_head_sha='d' * 40)
        code, evidence = owner_exception.verify(URL, 42, gh_get_head, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_SHA_MISMATCH')
        self.assertIsNone(evidence)

    def test_missing_file_in_pr(self):
        body = make_comment_body(files=[(HEAD_HASH, FILE_PATH), ('1' * 64, 'config/other.txt')])
        gh_get = make_fake_github_get(comment_body=body, pr_files=[{'filename': FILE_PATH, 'status': 'modified'}])
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_FILES_MISMATCH')
        self.assertIsNone(evidence)

    def test_extra_file_in_pr(self):
        body = make_comment_body(files=[(HEAD_HASH, FILE_PATH)])
        extra_pr_files = [
            {'filename': FILE_PATH, 'status': 'modified'},
            {'filename': 'config/extra.txt', 'status': 'modified'},
        ]
        gh_get = make_fake_github_get(comment_body=body, pr_files=extra_pr_files)
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_FILES_MISMATCH')
        self.assertIsNone(evidence)

    def test_non_modified_status(self):
        for status in ('added', 'removed', 'renamed'):
            with self.subTest(status=status):
                pr_files = [{'filename': FILE_PATH, 'status': status}]
                gh_get = make_fake_github_get(pr_files=pr_files)
                code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
                self.assertEqual(code, 'OWNER_EXCEPTION_FILES_MISMATCH')
                self.assertIsNone(evidence)

    def test_hash_mismatch(self):
        wrong_hash = 'f' * 64
        body = make_comment_body(files=[(wrong_hash, FILE_PATH)])
        gh_get = make_fake_github_get(comment_body=body)
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_HASH_MISMATCH')
        self.assertIsNone(evidence)

    def test_non_mechanical_extra_edit(self):
        unmechanical_head = b"connect to placeholder-a on port 80\nextra non-mechanical line\n"
        unmechanical_hash = hashlib.sha256(unmechanical_head).hexdigest()
        body = make_comment_body(files=[(unmechanical_hash, FILE_PATH)])
        gh_get = make_fake_github_get(comment_body=body, head_file_bytes=unmechanical_head)
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_NOT_MECHANICAL')
        self.assertIsNone(evidence)

    def test_zero_replacements(self):
        unchanged_base = b"connect to unredacted on port 80\n"
        unchanged_head = b"connect to unredacted on port 80\n"
        unchanged_hash = hashlib.sha256(unchanged_head).hexdigest()
        body = make_comment_body(files=[(unchanged_hash, FILE_PATH)])
        gh_get = make_fake_github_get(
            comment_body=body,
            base_file_bytes=unchanged_base,
            head_file_bytes=unchanged_head,
        )
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_NOT_MECHANICAL')
        self.assertIsNone(evidence)

    def test_reuse_for_second_pr(self):
        earlier_comment = {
            'id': 9001,
            'html_url': f"https://github.com/{SLUG}/issues/1253#issuecomment-9001",
            'created_at': '2026-11-10T10:30:00Z',
            'updated_at': '2026-11-10T10:30:00Z',
            'body': make_comment_body(slot='S2a', pr='#41'),
            'user': {'type': 'User', 'login': 'davidcatcat'},
        }
        current_comment = {
            'id': 9999,
            'html_url': URL,
            'created_at': REG_TIME,
            'updated_at': REG_TIME,
            'body': make_comment_body(slot='S2a', pr='#42'),
            'user': {'type': 'User', 'login': 'ddavidccatssun'},
        }
        gh_get = make_fake_github_get(issue_comments=[earlier_comment, current_comment])
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_REUSED')
        self.assertIsNone(evidence)

    def test_reuse_two_slots_name_same_pr(self):
        comment_s2a = {
            'id': 9001,
            'html_url': f"https://github.com/{SLUG}/issues/1253#issuecomment-9001",
            'created_at': '2026-11-10T10:30:00Z',
            'updated_at': '2026-11-10T10:30:00Z',
            'body': make_comment_body(slot='S2a', pr='#42'),
            'user': {'type': 'User', 'login': 'davidcatcat'},
        }
        comment_s2b = {
            'id': 9999,
            'html_url': URL,
            'created_at': REG_TIME,
            'updated_at': REG_TIME,
            'body': make_comment_body(slot='S2b', pr='#42'),
            'user': {'type': 'User', 'login': 'ddavidccatssun'},
        }
        gh_get = make_fake_github_get(comment_body=comment_s2b['body'], issue_comments=[comment_s2a, comment_s2b])
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_REUSED')
        self.assertIsNone(evidence)

    def test_reuse_for_second_head(self):
        earlier_comment = {
            'id': 9001,
            'html_url': f"https://github.com/{SLUG}/issues/1253#issuecomment-9001",
            'created_at': '2026-11-10T10:30:00Z',
            'updated_at': '2026-11-10T10:30:00Z',
            'body': make_comment_body(slot='S2a', pr='#42', head='c' * 40),
            'user': {'type': 'User', 'login': 'davidcatcat'},
        }
        current_comment = {
            'id': 9999,
            'html_url': URL,
            'created_at': REG_TIME,
            'updated_at': REG_TIME,
            'body': make_comment_body(slot='S2a', pr='#42', head=HEAD_SHA),
            'user': {'type': 'User', 'login': 'ddavidccatssun'},
        }
        gh_get = make_fake_github_get(issue_comments=[earlier_comment, current_comment])
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_REUSED')
        self.assertIsNone(evidence)

    def test_retired_marker(self):
        retire_comment = {
            'id': 9500,
            'html_url': f"https://github.com/{SLUG}/issues/1253#issuecomment-9500",
            'created_at': '2026-11-10T10:45:00Z',
            'updated_at': '2026-11-10T10:45:00Z',
            'body': owner_exception.RETIRE_MARKER + "\nexception retired",
            'user': {'type': 'User', 'login': 'davidcatcat'},
        }
        current_comment = {
            'id': 9999,
            'html_url': URL,
            'created_at': REG_TIME,
            'updated_at': REG_TIME,
            'body': make_comment_body(),
            'user': {'type': 'User', 'login': 'ddavidccatssun'},
        }
        gh_get = make_fake_github_get(issue_comments=[retire_comment, current_comment])
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_RETIRED')
        self.assertIsNone(evidence)

    def test_pr_not_open_refuses(self):
        for state, merged in (('closed', False), ('open', True)):
            with self.subTest(state=state, merged=merged):
                gh_get = make_fake_github_get(pr_state=state, pr_merged=merged)
                code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
                self.assertEqual(code, 'OWNER_EXCEPTION_UNAVAILABLE')
                self.assertIsNone(evidence)

    def test_duplicate_file_in_body_refuses(self):
        body = make_comment_body(files=[(HEAD_HASH, FILE_PATH), (HEAD_HASH, FILE_PATH)])
        gh_get = make_fake_github_get(comment_body=body)
        code, evidence = owner_exception.verify(URL, 42, gh_get, replacement_map=REPLACEMENT_MAP)
        self.assertEqual(code, 'OWNER_EXCEPTION_FILES_MISMATCH')
        self.assertIsNone(evidence)

    def test_default_replacement_map_never_prints(self):
        fake_mod = MagicMock()
        fake_mod.find_repo_root.return_value = '/fake/root'
        fake_mod.resolve_map_path.return_value = '/fake/map.json'
        fake_mod.load_and_validate_map.return_value = [('fake-val', 'fake-plc')]

        with patch('importlib.util.spec_from_file_location') as mock_spec, \
             patch('importlib.util.module_from_spec', return_value=fake_mod):
            loader = MagicMock()
            mock_spec.return_value = MagicMock(loader=loader)
            buf = io.StringIO()
            with redirect_stdout(buf):
                res = owner_exception.default_replacement_map()
            self.assertEqual(res, [('fake-val', 'fake-plc')])
            self.assertEqual(buf.getvalue(), '')

    def test_allow_merged_accepts_only_a_closed_merged_pr(self):
        # #1519: the reconcile path verifies an exception PR that is already merged.
        gh_get = make_fake_github_get(pr_state='closed', pr_merged=True)
        code, evidence = owner_exception.verify(
            URL, 42, gh_get, replacement_map=REPLACEMENT_MAP, allow_merged=True)
        self.assertIsNone(code)
        self.assertEqual((evidence['pr'], evidence['head']), (42, HEAD_SHA))
        for state, merged in (('open', False), ('closed', False), ('open', True)):
            with self.subTest(state=state, merged=merged):
                gh_get = make_fake_github_get(pr_state=state, pr_merged=merged)
                self.assertEqual(owner_exception.verify(
                    URL, 42, gh_get, replacement_map=REPLACEMENT_MAP, allow_merged=True),
                    ('OWNER_EXCEPTION_UNAVAILABLE', None))

    def test_allow_merged_still_checks_the_head(self):
        gh_get = make_fake_github_get(pr_state='closed', pr_merged=True, pr_head_sha='c' * 40)
        self.assertEqual(owner_exception.verify(
            URL, 42, gh_get, replacement_map=REPLACEMENT_MAP, allow_merged=True),
            ('OWNER_EXCEPTION_SHA_MISMATCH', None))

    def test_default_still_refuses_a_merged_pr(self):
        gh_get = make_fake_github_get(pr_state='closed', pr_merged=True)
        self.assertEqual(owner_exception.verify(
            URL, 42, gh_get, replacement_map=REPLACEMENT_MAP),
            ('OWNER_EXCEPTION_UNAVAILABLE', None))


if __name__ == '__main__':
    unittest.main()
