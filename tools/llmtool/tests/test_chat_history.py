"""Chat history persistence tests (#683).

Pins the public behaviour of llmtool.chat_history.ChatHistory: saving
then loading history, loading when the history file is missing, loading
a corrupt history file, sensitive content being withheld on disk, and
clearing history. All tests run in temporary directories only, with no
network and no real provider involved.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import chat_history
from llmtool import test_output_excerpts

# Labelled synthetic marker, not a credential-shaped value. It is only
# matched by the detector stand-in installed in one test below, so no
# real detector input is exercised.
_MARK = "synthetic sample marker 683"


def _restore_emitted_sensitive(value):
    """Restore the real sensitive detector after the test that swapped it."""
    test_output_excerpts._emitted_sensitive = value


class ChatHistoryTests(unittest.TestCase):
    """Tests for ChatHistory public functions in temporary directories."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hist_path = Path(self.tmp.name) / "history.json"
        self.history = chat_history.ChatHistory(self.hist_path)

    def test_save_then_load_roundtrip(self):
        """Turns saved via save() come back unchanged from load()."""
        turns = [
            {"role": "user", "content": "What is the capital of France?"},
            {"role": "assistant", "content": "The capital of France is Paris."},
        ]

        self.history.save(turns)

        self.assertTrue(self.hist_path.is_file())
        self.assertEqual(self.history.load(), turns)

    def test_load_missing_file_returns_empty_list(self):
        """A missing history file yields an empty list and stays enabled."""
        self.assertFalse(self.hist_path.exists())

        self.assertEqual(self.history.load(), [])
        self.assertIsNone(self.history.notice)

    def test_load_corrupt_file_returns_empty_list(self):
        """A corrupt history file yields an empty list and disables the module."""
        fd = os.open(str(self.hist_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"{this is not json")
        os.chmod(self.hist_path, 0o600)

        self.assertEqual(self.history.load(), [])
        self.assertIsNotNone(self.history.notice)
        self.assertFalse(self.history.enabled)

    def test_sensitive_content_is_withheld_on_disk(self):
        """Content flagged as sensitive is withheld, not written to the file."""
        original = test_output_excerpts._emitted_sensitive
        self.addCleanup(_restore_emitted_sensitive, original)
        test_output_excerpts._emitted_sensitive = lambda text: _MARK in text
        turns = [
            {"role": "user", "content": "please note the value " + _MARK},
            {"role": "assistant", "content": "understood"},
        ]

        self.history.save(turns)
        on_disk = self.hist_path.read_bytes().decode("utf-8")

        self.assertNotIn(_MARK, on_disk)
        self.assertIn("[withheld]", on_disk)
        loaded = self.history.load()
        self.assertEqual(loaded[0]["content"], "[withheld]")
        self.assertEqual(loaded[1]["content"], "understood")

    def test_clear_removes_saved_turns(self):
        """clear() makes a later load() return an empty list."""
        self.history.save([
            {"role": "user", "content": "Remember this line."},
            {"role": "assistant", "content": "Noted."},
        ])

        self.history.clear()

        self.assertEqual(self.history.load(), [])
