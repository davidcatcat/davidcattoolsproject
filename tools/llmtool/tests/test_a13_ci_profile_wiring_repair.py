"""#2793 A13.0 wiring (b): the CI profile drives CI repair packet binding.

R1: the default profile accepts and refuses exactly the names accepted before.
R2: a substituted profile's names are accepted when a packet is built and bind
    when the packet is verified; none of the original names is accepted.
R3: a missing, invalid or ambiguous profile refuses with a fixed code and never
    falls back to the original names.
R5: later verification uses the check names stored when the packet was built,
    including claim verification and retry, which pass no profile.

The checks go through the real CI repair builder, the real repair dispatcher
and the real packet binding verification. All text is plain prose (Owner #763 A).
"""

from __future__ import annotations

import hashlib
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import ci_profile, review_repair, start_failure  # noqa: E402
from llmtool.ci_profile import CiProfile, CiProfileError, default_profile  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

import review_trust_fixture  # noqa: E402
import worker_policy_fixture  # noqa: E402
from test_review_repair import (  # noqa: E402
    EXECUTOR as REAL_EXECUTOR, HEAD as REAL_HEAD, PR as REAL_PR,
    TASK as REAL_TASK, TempTaskStore, _record as review_record,
)

HEAD = 'a' * 40
TASK = '1' * 32
D6 = 'executor-ci-profile'
PR = 42
ROOT = {'task_id': TASK}
REAL_ROOT = {'task_id': REAL_TASK}
_UNSET = object()

ALT_PROFILE = CiProfile(
    linux_code_check='alt-linux-check',
    docs_check='alt-docs-check',
    gateway_check='alt-gateway-check',
    ai_loop_check='alt-ai-loop-check',
    macos_check='alt-macos-check',
    linux_workflow_file='alt-linux-ci.yml',
    macos_workflow_file='alt-mac-ci.yml',
)


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _default_checks():
    profile = default_profile()
    return (profile.linux_code_check, profile.docs_check, profile.gateway_check,
            profile.ai_loop_check, profile.macos_check)


def _alt_checks():
    return (ALT_PROFILE.linux_code_check, ALT_PROFILE.docs_check,
            ALT_PROFILE.gateway_check, ALT_PROFILE.ai_loop_check,
            ALT_PROFILE.macos_check)


def _failure(check_name, *, head=HEAD, run_id=9001):
    return {'head': head, 'check_name': check_name, 'check_run_id': run_id,
            'conclusion': 'failure', 'excerpt': 'FAIL: example check\nAssertionError'}


def _build(check_name, *, profile=_UNSET, patched_default=_UNSET):
    """Build a real CI repair packet, optionally with a substituted profile."""
    kwargs = {'task': {'task_id': TASK}, 'executor_task': D6, 'pr_number': PR,
              'ci_failure': _failure(check_name)}
    if profile is not _UNSET:
        kwargs['profile'] = profile
    if patched_default is _UNSET:
        return review_repair._ci_failure(**kwargs)
    if isinstance(patched_default, BaseException):
        context = mock.patch.object(ci_profile, 'default_profile',
                                    side_effect=patched_default)
    else:
        context = mock.patch.object(ci_profile, 'default_profile',
                                    return_value=patched_default)
    with context:
        return review_repair._ci_failure(**kwargs)


def _claim(packet, identity, check_names=None):
    claim = {'executor_task': D6, 'pr': PR, 'head_sha': HEAD, 'version': 1,
             'attempt': 1, 'family': f'pr-{PR}-ec2', 'state': 'DISPATCHED',
             'repair_task_id': '2' * 32, 'comment_id': None,
             'packet_sha256': identity, 'claimed_at': '2026-11-27T00:00:00Z'}
    if check_names is not None:
        claim['ci_check_names'] = list(check_names)
    return claim


def _identity(check_name, *, task=TASK, executor=D6, head=HEAD, pr=PR, run_id=9001):
    value = {'kind': 'CI', 'task': task, 'pr': pr, 'executor_task': executor,
             'head': head, 'check_name': check_name, 'check_run_id': run_id,
             'conclusion': 'failure'}
    return hashlib.sha256(start_failure.canonical(value).encode('utf-8')).hexdigest()


def _header(packet):
    return packet['reviewer_text_excerpt_untrusted_data'].split('\n', 1)[0]


class RealRepairPathTests(unittest.TestCase):
    """Build a claim through the real dispatcher, then verify it without a profile."""

    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self._stores = 0
        worker_policy_fixture.install(self)
        for patcher in (
                mock.patch.object(review_repair.worker_dispatch, 'evaluate',
                                  return_value=types.SimpleNamespace(allowed=True)),
                mock.patch.object(review_repair.shutil, 'which',
                                  return_value='/usr/bin/agy')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _real_claim(self, check_name, *, profile=_UNSET, patched_default=_UNSET):
        self._stores += 1
        store_dir = Path(self.temp.name) / f'store{self._stores}'
        store_dir.mkdir()
        store = TempTaskStore(str(store_dir), review_record())
        calls = []

        def adapter(**request):
            calls.append(request)
            return {'status': 'DISPATCHED', 'head': REAL_HEAD}

        def recorder(**request):
            return {'task_id': request['repair_task_id'],
                    'pm_assignment': {'acceptance': ['Repair the failed check.']}}

        kwargs = {
            'store': store, 'task_id': REAL_TASK, 'executor_task': REAL_EXECUTOR,
            'pr_number': REAL_PR, 'github_get': lambda _path: [],
            'dispatch_adapter': adapter,
            'current_head': lambda _task, _executor: REAL_HEAD,
            'repair_task_recorder': recorder,
            'ci_failure': _failure(check_name, head=REAL_HEAD),
        }
        if profile is not _UNSET:
            kwargs['profile'] = profile
        if patched_default is _UNSET:
            review_repair.repair_review(**kwargs)
        else:
            with mock.patch.object(ci_profile, 'default_profile',
                                   return_value=patched_default):
                review_repair.repair_review(**kwargs)
        claim = store.get(REAL_TASK)['review_repair_claims'][0]
        return calls[0]['packet'], claim

    def test_real_default_claim_stores_the_default_names_and_binds(self):
        name = _default_checks()[0]
        packet, claim = self._real_claim(name)
        self.assertEqual(claim['ci_check_names'], list(_default_checks()))
        self.assertTrue(start_failure.repair_packet_matches(packet, claim, REAL_ROOT))
        other = dict(packet, reviewer_text_excerpt_untrusted_data=(
            _header(packet).replace(name, _alt_checks()[0]) + '\n' +
            packet['reviewer_text_excerpt_untrusted_data'].split('\n', 1)[1]))
        forged = dict(claim, packet_sha256=_identity(
            _alt_checks()[0], task=REAL_TASK, executor=REAL_EXECUTOR,
            head=REAL_HEAD, pr=REAL_PR))
        self.assertFalse(start_failure.repair_packet_matches(other, forged, REAL_ROOT))

    def test_real_substituted_claim_with_an_explicit_profile_binds(self):
        packet, claim = self._real_claim(_alt_checks()[0], profile=ALT_PROFILE)
        self.assertEqual(claim['ci_check_names'], list(_alt_checks()))
        self.assertTrue(start_failure.repair_packet_matches(packet, claim, REAL_ROOT))

    def test_real_substituted_claim_binds_after_the_default_profile_changes(self):
        # R5: the later no-profile verification accepts the names stored when
        # the packet was built, not today's default names.
        with mock.patch.object(ci_profile, 'default_profile',
                               return_value=ALT_PROFILE):
            packet, claim = self._real_claim(_alt_checks()[0])
        self.assertTrue(start_failure.repair_packet_matches(packet, claim, REAL_ROOT))
        original = _default_checks()[0]
        header, _, rest = packet['reviewer_text_excerpt_untrusted_data'].partition('\n')
        other = dict(packet, reviewer_text_excerpt_untrusted_data=(
            header.replace(_alt_checks()[0], original) + '\n' + rest))
        forged = dict(claim, packet_sha256=_identity(
            original, task=REAL_TASK, executor=REAL_EXECUTOR,
            head=REAL_HEAD, pr=REAL_PR))
        self.assertFalse(start_failure.repair_packet_matches(other, forged, REAL_ROOT))


class R1DefaultProfileTests(unittest.TestCase):
    def test_default_names_build_and_bind_exactly_as_before(self):
        for name in _default_checks():
            with self.subTest(check=name):
                packet, identity = _build(name)
                self.assertEqual(
                    _header(packet),
                    f'CI_CHECK_FAILURE check={name} run=9001 '
                    f'conclusion=failure head={HEAD}')
                self.assertEqual(identity, _identity(name))
                claim = _claim(packet, identity)
                self.assertTrue(start_failure.repair_packet_matches(packet, claim, ROOT))

    def test_default_profile_refuses_names_it_does_not_carry(self):
        for name in _alt_checks():
            with self.subTest(check=name):
                with self.assertRaises(LLMToolError) as caught:
                    _build(name)
                self.assertEqual(caught.exception.review_repair_code,
                                 'CI_FAILURE_INVALID')

    def test_verification_with_the_default_profile_rejects_another_name(self):
        packet, _identity_value = _build(_default_checks()[0])
        other_name = _alt_checks()[0]
        header, _, rest = packet['reviewer_text_excerpt_untrusted_data'].partition('\n')
        other = dict(packet, reviewer_text_excerpt_untrusted_data=(
            header.replace(_default_checks()[0], other_name) + '\n' + rest))
        claim = _claim(packet, _identity(other_name))
        self.assertFalse(start_failure.repair_packet_matches(
            other, claim, ROOT, profile=default_profile()))

    def test_packet_built_with_the_default_profile_binds_without_a_profile(self):
        packet, identity = _build(_default_checks()[0])
        claim = _claim(packet, identity)
        self.assertTrue(start_failure.repair_packet_matches(packet, claim, ROOT))

    def test_changing_the_built_name_breaks_the_digest_without_a_profile(self):
        packet, identity = _build(_default_checks()[0])
        claim = _claim(packet, identity)
        header, _, rest = packet['reviewer_text_excerpt_untrusted_data'].partition('\n')
        other = dict(packet, reviewer_text_excerpt_untrusted_data=(
            header.replace(_default_checks()[0], _alt_checks()[0]) + '\n' + rest))
        self.assertFalse(start_failure.repair_packet_matches(other, claim, ROOT))

    def test_a_name_outside_the_default_profile_never_binds_with_a_matching_digest(self):
        # F1: a forged packet and matching claim must not pass just because the
        # name is printable; the default profile does not carry it.
        packet, identity = _build(_default_checks()[0])
        name = _alt_checks()[0]
        header, _, rest = packet['reviewer_text_excerpt_untrusted_data'].partition('\n')
        other = dict(packet, reviewer_text_excerpt_untrusted_data=(
            header.replace(_default_checks()[0], name) + '\n' + rest))
        claim = _claim(packet, _identity(name))
        self.assertFalse(start_failure.repair_packet_matches(other, claim, ROOT))


class R2SubstitutedProfileTests(unittest.TestCase):
    def test_substituted_names_build_and_bind_and_original_names_are_refused(self):
        for via_patch in (False, True):
            for name in _alt_checks():
                with self.subTest(via_patch=via_patch, check=name):
                    if via_patch:
                        with mock.patch.object(ci_profile, 'default_profile',
                                               return_value=ALT_PROFILE):
                            packet, identity = _build(name)
                        claim = _claim(packet, identity, _alt_checks())
                    else:
                        packet, identity = _build(name, profile=ALT_PROFILE)
                        claim = _claim(packet, identity, _alt_checks())
                    self.assertTrue(start_failure.repair_packet_matches(
                        packet, claim, ROOT))
                    self.assertEqual(
                        _header(packet),
                        f'CI_CHECK_FAILURE check={name} run=9001 '
                        f'conclusion=failure head={HEAD}')
                    self.assertEqual(identity, _identity(name))
                    self.assertTrue(start_failure.repair_packet_matches(
                        packet, claim, ROOT, profile=ALT_PROFILE))

            for name in _default_checks():
                with self.subTest(via_patch=via_patch, original=name):
                    if via_patch:
                        with mock.patch.object(ci_profile, 'default_profile',
                                               return_value=ALT_PROFILE):
                            with self.assertRaises(LLMToolError) as caught:
                                _build(name)
                    else:
                        with self.assertRaises(LLMToolError) as caught:
                            _build(name, profile=ALT_PROFILE)
                    self.assertEqual(caught.exception.review_repair_code,
                                     'CI_FAILURE_INVALID')

    def test_substituted_verification_rejects_the_original_names(self):
        packet, _identity_value = _build(_alt_checks()[0], profile=ALT_PROFILE)
        header, _, rest = packet['reviewer_text_excerpt_untrusted_data'].partition('\n')
        original = _default_checks()[0]
        other = dict(packet, reviewer_text_excerpt_untrusted_data=(
            header.replace(_alt_checks()[0], original) + '\n' + rest))
        claim = _claim(packet, _identity(original), _alt_checks())
        self.assertFalse(start_failure.repair_packet_matches(other, claim, ROOT))
        self.assertFalse(start_failure.repair_packet_matches(
            other, claim, ROOT, profile=ALT_PROFILE))


class R3InvalidProfileTests(unittest.TestCase):
    def _bad_profiles(self):
        mutated = CiProfile(
            linux_code_check='a', docs_check='b', gateway_check='c',
            ai_loop_check='d', macos_check='e',
            linux_workflow_file='l.yml', macos_workflow_file='m.yml',
        )
        object.__setattr__(mutated, 'macos_check', '')
        bad_path = CiProfile(
            linux_code_check='a', docs_check='b', gateway_check='c',
            ai_loop_check='d', macos_check='e',
            linux_workflow_file='l.yml', macos_workflow_file='dir/m.yml',
        )
        return (
            ('missing_none', None),
            ('invalid_str', 'llmtool-mac-ci.yml'),
            ('invalid_dict', {'macos_check': 'check (macOS 3.13)'}),
            ('invalid_namespace', types.SimpleNamespace(macos_check='check (macOS 3.13)')),
            ('mutated_empty', mutated),
            ('path_separator', bad_path),
            ('ambiguous_list', [ALT_PROFILE, default_profile()]),
            ('ambiguous_tuple', (ALT_PROFILE,)),
        )

    def test_direct_bad_profile_refuses_with_the_fixed_code(self):
        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                with self.assertRaises(LLMToolError) as caught:
                    _build(_default_checks()[0], profile=bad)
                self.assertEqual(caught.exception.review_repair_code,
                                 'CI_REPAIR_PROFILE_INVALID')

    def test_default_profile_error_or_bad_value_refuses_with_the_fixed_code(self):
        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                with self.assertRaises(LLMToolError) as caught:
                    _build(_default_checks()[0], patched_default=bad)
                self.assertEqual(caught.exception.review_repair_code,
                                 'CI_REPAIR_PROFILE_INVALID')

        for exc in (CiProfileError(), RuntimeError('missing profile')):
            with self.subTest(exc=type(exc).__name__):
                with self.assertRaises(LLMToolError) as caught:
                    _build(_default_checks()[0], patched_default=exc)
                self.assertEqual(caught.exception.review_repair_code,
                                 'CI_REPAIR_PROFILE_INVALID')

    def test_bad_profile_never_binds_a_packet(self):
        packet, identity = _build(_default_checks()[0])
        claim = _claim(packet, identity)
        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                self.assertFalse(start_failure.repair_packet_matches(
                    packet, claim, ROOT, profile=bad))

    def test_invalid_default_refuses_instead_of_using_the_original_names(self):
        packet, identity = _build(_default_checks()[0])
        claim = _claim(packet, identity)
        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                with mock.patch.object(ci_profile, 'default_profile',
                                       return_value=bad):
                    self.assertFalse(start_failure.repair_packet_matches(
                        packet, claim, ROOT))
        for exc in (CiProfileError(), RuntimeError('missing profile')):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(ci_profile, 'default_profile',
                                       side_effect=exc):
                    self.assertFalse(start_failure.repair_packet_matches(
                        packet, claim, ROOT))

    def test_malformed_stored_names_never_bind(self):
        packet, identity = _build(_default_checks()[0])
        for names in ([], ['one'], list(_default_checks())[:4],
                     list(_default_checks())[:4] + [None],
                     list(_default_checks())[:4] + ['']):
            with self.subTest(names=names):
                claim = _claim(packet, identity, names)
                self.assertFalse(start_failure.repair_packet_matches(
                    packet, claim, ROOT))


if __name__ == '__main__':
    unittest.main()
