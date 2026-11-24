"""#2281 row 12: the review packet limit follows the policy key
review_packet_limit_bytes (A1 absent -> 262144, A2 configured value applies,
A3 invalid -> POLICY_INVALID). Real policy loader on a temp policy file; the
modules are reloaded so their import-time constants are read again, then their
original state is restored. No network, process launch or credentials."""

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool import repair_policy as rp
from llmtool import task_review_part1 as part1
from llmtool import task_review_part2 as part2
from llmtool.errors import LLMToolError

BASE = json.loads((Path(__file__).resolve().parent.parent / 'policy.json').read_text('utf-8'))
KEY = 'review_packet_limit_bytes'


class ReviewPacketPolicyTest(unittest.TestCase):
    def setUp(self):
        # Reloading re-creates the module classes; put the original objects back
        # so later test modules keep their imported identities.
        self._originals = ((part1, dict(part1.__dict__)),
                           (part2, dict(part2.__dict__)))
        self.addCleanup(self._restore)

    def _restore(self):
        for module, state in self._originals:
            module.__dict__.clear()
            module.__dict__.update(state)

    def policy(self, policy):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'policy.json'
        path.write_bytes(policy if type(policy) is bytes else json.dumps(policy).encode('utf-8'))
        patcher = mock.patch.object(rp, 'policy_path', return_value=str(path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _reload(self):
        importlib.reload(part1)
        importlib.reload(part2)

    def test_absent_key_keeps_default(self):
        self.policy({key: value for key, value in BASE.items() if key != KEY})
        self._reload()
        self.assertEqual(part1.PACKET_LIMIT, 262144)
        self.assertEqual(part1.PACKET_LIMIT, rp.REVIEW_PACKET_LIMIT_BYTES)
        self.assertEqual(part2.PACKET_LIMIT, part1.PACKET_LIMIT)

    def test_configured_limit_bounds_packet(self):
        limit = 4096
        self.policy(dict(BASE, **{KEY: limit}))
        self._reload()
        self.assertEqual(part1.PACKET_LIMIT, limit)
        self.assertEqual(part2.PACKET_LIMIT, limit)
        packet = b'{}' + b' ' * (limit - 2)
        self.assertEqual(part2.ReviewPacket(packet).data, packet)
        with self.assertRaises(LLMToolError) as caught:
            part2.ReviewPacket(packet + b' ')
        self.assertEqual(caught.exception.review_code, 'REVIEW_PACKET_TOO_LARGE')

    def test_invalid_key_refuses_policy_invalid(self):
        for value in (0, -1, True, '4096'):
            with self.subTest(value=value):
                self.policy(dict(BASE, **{KEY: value}))
                with self.assertRaises(LLMToolError) as caught:
                    rp.review_packet_limit_bytes()
                code = (getattr(caught.exception, 'code', None) or
                        getattr(caught.exception, 'review_code', None) or
                        str(caught.exception))
                self.assertEqual(code, 'POLICY_INVALID')


if __name__ == '__main__':
    unittest.main()
