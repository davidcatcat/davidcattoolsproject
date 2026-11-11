import argparse
import unittest

from llmtool import task_submission_cli


class CliHelpHomeLlmTests(unittest.TestCase):
    def test_context_stdin_help_names_no_retired_route(self):
        parser = argparse.ArgumentParser()
        actions = parser.add_subparsers(dest='subcommand')
        task_submission_cli.add_task_submission_parsers(actions)
        delegate = actions.choices['delegate']
        flag = next(a for a in delegate._actions if '--context-stdin' in a.option_strings)
        self.assertNotIn('company-gw', flag.help)
        self.assertTrue(flag.help.startswith('codex text proposals only:'))


if __name__ == '__main__':
    unittest.main()
