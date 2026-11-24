"""Offline tests for configurable EC2 REMOTE_BUSY window (#2281 row 20)."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


_PATH = Path(__file__).with_name("llmtool_monitor.py")
_SPEC = importlib.util.spec_from_file_location("llmtool_monitor_busy_window_2281", _PATH)
mon = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mon
_SPEC.loader.exec_module(mon)


class BusyWindowPolicyTests(unittest.TestCase):
    NOW = 1_800_000_000.0

    def _write_policy(self, root: str, content: str) -> Path:
        policy_dir = Path(root) / "tools" / "llmtool"
        policy_dir.mkdir(parents=True, exist_ok=True)
        policy_path = policy_dir / "policy.json"
        policy_path.write_text(content, encoding="utf-8")
        return policy_path

    def test_reader_never_raises_skips_symlinks_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            self.assertEqual(mon.ec2_busy_window_seconds(str(path)), 1800)
            self.assertEqual(mon.ec2_busy_window_seconds(directory), 1800)

            for content, expected in (
                ('{"schema_version": 1, "monitor_ec2_busy_window_seconds": 300}', 300),
                ('{"monitor_ec2_busy_window_seconds": 1}', 1),
                ('{"monitor_ec2_busy_window_seconds": 7200}', 7200),
                ('{"monitor_ec2_busy_window_seconds": 0}', 1800),
                ('{"monitor_ec2_busy_window_seconds": -1}', 1800),
                ('{"monitor_ec2_busy_window_seconds": true}', 1800),
                ('{"monitor_ec2_busy_window_seconds": false}', 1800),
                ('{"monitor_ec2_busy_window_seconds": "300"}', 1800),
                ('{"monitor_ec2_busy_window_seconds": 300.5}', 1800),
                ('{"monitor_ec2_busy_window_seconds": null}', 1800),
                ('{"other": 300}', 1800),
                ("[300]", 1800),
                ("{", 1800),
                ('{"monitor_ec2_busy_window_seconds": 60, "pad": "' + "x" * 5000 + '"}', 1800),
            ):
                with self.subTest(content=content[:60]):
                    path.write_text(content, encoding="utf-8")
                    self.assertEqual(mon.ec2_busy_window_seconds(str(path)), expected)

            path.write_text('{"monitor_ec2_busy_window_seconds": 60}', encoding="utf-8")
            link = Path(directory) / "link.json"
            link.symlink_to(path)
            self.assertEqual(mon.ec2_busy_window_seconds(str(link)), 1800)

            with patch.object(mon, "canonical_root", side_effect=RuntimeError("boom")):
                self.assertEqual(mon.ec2_busy_window_seconds(), 1800)
            self.assertEqual(mon.ec2_busy_window_seconds(123), 1800)

    def test_stuck_alerts_default_1800_when_key_missing_or_invalid(self):
        records = [
            mon.StuckRecord("00000001", "", False, self.NOW - 1800),
            mon.StuckRecord("00000002", "", False, self.NOW - 1801),
            mon.StuckRecord("00000003", "", False, self.NOW + 1),
        ]
        for content in (
            None,
            '{"schema_version": 1}',
            '{"monitor_ec2_busy_window_seconds": 0}',
            '{"monitor_ec2_busy_window_seconds": true}',
            '{"monitor_ec2_busy_window_seconds": "300"}',
            "{",
        ):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as root:
                if content is not None:
                    self._write_policy(root, content)
                with patch.object(mon, "canonical_root", return_value=root):
                    alerts = mon.stuck_alerts(self.NOW, None, [], records, set())
                self.assertEqual(
                    alerts,
                    ["EC2 忙碌：task 00000001（REMOTE_BUSY，30 分鐘前）"],
                )

    def test_stuck_alerts_uses_valid_configured_busy_window(self):
        records = [
            mon.StuckRecord("00000001", "", False, self.NOW - 60),
            mon.StuckRecord("00000002", "", False, self.NOW - 300),
            mon.StuckRecord("00000003", "", False, self.NOW - 301),
        ]
        with tempfile.TemporaryDirectory() as root:
            self._write_policy(root, json.dumps({"monitor_ec2_busy_window_seconds": 300}))
            with patch.object(mon, "canonical_root", return_value=root):
                alerts = mon.stuck_alerts(self.NOW, None, [], records, set())
        self.assertEqual(
            alerts,
            [
                "EC2 忙碌：task 00000001（REMOTE_BUSY，1 分鐘前）",
                "EC2 忙碌：task 00000002（REMOTE_BUSY，5 分鐘前）",
            ],
        )

        with tempfile.TemporaryDirectory() as root:
            self._write_policy(root, json.dumps({"monitor_ec2_busy_window_seconds": 3600}))
            older_records = [
                mon.StuckRecord("00000004", "", False, self.NOW - 2400),
                mon.StuckRecord("00000005", "", False, self.NOW - 3601),
            ]
            with patch.object(mon, "canonical_root", return_value=root):
                alerts = mon.stuck_alerts(self.NOW, None, [], older_records, set())
        self.assertEqual(
            alerts,
            ["EC2 忙碌：task 00000004（REMOTE_BUSY，40 分鐘前）"],
        )

    def test_stuck_alerts_never_follows_symlinked_policy_file(self):
        records = [mon.StuckRecord("00000001", "", False, self.NOW - 600)]
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "real.json"
            target.write_text('{"monitor_ec2_busy_window_seconds": 60}', encoding="utf-8")
            policy_dir = Path(root) / "tools" / "llmtool"
            policy_dir.mkdir(parents=True)
            (policy_dir / "policy.json").symlink_to(target)
            with patch.object(mon, "canonical_root", return_value=root):
                alerts = mon.stuck_alerts(self.NOW, None, [], records, set())
        self.assertEqual(
            alerts,
            ["EC2 忙碌：task 00000001（REMOTE_BUSY，10 分鐘前）"],
        )


if __name__ == "__main__":
    unittest.main()
