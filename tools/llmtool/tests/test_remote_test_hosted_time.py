"""Direct tests for the hosted-run time helpers of remote test part 4.

Issue #2281 and Issue #2602 item g: ``_as_utc_datetime`` and
``_parse_dispatched_at`` (defined in ``remote_test_part4.py`` and executed
inside the ``llmtool.remote_test`` module namespace) had no direct tests.

Only ``unittest`` is used; the tests exercise both functions with accepted
and refused inputs and check the resulting datetimes or the raised errors.
No network access and no real remote host are involved, and no test data
resembles a credential, key or token.
"""

import unittest
from datetime import datetime, timedelta, timezone

from llmtool.remote_test import (
    _as_utc_datetime,
    _parse_dispatched_at,
)


class AsUtcDatetimeTest(unittest.TestCase):
    """Accepted and refused inputs for _as_utc_datetime."""

    def test_none_yields_current_utc_time(self):
        result = _as_utc_datetime(None)
        self.assertIsInstance(result, datetime)
        self.assertIsNotNone(result.tzinfo)
        self.assertLess(
            abs((result - datetime.now(timezone.utc)).total_seconds()), 5)

    def test_naive_datetime_is_assumed_utc(self):
        self.assertEqual(_as_utc_datetime(datetime(2024, 5, 1, 12, 30)),
                         datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc))

    def test_aware_datetime_is_converted_to_utc(self):
        self.assertEqual(
            _as_utc_datetime(
                datetime(2024, 5, 1, 14, 30,
                         tzinfo=timezone(timedelta(hours=2)))),
            datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc))

    def test_numeric_timestamps_are_from_timestamp_utc(self):
        self.assertEqual(_as_utc_datetime(0),
                         datetime(1970, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(_as_utc_datetime(1.5),
                         datetime(1970, 1, 1, 0, 0, 1, 500000,
                                  tzinfo=timezone.utc))

    def test_isoformat_strings(self):
        utc = datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc)
        self.assertEqual(_as_utc_datetime('2024-05-01T12:30:00Z'), utc)
        self.assertEqual(_as_utc_datetime('2024-05-01T12:30:00'), utc)
        self.assertEqual(_as_utc_datetime('2024-05-01T14:30:00+02:00'),
                         utc)

    def test_callable_is_invoked_before_parsing(self):
        self.assertEqual(_as_utc_datetime(lambda: '2024-05-01T12:30:00Z'),
                         datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc))

    def test_unsupported_types_raise_type_error(self):
        for bad in ([], {}, object(), b'2024-05-01T12:30:00Z'):
            with self.assertRaises(TypeError):
                _as_utc_datetime(bad)

    def test_unparseable_string_raises_value_error(self):
        with self.assertRaises(ValueError):
            _as_utc_datetime('not-a-timestamp')


class ParseDispatchedAtTest(unittest.TestCase):
    """Accepted and refused inputs for _parse_dispatched_at."""

    def test_z_suffixed_string(self):
        self.assertEqual(_parse_dispatched_at('2024-05-01T12:30:00Z'),
                         datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc))

    def test_offset_string_is_converted_to_utc(self):
        self.assertEqual(
            _parse_dispatched_at('2024-05-01T14:30:00+02:00'),
            datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc))

    def test_naive_string_is_assumed_utc(self):
        self.assertEqual(_parse_dispatched_at('2024-05-01T12:30:00'),
                         datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc))

    def test_dispatch_format_round_trips(self):
        raw = datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc).strftime(
            '%Y-%m-%dT%H:%M:%SZ')
        self.assertEqual(_parse_dispatched_at(raw),
                         datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc))

    def test_non_string_values_raise_attribute_error(self):
        for bad in (None, 1714578600, 1.0, [], {}):
            with self.assertRaises(AttributeError):
                _parse_dispatched_at(bad)

    def test_unparseable_string_raises_value_error(self):
        with self.assertRaises(ValueError):
            _parse_dispatched_at('not-a-timestamp')


if __name__ == '__main__':
    unittest.main()
