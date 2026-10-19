# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Cross-process tests of the actual patched JobBench judge, without model calls.

Set HARNESS2_TEST_JB_JUDGE to an independently staged eval/judge.py and
HARNESS2_TEST_JB_PYTHON to its interpreter to exercise an external checkout.
Without a dependency checkout these integration tests explicitly skip.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from harness2 import config

# Each child imports the upstream implementation directly. Only the workload
# and synchronization markers live here; no locking implementation is copied.
_WORKER = r"""
import json
import os
from pathlib import Path
import runpy
import sys
import time

source = Path(sys.argv[1]).resolve()
judge = runpy.run_path(str(source), run_name="_harness2_test_judge_native")
assert judge["__name__"] == "_harness2_test_judge_native"
file_lock = judge["file_lock"]
root = Path(sys.argv[2])
mode, label = sys.argv[3:5]
lock = root / "judge.lock"

def mark(name, **fields):
    payload = dict(source=str(source), python=sys.executable, pid=os.getpid(),
                   time=time.monotonic(), **fields)
    (root / name).write_text(json.dumps(payload), encoding="utf-8")

def wait(name):
    deadline = time.monotonic() + 30
    while not (root / name).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Waiting for " + name)
        time.sleep(0.01)

if mode == "increment":
    mark("ready-" + label)
    wait("start")
    for _ in range(20):
        with file_lock(lock):
            claim = root / "exclusive-owner"
            claim.open("x", encoding="utf-8").close()
            try:
                value = int((root / "counter").read_text(encoding="utf-8"))
                time.sleep(0.006)
                (root / "counter").write_text(str(value + 1), encoding="utf-8")
            finally:
                claim.unlink()
    mark("done-" + label)
elif mode in ("hold", "exception"):
    try:
        with file_lock(lock):
            mark("owner-held")
            wait("owner-release")
            if mode == "exception":
                raise RuntimeError("Intentional critical-section failure")
    except RuntimeError:
        if mode != "exception":
            raise
        mark("owner-exception")
    else:
        mark("owner-released")
    # Stay alive so sibling acquisition proves release by the context manager.
    wait("owner-exit")
elif mode == "waiter":
    attempted = time.monotonic()
    mark("waiter-ready")
    with file_lock(lock):
        mark("waiter-acquired", waited=time.monotonic() - attempted)
    mark("waiter-done")
else:
    raise ValueError(mode)
"""


class JobBenchNativeJudgeLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.judge_source = Path(
            os.environ.get("HARNESS2_TEST_JB_JUDGE") or config.jb_repo() / "eval/judge.py"
        ).resolve()
        if not cls.judge_source.is_file():
            raise unittest.SkipTest(f"Actual JobBench judge checkout missing: {cls.judge_source}")
        cls.python = Path(os.environ.get("HARNESS2_TEST_JB_PYTHON") or sys.executable).resolve()
        if not cls.python.is_file():
            raise unittest.SkipTest(f"JobBench test interpreter missing: {cls.python}")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native judge lock ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)

    def spawn(self, mode, label="worker"):
        child = subprocess.Popen(
            [
                str(self.python),
                "-I",
                "-B",
                "-c",
                _WORKER,
                str(self.judge_source),
                str(self.root),
                mode,
                label,
            ],
            cwd=self.judge_source.parent.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.children.append(child)
        return child

    def marker(self, name, child, timeout=10):
        deadline = time.monotonic() + timeout
        path = self.root / name
        while time.monotonic() < deadline:
            if path.is_file():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    # The marker is tiny but its creation and write are separate.
                    pass
                else:
                    self.assertEqual(Path(value["source"]), self.judge_source)
                    self.assertEqual(Path(value["python"]).resolve(), self.python)
                    return value
            if child.poll() is not None:
                stdout, stderr = child.communicate(timeout=5)
                self.fail(f"Child exited {child.returncode} before {name}: {stdout} {stderr}")
            time.sleep(0.01)
        self.fail(f"Timed out waiting for {name}")

    def signal(self, name):
        (self.root / name).touch()

    def finish(self, child):
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 0, (stdout, stderr))

    def assert_waiting(self, child, seconds=0.25):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.assertIsNone(child.poll(), "Contending process exited before lock release")
            self.assertFalse((self.root / "waiter-acquired").exists())
            time.sleep(0.02)

    def start_contention(self, owner_mode="hold"):
        owner = self.spawn(owner_mode, "owner")
        self.marker("owner-held", owner)
        waiter = self.spawn("waiter", "waiter")
        self.marker("waiter-ready", waiter)
        return owner, waiter

    def test_four_sibling_writers_serialize_critical_sections_and_increments(self):
        (self.root / "counter").write_text("0", encoding="utf-8")
        children = [self.spawn("increment", str(index)) for index in range(4)]
        for index, child in enumerate(children):
            self.marker(f"ready-{index}", child)
        self.signal("start")
        for child in children:
            self.finish(child)
        self.assertEqual((self.root / "counter").read_text(encoding="utf-8"), "80")
        self.assertTrue((self.root / "judge.lock").is_file())
        self.assertFalse((self.root / "exclusive-owner").exists())

    def test_exception_releases_to_sibling_while_owner_process_stays_alive(self):
        owner, waiter = self.start_contention("exception")
        self.assert_waiting(waiter)
        self.signal("owner-release")
        self.marker("owner-exception", owner)
        self.marker("waiter-acquired", waiter)
        self.finish(waiter)
        self.assertIsNone(owner.poll(), "Owner must still be alive after context cleanup")
        self.signal("owner-exit")
        self.finish(owner)

    def test_owner_death_releases_to_waiting_sibling(self):
        owner, waiter = self.start_contention()
        self.assert_waiting(waiter)
        owner.kill()
        owner.communicate(timeout=10)
        self.assertNotEqual(owner.returncode, 0)
        self.marker("waiter-acquired", waiter)
        self.finish(waiter)
        self.assertTrue((self.root / "judge.lock").is_file())

    def test_blocking_wait_survives_more_than_ten_seconds_of_contention(self):
        owner, waiter = self.start_contention()
        # Windows LK_LOCK abandons contention after ten one-second retries.
        # Keep the actual patched lock contended beyond that limit; POSIX runs
        # the same real-process case against its flock branch.
        self.assert_waiting(waiter, seconds=11.5)
        self.signal("owner-release")
        acquired = self.marker("waiter-acquired", waiter)
        self.assertGreaterEqual(acquired["waited"], 11.0)
        self.finish(waiter)
        self.assertIsNone(owner.poll())
        self.signal("owner-exit")
        self.finish(owner)


if __name__ == "__main__":
    unittest.main()
