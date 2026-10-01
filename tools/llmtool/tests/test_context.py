"""Packet structural validation and CLI boundaries, using fake providers only."""
import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool.cli import main
from llmtool.chat import ChatSession
from llmtool.context import (ContextPacket, EvidenceItem, PACKET_LIMIT, PACKET_FIELDS,
                            parse_context_packet, format_context_packet)
from llmtool.errors import LLMToolError
from llmtool.prompts import COMMON, messages_for
from llmtool.providers.base import Reply
from llmtool.skills import load_skill

INCIDENT = '  2026-10-01T10:00Z api-7 returned HTTP 500; startup config unavailable.  '
DATA = {
    'facts': ['api-7 returned HTTP 500'],
    'evidence': [{'statement': 'HTTP 500 at 2026-10-01T10:00Z', 'source': 'supplied log', 'locator': 'line 1'}],
    'inferences': ['Request processing did not complete successfully'],
    'hypotheses': ['Startup configuration may be unavailable'],
    'unknowns': ['The previous container exception is not provided'],
    'verify_next': ['Inspect previous container logs'],
}


class ContextTests(unittest.TestCase):
    def parse(self, data=None):
        return parse_context_packet(json.dumps(DATA if data is None else data))

    def test_valid_construction(self):
        packet = ContextPacket(['fact'], [EvidenceItem('material', 'supplied log')], [], [], [], [])
        self.assertEqual(packet.facts, ('fact',))
        self.assertEqual(packet.evidence[0].locator, None)

    def test_immutable_detached_containers(self):
        source = ['fact']
        packet = ContextPacket(source, [], [], [], [], [])
        source.append('changed')
        self.assertEqual(packet.facts, ('fact',))
        with self.assertRaises(FrozenInstanceError):
            packet.facts = ('changed',)
        evidence = EvidenceItem('material', 'supplied log')
        with self.assertRaises(FrozenInstanceError):
            evidence.source = 'changed'
        detached = self.parse().to_dict()
        detached['evidence'][0]['source'] = 'changed'
        self.assertEqual(self.parse().evidence[0].source, 'supplied log')

    def test_deterministic_json_roundtrip_order(self):
        packet = self.parse()
        self.assertEqual(packet.to_json(), self.parse().to_json())
        self.assertEqual(list(json.loads(packet.to_json())), list(PACKET_FIELDS))
        self.assertEqual(parse_context_packet(packet.to_json()), packet)
        self.assertEqual(packet.to_dict(), DATA)

    def test_deterministic_human_sections(self):
        packet = self.parse()
        output = format_context_packet(packet)
        self.assertEqual(output, format_context_packet(packet))
        self.assertEqual([line for line in output.splitlines() if line.endswith(':')], [n.upper()+':' for n in PACKET_FIELDS])
        self.assertIn('  source: supplied log\n  locator: line 1', output)
        self.assertNotIn('EvidenceItem(', output)

    def test_valid_json_parse(self):
        self.assertIsInstance(self.parse(), ContextPacket)
        self.assertIsInstance(self.parse().evidence[0], EvidenceItem)

    def test_missing_fields(self):
        for name in PACKET_FIELDS:
            data = copy.deepcopy(DATA)
            del data[name]
            with self.subTest(name=name), self.assertRaises(LLMToolError):
                self.parse(data)

    def test_extra_fields(self):
        for name in ('reasoning', 'confidence', 'command', 'PRIVATE'):
            with self.subTest(name=name), self.assertRaises(LLMToolError) as error:
                self.parse({**DATA, name: 'PRIVATE raw output'})
            self.assertNotIn('PRIVATE', str(error.exception))

    def test_wrong_category_types(self):
        for name in PACKET_FIELDS:
            for value in (None, {}, 'text', 1, True):
                with self.subTest(name=name, value=value), self.assertRaises(LLMToolError):
                    self.parse({**DATA, name: value})

    def test_nonstring_and_blank_items(self):
        for name in set(PACKET_FIELDS) - {'evidence'}:
            for item in (None, 1, True, [], {}, '', ' \n\t'):
                with self.subTest(name=name, item=item), self.assertRaises(LLMToolError):
                    self.parse({**DATA, name: [item]})

    def test_malformed_evidence(self):
        invalid = [None, 'statement', [], {}, {'statement': 'x'}, {'source': 'log'},
                   {'statement': 'x', 'source': 'log', 'extra': 'PRIVATE'}]
        for key in ('statement', 'source', 'locator'):
            for value in (' ', 3, [], {}):
                invalid.append({'statement': 'x', 'source': 'log', key: value})
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(LLMToolError):
                self.parse({**DATA, 'evidence': [item]})

    def test_optional_locator_no_invention(self):
        for evidence in ({'statement': 'x', 'source': 'user notes'},
                         {'statement': 'x', 'source': 'user notes', 'locator': None}):
            packet = self.parse({**DATA, 'evidence': [evidence]})
            self.assertIsNone(packet.evidence[0].locator)
            self.assertNotIn('locator:', format_context_packet(packet))
            self.assertIsNone(packet.to_dict()['evidence'][0]['locator'])

    def test_malformed_json_and_roots(self):
        for text in ('PRIVATE', '', '{', '[]', 'null', 'true', '1', '"PRIVATE"',
                     '```json\n'+json.dumps(DATA)+'\n```', json.dumps(DATA)+' trailing',
                     '{"facts": NaN}', '{"facts": Infinity}', '{"facts": -Infinity}'):
            with self.subTest(text=text), self.assertRaises(LLMToolError) as error:
                parse_context_packet(text)
            self.assertNotIn('PRIVATE', str(error.exception))

    def test_size_bytes_and_invalid_utf8(self):
        for text in ('x'*(PACKET_LIMIT+1), '測'*(PACKET_LIMIT//3+1), '\ud800'):
            with self.assertRaises(LLMToolError):
                parse_context_packet(text)
        for value in (None, b'{}', {}):
            with self.assertRaises(LLMToolError):
                parse_context_packet(value)

    def test_duplicate_keys_top_and_evidence(self):
        raw = json.dumps(DATA)
        for text in (raw.replace('"facts":', '"facts": [], "facts":', 1),
                     raw.replace('"source":', '"source": "other", "source":', 1)):
            with self.assertRaisesRegex(LLMToolError, 'duplicate keys'):
                parse_context_packet(text)

    def test_bounded_depth(self):
        with self.assertRaisesRegex(LLMToolError, 'nesting limit'):
            parse_context_packet('['*10000+'0'+']'*10000)
        with self.assertRaises(LLMToolError):
            self.parse({**DATA, 'facts': [[[[['deep']]]]]})
        self.assertIsInstance(self.parse(), ContextPacket)

    def test_escaped_brackets_are_text(self):
        text = 'literal [ { "quoted" \\ path } ] '*20
        packet = self.parse({**DATA, 'facts': [text]})
        self.assertEqual(packet.facts, (text,))

    def test_categories_preserved_separately(self):
        packet = self.parse()
        self.assertEqual(packet.facts, tuple(DATA['facts']))
        self.assertEqual(packet.inferences, tuple(DATA['inferences']))
        self.assertEqual(packet.hypotheses, tuple(DATA['hypotheses']))
        self.assertNotIn(DATA['hypotheses'][0], packet.facts)
        self.assertNotIn(DATA['hypotheses'][0], packet.inferences)

    def test_provenance_unknowns_verify_preserved(self):
        packet = self.parse()
        self.assertEqual(packet.evidence[0].source, 'supplied log')
        self.assertEqual(packet.evidence[0].locator, 'line 1')
        self.assertEqual(packet.unknowns, tuple(DATA['unknowns']))
        self.assertEqual(packet.verify_next, tuple(DATA['verify_next']))

    def test_text_exact_unicode_duplicates(self):
        text = '  證據\nvalue  '
        packet = self.parse({**DATA, 'facts': [text, text]})
        self.assertEqual(packet.facts, (text, text))
        self.assertIn(text, format_context_packet(packet))
        self.assertEqual(json.loads(packet.to_json())['facts'], [text, text])

    def test_control_and_surrogate_strings(self):
        for value in ('PRIVATE\x00', '\x1bPRIVATE', '\ud800', '\x7f'):
            with self.subTest(value=value), self.assertRaises(LLMToolError):
                self.parse({**DATA, 'facts': [value]})

    def test_empty_categories_valid(self):
        packet = self.parse({name: [] for name in PACKET_FIELDS})
        self.assertEqual(format_context_packet(packet).count('(none supplied)'), 6)

    def test_constructor_rejects_mutable_or_untyped_items(self):
        for kwargs in ({'facts': 'text'}, {'facts': [{}]}, {'evidence': [{}]}, {'evidence': ['x']}):
            values = {name: [] for name in PACKET_FIELDS}
            values.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(LLMToolError):
                ContextPacket(**values)

    def test_skill_schema_version(self):
        skill = load_skill('context-pack')
        self.assertEqual(skill.version, 2)
        for key in PACKET_FIELDS:
            self.assertIn('"'+key+'"', skill.instructions)
        self.assertIn('never promote', skill.instructions)
        self.assertIn('hidden reasoning', skill.instructions)


class ContextCLITests(unittest.TestCase):
    def cli(self, response, args=None):
        client = Mock()
        client.complete.return_value = Reply(response, {'internal': 'PRIVATE_REASONING'})
        out, err = io.StringIO(), io.StringIO()
        with patch('llmtool.cli.resolve_config', return_value={}), patch('llmtool.cli.api_key', return_value=None), patch('llmtool.cli.create_client', return_value=client), patch('llmtool.cli.read_input', return_value=INCIDENT), redirect_stdout(out), redirect_stderr(err):
            status = main(args or ['context-pack'])
        return status, out.getvalue(), err.getvalue(), client

    def test_json_output_and_exact_request(self):
        status, out, err, client = self.cli(json.dumps(DATA), ['context-pack', '--json'])
        self.assertEqual((status, err), (0, ''))
        self.assertEqual(json.loads(out), DATA)
        self.assertNotIn('PRIVATE_REASONING', out)
        client.complete.assert_called_once_with(messages_for('context-pack', INCIDENT))

    def test_human_output(self):
        status, out, err, _ = self.cli(json.dumps(DATA))
        self.assertEqual((status, err), (0, ''))
        self.assertEqual(out, format_context_packet(parse_context_packet(json.dumps(DATA)))+'\n')

    def test_context_packet_tty_escape_preserves_json_data(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True
        hostile = {**DATA, 'facts': ['A\rB\x9b31m\u202eC']}
        raw = json.dumps(hostile, ensure_ascii=False)
        client = Mock()
        client.complete.return_value = Reply(raw, {})
        tty = TTY()
        with patch('llmtool.cli.resolve_config', return_value={}), patch('llmtool.cli.api_key', return_value=None), \
                patch('llmtool.cli.create_client', return_value=client), patch('llmtool.cli.read_input', return_value=INCIDENT), \
                patch('llmtool.cli.sys.stdout', tty):
            self.assertEqual(main(['context-pack']), 0)
        self.assertIn('A\\x0dB\\x9b31m\\u202eC', tty.getvalue())
        output = io.StringIO()
        with patch('llmtool.cli.resolve_config', return_value={}), patch('llmtool.cli.api_key', return_value=None), \
                patch('llmtool.cli.create_client', return_value=client), patch('llmtool.cli.read_input', return_value=INCIDENT), \
                patch('llmtool.cli.sys.stdout', output):
            self.assertEqual(main(['context-pack', '--json']), 0)
        self.assertEqual(json.loads(output.getvalue()), hostile)

    def test_malformed_provider_output_no_stdout_sanitized(self):
        for response in ('PRIVATE_RAW_RESPONSE', '{"facts": ["PRIVATE"]}', json.dumps({**DATA, 'hypotheses': [4]})):
            for args in (['context-pack'], ['context-pack', '--json', '--verbose']):
                with self.subTest(response=response, args=args):
                    status, out, err, _ = self.cli(response, args)
                    self.assertEqual(status, 1)
                    self.assertEqual(out, '')
                    self.assertNotIn('PRIVATE', err)
                    self.assertNotIn('Traceback', err)

    def test_command_text_no_execution(self):
        data = {**DATA, 'verify_next': ['kubectl logs api-7; git status; touch forbidden']}
        with patch('subprocess.run', side_effect=AssertionError), patch('subprocess.Popen', side_effect=AssertionError), patch('os.system', side_effect=AssertionError):
            for args in (['context-pack'], ['context-pack', '--json']):
                status, out, err, _ = self.cli(json.dumps(data), args)
                self.assertEqual((status, err), (0, ''))
                self.assertIn(data['verify_next'][0], out)

    def test_ask_unchanged_plain_text(self):
        status, out, err, client = self.cli('plain answer', ['ask'])
        self.assertEqual((status, out, err), (0, 'plain answer\n', ''))
        client.complete.assert_called_once_with(messages_for('ask', INCIDENT))

    def test_chat_unchanged_not_automatic_packet(self):
        config = {'active_provider': 'local', 'providers': {'local': {'type': 'claude-cli'}}, 'generation': {}}
        session = ChatSession(config)
        client = Mock()
        client.complete.return_value = Reply('ordinary reply', {})
        with patch('llmtool.chat.create_client', return_value=client), patch('llmtool.chat.api_key', return_value=None):
            self.assertEqual(session.send(INCIDENT), 'ordinary reply')
        self.assertEqual(client.complete.call_args.args[0][0], {'role': 'system', 'content': COMMON})
        self.assertEqual(session.history[-1]['content'], 'ordinary reply')
