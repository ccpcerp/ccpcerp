"""Pure-stdlib offline checks of the Finance G2 cumulative deadline helper.

Uses a temporary state file and harmless local Python child processes only.
No Frappe, MariaDB, CircleCI account, network or real fixture is accessed.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("finance_baseline_deadline.py")
spec = importlib.util.spec_from_file_location("finance_deadline_under_test", SCRIPT)
deadline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deadline)


class FinanceDeadlineOfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_patch = patch.object(deadline, "STATE", Path(self.temp.name) / "deadline")
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def _write(self, value):
        deadline.STATE.write_text(str(value) + "\n", encoding="ascii")

    def test_exclusive_start_and_no_clock_reset(self):
        deadline.start()
        original = deadline.STATE.read_text()
        self.assertTrue(original.strip().isdigit())
        self.assertEqual(deadline.STATE.stat().st_mode & 0o077, 0)
        with self.assertRaisesRegex(RuntimeError, "already initialized"):
            deadline.start()
        self.assertEqual(deadline.STATE.read_text(), original)

    def test_missing_state_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "absent or invalid"):
            deadline._read_remaining()

    def test_corrupt_or_nondecimal_state_fails_closed(self):
        for value in ("", "-1", "1.5", "1234567890123", "not-a-time"):
            with self.subTest(value=value):
                self._write(value)
                with self.assertRaisesRegex(RuntimeError, "absent or invalid"):
                    deadline._read_remaining()

    def test_expired_deadline_fails_closed_before_child(self):
        self._write(int(time.time()) - 1)
        with self.assertRaisesRegex(RuntimeError, "expired"):
            deadline.run([sys.executable, "-c", "raise AssertionError('never run')"])

    def test_excessive_future_deadline_fails_closed(self):
        self._write(int(time.time()) + deadline.MAX_SECONDS + 20)
        with self.assertRaisesRegex(RuntimeError, "safety ceiling"):
            deadline._read_remaining()

    def test_harmless_child_success(self):
        self._write(int(time.time()) + deadline.MAX_SECONDS - 10)
        result = deadline.run([sys.executable, "-c", "pass"])
        self.assertEqual(result, 0)

    def test_child_nonzero_is_preserved(self):
        self._write(int(time.time()) + deadline.MAX_SECONDS - 10)
        result = deadline.run([sys.executable, "-c", "import sys; sys.exit(7)"])
        self.assertEqual(result, 7)

    def test_command_timeout_kills_local_child(self):
        self._write(int(time.time()) + 1)
        with self.assertRaisesRegex(RuntimeError, "deadline exhausted"):
            deadline.run([sys.executable, "-c", "import time; time.sleep(30)"])

    def test_missing_command_fails_closed(self):
        self._write(int(time.time()) + deadline.MAX_SECONDS - 10)
        with self.assertRaisesRegex(RuntimeError, "explicit command"):
            deadline.run([])

    def test_no_unsupported_dispatch(self):
        with self.assertRaisesRegex(RuntimeError, "Usage"):
            deadline.main(["unsafe", "do-it"])


if __name__ == "__main__":
    unittest.main()
