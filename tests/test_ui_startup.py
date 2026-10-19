"""Real startup checks for exclusive sockets and one server per workspace."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from harness2.ui.server import App, LocalServer, existing_session


class StartupTests(unittest.TestCase):
    def test_windows_port_is_exclusive(self):
        if os.name != "nt":
            self.skipTest("Windows socket exclusivity")
        with tempfile.TemporaryDirectory() as temporary:
            app = App(Path(temporary))
            first = LocalServer(("127.0.0.1", 0), app)
            try:
                with self.assertRaises(OSError):
                    LocalServer(("127.0.0.1", first.server_port), app)
            finally:
                first.server_close()
                app.close()

    def test_simultaneous_launches_reconnect_and_recover_after_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = (
                "from pathlib import Path; from harness2 import config; "
                f"config.ROOT=Path({str(root)!r}); "
                "from harness2.ui.server import main; raise SystemExit(main(['--no-browser']))"
            )
            first = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-u", "-c", script], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
            )
            try:
                deadline = time.monotonic() + 15
                session = root / ".harness2-ui/server.json"
                while not session.is_file() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(session.is_file())
                before = json.loads(session.read_text())
                others = [subprocess.Popen(
                    [sys.executable, "-X", "utf8", "-u", "-c", script], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                ) for _ in range(2)]
                for other in others:
                    stdout, stderr = other.communicate(timeout=15)
                    self.assertEqual(other.returncode, 0, stderr)
                    self.assertIn("already running", stdout)
                self.assertEqual(json.loads(session.read_text()), before)
                self.assertIsNotNone(existing_session(root))
            finally:
                first.kill()
                first.communicate(timeout=15)
            self.assertIsNone(existing_session(root))
            # A killed owner releases the OS lease even though metadata remains.
            from harness2.locking import file_lock
            with file_lock(root / ".harness2-ui/server.lock", blocking=False):
                pass
