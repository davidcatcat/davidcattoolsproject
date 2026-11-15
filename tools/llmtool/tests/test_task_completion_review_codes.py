import hashlib
import unittest
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool import task_completion


def make_intent():
    return task_completion.review_dispatch_intent(
        task_id='a' * 32,
        run_id='b' * 32,
        contract_digest='c' * 64,
        developer_task_id='dev-task-1',
        developer_identity='dev-identity',
        developer_session_ref='dev-session',
        source_sha='d' * 40,
        result_head='e' * 40,
        diff_sha256='f' * 64,
        changed_paths=['src/a.py'],
        repair_round=0,
    )


def rehash(obj, key, code):
    copy = {k: v for k, v in obj.items() if k != key}
    digest = hashlib.sha256(task_completion._canonical_json_bytes(copy, code)).hexdigest()
    copy[key] = digest
    obj[key] = digest
    return copy


def assert_code(code, func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except LLMToolError as exc:
        if getattr(exc, 'completion_code', None) != code:
            raise AssertionError(
                f'Expected completion_code {code!r}, got {getattr(exc, "completion_code", None)!r}'
            ) from exc
        return exc
    else:
        raise AssertionError(f'Expected LLMToolError with completion_code {code!r}, but no exception was raised')


class TestTaskCompletionReviewCodes(unittest.TestCase):

    def test_case_1_intent_digest_mismatch(self):
        intent = make_intent()
        intent['result_head'] = '9' * 40
        assert_code('REVIEW_DISPATCH_DIGEST_MISMATCH', task_completion.validate_review_dispatch_intent, intent)

    def test_case_2_intent_rehash_valid(self):
        intent = make_intent()
        intent['result_head'] = '9' * 40
        rehashed = rehash(intent, 'intent_sha256', 'REVIEW_DISPATCH_BINDING_INVALID')
        validated = task_completion.validate_review_dispatch_intent(rehashed)
        self.assertEqual(validated, rehashed)

    def test_case_3_receipt_digest_mismatch(self):
        intent = make_intent()
        receipt = task_completion.review_dispatch_blocked(intent)
        receipt['code'] = 'OTHER_CODE'
        assert_code('REVIEW_RECEIPT_DIGEST_MISMATCH', task_completion.validate_review_dispatch_receipt, intent, receipt)

    def test_case_4_receipt_binding_mismatch(self):
        intent = make_intent()
        receipt = task_completion.review_dispatch_blocked(intent)
        receipt['task_id'] = '9' * 32
        rehashed = rehash(receipt, 'receipt_sha256', 'REVIEW_RECEIPT_INVALID')
        assert_code('REVIEW_RECEIPT_BINDING_MISMATCH', task_completion.validate_review_dispatch_receipt, intent, rehashed)

    def test_case_5_reviewer_not_independent_same_developer_task_id(self):
        intent = make_intent()
        receipt = task_completion.review_dispatch_blocked(intent)
        receipt['execution_status'] = 'SUCCEEDED'
        receipt['verdict'] = 'PASS'
        receipt['security'] = 'PASS'
        receipt['functional'] = 'PASS'
        receipt['provenance'] = 'TRUSTED_REVIEW_ADAPTER'
        receipt['review_packet_digest'] = '1' * 64
        receipt['findings_sha256'] = '2' * 64
        receipt['reviewer_task_id'] = 'dev-task-1'
        receipt['reviewer_identity'] = 'rev-identity'
        receipt['reviewer_session_ref'] = 'rev-session'
        rehashed = rehash(receipt, 'receipt_sha256', 'REVIEW_RECEIPT_INVALID')
        assert_code('REVIEWER_NOT_INDEPENDENT', task_completion.validate_review_dispatch_receipt, intent, rehashed)

    def test_case_6_reviewer_independent_control(self):
        intent = make_intent()
        receipt = task_completion.review_dispatch_blocked(intent)
        receipt['execution_status'] = 'SUCCEEDED'
        receipt['verdict'] = 'PASS'
        receipt['security'] = 'PASS'
        receipt['functional'] = 'PASS'
        receipt['provenance'] = 'TRUSTED_REVIEW_ADAPTER'
        receipt['review_packet_digest'] = '1' * 64
        receipt['findings_sha256'] = '2' * 64
        receipt['reviewer_task_id'] = 'rev-task-1'
        receipt['reviewer_identity'] = 'rev-identity'
        receipt['reviewer_session_ref'] = 'rev-session'
        rehashed = rehash(receipt, 'receipt_sha256', 'REVIEW_RECEIPT_INVALID')
        validated = task_completion.validate_review_dispatch_receipt(intent, rehashed)
        self.assertEqual(validated, rehashed)

    def test_case_7_reviewer_not_independent_same_developer_session_ref(self):
        intent = make_intent()
        receipt = task_completion.review_dispatch_blocked(intent)
        receipt['execution_status'] = 'SUCCEEDED'
        receipt['verdict'] = 'PASS'
        receipt['security'] = 'PASS'
        receipt['functional'] = 'PASS'
        receipt['provenance'] = 'TRUSTED_REVIEW_ADAPTER'
        receipt['review_packet_digest'] = '1' * 64
        receipt['findings_sha256'] = '2' * 64
        receipt['reviewer_task_id'] = 'rev-task-1'
        receipt['reviewer_identity'] = 'rev-identity'
        receipt['reviewer_session_ref'] = 'dev-session'
        rehashed = rehash(receipt, 'receipt_sha256', 'REVIEW_RECEIPT_INVALID')
        assert_code('REVIEWER_NOT_INDEPENDENT', task_completion.validate_review_dispatch_receipt, intent, rehashed)

    def test_case_8_review_handoff_record_valid_and_binding_mismatch(self):
        intent = make_intent()
        record = task_completion.review_handoff_pending(intent)
        validated = task_completion.validate_review_handoff_record(
            record,
            task_id=intent['task_id'],
            run_id=intent['run_id'],
            contract_digest=intent['contract_digest'],
            developer_task_id=intent['developer_task_id'],
            developer_identity=intent['developer_identity'],
            developer_session_ref=intent['developer_session_ref'],
            source_sha=intent['source_sha'],
            result_head=intent['result_head'],
            diff_sha256=intent['diff_sha256'],
            changed_paths=intent['changed_paths'],
        )
        self.assertEqual(validated, record)

        assert_code(
            'REVIEW_RECEIPT_BINDING_MISMATCH',
            task_completion.validate_review_handoff_record,
            record,
            task_id=intent['task_id'],
            run_id=intent['run_id'],
            contract_digest=intent['contract_digest'],
            developer_task_id=intent['developer_task_id'],
            developer_identity=intent['developer_identity'],
            developer_session_ref=intent['developer_session_ref'],
            source_sha='8' * 40,
            result_head=intent['result_head'],
            diff_sha256=intent['diff_sha256'],
            changed_paths=intent['changed_paths'],
        )

    def test_case_9_read_d6_registration_invalid_id(self):
        assert_code(
            'D6_REGISTRY_ID_INVALID',
            task_completion.read_d6_registration,
            'Bad Id!',
            '/nonexistent/.git',
        )

    def test_case_10_collect_github_evidence_pr_number_invalid(self):
        with patch.object(task_completion, '_github_get', side_effect=AssertionError('Network called unexpectedly')):
            for bad_pr in (0, 'x'):
                assert_code(
                    'PR_NUMBER_INVALID',
                    task_completion.collect_github_evidence,
                    pr_number=bad_pr,
                    result_sha='e' * 40,
                    task_id='a' * 32,
                    run_id='b' * 32,
                    contract_digest='c' * 64,
                    executor_identity='exec-identity',
                    executor_session_ref='exec-session',
                    executor_native_session_id='exec-native-session',
                    reviewer_task_id='rev-task-1',
                    common_dir='/nonexistent/.git',
                )


if __name__ == '__main__':
    unittest.main()
