"""Pure offline validator coverage; suggested commands are never run."""
import ast
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import validator as v
from llmtool.cli import main
from llmtool.context import ContextPacket, EvidenceItem, PACKET_LIMIT
from llmtool.errors import LLMToolError
from llmtool.providers.base import Reply


def packet(**overrides):
    data = dict(facts=['HTTP 500 observed'], evidence=[EvidenceItem('HTTP 500 at 10:00', 'supplied log', 'line 1')],
                inferences=['Request failed'], hypotheses=['Configuration mismatch'],
                unknowns=['Previous exception unavailable'], verify_next=['Inspect previous log evidence'])
    return ContextPacket(**{**data, **overrides})


class CommandTests(unittest.TestCase):
    def check_table(self, risk, commands):
        for command in commands:
            with self.subTest(command=command):
                result = v.classify_command(command)
                self.assertEqual(result.risk, risk)
                self.assertEqual(result.command, command)
                self.assertTrue(result.reason)

    def test_safe_reads(self):
        self.check_table(v.CommandRisk.SAFE_READ, ['pwd', 'ls', 'ls -l', 'ls -l /etc/company/app/config.yaml',
            'stat /etc/company/app/config.yaml',
            'kubectl get pods', 'kubectl get deployments', 'kubectl get events',
            'kubectl rollout status deployment/api'])

    def test_sensitive_reads(self):
        self.check_table(v.CommandRisk.SENSITIVE_READ, ['cat .env', 'cat */.env', 'cat *credentials*',
            'cat *credential*', 'cat *secret*', 'cat *token*', 'cat ~/.ssh/id_rsa', 'cat ~/.ssh/id_ed25519',
            'cat ~/.ssh/id_ecdsa', 'cat ~/.aws/credentials', 'cat ~/.kube/config', 'cat kubeconfig',
            'cat private-key.pem', 'cat signing.key', 'cat application.yml', 'cat application.yaml',
            'cat ~/.password-store/password', 'cat ~/.netrc', 'cat ~/.npmrc',
            'cat application.properties', 'cat /etc/company/app/config.yaml', 'env', 'printenv', 'printenv PATH',
            'kubectl get secret', 'kubectl get secrets', 'kubectl get secret my-secret -o yaml',
            'kubectl get secret -o json', 'kubectl get pods -o yaml', 'kubectl describe pod api',
            'aws configure export-credentials', 'security find-generic-password -w', 'cat login.keychain-db',
            'head .env', 'grep PASSWORD credentials',
            'systemctl status api', 'ps', 'ps aux'])

    def test_mutating(self):
        self.check_table(v.CommandRisk.MUTATING, ['touch file', 'mkdir dir', 'cp a b', 'mv a b', 'chmod 600 file',
            'chown user file', 'git add .', 'git commit -am test', 'git push', 'git pull', 'git fetch',
            'git merge other', 'git rebase main', 'git checkout branch', 'git switch main', 'git restore file',
            'git stash', 'git reset HEAD', 'git diff --output=out', 'kubectl apply -f manifest.yaml',
            'kubectl patch pod api', 'kubectl edit deployment/api', 'kubectl scale deployment/api',
            'kubectl rollout restart deployment/api', 'systemctl restart api', 'systemctl start api',
            'systemctl stop api', 'terraform apply', 'ansible-playbook playbook.yml'])

    def test_destructive(self):
        self.check_table(v.CommandRisk.DESTRUCTIVE, ['rm file', 'rm -rf /', 'git reset --hard', 'git clean -f',
            'git clean -fd', 'git branch -D obsolete', 'kubectl delete namespace production', 'terraform destroy',
            'DROP DATABASE production', 'DROP TABLE users', 'truncate -s 0 file', 'mkfs.ext4 /dev/sda',
            'shutdown now', 'reboot', 'kill -9 123', '/bin/rm file', 'sudo rm file'])

    def test_unknown(self):
        self.check_table(v.CommandRisk.UNKNOWN, ['find /', 'grep -R /', 'cat arbitrary-config-file', 'curl example',
            'custom-command', 'git -c core.pager=evil status', 'git diff --ext-diff', 'git branch new',
            '/bin/../tmp/ls', '/usr/bin/other/ls',
            'git status', 'git status --short', 'git diff --stat', 'git diff --name-only',
            'git log --oneline', 'git show --stat', 'git -C . status',
            'sudo ls /tmp', 'sudo git status', 'sudo /usr/bin/ls /tmp',
            'kubectl exec api -- cat file', 'sh -c pwd', 'env X=1 rm file', 'ls --unknown', 'stat "$PATH"'])

    def test_raw_git_reads_are_not_approval_free(self):
        for command in ('git status', 'git log --oneline', 'git diff --stat',
                        'git diff --name-only', 'git show --stat'):
            assessment = v.classify_command(command)
            self.assertEqual(assessment.risk, v.CommandRisk.UNKNOWN)
            self.assertTrue(v.requires_approval(assessment.risk))

    def test_blank(self):
        self.check_table(v.CommandRisk.UNKNOWN, ['', ' ', '\t'])

    def test_historical_regression(self):
        risk = v.classify_command('cat /etc/company/app/config.yaml').risk
        self.assertEqual(risk, v.CommandRisk.SENSITIVE_READ)
        for command in ('ls -l /etc/company/app/config.yaml', 'stat /etc/company/app/config.yaml'):
            self.assertEqual(v.classify_command(command).risk, v.CommandRisk.SAFE_READ)

    def test_compound_danger_floor(self):
        cases = {'ls -l && pwd': v.CommandRisk.SAFE_READ, 'cat file && rm file': v.CommandRisk.DESTRUCTIVE,
                 'git status; curl example': v.CommandRisk.UNKNOWN, 'cat .env || pwd': v.CommandRisk.SENSITIVE_READ,
                 'custom; git commit -am test': v.CommandRisk.MUTATING, 'rm file; custom': v.CommandRisk.DESTRUCTIVE}
        for command, risk in cases.items():
            self.check_table(risk, [command])

    def test_pipes(self):
        self.check_table(v.CommandRisk.SENSITIVE_READ, ['cat .env | custom', 'pwd | printenv'])
        self.check_table(v.CommandRisk.MUTATING, ['pwd | tee file'])
        self.check_table(v.CommandRisk.UNKNOWN, ['pwd | custom'])

    def test_redirects(self):
        self.check_table(v.CommandRisk.MUTATING, ['pwd > out', 'pwd >> out', 'pwd 2> out'])
        self.check_table(v.CommandRisk.SENSITIVE_READ, ['cat < .env'])
        self.check_table(v.CommandRisk.UNKNOWN, ['pwd < file', 'pwd |', 'ls &&', 'pwd <<< file'])

    def test_substitution_and_backticks(self):
        self.check_table(v.CommandRisk.UNKNOWN, ['echo "$(rm -rf /)"', '`whoami`', 'ls $(pwd)', 'pwd `rm file`'])
        self.check_table(v.CommandRisk.DESTRUCTIVE, ['rm file && echo "$(pwd)"'])

    def test_quoting_unicode_preserved(self):
        self.check_table(v.CommandRisk.SAFE_READ, ['  stat "日誌 file"  ', "stat 'semi;colon'", "ls 'a && b'", "stat '>'"])
        self.check_table(v.CommandRisk.SENSITIVE_READ, ["cat '/etc/company/app/config.yaml'"])
        self.check_table(v.CommandRisk.UNKNOWN, ["stat 'unterminated", 'stat "literal $variable"'])

    def test_controls_and_bidi_are_never_safe(self):
        for value in ('ls\x00', 'ls\x1b', 'ls\n', 'ls\x7f', 'ls\x9b', 'pwd\u202e'):
            with self.subTest(value=value):
                self.assertNotEqual(v.classify_command(value).risk, v.CommandRisk.SAFE_READ)
        self.check_table(v.CommandRisk.DESTRUCTIVE, ['rm -rf ~ \r- git status'])
        risky = 'rm -rf ~ \r- git status'
        assessed = v.assess_verify_next(packet(verify_next=[risky]))[0]
        self.assertEqual(assessed.command, risky)
        self.assertEqual(assessed.risk, v.CommandRisk.DESTRUCTIVE)

    def test_review_hardening_cases(self):
        self.check_table(v.CommandRisk.DESTRUCTIVE, [
            'git reset --har', 'git clean --forc', 'git branch -Df x', 'git push --force',
            'git push origin :main', 'kubectl -n prod delete ns prod', '/usr/bin/sudo rm -rf /',
            'rmdir x', 'unlink x', 'shred x', 'dd if=x of=y', 'find . -delete',
            'docker rm x', 'docker rm -f x'])
        self.check_table(v.CommandRisk.MUTATING, ['sed -i s/a/b/ file', 'sed -i s/a/b/ .env',
                                                   'git commit -am test', 'kubectl rollout restart deployment/api'])
        self.check_table(v.CommandRisk.SENSITIVE_READ, ['sudo cat ~/.aws/credentials'])
        self.assertTrue(v.requires_approval(v.classify_command('git status').risk))

    def test_bounds_utf8_bytes_and_type(self):
        for value in ('x' * (v.COMMAND_LIMIT + 1), '日' * 3000, None, []):
            with self.subTest(type=type(value)), self.assertRaises(LLMToolError):
                v.classify_command(value)
        self.assertEqual(v.classify_command('x' * v.COMMAND_LIMIT).risk, v.CommandRisk.UNKNOWN)

    def test_approval_separate(self):
        for risk in v.CommandRisk:
            self.assertEqual(v.requires_approval(risk), risk != v.CommandRisk.SAFE_READ)
        self.assertTrue(v.requires_approval('unsupported'))

    def test_models_immutable_deterministic(self):
        a = v.classify_command('pwd')
        self.assertEqual(a.to_json(), v.classify_command('pwd').to_json())
        with self.assertRaises(FrozenInstanceError):
            a.risk = v.CommandRisk.DESTRUCTIVE
        data = a.to_dict(); data['risk'] = 'different'
        self.assertEqual(a.risk, v.CommandRisk.SAFE_READ)

    def test_hostile_input_never_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'MUST_NOT_EXIST'
            inputs = ['touch '+str(marker), 'echo "$(rm -rf /)"', '`whoami`', 'cat file && rm file',
                      'git status; curl example', 'kubectl delete namespace production', 'terraform destroy']
            with patch('subprocess.Popen', side_effect=AssertionError('process started')), \
                 patch('subprocess.run', side_effect=AssertionError('process started')), \
                 patch('os.system', side_effect=AssertionError('shell started')), \
                 patch('socket.socket', side_effect=AssertionError('network started')):
                for command in inputs: v.classify_command(command)
                v.validate_context_packet(packet(verify_next=inputs))
            self.assertFalse(marker.exists())

    def test_source_has_no_execution_calls(self):
        tree = ast.parse(Path(v.__file__).read_text())
        forbidden = {'subprocess', 'os', 'socket', 'urllib', 'requests'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertFalse(forbidden & {a.name.split('.')[0] for a in node.names})
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ('eval', 'exec', 'compile', 'open', '__import__'))


class ContextValidationTests(unittest.TestCase):
    def test_valid_packet_and_support_boundary(self):
        result = v.validate_context_packet(packet())
        self.assertTrue(result.valid)
        # Presence alone does not prove truth or a per-claim support relationship.
        self.assertTrue(v.validate_context_packet(packet(facts=['arbitrary false claim'])).valid)
        self.assertFalse(any(f.severity == v.Severity.ERROR for f in result.findings))

    def test_facts_without_evidence(self):
        result = v.validate_context_packet(packet(evidence=[]))
        self.assertFalse(result.valid)
        self.assertIn('EVIDENCE_MISSING', [f.code for f in result.findings])

    def test_inference_without_support(self):
        result = v.validate_context_packet(packet(facts=[], evidence=[]))
        self.assertFalse(result.valid)
        self.assertIn('INFERENCE_SUPPORT_MISSING', [f.code for f in result.findings])

    def test_fact_hypothesis_duplicate(self):
        result = v.validate_context_packet(packet(hypotheses=['HTTP 500 observed']))
        finding = next(f for f in result.findings if f.code == 'CATEGORY_COLLISION')
        self.assertFalse(result.valid)
        self.assertEqual(finding.category, 'facts/hypotheses')
        self.assertEqual(finding.locator, 'facts[0]')

    def test_other_collisions(self):
        for category in ('inferences', 'unknowns'):
            with self.subTest(category=category):
                self.assertFalse(v.validate_context_packet(packet(**{category: ['HTTP 500 observed']})).valid)
        self.assertFalse(v.validate_context_packet(packet(inferences=['same'], hypotheses=['same'])).valid)

    def test_different_wording_not_equivalence(self):
        self.assertTrue(v.validate_context_packet(packet(hypotheses=['HTTP 500 observed '])).valid)
        self.assertTrue(v.validate_context_packet(packet(hypotheses=['http 500 observed'])).valid)

    def test_unknowns_without_next_warning(self):
        result = v.validate_context_packet(packet(verify_next=[]))
        self.assertTrue(result.valid)
        self.assertEqual(result.findings[0].code, 'VERIFY_NEXT_MISSING')
        self.assertEqual(result.findings[0].severity, v.Severity.WARNING)

    def test_verify_next_risk_and_original_text(self):
        suggestions = ['  ls -l  ', 'cat /etc/company/app/config.yaml', 'rm -rf /', 'git commit -am test', 'Inspect evidence']
        result = v.validate_context_packet(packet(verify_next=suggestions))
        self.assertTrue(result.valid)  # Warnings do not grant execution permission.
        self.assertEqual([a.command for a in result.command_assessments], suggestions)
        self.assertEqual([a.risk for a in result.command_assessments],
                         [v.CommandRisk.SAFE_READ, v.CommandRisk.SENSITIVE_READ, v.CommandRisk.DESTRUCTIVE,
                          v.CommandRisk.MUTATING, v.CommandRisk.UNKNOWN])
        self.assertEqual([f.locator for f in result.findings], ['verify_next[1]', 'verify_next[2]', 'verify_next[3]', 'verify_next[4]'])

    def test_long_prose_next_unknown(self):
        text = 'Evidence suggestion ' * 1000
        result = v.validate_context_packet(packet(verify_next=[text]))
        self.assertEqual(result.command_assessments[0].risk, v.CommandRisk.UNKNOWN)
        self.assertEqual(result.command_assessments[0].command, text)

    def test_stable_findings_json_and_immutable(self):
        p = packet(evidence=[], facts=['same'], inferences=['same'], hypotheses=['same'], verify_next=[])
        first, second = v.validate_context_packet(p), v.validate_context_packet(p)
        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual([f.code for f in first.findings], ['EVIDENCE_MISSING', 'CATEGORY_COLLISION',
                         'CATEGORY_COLLISION', 'CATEGORY_COLLISION', 'VERIFY_NEXT_MISSING'])
        self.assertEqual(list(json.loads(first.to_json())), ['valid', 'findings', 'command_assessments'])
        source = list(first.findings); detached = v.ValidationResult(source, [])
        source.clear(); self.assertEqual(detached.findings, first.findings)
        with self.assertRaises(FrozenInstanceError): first.findings[0].message = 'changed'
        with self.assertRaises(FrozenInstanceError): first.findings = ()
        data = first.to_dict(); data['findings'].clear(); self.assertTrue(first.findings)

    def test_packet_size_item_type_bounds(self):
        for p in (packet(facts=['x'*(PACKET_LIMIT+1)]), packet(facts=['x']*(v.PACKET_ITEM_LIMIT+1)), None):
            with self.subTest(type=type(p)), self.assertRaises(LLMToolError):
                v.validate_context_packet(p)

    def test_empty_packet_valid(self):
        empty = ContextPacket([], [], [], [], [], [])
        self.assertTrue(v.validate_context_packet(empty).valid)
        self.assertEqual(v.assess_verify_next(empty), ())


class ValidatorCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name)/'packet.json'
        self.file.write_text(packet().to_json())

    def cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_command_human(self):
        code, out, err = self.cli(['validate', 'command', 'cat /etc/company/app/config.yaml'])
        self.assertEqual((code, err), (0, ''))
        self.assertIn('Risk: SENSITIVE_READ', out)
        self.assertNotIn('/etc/company', out)

    def test_command_json(self):
        args = ['validate', 'command', 'rm -rf /', '--json']
        first, second = self.cli(args), self.cli(args)
        self.assertEqual(first, second)
        self.assertEqual(first[0], 0)
        self.assertEqual(json.loads(first[1])['risk'], 'DESTRUCTIVE')
        self.assertEqual(list(json.loads(first[1])), ['risk', 'reason'])

    def test_context_human(self):
        code, out, err = self.cli(['validate', 'context', '--file', str(self.file)])
        self.assertEqual((code, err), (0, ''))
        self.assertTrue(out.startswith('VALID\n'))
        self.assertIn('WARNING VERIFY_NEXT_UNKNOWN', out)

    def test_context_json_invalid(self):
        self.file.write_text(packet(evidence=[]).to_json())
        code, out, err = self.cli(['validate', 'context', '--file', str(self.file), '--json'])
        self.assertEqual((code, err), (1, ''))
        self.assertFalse(json.loads(out)['valid'])
        self.assertEqual(json.loads(out)['findings'][0]['code'], 'EVIDENCE_MISSING')

    def test_malformed_empty_stdout_sanitized(self):
        for content in ('PRIVATE malformed', '{"PRIVATE":1}', '{"facts":[],"facts":[]}'):
            self.file.write_text(content)
            code, out, err = self.cli(['validate', 'context', '--file', str(self.file), '--json'])
            self.assertEqual((code, out), (1, ''))
            self.assertNotIn('PRIVATE', err)
            self.assertNotIn(str(self.file), err)
            self.assertNotIn('Traceback', err)

    def test_file_bounds_and_binary(self):
        for content in (b'x'*(PACKET_LIMIT+1), b'\xff'):
            self.file.write_bytes(content)
            code, out, err = self.cli(['validate', 'context', '--file', str(self.file)])
            self.assertEqual((code, out), (1, ''))
            self.assertNotIn(str(self.file), err)

    def test_missing_directory_fifo_sanitized(self):
        fifo = self.file.parent/'fifo'; os.mkfifo(fifo)
        for name in (self.file.parent/'PRIVATE_MISSING', self.file.parent, fifo):
            code, out, err = self.cli(['validate', 'context', '--file', str(name)])
            self.assertEqual((code, out), (1, ''))
            self.assertNotIn('PRIVATE', err)
            self.assertNotIn(str(name), err)

    def test_command_oversize_sanitized(self):
        code, out, err = self.cli(['validate', 'command', 'PRIVATE' * 2000])
        self.assertEqual((code, out), (1, ''))
        self.assertNotIn('PRIVATE', err)

    def test_offline_no_config_credentials_provider_git(self):
        with ExitStack() as stack:
            for target in ('llmtool.cli.resolve_config', 'llmtool.cli.api_key', 'llmtool.cli.create_client',
                           'llmtool.cli.load_skill', 'llmtool.cli.collect_git_snapshot', 'socket.socket', 'subprocess.Popen'):
                stack.enter_context(patch(target, side_effect=AssertionError('unexpected operation')))
            self.assertEqual(self.cli(['validate', 'command', 'env'])[0], 0)
            self.assertEqual(self.cli(['validate', 'context', '--file', str(self.file), '--json'])[0], 0)

    def test_existing_request_commands_not_auto_validated(self):
        client = Mock(); client.complete.return_value = Reply('reply', {})
        with patch('llmtool.cli.classify_command', side_effect=AssertionError('automatic validation')), \
             patch('llmtool.cli.validate_context_packet', side_effect=AssertionError('automatic validation')), \
             patch('llmtool.cli.resolve_config', return_value={}), patch('llmtool.cli.api_key', return_value=None), \
             patch('llmtool.cli.create_client', return_value=client), patch('llmtool.cli.read_input', return_value=' user '):
            self.assertEqual(self.cli(['ask', ' user ']), (0, 'reply\n', ''))
            self.assertEqual(client.complete.call_args.args[0][-1]['content'], ' user ')
            client.complete.return_value = Reply(packet().to_json(), {})
            code, out, err = self.cli(['context-pack', ' user ', '--json'])
            self.assertEqual((code, err), (0, ''))
            self.assertEqual(json.loads(out)['facts'], ['HTTP 500 observed'])


if __name__ == '__main__':
    unittest.main()
