"""HTTP status classification for provider transport errors."""

import unittest

from llmtool.errors import http_error_code


class HttpErrorCodeTests(unittest.TestCase):
    def test_authentication_statuses_are_classified_as_provider_rejections(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.assertEqual(http_error_code(status), 'AUTH_PROVIDER_REJECTED')

    def test_rate_limit_status_is_classified_separately(self):
        self.assertEqual(http_error_code(429), 'PROVIDER_RATE_LIMITED')

    def test_adjacent_and_other_http_statuses_keep_generic_classification(self):
        for status in (400, 402, 428, 430, 500, 503):
            with self.subTest(status=status):
                self.assertEqual(http_error_code(status), 'PROVIDER_HTTP_ERROR')


if __name__ == '__main__':
    unittest.main()
