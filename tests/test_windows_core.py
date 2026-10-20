"""Native Windows regressions that also run on POSIX CI."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness2.core import context, tree
from harness2.core.spec import RunView
from harness2.demo import DemoAdapter


class PortableCoreTests(unittest.TestCase):
    def test_missing_rollout_preserves_original_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = DemoAdapter(root)
            dest = root / "context"
            (dest / "rollouts").mkdir(parents=True)
            (dest / "deliverables").mkdir()
            rollout = RunView(
                label="failed",
                run_id="failed",
                run_dir=root / "missing",
                ok=False,
                delivered=False,
                error="executor could not start",
            )
            failure = context._write_rollout(dest, rollout, adapter, 1000, False)
            self.assertEqual(failure, "failed: executor could not start")
            self.assertTrue((dest / "deliverables/failed.md").is_file())

    def test_nested_harness_paths_and_unicode_round_trip(self):
        with tempfile.TemporaryDirectory(prefix="harness space ") as temporary:
            root = Path(temporary)
            adapter = DemoAdapter(root)
            surface = adapter.substrate.surface()
            expected = tree.read_tree(surface, adapter.base_harness())
            self.assertIn("scripts/summarize.py", expected)
            expected["scripts/summarize.py"] += "\n# Résumé — 日本語\n"
            dest = root / "unicode harness"
            tree.write_tree(surface, expected, dest)
            self.assertEqual(tree.read_tree(surface, dest), expected)
            adapter.validate_tree(tree.read_tree(surface, dest))
            tree.validate(surface, tree.read_tree(surface, dest))

    def test_file_lock_serializes_independent_processes(self):
        worker = """
import sys, time
from pathlib import Path
from harness2.locking import file_lock
root = Path(sys.argv[1])
for _ in range(20):
    with file_lock(root / 'counter.lock'):
        path = root / 'counter.json'
        value = int(path.read_text(encoding='utf-8'))
        time.sleep(0.002)
        path.write_text(str(value + 1), encoding='utf-8')
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "counter.json").write_text("0", encoding="utf-8")
            workers = [
                subprocess.Popen(
                    [sys.executable, "-c", worker, str(root)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for _ in range(4)
            ]
            try:
                for process in workers:
                    stdout, stderr = process.communicate(timeout=30)
                    self.assertEqual(process.returncode, 0, (stdout, stderr))
            finally:
                for process in workers:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            self.assertEqual(json.loads((root / "counter.json").read_text()), 80)


if __name__ == "__main__":
    unittest.main()
