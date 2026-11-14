"""#1928 PR2: E3 authorization (and later E4/E5) checks for repair claim recovery."""
import unittest

from llmtool import review_repair_recovery as rec
from llmtool.errors import LLMToolError

HEAD, DIGEST, ASSIGN = 'c' * 40, 'd' * 64, 'a' * 32
CLAIM = {'executor_task': 'exec-1', 'head_sha': HEAD, 'repair_task_id': 'b' * 32}
WORKERS = {'gw-agent': 'gw-agent', 'opencode': 'opencode-developer',
           'claude': 'claude-developer', 'agy': 'agy'}


def entry(attempt, **extra):
    return {'action': 'DISPATCH', 'decision': 'ALLOWED', 'worker_key': 'gw-agent',
            'attempt_id': attempt, 'assignment_id': ASSIGN, 'source_sha': HEAD,
            'contract_digest': DIGEST, **extra}


def records(profile='gw-agent'):
    root = {'task_id': 'e' * 32, 'developer': {'provider_profile': profile}}
    repair = {
        'task_id': 'b' * 32, 'run_id': 'f' * 32, 'task_binding': {'contract_digest': DIGEST},
        'developer': {'selection_source': 'PM', 'state': 'DEVELOPER_RESPONDED',
                      'provider_profile': profile, 'source_sha': HEAD, 'assignment_id': ASSIGN,
                      'execution': {'process_exit_code': 0, 'native_session_id': 's-1'}},
        'completion': {'executor_task_id': 'exec-1', 'evidence': {
            'task_id': 'b' * 32, 'run_id': 'f' * 32, 'contract_digest': DIGEST,
            'source_sha': HEAD}},
        'dispatch_audit': [entry('1' * 32), entry('2' * 32, authorization_id='1' * 32)]}
    for item in repair['dispatch_audit']:
        item['worker_key'] = WORKERS[profile]
    return root, repair


class RecoveryEvidenceTests(unittest.TestCase):
    def refused(self, check, root, repair):
        with self.assertRaises(LLMToolError) as caught:
            check(root, repair, CLAIM)
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_RECOVERY_EVIDENCE_INVALID')

    def changed(self, path, value, profile='gw-agent'):
        root, repair = records(profile)
        target = repair
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return root, repair

    def test_valid_records_pass_e3(self):
        for profile in ('gw-agent', 'opencode', 'claude', 'agy'):
            root, repair = records(profile)
            if profile in ('claude', 'agy'):
                repair['dispatch_audit'] = repair['dispatch_audit'][:1]
            with self.subTest(profile=profile):
                self.assertIsNone(rec.check_authorization(root, repair, CLAIM))

    def test_e3_refuses_missing_or_mismatched_authorization(self):
        bad = [entry('1' * 32, decision='BLOCKED'), entry('1' * 32, source_sha='9' * 40),
               entry('1' * 32, contract_digest='9' * 64), entry('1' * 32, worker_key='agy'),
               entry('1' * 32, assignment_id='9' * 32), entry('1' * 32, action='REPAIR')]
        for first in bad:
            with self.subTest(first=first):
                self.refused(rec.check_authorization, *self.changed(
                    ('dispatch_audit',), [first, entry('2' * 32, authorization_id='1' * 32)]))
        # gw-agent and opencode also need the adapter entry bound to the transition.
        for audit in ([entry('1' * 32)], [entry('2' * 32, authorization_id='2' * 32)],
                      [entry('1' * 32), entry('2' * 32, authorization_id='9' * 32)]):
            with self.subTest(audit=audit):
                self.refused(rec.check_authorization, *self.changed(('dispatch_audit',), audit))
        root, repair = records()
        root['developer']['provider_profile'] = 'codex'
        self.refused(rec.check_authorization, root, repair)


if __name__ == '__main__':
    unittest.main()
