"""#2793 A13.0 wiring (b): the CI profile drives CI repair packet binding.

R1: the default profile accepts and refuses exactly the names accepted before.
R2: a substituted profile's names are accepted when a packet is built and bind
    when the packet is verified; none of the original names is accepted.
R3: a missing, invalid or ambiguous profile refuses with a fixed code and never
    falls back to the original names.

The checks go through the real CI repair builder and the real packet binding
verification. All text is plain prose (Owner #763 A).
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

HEAD = 'a' * 40
TASK = '1' * 32
D6 = 'executor-ci-profile'
PR = 42
REPAIR_TASK = '2' * 32
ROOT = {'task_id': TASK}
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


def _default_checks():
    profile = default_profile()
    return (profile.linux_code_check, profile.docs_check, profile.gateway_check,
            profile.ai_loop_check, profile.macos_check)


def _alt_checks():
    return (ALT_PROFILE.linux_code_check, ALT_PROFILE.docs_check,
            ALT_PROFILE.gateway_check, ALT_PROFILE.ai_loop_check,
            ALT_PROFILE.macos_check)


def _failure(check_name):
    return {'head': HEAD, 'check_name': check_name, 'check_run_id': 9001,
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


def _claim(packet, identity):
    return {'executor_task': D6, 'pr': PR, 'head_sha': HEAD, 'version': 1,
            'attempt': 1, 'family': f'pr-{PR}-ec2', 'state': 'DISPATCHED',
            'repair_task_id': REPAIR_TASK, 'comment_id': None,
            'packet_sha256': identity, 'claimed_at': '2026-11-27T00:00:00Z'}


def _identity(check_name):
    value = {'kind': 'CI', 'task': TASK, 'pr': PR, 'executor_task': D6,
             'head': HEAD, 'check_name': check_name, 'check_run_id': 9001,
             'conclusion': 'failure'}
    return hashlib.sha256(start_failure.canonical(value)).hexdigest()


def _header(packet):
    return packet['reviewer_text_excerpt_untrusted_data'].split('\n', 1)[0]


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


class R2SubstitutedProfileTests(unittest.TestCase):
    def test_substituted_names_build_and_bind_and_original_names_are_refused(self):
        for via_patch in (False, True):
            for name in _alt_checks():
                with self.subTest(via_patch=via_patch, check=name):
                    if via_patch:
                        with mock.patch.object(ci_profile, 'default_profile',
                                               return_value=ALT_PROFILE):
                            packet, identity = _build(name)
                            claim = _claim(packet, identity)
                            self.assertTrue(start_failure.repair_packet_matches(
                                packet, claim, ROOT))
                    else:
                        packet, identity = _build(name, profile=ALT_PROFILE)
                        claim = _claim(packet, identity)
                        self.assertTrue(start_failure.repair_packet_matches(
                            packet, claim, ROOT, profile=ALT_PROFILE))
                    self.assertEqual(
                        _header(packet),
                        f'CI_CHECK_FAILURE check={name} run=9001 '
                        f'conclusion=failure head={HEAD}')
                    self.assertEqual(identity, _identity(name))

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
        claim = _claim(packet, _identity(original))
        self.assertFalse(start_failure.repair_packet_matches(
            other, claim, ROOT, profile=ALT_PROFILE))

    def test_a_built_name_binds_after_the_default_profile_changes(self):
        # R5: the later verification accepts the built name, not today's default.
        for via_patch in (False, True):
            with self.subTest(via_patch=via_patch):
                if via_patch:
                    with mock.patch.object(ci_profile, 'default_profile',
                                           return_value=ALT_PROFILE):
                        packet, identity = _build(_alt_checks()[0])
                else:
                    packet, identity = _build(_alt_checks()[0], profile=ALT_PROFILE)
                claim = _claim(packet, identity)
                self.assertTrue(start_failure.repair_packet_matches(packet, claim, ROOT))


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


if __name__ == '__main__':
    unittest.main()
