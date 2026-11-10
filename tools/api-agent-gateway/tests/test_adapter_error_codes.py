"""Adapter transport error code tests (offline, no real HTTP traffic).

http.client.HTTPConnection is replaced with a fake whose request() raises
a scripted exception, so the real call() code path maps it to a ModelError.
The profile mirrors the shape used by tests/helpers.py (GatewayCase.profile).
"""
import errno
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from api_agent_gateway import adapters
from api_agent_gateway.adapters import ModelError


class FakeConnection:
    """Stands in for http.client.HTTPConnection; request() raises the scripted error."""

    def __init__(self, failure):
        self.failure = failure

    def request(self, *args, **kwargs):
        raise self.failure

    def close(self):
        pass


def make_adapter():
    # Profile shape mirrors tests/helpers.py; loopback host, never connected.
    profile = types.SimpleNamespace(
        adapter='openai-chat-tools',
        base_url='http://127.0.0.1:9/v1',
        model='fake-model',
        auth_env='GW_TEST_KEY',
        context_cap_tokens=200000,
        max_output_tokens=4096,
        allowed_data_classes=['synthetic', 'internal-code'],
        session_expiry_hours=72,
        timeout_seconds=10,
    )
    return adapters.make_adapter(profile, None)


class AdapterErrorCodesTest(unittest.TestCase):
    def code_for(self, failure):
        adapter = make_adapter()
        fake = FakeConnection(failure)
        with mock.patch('http.client.HTTPConnection', return_value=fake):
            with self.assertRaises(ModelError) as caught:
                adapter.call(b'{}', 5)
        # The original exception is chained, not swallowed.
        self.assertIs(caught.exception.__cause__, failure)
        return caught.exception.code

    def test_etimedout_maps_to_network_timeout(self):
        failure = OSError(errno.ETIMEDOUT, 'Operation timed out')
        self.assertEqual(self.code_for(failure), 'NETWORK_TIMEOUT')

    def test_timeout_error_maps_to_timeout(self):
        failure = TimeoutError('timed out')
        self.assertEqual(self.code_for(failure), 'TIMEOUT')

    def test_refused_connection_maps_to_transport_error(self):
        failure = ConnectionRefusedError('connection refused')
        self.assertEqual(self.code_for(failure), 'TRANSPORT_ERROR')


if __name__ == '__main__':
    unittest.main()
