"""Offline unit coverage for probe input screening helpers."""

import unittest

from probe import _local_url, _safe_served_model


class ProbeGuardTests(unittest.TestCase):
    def test_loopback_override_requires_plain_local_origin(self):
        self.assertTrue(_local_url("http://127.0.0.1:8123"))
        self.assertTrue(_local_url("http://localhost:8123/"))
        self.assertFalse(_local_url("https://example.invalid:443"))
        self.assertFalse(_local_url("http://localhost:8123/path"))
        self.assertFalse(_local_url("http://user:pass@127.0.0.1:8123"))

    def test_served_model_is_rejected_if_credential_like(self):
        self.assertEqual(_safe_served_model("demo/model-v1", "private-key"), "demo/model-v1")
        self.assertIsNone(_safe_served_model("sk-12345678901234567890", None))
        self.assertIsNone(_safe_served_model("model-private-key", "private-key"))


if __name__ == "__main__":
    unittest.main()
