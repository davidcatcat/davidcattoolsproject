"""Tests for GitHub-hosted remote-test artifact retrieval and validation."""

import io
import unittest
import zipfile

from llmtool import remote_test


_NONCE = 'fedcba9876543210fedcba9876543210'
_RUN_ID = 8675309
_RECEIPT_CONTENT = b'{"result": "PASS", "exit_code": 0, "ran_total": 5}'


def _make_zip(filename='receipt.json', content=_RECEIPT_CONTENT, **extra_files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, content)
        for name, data in extra_files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _artifact(
    artifact_id=101,
    *,
    nonce=_NONCE,
    name=None,
    size=128,
    expired=False,
    **overrides,
):
    value = {
        'id': artifact_id,
        'name': name if name is not None else (remote_test._HOSTED_ARTIFACT_PREFIX + nonce),
        'size_in_bytes': size,
        'expired': expired,
        'archive_download_url': f'https://api.github.com/repos/owner/repo/actions/artifacts/{artifact_id}/zip',
    }
    value.update(overrides)
    return value


class HostedArtifactTests(unittest.TestCase):
    def test_one_valid_artifact(self):
        valid_zip = _make_zip()
        art = _artifact(101, size=len(valid_zip))
        data = {'total_count': 1, 'artifacts': [art]}
        bytes_calls = []

        def fake_get(path):
            self.assertEqual(
                path,
                f'actions/runs/{_RUN_ID}/artifacts?per_page=100&page=1',
            )
            return data

        def fake_get_bytes(path):
            bytes_calls.append(path)
            return valid_zip

        result = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=fake_get, get_bytes=fake_get_bytes,
        )
        self.assertEqual(result, _RECEIPT_CONTENT)
        self.assertEqual(bytes_calls, ['actions/artifacts/101/zip'])

    def test_none(self):
        data = {'total_count': 0, 'artifacts': []}
        result = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=lambda _: data, get_bytes=lambda _: b'',
        )
        self.assertIsNone(result)

    def test_two(self):
        art1 = _artifact(101)
        art2 = _artifact(102)
        data = {'total_count': 2, 'artifacts': [art1, art2]}
        result = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=lambda _: data, get_bytes=lambda _: b'',
        )
        self.assertIsNone(result)

    def test_match_on_later_page(self):
        valid_zip = _make_zip()
        unrelated = _artifact(100, name='unrelated-receipt')
        matching = _artifact(101, size=len(valid_zip))

        page1 = {'total_count': 2, 'artifacts': [unrelated]}
        page2 = {'total_count': 2, 'artifacts': [matching]}
        get_calls = []
        bytes_calls = []

        def fake_get(path):
            get_calls.append(path)
            if path == f'actions/runs/{_RUN_ID}/artifacts?per_page=100&page=1':
                return page1
            if path == f'actions/runs/{_RUN_ID}/artifacts?per_page=100&page=2':
                return page2
            raise RuntimeError(f'Unexpected path: {path}')

        def fake_get_bytes(path):
            bytes_calls.append(path)
            return valid_zip

        result = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=fake_get, get_bytes=fake_get_bytes,
        )
        self.assertEqual(result, _RECEIPT_CONTENT)
        self.assertEqual(
            get_calls,
            [
                f'actions/runs/{_RUN_ID}/artifacts?per_page=100&page=1',
                f'actions/runs/{_RUN_ID}/artifacts?per_page=100&page=2',
            ],
        )
        self.assertEqual(bytes_calls, ['actions/artifacts/101/zip'])

    def test_second_match_on_later_page(self):
        matching1 = _artifact(101)
        matching2 = _artifact(102)

        page1 = {'total_count': 2, 'artifacts': [matching1]}
        page2 = {'total_count': 2, 'artifacts': [matching2]}

        def fake_get(path):
            if path == f'actions/runs/{_RUN_ID}/artifacts?per_page=100&page=1':
                return page1
            if path == f'actions/runs/{_RUN_ID}/artifacts?per_page=100&page=2':
                return page2
            raise RuntimeError(f'Unexpected path: {path}')

        result = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=fake_get, get_bytes=lambda _: b'',
        )
        self.assertIsNone(result)

    def test_listing_past_the_bound(self):
        # Case A: total_count is larger than artifacts returned across pages
        page1 = {'total_count': 5, 'artifacts': [_artifact(101)]}
        page2 = {'total_count': 5, 'artifacts': []}

        def fake_get_short(path):
            if path.endswith('&page=1'):
                return page1
            return page2

        result_short = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=fake_get_short, get_bytes=lambda _: b'',
        )
        self.assertIsNone(result_short)

        # Case B: all pages within bound read, but total_count exceeds items seen
        def fake_get_past_bound(path):
            # Page limit is 10, return 1 artifact per page for 10 pages with total_count=11
            return {'total_count': 11, 'artifacts': [_artifact(200, name='unrelated')]}

        result_bound = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=fake_get_past_bound, get_bytes=lambda _: b'',
        )
        self.assertIsNone(result_bound)

    def test_expired(self):
        art = _artifact(101, expired=True)
        data = {'total_count': 1, 'artifacts': [art]}
        result = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=lambda _: data, get_bytes=lambda _: b'',
        )
        self.assertIsNone(result)

    def test_oversized(self):
        # Case A: artifact size in listing metadata exceeds zip limit
        oversized_art = _artifact(101, size=remote_test._HOSTED_ZIP_LIMIT + 1)
        data_a = {'total_count': 1, 'artifacts': [oversized_art]}
        result_a = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=lambda _: data_a, get_bytes=lambda _: b'',
        )
        self.assertIsNone(result_a)

        # Case B: downloaded zip exceeds zip limit
        art = _artifact(101, size=100)
        data_b = {'total_count': 1, 'artifacts': [art]}
        oversized_zip = b'PK\x03\x04' + (b'x' * (remote_test._HOSTED_ZIP_LIMIT + 1))
        result_b = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=lambda _: data_b, get_bytes=lambda _: oversized_zip,
        )
        self.assertIsNone(result_b)

    def test_misnamed(self):
        for bad_name in (
            'other-receipt-' + _NONCE,
            'remote-test-receipt',
            _NONCE,
            'remote-test-receipt-' + _NONCE + '-extra',
            'receipt.json',
        ):
            with self.subTest(bad_name=bad_name):
                art = _artifact(101, name=bad_name)
                data = {'total_count': 1, 'artifacts': [art]}
                result = remote_test._hosted_artifact(
                    _RUN_ID, _NONCE, get=lambda _: data, get_bytes=lambda _: b'',
                )
                self.assertIsNone(result)

    def test_non_zip(self):
        valid_art = _artifact(101)
        data = {'total_count': 1, 'artifacts': [valid_art]}
        result = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=lambda _: data, get_bytes=lambda _: b'this is not a zip file',
        )
        self.assertIsNone(result)

    def test_two_members(self):
        two_members_zip = _make_zip(extra_member=b'second-member-content')
        valid_art = _artifact(101, size=len(two_members_zip))
        data = {'total_count': 1, 'artifacts': [valid_art]}
        result = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=lambda _: data, get_bytes=lambda _: two_members_zip,
        )
        self.assertIsNone(result)

    def test_nested_member(self):
        nested_zip = _make_zip(filename='subdir/receipt.json')
        valid_art = _artifact(101, size=len(nested_zip))
        data = {'total_count': 1, 'artifacts': [valid_art]}
        result = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=lambda _: data, get_bytes=lambda _: nested_zip,
        )
        self.assertIsNone(result)

    def test_oversized_member(self):
        oversized_content = b'x' * (remote_test._HOSTED_MEMBER_LIMIT + 1)
        oversized_member_zip = _make_zip(content=oversized_content)
        valid_art = _artifact(101, size=len(oversized_member_zip))
        data = {'total_count': 1, 'artifacts': [valid_art]}
        result = remote_test._hosted_artifact(
            _RUN_ID, _NONCE, get=lambda _: data, get_bytes=lambda _: oversized_member_zip,
        )
        self.assertIsNone(result)

    def test_invalid_run_id_and_nonce(self):
        valid_zip = _make_zip()

        for bad_run_id in (0, -1, 'abc', '', None, 12.34):
            with self.subTest(bad_run_id=bad_run_id):
                result = remote_test._hosted_artifact(
                    bad_run_id, _NONCE, get=lambda _: {}, get_bytes=lambda _: valid_zip,
                )
                self.assertIsNone(result)

        for bad_nonce in ('', 'short', _NONCE[:-1], _NONCE + 'a', _NONCE.upper(), 'g' * 32, None, 123):
            with self.subTest(bad_nonce=bad_nonce):
                result = remote_test._hosted_artifact(
                    _RUN_ID, bad_nonce, get=lambda _: {}, get_bytes=lambda _: valid_zip,
                )
                self.assertIsNone(result)


class HostedGetBytesTests(unittest.TestCase):
    def test_path_pattern(self):
        self.assertIsNotNone(remote_test._ARTIFACT_ZIP_PATH.fullmatch('actions/artifacts/12345/zip'))
        self.assertIsNotNone(remote_test._ARTIFACT_ZIP_PATH.fullmatch('actions/artifacts/0/zip'))
        self.assertIsNone(remote_test._ARTIFACT_ZIP_PATH.fullmatch('actions/artifacts/zip'))
        self.assertIsNone(remote_test._ARTIFACT_ZIP_PATH.fullmatch('actions/artifacts/abc/zip'))
        self.assertIsNone(remote_test._ARTIFACT_ZIP_PATH.fullmatch('actions/artifacts/123/tar'))
        self.assertIsNone(remote_test._ARTIFACT_ZIP_PATH.fullmatch('/actions/artifacts/123/zip'))
        self.assertIsNone(remote_test._ARTIFACT_ZIP_PATH.fullmatch('actions/artifacts/123/zip/'))
        self.assertIsNone(remote_test._ARTIFACT_ZIP_PATH.fullmatch('actions/workflows/123/dispatches'))

    def test_path_validation_raises(self):
        for bad_path in (
            'actions/artifacts/zip',
            'actions/artifacts/abc/zip',
            'actions/workflows/123/dispatches',
            'actions/artifacts/123/tar',
            '',
            None,
            123,
        ):
            with self.subTest(bad_path=bad_path):
                with self.assertRaises(ValueError):
                    remote_test._hosted_get_bytes(bad_path)


if __name__ == '__main__':
    unittest.main()
