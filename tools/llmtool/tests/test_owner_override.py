"""Tests for the #1502 WP-E owner override accident guard."""

import unittest

from llmtool.owner_override import OwnerOverrideError, validate_owner_override

COMMENT_ID = 987654321
REF = ("https://github.com/davidcatcat/davidcattoolsproject/issues/1502"
       "#issuecomment-987654321")
MARKER = "<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->"
ISSUE_URL = "https://api.github.com/repos/davidcatcat/davidcattoolsproject/issues/1502"
MODEL = "example-model-1"
ROUTE = "build"

_UNSET = object()


class FakeFetch:
    """Record comment lookups and return or raise a prepared result."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def __call__(self, comment_id):
        self.calls.append(comment_id)
        if self.error is not None:
            raise self.error
        return self.result


def comment(body, issue_url=ISSUE_URL):
    return {"body": body, "issue_url": issue_url}


def body(*lines):
    return "\n".join(lines)


def override_line(route=ROUTE, model=MODEL):
    return "owner-model-override: route={0} model={1}".format(route, model)


class OwnerOverrideTest(unittest.TestCase):
    def setUp(self):
        self.override = {"model": MODEL, "ref": REF}

    def make_fetch(self, result=_UNSET, error=None):
        if result is _UNSET:
            result = comment(body(MARKER, override_line()))
        return FakeFetch(result, error)

    def validate(self, fetch, override=_UNSET, route=_UNSET):
        return validate_owner_override(
            self.override if override is _UNSET else override,
            ROUTE if route is _UNSET else route,
            fetch_comment=fetch,
        )

    def assert_refusal(self, code, fetch, override=_UNSET, route=_UNSET):
        with self.assertRaises(OwnerOverrideError) as caught:
            self.validate(fetch, override, route)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_success_returns_exact_dict_and_reads_comment_once(self):
        fetch = self.make_fetch()
        result = self.validate(fetch)
        self.assertEqual(result, {
            "route": ROUTE,
            "model": MODEL,
            "ref": REF,
            "comment_id": COMMENT_ID,
        })
        self.assertEqual(fetch.calls, [COMMENT_ID])

    def test_success_ignores_surrounding_whitespace(self):
        fetch = self.make_fetch(comment(body(
            "  " + MARKER + "  ",
            "   " + override_line() + "   ",
        )))
        self.assertEqual(self.validate(fetch)["comment_id"], COMMENT_ID)

    def test_success_returns_a_new_dict(self):
        result = self.validate(self.make_fetch())
        result["model"] = "changed"
        self.assertEqual(self.override["model"], MODEL)

    def test_extra_override_key_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_INVALID", fetch, override={
            "model": MODEL, "ref": REF, "extra": "no"})

    def test_missing_override_key_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_INVALID", fetch, override={
            "model": MODEL})

    def test_non_dict_override_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_INVALID", fetch, override=None)

    def test_bad_model_text_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_INVALID", fetch, override={
            "model": "example model 1", "ref": REF})

    def test_non_str_model_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_INVALID", fetch, override={
            "model": 1, "ref": REF})

    def test_model_with_scheme_separator_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_INVALID", fetch, override={
            "model": "example://model-1", "ref": REF})

    def test_bad_route_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_INVALID", fetch, route="Build")

    def test_non_str_route_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_INVALID", fetch, route=None)

    def test_pull_request_ref_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_REF_INVALID", fetch, override={
            "model": MODEL,
            "ref": ("https://github.com/davidcatcat/davidcattoolsproject"
                    "/pull/1502#issuecomment-987654321"),
        })
        self.assertEqual(fetch.calls, [])

    def test_other_owner_ref_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_REF_INVALID", fetch, override={
            "model": MODEL,
            "ref": ("https://github.com/othercat/davidcattoolsproject"
                    "/issues/1502#issuecomment-987654321"),
        })

    def test_other_repository_ref_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_REF_INVALID", fetch, override={
            "model": MODEL,
            "ref": ("https://github.com/davidcatcat/otherrepo"
                    "/issues/1502#issuecomment-987654321"),
        })

    def test_leading_zero_issue_number_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_REF_INVALID", fetch, override={
            "model": MODEL,
            "ref": ("https://github.com/davidcatcat/davidcattoolsproject"
                    "/issues/01502#issuecomment-987654321"),
        })

    def test_leading_zero_comment_id_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_REF_INVALID", fetch, override={
            "model": MODEL,
            "ref": ("https://github.com/davidcatcat/davidcattoolsproject"
                    "/issues/1502#issuecomment-0987654321"),
        })

    def test_trailing_text_after_ref_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_REF_INVALID", fetch, override={
            "model": MODEL, "ref": REF + " trailing"})

    def test_query_string_on_ref_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_REF_INVALID", fetch, override={
            "model": MODEL, "ref": REF + "?plain=1"})

    def test_non_str_ref_is_invalid(self):
        fetch = self.make_fetch()
        self.assert_refusal("OWNER_OVERRIDE_REF_INVALID", fetch, override={
            "model": MODEL, "ref": 1502})

    def test_fetch_raising_is_unreadable_and_called_once(self):
        fetch = FakeFetch(error=RuntimeError("lookup failed"))
        error = self.assert_refusal("OWNER_OVERRIDE_REF_UNREADABLE", fetch)
        self.assertEqual(fetch.calls, [COMMENT_ID])
        self.assertIsNone(error.__cause__)

    def test_fetch_returning_non_dict_is_unreadable(self):
        fetch = self.make_fetch(result=["not", "a", "comment"])
        self.assert_refusal("OWNER_OVERRIDE_REF_UNREADABLE", fetch)

    def test_missing_body_is_unreadable(self):
        fetch = self.make_fetch(result={"issue_url": ISSUE_URL})
        self.assert_refusal("OWNER_OVERRIDE_REF_UNREADABLE", fetch)

    def test_non_str_body_is_unreadable(self):
        fetch = self.make_fetch(result=comment(None))
        self.assert_refusal("OWNER_OVERRIDE_REF_UNREADABLE", fetch)

    def test_missing_issue_url_is_unreadable(self):
        fetch = self.make_fetch(result={"body": body(MARKER, override_line())})
        self.assert_refusal("OWNER_OVERRIDE_REF_UNREADABLE", fetch)

    def test_issue_url_for_different_issue_is_invalid(self):
        fetch = self.make_fetch(result=comment(
            body(MARKER, override_line()), ISSUE_URL.replace("/1502", "/1503")))
        self.assert_refusal("OWNER_OVERRIDE_REF_INVALID", fetch)

    def test_missing_marker_is_rejected(self):
        fetch = self.make_fetch(result=comment(body(override_line())))
        self.assert_refusal("OWNER_OVERRIDE_MARKER_MISSING", fetch)

    def test_marker_with_tracking_status_is_rejected(self):
        marker = MARKER.replace("status=ANSWERED", "status=TRACKING")
        fetch = self.make_fetch(result=comment(body(marker, override_line())))
        self.assert_refusal("OWNER_OVERRIDE_MARKER_MISSING", fetch)

    def test_marker_with_other_target_is_rejected(self):
        marker = MARKER.replace("target=Owner", "target=PM")
        fetch = self.make_fetch(result=comment(body(marker, override_line())))
        self.assert_refusal("OWNER_OVERRIDE_MARKER_MISSING", fetch)

    def test_no_override_line_is_rejected(self):
        fetch = self.make_fetch(result=comment(body(MARKER)))
        self.assert_refusal("OWNER_OVERRIDE_LINE_MISSING", fetch)

    def test_two_override_lines_are_ambiguous(self):
        fetch = self.make_fetch(result=comment(body(
            MARKER,
            override_line(),
            override_line(model="example-model-2"),
        )))
        self.assert_refusal("OWNER_OVERRIDE_LINE_AMBIGUOUS", fetch)

    def test_malformed_override_line_is_missing(self):
        fetch = self.make_fetch(result=comment(body(
            MARKER, "owner-model-override: model=" + MODEL)))
        self.assert_refusal("OWNER_OVERRIDE_LINE_MISSING", fetch)

    def test_override_line_with_trailing_text_is_missing(self):
        fetch = self.make_fetch(result=comment(body(
            MARKER, override_line() + " extra")))
        self.assert_refusal("OWNER_OVERRIDE_LINE_MISSING", fetch)

    def test_route_mismatch_is_rejected(self):
        fetch = self.make_fetch(result=comment(body(
            MARKER, override_line(route="release"))))
        self.assert_refusal("OWNER_OVERRIDE_MISMATCH", fetch)

    def test_model_mismatch_is_rejected(self):
        fetch = self.make_fetch(result=comment(body(
            MARKER, override_line(model="example-model-2"))))
        self.assert_refusal("OWNER_OVERRIDE_MISMATCH", fetch)


if __name__ == "__main__":
    unittest.main()
