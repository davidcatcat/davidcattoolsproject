"""#1928 PR3a: verify_recovery runs E1-E5 on fresh store reads and never writes."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import test_review_repair_recovery_evidence as ev
import test_review_repair_recovery_identity as ident
from llmtool import review_repair, review_repair_recovery as rec, task_completion
from llmtool.errors import LLMToolError

ROOT, REPAIR, HEAD = ident.ROOT, ident.REPAIR, ident.HEAD


class FakeStore:
    def __init__(self, records):
        self.records, self.writes = records, 0

    def task_ids(self):
        return list(self.records)

    def get(self, task_id):
        return json.loads(json.dumps(self.records[task_id]))


def chain(profile='gw-agent'):
    root = dict(ident.identity_root(), developer={'provider_profile': profile})
    _, repair = ev.records(profile)
    repair.update(ident.recorded())
    repair['completion']['evidence'].update(task_id=REPAIR, root='/d6', branch='fix/x')
    for item in repair['dispatch_audit']:
        item['source_sha'] = HEAD
    repair['developer']['source_sha'] = repair['completion']['evidence']['source_sha'] = HEAD
    return root, repair


class VerifyRecoveryTests(unittest.TestCase):
    def verify(self, root, repair, result=HEAD, others=()):
        store = FakeStore({ROOT: root, REPAIR: repair, **{o['task_id']: o for o in others}})
        d6 = SimpleNamespace(root='/d6', branch='fix/x', result_sha=result)
        with mock.patch.object(review_repair, '_registered_d6', return_value=(d6, None)), \
                mock.patch.object(task_completion, '_is_ancestor', return_value=False):
            return rec.verify_recovery(store, root, REPAIR, executor_task='exec-1', pr_number=17)

    def refused(self, *args, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            self.verify(*args, **kwargs)
        return caught.exception.review_repair_code

    def test_full_evidence_returns_the_claim(self):
        self.assertEqual(self.verify(*chain()), ident.CLAIM)

    def test_any_failed_check_refuses(self):
        root, repair = chain()
        self.assertEqual(self.refused(root, repair, result='9' * 40),
                         'REPAIR_RECOVERY_EVIDENCE_INVALID')
        for path, value in ((('request',), 'REPAIR 2/2'), (('dispatch_audit',), []),
                            (('developer', 'state'), 'DEVELOPER_DISPATCHING'),
                            (('task_id',), 'f' * 32)):
            root, repair = chain()
            target = repair
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path):
                self.assertEqual(self.refused(root, repair), 'REPAIR_RECOVERY_EVIDENCE_INVALID')
        root, repair = chain()
        other = {'task_id': 'f' * 32, 'review_repair_claims': [dict(ident.CLAIM, pr=18)]}
        self.assertEqual(self.refused(root, repair, others=[other]),
                         'REPAIR_RECOVERY_CLAIM_INVALID')


if __name__ == '__main__':
    unittest.main()
