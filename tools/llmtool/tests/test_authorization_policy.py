"""A11b.3 D1 policy decisions: host-vouched contracts only, no forged authority."""

import ast
import dataclasses
import json
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from llmtool import action_contract as c
from llmtool import authorization_policy as p
from llmtool.errors import LLMToolError
from test_action_contract import make_action, make_approval

PACKAGE = Path(__file__).resolve().parents[1] / 'llmtool'
REDEEM_RUN = 'a'*32


class FrozenSubclass(frozenset):
    pass


def Subclassed(value, model):
    # Same field values, but a subclass instance rather than the exact model type.
    sub = type('Sub' + model.__name__, (model,), {})
    return sub(**{f.name: getattr(value, f.name) for f in dataclasses.fields(model)})


class FakeHostIssuer(p.HostIssuer):
    """TEST_FAKE_HOST: the only ALLOW-capable issuer, and it lives in tests."""

    def __init__(self, *policies, issuer_class=p.IssuerClass.POLICY_CONTRACT,
                 capability_id='test-fake-host'):
        self._digests = {policy.digest for policy in policies}
        self._class, self._capability = issuer_class, capability_id

    @property
    def issuer_class(self):
        return self._class

    @property
    def capability_id(self):
        return self._capability

    def vouches_for(self, policy_digest):
        return policy_digest in self._digests


def make_create():
    return make_action(c.ActionKind.FILE_CREATE)


def make_policy(action=None, **changes):
    action = action or make_create()
    values = dict(policy_id='d1-policy', version=1, task=action.task,
                  kinds=(c.ActionKind.FILE_CREATE,), scope=action.scope,
                  effect_kind=p.EffectKind.FAKE_FILE_CREATE)
    values.update(changes)
    return p.PolicyContract(**values)


def make_observed(action=None, **changes):
    action = action or make_create()
    values = dict(repository=action.repository, head=action.repository.expected_head,
                  index_sha256='9'*64, worktree_sha256='b'*64,
                  target_path=action.payload.target.path, target_absent=True,
                  parent_device=10, parent_inode=30)
    values.update(changes)
    return p.ObservedState(**values)


def allow(action=None, policy=None, observed=None, **changes):
    action = action or make_create()
    policy = policy or make_policy(action)
    values = dict(issuer=FakeHostIssuer(policy), observed=observed or make_observed(action),
                  redeem_run_id=REDEEM_RUN)
    values.update(changes)
    return p.decide(action, policy, **values)


class PolicyDecisionTests(unittest.TestCase):
    def assertDecision(self, result, decision, code):
        self.assertIs(type(result), p.PolicyDecision)
        self.assertIs(result.decision, decision)
        self.assertIs(result.code, code)
        if decision is not p.Decision.ALLOW_WITH_TRUSTED_RECEIPT:
            self.assertIsNone(result.binding)

    def test_host_vouched_contract_allows_routine_action_without_owner_prompt(self):
        action = make_create()
        result = allow(action)
        self.assertDecision(result, p.Decision.ALLOW_WITH_TRUSTED_RECEIPT, p.DecisionCode.POLICY_ALLOWED)
        self.assertEqual(result.status, 'FAKE_POLICY_ALLOWED')
        binding = result.binding
        policy = make_policy(action)
        self.assertEqual((binding.task_id, binding.task_run_id, binding.contract_version,
                          binding.contract_digest), (action.task.task_id, action.task.run_id,
                          action.task.contract_version, action.task.contract_digest))
        self.assertEqual((binding.action_digest, binding.action_kind, binding.action_paths,
                          binding.content_sha256), (action.digest, 'FILE_CREATE', action.scope,
                          action.payload.new_content_sha256))
        self.assertEqual((binding.repository_id, binding.canonical_root, binding.root_device,
                          binding.root_inode, binding.head), (action.repository.repository_id,
                          action.repository.canonical_root, 10, 20, action.repository.expected_head))
        self.assertEqual((binding.index_sha256, binding.worktree_sha256), ('9'*64, 'b'*64))
        self.assertEqual((binding.policy_id, binding.policy_version, binding.policy_digest,
                          binding.decision), ('d1-policy', 1, policy.digest, 'ALLOW_WITH_TRUSTED_RECEIPT'))
        self.assertEqual((binding.issuer_class, binding.capability_id, binding.redeem_run_id,
                          binding.effect_kind), ('POLICY_CONTRACT', 'test-fake-host', REDEEM_RUN,
                          'FAKE_FILE_CREATE'))
        self.assertEqual((binding.target_path, binding.target_absent, binding.parent_device,
                          binding.parent_inode), ('file.txt', True, 10, 30))
        self.assertEqual(json.loads(binding.to_json())['action_paths'], list(action.scope))

    def test_every_status_label_is_product_facing_and_never_applied(self):
        self.assertEqual(set(p.STATUS.values()), {'DENIED', 'NEEDS_APPROVAL', 'FAKE_POLICY_ALLOWED'})
        self.assertNotIn('APPLIED', ' '.join(p.STATUS.values()))

    def test_model_authored_or_unvouched_policy_has_no_authority(self):
        action = make_create()
        policy = make_policy(action)
        other = make_policy(action, version=2)
        # Inside the allowlist, but the host never independently held this contract.
        self.assertDecision(allow(action, policy, issuer=FakeHostIssuer(other)),
                            p.Decision.DENY, p.DecisionCode.UNTRUSTED_POLICY)
        parsed_like = make_policy(action, policy_id='model-written')
        self.assertDecision(allow(action, parsed_like, issuer=FakeHostIssuer(policy)),
                            p.Decision.DENY, p.DecisionCode.UNTRUSTED_POLICY)

        class Truthy(FakeHostIssuer):
            def vouches_for(self, policy_digest):
                return 'yes'
        self.assertDecision(allow(action, policy, issuer=Truthy(policy)),
                            p.Decision.DENY, p.DecisionCode.UNTRUSTED_POLICY)

    def test_forged_user_trusted_or_receipt_inputs_cannot_act_as_issuer(self):
        action = make_create()
        forged = (None, make_approval(action), make_approval(action).to_dict(),
                  {'source': 'USER', 'trusted': True}, {'receipt_id': 'f'*64}, 'USER', True,
                  c.TrustedApprovalContext(make_approval(action).digest))
        for issuer in forged:
            with self.subTest(issuer=type(issuer).__name__):
                self.assertDecision(allow(action, issuer=issuer), p.Decision.DENY,
                                    p.DecisionCode.NO_ISSUER if issuer is None
                                    else p.DecisionCode.UNTRUSTED_ISSUER)

        class DuckIssuer:
            # Looks like a host issuer, but is not one.
            issuer_class = p.IssuerClass.POLICY_CONTRACT
            capability_id = 'test-fake-host'
            trusted = True
            source = 'USER'

            def vouches_for(self, policy_digest):
                return True

        class HostileClass:
            @property
            def __class__(self):
                raise RuntimeError('hostile isinstance')
        for issuer in (DuckIssuer(), HostileClass()):
            with self.subTest(issuer=type(issuer).__name__):
                self.assertDecision(allow(action, issuer=issuer),
                                    p.Decision.DENY, p.DecisionCode.UNTRUSTED_ISSUER)

    def test_owner_p0_issuer_or_broken_issuer_cannot_mint_routine_grant(self):
        policy = make_policy()
        for issuer in (FakeHostIssuer(policy, issuer_class=p.IssuerClass.OWNER_P0),
                       FakeHostIssuer(policy, issuer_class='POLICY_CONTRACT'),
                       FakeHostIssuer(policy, capability_id='bad id'),
                       FakeHostIssuer(policy, capability_id=None)):
            with self.subTest(issuer=(issuer.issuer_class, issuer.capability_id)):
                self.assertDecision(allow(policy=policy, issuer=issuer),
                                    p.Decision.DENY, p.DecisionCode.UNTRUSTED_ISSUER)

        class Raising(FakeHostIssuer):
            def vouches_for(self, policy_digest):
                raise RuntimeError('host failure')
        self.assertDecision(allow(policy=policy, issuer=Raising(policy)),
                            p.Decision.DENY, p.DecisionCode.UNTRUSTED_ISSUER)

        class RaisingClass(FakeHostIssuer):
            @property
            def issuer_class(self):
                raise RuntimeError('host failure')

        class RaisingCapability(FakeHostIssuer):
            @property
            def capability_id(self):
                raise RuntimeError('host failure')

        class Text(str):
            pass
        for issuer in (RaisingClass(policy), RaisingCapability(policy),
                       FakeHostIssuer(policy, capability_id=Text('test-fake-host'))):
            with self.subTest(issuer=type(issuer).__name__):
                self.assertDecision(allow(policy=policy, issuer=issuer),
                                    p.Decision.DENY, p.DecisionCode.UNTRUSTED_ISSUER)

        class TruthyInt(FakeHostIssuer):
            def vouches_for(self, policy_digest):
                return 1
        self.assertDecision(allow(policy=policy, issuer=TruthyInt(policy)),
                            p.Decision.DENY, p.DecisionCode.UNTRUSTED_POLICY)

    def test_canonical_owner_p0_is_exactly_three_classes_and_waits(self):
        self.assertEqual({r.value for r in p.OWNER_P0}, {'OWNER_P0_PRODUCT_MISMATCH',
                         'OWNER_P0_ADMIN_PRIVILEGE', 'OWNER_P0_MACOS_SYSTEM'})
        self.assertFalse(p.OWNER_P0 & p.SEPARATE_AUTHORITY)
        for requirement in p.OWNER_P0:
            for interactive, code in ((True, p.DecisionCode.OWNER_P0_REQUIRED),
                                      (False, p.DecisionCode.OWNER_P0_NON_INTERACTIVE)):
                with self.subTest(requirement=requirement, interactive=interactive):
                    result = allow(requirements=frozenset((requirement,)), interactive=interactive)
                    self.assertDecision(result, p.Decision.WAITING_APPROVAL, code)
                    self.assertEqual(result.status, 'NEEDS_APPROVAL')

    def test_owner_p0_is_reached_only_inside_a_vouched_contract(self):
        # A model-authored, unvouched, issuer-less or out-of-contract request is
        # denied, never escalated to the owner as a P0 prompt (Issue #97 R5).
        p0 = frozenset((p.Requirement.OWNER_P0_ADMIN_PRIVILEGE,))
        action, policy = make_create(), make_policy()
        denied = (
            (dict(issuer=None), p.DecisionCode.NO_ISSUER),
            (dict(issuer=FakeHostIssuer(make_policy(version=2))), p.DecisionCode.UNTRUSTED_POLICY),
            (dict(issuer=FakeHostIssuer(policy, issuer_class=p.IssuerClass.OWNER_P0)),
             p.DecisionCode.UNTRUSTED_ISSUER),
        )
        for interactive in (True, False):
            for change, code in denied:
                with self.subTest(code=code, interactive=interactive):
                    self.assertDecision(allow(action, policy, requirements=p0,
                                              interactive=interactive, **change), p.Decision.DENY, code)
            narrow = make_policy(action, scope=('file.txt',))
            self.assertDecision(allow(action, narrow, requirements=p0, interactive=interactive),
                                p.Decision.DENY, p.DecisionCode.OUT_OF_CONTRACT)
            git = make_action(c.ActionKind.GIT_OPERATION)
            self.assertDecision(allow(git, make_policy(), make_observed(), requirements=p0,
                                      interactive=interactive),
                                p.Decision.DENY, p.DecisionCode.UNSUPPORTED_ACTION)
            # Inside the vouched contract the same P0 waits for the owner.
            self.assertDecision(allow(action, policy, requirements=p0, interactive=interactive),
                                p.Decision.WAITING_APPROVAL,
                                p.DecisionCode.OWNER_P0_REQUIRED if interactive
                                else p.DecisionCode.OWNER_P0_NON_INTERACTIVE)

    def test_new_cost_or_egress_stops_and_is_never_p0_or_implied(self):
        for requirement in p.SEPARATE_AUTHORITY:
            for extra in (frozenset(), p.OWNER_P0):
                with self.subTest(requirement=requirement, with_p0=bool(extra)):
                    result = allow(requirements=frozenset((requirement,)) | extra, interactive=True)
                    self.assertDecision(result, p.Decision.DENY,
                                        p.DecisionCode.SEPARATE_AUTHORIZATION_REQUIRED)

    def test_out_of_contract_action_is_denied_not_escalated_to_owner(self):
        action = make_create()
        for policy in (make_policy(action, scope=('file.txt',)),
                       make_policy(action, kinds=(c.ActionKind.FILE_WRITE,))):
            with self.subTest(policy=policy.to_dict()):
                self.assertDecision(allow(action, policy), p.Decision.DENY, p.DecisionCode.OUT_OF_CONTRACT)
        other_task = replace(action.task, task_id='task-2')
        self.assertDecision(allow(action, make_policy(action, task=other_task)),
                            p.Decision.DENY, p.DecisionCode.POLICY_BINDING_MISMATCH)

    def test_only_synthetic_file_create_is_supported(self):
        for kind in (c.ActionKind.FILE_WRITE, c.ActionKind.FILE_DELETE, c.ActionKind.GIT_OPERATION):
            with self.subTest(kind=kind):
                action = make_action(kind)
                policy = make_policy(action, kinds=tuple(sorted(c.ActionKind, key=lambda k: k.value)))
                self.assertDecision(allow(action, policy, observed=make_observed(make_create())),
                                    p.Decision.DENY, p.DecisionCode.UNSUPPORTED_ACTION)

    def test_observed_state_mismatches_deny(self):
        action = make_create()
        cases = {
            'repository': dict(repository=replace(action.repository, root_inode=99)),
            'head': dict(head='c'*40),
            'target path': dict(target_path='other.txt'),
            'target present': dict(target_absent=False),
            'repository id': dict(repository=replace(action.repository, repository_id='c'*64)),
            'canonical root': dict(repository=replace(action.repository,
                                                      canonical_root='/synthetic/other')),
            'root device': dict(repository=replace(action.repository, root_device=11)),
            'repository head': dict(repository=replace(action.repository, expected_head='c'*40)),
            'head length': dict(head='c'*64),
        }
        for label, change in cases.items():
            with self.subTest(label=label):
                self.assertDecision(allow(action, observed=make_observed(action, **change)),
                                    p.Decision.DENY, p.DecisionCode.STATE_MISMATCH)

    def test_malformed_or_model_supplied_inputs_deny_with_stable_code(self):
        action = make_create()
        policy = make_policy(action)
        cases = (
            dict(action=action.to_dict()), dict(policy=policy.to_dict()),
            dict(observed=make_observed(action).to_dict()), dict(redeem_run_id='A'*32),
            dict(redeem_run_id='a'*31), dict(requirements={'NEW_COST'}),
            dict(requirements=frozenset(('OWNER_P0_ADMIN_PRIVILEGE',))), dict(interactive=1),
            dict(interactive=None), dict(redeem_run_id=None), dict(requirements=None),
            dict(requirements=FrozenSubclass()),
            dict(requirements=FrozenSubclass((p.Requirement.NEW_COST,))),
            dict(action=Subclassed(action, c.Action)), dict(policy=Subclassed(policy, p.PolicyContract)),
            dict(observed=Subclassed(make_observed(action), p.ObservedState)),
        )
        for change in cases:
            with self.subTest(change=sorted(change)):
                args = dict(action=action, policy=policy)
                args.update({k: v for k, v in change.items() if k in args})
                kwargs = dict(issuer=FakeHostIssuer(policy), observed=make_observed(action),
                              redeem_run_id=REDEEM_RUN)
                kwargs.update({k: v for k, v in change.items() if k not in args})
                self.assertDecision(p.decide(args['action'], args['policy'], **kwargs),
                                    p.Decision.DENY, p.DecisionCode.INVALID_REQUEST)

    def test_decisions_and_bindings_cannot_be_built_from_data(self):
        binding = allow().binding
        with self.assertRaises(LLMToolError):
            p.PolicyDecision(p.Decision.ALLOW_WITH_TRUSTED_RECEIPT, p.DecisionCode.POLICY_ALLOWED, binding)
        denied = allow(requirements=frozenset((p.Requirement.NEW_COST,)))
        self.assertIs(denied.code, p.DecisionCode.SEPARATE_AUTHORIZATION_REQUIRED)
        for forged in (dict(decision=p.Decision.ALLOW_WITH_TRUSTED_RECEIPT,
                            code=p.DecisionCode.POLICY_ALLOWED, binding=binding), {}):
            with self.subTest(forged=sorted(forged)), self.assertRaises(LLMToolError):
                replace(denied, **forged)
        with self.assertRaises(TypeError):
            p.PolicyDecision(**{'decision': 'ALLOW_WITH_TRUSTED_RECEIPT', 'code': 'POLICY_ALLOWED'})
        raw = json.loads(binding.to_json())
        for field, value in (('decision', 'DENY'), ('issuer_class', 'OWNER_P0'),
                             ('effect_kind', 'REAL_FILE_CREATE'), ('action_kind', 'FILE_WRITE'),
                             ('target_absent', False), ('target_absent', 1)):
            with self.subTest(field=field, value=value), self.assertRaises(LLMToolError):
                p.ReceiptBinding(**{**raw, 'action_paths': tuple(raw['action_paths']), field: value})

        class Text(str):
            pass
        raw['action_paths'] = tuple(raw['action_paths'])
        self.assertEqual(p.ReceiptBinding(**raw), binding)
        for field, value in (('action_kind', c.ActionKind.FILE_CREATE),
                             ('decision', p.Decision.ALLOW_WITH_TRUSTED_RECEIPT),
                             ('issuer_class', p.IssuerClass.POLICY_CONTRACT),
                             ('effect_kind', p.EffectKind.FAKE_FILE_CREATE),
                             ('decision', Text('ALLOW_WITH_TRUSTED_RECEIPT')),
                             ('target_path', 'zzz.txt')):
            with self.subTest(field=field, value=type(value).__name__), self.assertRaises(LLMToolError):
                p.ReceiptBinding(**{**raw, field: value})
        for model in (p.PolicyContract, p.ObservedState):
            with self.assertRaises(TypeError):
                model(trusted=True)

    def test_policy_contract_is_immutable_and_digest_bound(self):
        policy = make_policy()
        self.assertEqual(policy.digest, make_policy().digest)
        for change in (dict(version=2), dict(policy_id='d1-policy-2'),
                       dict(scope=('file.txt',)), dict(kinds=(c.ActionKind.FILE_CREATE,
                       c.ActionKind.FILE_WRITE))):
            with self.subTest(change=change):
                self.assertNotEqual(make_policy(**change).digest, policy.digest)
        for kinds in ((), (c.ActionKind.FILE_WRITE, c.ActionKind.FILE_CREATE),
                      (c.ActionKind.FILE_CREATE, c.ActionKind.FILE_CREATE), ('FILE_CREATE',)):
            with self.subTest(kinds=kinds), self.assertRaises(LLMToolError):
                make_policy(kinds=kinds)
        scope = list(policy.scope)
        listed = make_policy(scope=scope)
        scope.append('zzz.txt')
        self.assertEqual(listed.scope, policy.scope)
        self.assertEqual(listed.digest, policy.digest)
        for name, value in (('scope', ('file.txt', 'other.txt', 'zzz.txt')),
                            ('kinds', tuple(c.ActionKind)), ('version', 2)):
            with self.subTest(name=name), self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(policy, name, value)
        self.assertEqual(policy.digest, make_policy().digest)


class NoProductionAuthorityTests(unittest.TestCase):
    """R4: behavior above is primary; this static guard only supplements it."""

    def test_no_concrete_issuer_parser_or_public_route_in_package(self):
        issuers, imports, d1_imports = set(), set(), set()
        host_only = {'compose_host_services', 'ProductionReviewAuthority',
                     'ProductionReviewerLauncher'}
        for path in sorted(PACKAGE.rglob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    bases = {getattr(b, 'id', getattr(b, 'attr', None)) for b in node.bases}
                    for base in bases & {'HostIssuer', 'EffectSink', 'ProductionReviewIssuer'}:
                        issuers.add((path.name, node.name, base))
                if isinstance(node, (ast.Import, ast.ImportFrom)) and path.stem not in (
                        'authorization_policy', 'authorization_ledger'):
                    names = {a.name for a in node.names} | {getattr(node, 'module', None) or ''}
                    if any('authorization_' in n for n in names):
                        imports.add((path.name, getattr(node, 'module', None) or '',
                                     frozenset(a.name for a in node.names)))
                if (path.stem in ('authorization_policy', 'authorization_ledger') and
                        isinstance(node, ast.ImportFrom) and node.level >= 1):
                    d1_imports.add((path.name, node.level, node.module,
                                   frozenset(a.name for a in node.names)))
                if path.name != 'review_authority.py':
                    if isinstance(node, ast.Name): self.assertNotIn(node.id, host_only)
                    if isinstance(node, ast.Attribute): self.assertNotIn(node.attr, host_only)
                    if isinstance(node, ast.alias):
                        self.assertNotIn(node.name, host_only)
                        self.assertNotIn(node.asname, host_only)
            if path.name in ('task_review.py', 'review_authority.py'):
                factories = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                             and n.name == 'review_authority_factory']
                self.assertEqual(len(factories), 1)
                body = factories[0].body
                if (body and isinstance(body[0], ast.Expr) and
                        isinstance(body[0].value, ast.Constant) and type(body[0].value.value) is str):
                    body = body[1:]
                self.assertEqual([ast.dump(n) for n in body],
                                 [ast.dump(ast.parse('return UnavailableReviewAuthority()').body[0])])
        # R-A/R-B: exact reviewed additions, no filename-wide exemption.
        self.assertEqual(issuers, {('review_authority.py', 'ProductionReviewIssuer', 'HostIssuer'),
            ('codex_pickup_native.py','NativePickupIssuer','HostIssuer')})
        self.assertEqual(imports, {
            ('review_authority.py', 'authorization_policy', frozenset({
                'HostIssuer', 'IssuerClass', 'Decision', 'ProviderReviewBinding',
                'ProviderObservedState', 'decide_provider_review'})),
            ('review_authority.py', 'authorization_ledger', frozenset({'ReceiptLedger', 'LedgerError'})),
            ('codex_handoff_transport.py','authorization_policy',frozenset({
                'NotificationBinding','NotificationObserved','NotificationDecision','Decision','decide_notification'})),
            ('codex_handoff_transport.py','authorization_ledger',frozenset({'ReceiptLedger'})),
            ('codex_pickup_native.py','authorization_policy',frozenset({'HostIssuer','IssuerClass','NotificationBinding'})),
            ('review_handoff.py','authorization_ledger',frozenset({'ReceiptLedger'})),
        })
        # R-C: base relative dependencies plus only the accepted lazy increments.
        self.assertEqual(d1_imports, {
            ('authorization_policy.py', 1, 'action_contract', frozenset({
                'Action','ActionKind','ContractValue','RepositoryBinding','TaskBinding',
                '_collection','_digest','_hex','_identifier','_integer','_path','_scope','_typed'})),
            ('authorization_policy.py', 1, 'errors', frozenset({'LLMToolError'})),
            ('authorization_policy.py', 1, 'task_review', frozenset({'ReviewCallBinding'})),
            ('authorization_policy.py', 1, 'task_review', frozenset({'digest'})),
            ('authorization_policy.py', 1, 'review_authority', frozenset({'ProductionReviewIssuer'})),
            ('authorization_ledger.py', 1, 'action_contract', frozenset({
                'MAX_APPROVAL_SECONDS','MAX_EPOCH','ContractValue','_hex','_typed'})),
            ('authorization_ledger.py', 1, 'authorization_policy', frozenset({
                'Decision','EffectKind','ObservedState','PolicyDecision','ReceiptBinding',
                'ProviderReviewBinding','ProviderPolicyDecision','decide_provider_review',
                'NotificationBinding','NotificationDecision','decide_notification'})),
            ('authorization_ledger.py', 1, 'errors', frozenset({'LLMToolError'})),
            ('authorization_ledger.py', 1, 'task_lifecycle', frozenset({'utc_time'})),
            ('authorization_ledger.py', 1, 'authorization_policy', frozenset({'notification_id'})),
        })
        for name in ('authorization_policy.py', 'authorization_ledger.py'):
            source = (PACKAGE / name).read_text(encoding='utf-8')
            for forbidden in ('json.loads', 'subprocess', 'socket', 'urllib', 'argparse',
                              'shutil', 'os.system', 'os.replace', 'os.rename', 'os.remove',
                              'os.unlink'):
                self.assertNotIn(forbidden, source, name)

    def test_legacy_data_only_validation_stays_unauthorized(self):
        action = make_create()
        approval = make_approval(action)
        result = c.validate_approval(action, approval, current_state=action.expected_state,
            expected_nonce='8'*64, now=150, trusted_context=c.TrustedApprovalContext(approval.digest))
        self.assertIs(result.code, c.ApprovalCode.USER_ATTESTED)
        self.assertFalse(result.authorized)
        self.assertFalse(result.to_dict()['authorized'])


class ProviderPolicyBoundaryTests(unittest.TestCase):
    def test_production_review_issuer_cannot_authorize_d1_effect(self):
        from test_review_authority import Harness
        host = Harness()
        self.addCleanup(host.close)
        for requirements, code in (
                (frozenset(), p.DecisionCode.UNTRUSTED_ISSUER),
                (frozenset({p.Requirement.NEW_COST}), p.DecisionCode.SEPARATE_AUTHORIZATION_REQUIRED),
                (frozenset({p.Requirement.NEW_DATA_EGRESS}), p.DecisionCode.SEPARATE_AUTHORIZATION_REQUIRED)):
            with self.subTest(requirements=requirements):
                result = allow(issuer=host.issuer, requirements=requirements)
                self.assertIs(result.decision, p.Decision.DENY)
                self.assertIs(result.code, code)
                self.assertIsNone(result.binding)

    def test_d1_separate_authority_and_labels_unchanged(self):
        result=allow(requirements=frozenset((p.Requirement.NEW_COST,p.Requirement.NEW_DATA_EGRESS)))
        self.assertIs(result.decision,p.Decision.DENY)
        self.assertIs(result.code,p.DecisionCode.SEPARATE_AUTHORIZATION_REQUIRED)
        self.assertEqual(p.STATUS[p.Decision.ALLOW_WITH_TRUSTED_RECEIPT],'FAKE_POLICY_ALLOWED')

    def test_data_fake_issuer_and_receipt_cannot_be_provider_grant(self):
        from llmtool import review_authority as a,task_review as r
        self.assertIsInstance(a.review_authority_factory(),r.UnavailableReviewAuthority)
        denied=p.decide_provider_review({'issuer_class':'PRODUCTION_REVIEW'},issuer={'trusted':True},observed={})
        self.assertIs(denied.decision,p.Decision.DENY)
        with self.assertRaisesRegex(LLMToolError, '^provider decision invalid; details omitted$'):p.ProviderPolicyDecision(p.Decision.ALLOW_WITH_TRUSTED_RECEIPT,p.DecisionCode.POLICY_ALLOWED,None)


if __name__ == '__main__':
    unittest.main()


class NotificationPolicyTests(unittest.TestCase):
    def setUp(self):
        from test_codex_handoff_transport import Trial
        self.t=Trial();self.addCleanup(self.t.close)
    def observed(self,**changes):
        return p.NotificationObserved(self.t.binding.digest,self.t.binding.host_pin,**dict(
            attested=True,idle=True,queue_empty=True,exclusive=True,no_tools=True,
            withdrawal_supported=True,turn_cancel_supported=True,**changes))
    def test_notification_decision_is_isolated_and_host_vouched(self):
        obs=self.observed()
        decision=p.decide_notification(self.t.binding,issuer=self.t.issuer,observed=obs)
        self.assertIs(decision.decision,p.Decision.ALLOW_WITH_TRUSTED_RECEIPT)
        for issuer in (None,{'trusted':True},FakeHostIssuer(make_policy()),
                       FakeHostIssuer(make_policy(),issuer_class=p.IssuerClass.PRODUCTION_REVIEW)):
            denied=p.decide_notification(self.t.binding,issuer=issuer,observed=obs)
            self.assertIs(denied.decision,p.Decision.DENY)
        forged=dataclasses.replace(self.t.binding,source_sha='0'*40)
        forged_obs=dataclasses.replace(obs,binding_digest=forged.digest)
        self.assertIs(p.decide_notification(forged,issuer=self.t.issuer,observed=forged_obs).decision,p.Decision.DENY)
        self.t.issuer.revoked=True
        self.assertIs(p.decide_notification(self.t.binding,issuer=self.t.issuer,observed=obs).decision,p.Decision.DENY)
    def test_each_m1_host_guard_required_and_dict_cannot_grant(self):
        obs=self.observed()
        for field in ('attested','idle','queue_empty','exclusive','no_tools','withdrawal_supported','turn_cancel_supported'):
            with self.subTest(field=field):
                denied=p.decide_notification(self.t.binding,issuer=self.t.issuer,observed=dataclasses.replace(obs,**{field:False}))
                self.assertIs(denied.decision,p.Decision.DENY)
        self.assertIs(p.decide_notification(self.t.binding.to_dict(),issuer=self.t.issuer,observed=obs).decision,p.Decision.DENY)
    def test_notification_issuer_cannot_authorize_d1_or_provider(self):
        self.assertIs(allow(issuer=self.t.issuer).decision,p.Decision.DENY)
        self.assertIs(p.decide_provider_review(self.t.binding,issuer=self.t.issuer,observed=self.observed()).decision,p.Decision.DENY)
    def test_scope_and_replay_keys_immutable_distinct_from_attempt(self):
        b=self.t.binding
        again=dataclasses.replace(b,event_id='event-other',attempt_id='c'*32,task=dataclasses.replace(b.task,run_id='f'*32))
        self.assertEqual(b.object_key,again.object_key);self.assertNotEqual(b.digest,again.digest)
        with self.assertRaises(dataclasses.FrozenInstanceError):b.reviewer_session='spoofed'
        for changes in ({'sequence':True},{'author_session':b.reviewer_session},{'expires_at':b.not_before+601}):
            with self.assertRaises(LLMToolError):dataclasses.replace(b,**changes)


class NativeDeltaPolicyTests(unittest.TestCase):
    def test_audit_only_is_separate_and_requires_whole_binding_acceptance(self):
        from test_codex_handoff_transport import DeltaTrial
        t=DeltaTrial();self.addCleanup(t.close)
        o=p.NotificationObserved(t.binding.digest,t.binding.host_pin,True,True,True,True,False,True,True,True,t.binding.risk_acceptance_digest)
        self.assertIs(p.decide_notification(t.binding,issuer=t.issuer,observed=o).decision,p.Decision.ALLOW_WITH_TRUSTED_RECEIPT)
        for changes in ({'audit_coverage':False},{'risk_acceptance_digest':'f'*64}):
            self.assertIs(p.decide_notification(t.binding,issuer=t.issuer,observed=dataclasses.replace(o,**changes)).decision,p.Decision.DENY)
        with self.assertRaisesRegex(LLMToolError, '^notification audit acceptance missing; details omitted$'):dataclasses.replace(t.binding,risk_acceptance_digest='0'*64)
        enforced=dataclasses.replace(t.binding,tool_mode='ENFORCED_NO_TOOLS');t.issuer.binding=enforced
        self.assertIs(p.decide_notification(enforced,issuer=t.issuer,observed=dataclasses.replace(o,binding_digest=enforced.digest)).decision,p.Decision.DENY)
    def test_native_issuer_requires_sealed_acceptance_and_cannot_authorize_d1(self):
        from test_codex_handoff_transport import DeltaTrial
        from llmtool import codex_pickup_native as n
        t=DeltaTrial();self.addCleanup(t.close)
        grant=n.NativeGrant(t.binding,'f'*64,n.PROTOCOL_SHA,n.CLI_VERSION,'reviewer-route','author-route','a'*64,'b'*64,True,'STOP_PRESERVE_CONTEXT',True,'c'*64)
        with self.assertRaisesRegex(LLMToolError, '^review rejected; details omitted$') as refused:n.NativePickupIssuer(grant,{'accepted':True})
        self.assertEqual(refused.exception.review_code, 'PICKUP_NOT_AUTHORIZED')
        issuer=n._fixture_issuer(grant)
        self.assertTrue(issuer.vouches_for(t.binding.digest));self.assertFalse(issuer.vouches_for(t.binding.grant_digest))
        self.assertIs(allow(make_create(),make_policy(),issuer=issuer).decision,p.Decision.DENY)


class CompositionPolicyTests(unittest.TestCase):
    def test_zero_child_topology_requires_complete_binding_and_observed_pins(self):
        from test_codex_handoff_transport import DeltaTrial
        t=DeltaTrial();self.addCleanup(t.close)
        for changes in ({'topology':'IN_PROCESS_ZERO_CHILD'},{'composition_digest':'f'*64},{'topology':'OWNED_CHILD'}):
            with self.assertRaisesRegex(LLMToolError, '^notification topology invalid; details omitted$'):dataclasses.replace(t.binding,**changes)
        b=dataclasses.replace(t.binding,topology='IN_PROCESS_ZERO_CHILD',composition_digest='f'*64)
        t.issuer.binding=b
        obs=p.NotificationObserved(b.digest,b.host_pin,True,True,True,True,False,True,True,True,b.risk_acceptance_digest)
        self.assertIs(p.decide_notification(b,issuer=t.issuer,observed=obs).decision,p.Decision.DENY)
        obs=dataclasses.replace(obs,topology=b.topology,composition_digest=b.composition_digest)
        self.assertIs(p.decide_notification(b,issuer=t.issuer,observed=obs).decision,p.Decision.ALLOW_WITH_TRUSTED_RECEIPT)
        for changes in ({'composition_digest':'e'*64},{'topology':'LEGACY_FIXTURE'}):
            self.assertIs(p.decide_notification(b,issuer=t.issuer,observed=dataclasses.replace(obs,**changes)).decision,p.Decision.DENY)
        old_digest=b.digest;t.issuer.binding=dataclasses.replace(b,composition_digest='e'*64)
        self.assertFalse(t.issuer.vouches_for(old_digest))
        self.assertIs(allow(make_create(),make_policy(),issuer=t.issuer).decision,p.Decision.DENY)


class ExceptionSpecificityTests(unittest.TestCase):
    def test_unrelated_faults_at_real_refusal_guards_are_not_accepted(self):
        # Inject only at construction of the expected error, after the real
        # constructor/replace validation has reached its refusal guard.
        cases = (
            (ProviderPolicyBoundaryTests, 'test_data_fake_issuer_and_receipt_cannot_be_provider_grant',
             'provider decision invalid; details omitted'),
            (NativeDeltaPolicyTests, 'test_audit_only_is_separate_and_requires_whole_binding_acceptance',
             'notification audit acceptance missing; details omitted'),
            (NativeDeltaPolicyTests, 'test_native_issuer_requires_sealed_acceptance_and_cannot_authorize_d1',
             'review rejected; details omitted'),
            (CompositionPolicyTests, 'test_zero_child_topology_requires_complete_binding_and_observed_pins',
             'notification topology invalid; details omitted'),
        )
        original_init = LLMToolError.__init__
        for fault_type in (AssertionError, RuntimeError):
            for case, method, message in cases:
                with self.subTest(method=method, fault=fault_type.__name__):
                    fault = fault_type('synthetic unrelated refusal fault')
                    reached, surfaced = [], []
                    def injected_init(instance, *args, **kwargs):
                        if args == (message,):
                            reached.append(True)
                            raise fault
                        original_init(instance, *args, **kwargs)
                    class Result(unittest.TestResult):
                        def addError(self, test, error):
                            surfaced.append(error[1])
                            super().addError(test, error)
                        def addFailure(self, test, error):
                            surfaced.append(error[1])
                            super().addFailure(test, error)
                    with patch.object(LLMToolError, '__init__', injected_init):
                        result = Result()
                        case(method).run(result)  # Includes all registered cleanups.
                    self.assertEqual(reached, [True])
                    self.assertEqual(len(surfaced), 1, 'unrelated fault silently accepted')
                    self.assertIs(surfaced[0], fault)
