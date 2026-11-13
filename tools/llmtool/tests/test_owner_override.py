"""Tests for #1502 WP-E owner model override validation."""

import unittest

from llmtool.owner_override import OwnerOverrideError, validate_owner_override

REPOSITORY = 'example-owner/example-repo'
ROUTE = 'example-route'
MODEL = 'example-model-1'
COMMENT_ID = 345
REF = ('https://github.com/example-owner/example-repo'
       '/issues/12#issuecomment-345')
ISSUE_URL = ('https://api.github.com/repos/example-owner/example-repo'
             '/issues/12')
MARKER = (
    '<!-- activity: type=pm_progress actor=PM target=Owner '
    'status=ANSWERED -->'
)
OVERRIDE_LINE = 'owner-model-override: route=example-route model=example-model-1'


def valid_override():
    return {'model': MODEL, 'ref': REF}


class FakeFetch:
    """Record calls and return or raise a caller-supplied result."""

    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result
        self.error = error

    def __call__(self, comment_id):
        self.calls.append(comment_id)
        if self.error is not None:
            raise self.error
        return self.result


def make_comment(body=None, issue_url=ISSUE_URL):
    result = {'issue_url': issue_url}
    if body is not None:
        result['body'] = body
    return result


class OwnerOverrideTest(unittest.TestCase):
    def assert_refused(self, code, *, override=None, route=ROUTE,
                       repository=REPOSITORY, fetch=None):
        if override is None:
            override = valid_override()
        if fetch is None:
            fetch = FakeFetch(make_comment(MARKER + '\n' + OVERRIDE_LINE))
        with self.assertRaises(OwnerOverrideError) as raised:
            validate_owner_override(
                override, route,
                repository=repository, fetch_comment=fetch)
        self.assertEqual(raised.exception.code, code)
        return fetch

    def test_success_returns_exact_values_and_fetches_once(self):
        body = '\n  ' + MARKER + '  \n\t' + OVERRIDE_LINE + '  \n'
        fetch = FakeFetch(make_comment(body))
        result = validate_owner_override(
            valid_override(), ROUTE,
            repository=REPOSITORY, fetch_comment=fetch)
        self.assertEqual(result, {
            'route': ROUTE,
            'model': MODEL,
            'ref': REF,
            'comment_id': COMMENT_ID,
        })
        self.assertEqual(fetch.calls, [COMMENT_ID])
        self.assertIs(type(fetch.calls[0]), int)

    def test_extra_override_key_is_invalid(self):
        override = valid_override()
        override['extra'] = 'example-value'
        self.assert_refused('OWNER_OVERRIDE_INVALID', override=override)

    def test_bad_model_text_is_invalid(self):
        override = valid_override()
        override['model'] = 'example model'
        self.assert_refused('OWNER_OVERRIDE_INVALID', override=override)

    def test_model_with_scheme_separator_is_invalid(self):
        override = valid_override()
        override['model'] = 'example://model'
        self.assert_refused('OWNER_OVERRIDE_INVALID', override=override)

    def test_bad_route_is_invalid(self):
        self.assert_refused('OWNER_OVERRIDE_INVALID', route='Example_Route')

    def test_bad_repository_argument_is_invalid(self):
        self.assert_refused('OWNER_OVERRIDE_INVALID', repository='example-owner')

    def test_pull_ref_is_invalid(self):
        override = valid_override()
        override['ref'] = REF.replace('/issues/', '/pull/')
        fetch = FakeFetch(make_comment(MARKER + '\n' + OVERRIDE_LINE))
        self.assert_refused('OWNER_OVERRIDE_REF_INVALID',
                            override=override, fetch=fetch)
        self.assertEqual(fetch.calls, [])

    def test_ref_of_another_repository_is_invalid(self):
        override = valid_override()
        override['ref'] = REF.replace(
            'example-owner/example-repo', 'other-owner/other-repo')
        self.assert_refused('OWNER_OVERRIDE_REF_INVALID', override=override)

    def test_leading_zero_issue_id_is_invalid(self):
        override = valid_override()
        override['ref'] = REF.replace('/issues/12', '/issues/012')
        self.assert_refused('OWNER_OVERRIDE_REF_INVALID', override=override)

    def test_trailing_text_after_ref_is_invalid(self):
        override = valid_override()
        override['ref'] = REF + 'x'
        self.assert_refused('OWNER_OVERRIDE_REF_INVALID', override=override)

    def test_non_str_ref_is_invalid(self):
        override = valid_override()
        override['ref'] = 123
        self.assert_refused('OWNER_OVERRIDE_REF_INVALID', override=override)

    def test_fetch_exception_is_unreadable_and_called_once(self):
        fetch = FakeFetch(error=RuntimeError('example failure'))
        self.assert_refused('OWNER_OVERRIDE_REF_UNREADABLE', fetch=fetch)
        self.assertEqual(fetch.calls, [COMMENT_ID])

    def test_non_dict_result_is_unreadable(self):
        self.assert_refused('OWNER_OVERRIDE_REF_UNREADABLE',
                            fetch=FakeFetch('not-a-dict'))

    def test_missing_body_is_unreadable(self):
        self.assert_refused('OWNER_OVERRIDE_REF_UNREADABLE',
                            fetch=FakeFetch(make_comment()))

    def test_issue_url_of_another_issue_is_invalid(self):
        wrong_url = ISSUE_URL.replace('/issues/12', '/issues/13')
        self.assert_refused(
            'OWNER_OVERRIDE_REF_INVALID',
            fetch=FakeFetch(make_comment(
                MARKER + '\n' + OVERRIDE_LINE, issue_url=wrong_url)))

    def test_missing_marker_is_refused(self):
        self.assert_refused(
            'OWNER_OVERRIDE_MARKER_MISSING',
            fetch=FakeFetch(make_comment(OVERRIDE_LINE)))

    def test_marker_with_tracking_status_is_refused(self):
        tracking = MARKER.replace('status=ANSWERED', 'status=TRACKING')
        self.assert_refused(
            'OWNER_OVERRIDE_MARKER_MISSING',
            fetch=FakeFetch(make_comment(tracking + '\n' + OVERRIDE_LINE)))

    def test_no_override_line_is_missing(self):
        self.assert_refused(
            'OWNER_OVERRIDE_LINE_MISSING',
            fetch=FakeFetch(make_comment(MARKER)))

    def test_two_override_lines_are_ambiguous(self):
        body = MARKER + '\n' + OVERRIDE_LINE + '\n' + OVERRIDE_LINE
        self.assert_refused(
            'OWNER_OVERRIDE_LINE_AMBIGUOUS',
            fetch=FakeFetch(make_comment(body)))

    def test_malformed_override_line_is_missing(self):
        self.assert_refused(
            'OWNER_OVERRIDE_LINE_MISSING',
            fetch=FakeFetch(make_comment(
                MARKER + '\nowner-model-override: route=example-route')))

    def test_route_mismatch_is_refused(self):
        line = OVERRIDE_LINE.replace('route=example-route', 'route=other-route')
        self.assert_refused(
            'OWNER_OVERRIDE_MISMATCH',
            fetch=FakeFetch(make_comment(MARKER + '\n' + line)))

    def test_model_mismatch_is_refused(self):
        line = OVERRIDE_LINE.replace(
            'model=example-model-1', 'model=example-model-2')
        self.assert_refused(
            'OWNER_OVERRIDE_MISMATCH',
            fetch=FakeFetch(make_comment(MARKER + '\n' + line)))


if __name__ == '__main__':
    unittest.main()
