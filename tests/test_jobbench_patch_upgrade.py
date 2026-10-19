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

"""Real Git upgrade tests using the pinned upstream judge and shipped patches.

Only scratch repositories are modified. HARNESS2_TEST_JB_SOURCE_REPO may select
an existing Git checkout to read the pinned blob from; no clone/network is used.
"""

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config, install

_PATCH_KEYS = ("job-bench-eval", "job-bench-eval-native-lock")


class JobBenchPatchUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("git"):
            raise unittest.SkipTest("Git is required for actual JobBench patch integration")
        cls.source_repo = Path(
            os.environ.get("HARNESS2_TEST_JB_SOURCE_REPO") or config.jb_repo()
        ).resolve()
        if not (cls.source_repo / ".git").exists():
            raise unittest.SkipTest(f"Actual JobBench Git checkout missing: {cls.source_repo}")
        # Read the committed blob, never the live dependency's possibly patched
        # working tree. No operation here writes to that source repository.
        cls.pinned_judge = subprocess.run(
            config.command(
                [
                    "git",
                    "-C",
                    cls.source_repo,
                    "show",
                    f"{config.DEPENDENCY_PINS['job-bench-eval']}:eval/judge.py",
                ]
            ),
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="jobbench patch upgrade ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.dependencies = self.root / "dependencies"
        self.repo = self.dependencies / "job-bench-eval"
        self.judge = self.repo / "eval/judge.py"
        self.judge.parent.mkdir(parents=True)
        self.judge.write_bytes(self.pinned_judge)
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        if os.name == "nt":
            self.git("config", "core.longpaths", "true")
        self.git("add", "eval/judge.py")
        self.git("update-index", "--chmod=+x", "eval/judge.py")

    def git(self, *args):
        return subprocess.run(
            config.command(["git", "-C", self.repo, *args]),
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout

    def apply_asset(self, key):
        with install.patch_source(key) as source:
            self.git("apply", "--ignore-space-change", source)

    def assert_both_applied(self):
        self.assertEqual(install.patch_keys("job-bench-eval"), _PATCH_KEYS)
        for key in _PATCH_KEYS:
            with self.subTest(patch=key), install.patch_source(key) as source:
                self.git("apply", "--ignore-space-change", "--reverse", "--check", source)

    def install_patches(self):
        # Only redirect installer narration; all Git checks and writes remain
        # actual subprocesses against this test's scratch repository.
        with (
            patch.object(config, "DEPENDENCIES", self.dependencies),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            install.patch("job-bench-eval")

    def test_fresh_pinned_judge_gets_api_and_native_patches(self):
        self.assertEqual(self.judge.read_bytes(), self.pinned_judge)
        self.install_patches()
        self.assert_both_applied()
        self.assertNotEqual(self.judge.read_bytes(), self.pinned_judge)
        self.assertEqual(self.git("diff", "--name-only").decode().strip(), "eval/judge.py")

    def test_existing_api_only_checkout_upgrades_to_native_without_reapplying_api(self):
        self.apply_asset("job-bench-eval")
        api_only = self.judge.read_bytes()
        with install.patch_source("job-bench-eval-native-lock") as source:
            self.git("apply", "--ignore-space-change", "--check", source)
        self.install_patches()
        self.assert_both_applied()
        self.assertNotEqual(self.judge.read_bytes(), api_only)

    def test_fully_patched_checkout_is_unchanged_by_repeat_setup(self):
        for key in _PATCH_KEYS:
            self.apply_asset(key)
        before = self.judge.read_bytes()
        timestamp = self.judge.stat().st_mtime_ns
        git_diff = self.git("diff", "--binary")
        self.install_patches()
        self.assert_both_applied()
        self.assertEqual(self.judge.read_bytes(), before)
        self.assertEqual(self.judge.stat().st_mtime_ns, timestamp)
        self.assertEqual(self.git("diff", "--binary"), git_diff)

    def test_conflicting_custom_lock_edit_is_refused_without_overwriting_it(self):
        self.apply_asset("job-bench-eval")
        before = self.judge.read_bytes()
        old = b"fcntl.flock(handle.fileno(), fcntl.LOCK_EX)"
        self.assertEqual(before.count(old), 1)
        custom = before.replace(old, b"fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)")
        self.judge.write_bytes(custom)
        with self.assertRaises(subprocess.CalledProcessError):
            self.install_patches()
        self.assertEqual(self.judge.read_bytes(), custom)


if __name__ == "__main__":
    unittest.main()
