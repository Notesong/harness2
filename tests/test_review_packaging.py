# Copyright 2026 Google LLC
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

"""Installed-layout and dependency provenance regressions; no downloads or models."""

import os
import runpy
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2 import config, preflight


class PackagingReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "invocation with spaces"
        self.workspace.mkdir()

    def load_config(self, package):
        package.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config.__file__, package / "config.py")
        with patch.dict(os.environ, {}, clear=True), patch("os.getcwd", return_value=str(self.workspace)):
            return runpy.run_path(str(package / "config.py"), run_name="harness2._config_layout")

    def test_installed_assets_cannot_be_shadowed_by_working_directory(self):
        package = self.root / "site-packages/harness2"
        bundled = package / "third_party"
        bundled.mkdir(parents=True)
        (self.workspace / "third_party").mkdir()
        (self.workspace / ".env").write_text("HARNESS2_SOLVER_MODEL=local/workspace\n", encoding="utf-8")
        loaded = self.load_config(package)
        self.assertEqual(loaded["THIRD_PARTY"], bundled)
        self.assertEqual(loaded["ROOT"], self.workspace)
        self.assertEqual(loaded["DEPENDENCIES"], self.workspace / "dependencies")
        self.assertEqual(loaded["SOLVER_MODEL_OC"], "local/workspace")

    def test_source_assets_stay_with_checkout_when_launched_elsewhere(self):
        source = self.root / "source checkout"
        source.mkdir()
        (source / "pyproject.toml").touch()
        (source / "third_party").mkdir()
        (source / ".env").write_text("HARNESS2_SOLVER_MODEL=local/source\n", encoding="utf-8")
        (self.workspace / "third_party").mkdir()
        loaded = self.load_config(source / "harness2")
        self.assertEqual(loaded["ROOT"], source)
        self.assertEqual(loaded["THIRD_PARTY"], source / "third_party")
        self.assertEqual(loaded["SOLVER_MODEL_OC"], "local/source")

    def test_preflight_requires_verifiable_dependency_checkouts(self):
        dependencies = self.root / "dependencies"
        for name in ("job-bench-eval", "opencode"):
            (dependencies / name).mkdir(parents=True)
        (dependencies / "opencode/node_modules").mkdir()
        adapter = Mock()
        adapter.spec.name = "jb"
        with (
            patch.object(config, "DEPENDENCIES", dependencies),
            patch.object(config, "OPENCODE_DIR", dependencies / "opencode"),
            patch.object(config, "validate_models"),
            patch.object(config, "SOLVER_MODEL_OC", "local/test"),
            patch.object(config, "IMPROVER_MODEL", "local/test"),
            patch.object(preflight, "missing_runners", return_value=""),
            patch.object(preflight.OpencodeSubstrate, "preflight"),
            patch.object(preflight.subprocess, "run") as process,
        ):
            errors = preflight.check(adapter)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("no Git checkout to verify" in error for error in errors))
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
