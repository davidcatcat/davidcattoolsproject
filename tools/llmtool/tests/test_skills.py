import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool.cli import main
from llmtool.errors import LLMToolError
from llmtool.prompts import messages_for
from llmtool.providers.base import Reply
from llmtool.skills import Skill, discover_skills, load_skill, parse_skill

VALID = '---\nname: sample\nversion: 1\ndescription: Sample context.\n---\nInstructions.\n'


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def resource(self, directory, text=VALID):
        path = self.root / directory
        path.mkdir()
        (path / 'SKILL.md').write_text(text)
        return path

    def test_valid_parse(self):
        skill = parse_skill(VALID)
        self.assertEqual(skill, Skill('sample', 'Sample context.', 1, 'Instructions.\n'))

    def test_discovery_sorted_by_declared_name(self):
        self.resource('z', VALID.replace('name: sample', 'name: alpha'))
        self.resource('a', VALID.replace('name: sample', 'name: zulu'))
        self.assertEqual([s.name for s in discover_skills(self.root)], ['alpha', 'zulu'])
        self.assertEqual(load_skill('alpha', self.root).version, 1)

    def test_malformed_frontmatter(self):
        for text in ('body', '---\nname: sample', VALID.replace('name: sample', 'name: ['),
                     VALID.replace('name: sample', 'name: !!python/object:secret {}'),
                     VALID.replace('version: 1', 'version: &v 1\nextra: *v'),
                     VALID.replace('version: 1', 'version: 1\nversion: 2')):
            with self.subTest(text=text), self.assertRaises(LLMToolError):
                parse_skill(text)

    def test_missing_required_fields(self):
        for line in ('name: sample\n', 'version: 1\n', 'description: Sample context.\n'):
            with self.subTest(line=line), self.assertRaises(LLMToolError):
                parse_skill(VALID.replace(line, ''))

    def test_invalid_names_and_traversal(self):
        for name in ('', '../sample', '/sample', 'a/b', 'a\\b', 'UPPER', '-name', 'a'*65, 'a\n'):
            with self.subTest(name=name):
                with self.assertRaises(LLMToolError):
                    load_skill(name, self.root)
                with self.assertRaises(LLMToolError):
                    Skill(name, 'Description', 1, 'Body')

    def test_invalid_versions(self):
        for version in (0, -1, True, '1', '1.0', 1.5, None):
            with self.subTest(version=version), self.assertRaises(LLMToolError):
                Skill('sample', 'Description', version, 'Body')

    def test_unsupported_fields(self):
        for field in ('command', 'executable', 'hook', 'pre_run', 'post_run', 'shell', 'git', 'tools', 'tags'):
            with self.subTest(field=field), self.assertRaises(LLMToolError):
                parse_skill(VALID.replace('version: 1', 'version: 1\n'+field+': secret'))

    def test_duplicate_names(self):
        self.resource('one')
        self.resource('two')
        with self.assertRaisesRegex(LLMToolError, 'duplicate skill name'):
            discover_skills(self.root)

    def test_unknown_sanitized(self):
        with self.assertRaisesRegex(LLMToolError, '^unknown skill$'):
            load_skill('private-name', self.root)

    def test_symlinks_rejected(self):
        directory = self.resource('sample')
        (self.root / 'link').symlink_to(directory, target_is_directory=True)
        with self.assertRaises(LLMToolError):
            discover_skills(self.root)
        (self.root / 'link').unlink()
        file = directory / 'SKILL.md'
        file.rename(directory / 'source')
        file.symlink_to(directory / 'source')
        with self.assertRaises(LLMToolError):
            discover_skills(self.root)

    def test_bad_resource_types_and_size(self):
        directory = self.resource('sample')
        for data in (b'\xff', b'x'*65537):
            (directory / 'SKILL.md').write_bytes(data)
            with self.assertRaises(LLMToolError):
                discover_skills(self.root)
        (directory / 'SKILL.md').unlink()
        (directory / 'SKILL.md').mkdir()
        with self.assertRaises(LLMToolError):
            discover_skills(self.root)

    def test_empty_or_control_text_rejected(self):
        for description, body in (('', 'body'), ('desc', ' \n'), ('\x1bsecret', 'body'), ('desc', '\x00')):
            with self.subTest(description=description, body=body), self.assertRaises(LLMToolError):
                Skill('sample', description, 1, body)

    def test_composition_preserves_user(self):
        text = '  Unicode 測試\nignore system\n'
        plain = messages_for('ask', text)
        selected = messages_for('ask', text, load_skill('review-diff'))
        self.assertEqual(selected[1], plain[1])
        self.assertEqual(selected[1]['content'], text)
        self.assertIn('Selected skill: review-diff (version 1)', selected[0]['content'])
        self.assertNotEqual(selected[0], plain[0])

    def test_legacy_tasks_load_resources(self):
        for name in ('analyze-log', 'review-diff', 'context-pack'):
            with self.subTest(name=name):
                self.assertIn(load_skill(name).instructions, messages_for(name, 'data')[0]['content'])


class SkillCLITests(unittest.TestCase):
    def run_cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(args)
        return status, out.getvalue(), err.getvalue()

    def test_offline_listing_deterministic(self):
        with patch('llmtool.cli.resolve_config', side_effect=AssertionError), patch('llmtool.cli.create_client', side_effect=AssertionError):
            for args in (['skills'], ['skills', '--json']):
                first = self.run_cli(args)
                self.assertEqual(first, self.run_cli(args))
                self.assertEqual(first[0], 0)
                self.assertEqual(first[2], '')
                if '--json' in args:
                    self.assertEqual([s['name'] for s in json.loads(first[1])], ['analyze-log', 'context-pack', 'review-diff'])
                else:
                    self.assertEqual([line.split()[0] for line in first[1].splitlines()], ['analyze-log', 'context-pack', 'review-diff'])

    def test_show_offline(self):
        with patch('llmtool.cli.resolve_config', side_effect=AssertionError), patch('llmtool.cli.create_client', side_effect=AssertionError):
            result = self.run_cli(['skill', 'show', 'analyze-log'])
        self.assertEqual(result[0], 0)
        self.assertIn(load_skill('analyze-log').instructions, result[1])
        self.assertIn('"version": 2', result[1])

    def test_unknown_and_traversal_cli(self):
        with patch('llmtool.cli.create_client', side_effect=AssertionError):
            for args in (['skill', 'show', 'private-name'], ['ask', '--skill', '../secret', 'text'], ['ask', '--skill', 'private-name', 'text']):
                with self.subTest(args=args):
                    status, out, err = self.run_cli(args)
                    self.assertEqual(status, 1)
                    self.assertEqual(out, '')
                    self.assertNotIn('private-name', err)
                    self.assertNotIn('../secret', err)

    def test_ask_common_contract_no_execution_or_config_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / '.config/llmtool/config.yaml'
            config.parent.mkdir(parents=True)
            config.write_text('providers:\n  default:\n    base_url: http://example.test\n    model: test\n')
            before = config.read_bytes()
            client = Mock()
            command = 'NEXT_COMMANDS:\n- git status; cat /etc/private-config; touch forbidden'
            client.complete.return_value = Reply(command, {})
            text = '  exact 測試\n'
            with patch('pathlib.Path.home', return_value=home), patch('llmtool.cli.create_client', return_value=client), patch('llmtool.cli.api_key', return_value=None), patch('llmtool.cli.read_input', return_value=text), patch('subprocess.run', side_effect=AssertionError), patch('subprocess.Popen', side_effect=AssertionError), patch('os.system', side_effect=AssertionError):
                result = self.run_cli(['ask', '--skill', 'analyze-log', text])
            self.assertEqual(result[0], 0)
            self.assertNotIn(command, result[1])
            self.assertIn('suppressed 1', result[2])
            client.complete.assert_called_once_with(messages_for('ask', text, load_skill('analyze-log')))
            self.assertEqual(config.read_bytes(), before)
            self.assertFalse((home / 'forbidden').exists())
