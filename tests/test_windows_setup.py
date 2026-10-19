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

"""Portable installer regressions that do not download benchmark dependencies."""

import contextlib
import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config, install
from harness2.cli import setup


class WindowsSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_native_paths_and_command_shims(self):
        with patch.object(config, "IS_WINDOWS", True):
            self.assertEqual(config.venv_python(self.root), self.root / ".venv/Scripts/python.exe")
            self.assertEqual(config.executable_name("codex"), "codex.exe")
            self.assertEqual(config.executable_name("codex.exe"), "codex.exe")
        with patch.object(config, "IS_WINDOWS", False):
            self.assertEqual(config.venv_python(self.root), self.root / ".venv/bin/python")
        with patch("harness2.config.shutil.which", return_value="C:/Node/npm.cmd"):
            self.assertEqual(config.command(["npm", "--version"]), ["C:/Node/npm.cmd", "--version"])

    def test_bun_uses_native_executable(self):
        native = self.root / "tools/node_modules/bun/bin/bun.exe"
        native.parent.mkdir(parents=True)
        native.write_bytes(b"native")
        with (
            patch.object(config, "DEPENDENCIES", self.root),
            patch.object(config, "IS_WINDOWS", True),
        ):
            self.assertEqual(config.bun_binary(), native)

    def test_codex_stages_native_binary_without_symlink(self):
        upstream = self.root / "codex/node_modules/@openai/codex-win32-x64/vendor/windows/bin"
        upstream.mkdir(parents=True)
        (upstream / "codex.exe").write_bytes(b"native codex")
        dest = self.root / "native dist"
        with (
            patch.object(config, "DEPENDENCIES", self.root),
            patch.object(config, "IS_WINDOWS", True),
            patch.object(config, "CODEX_DIST", str(dest)),
            patch.object(install, "run"),
            patch.object(install, "configure_codex"),
            patch.object(install, "_codex_linux") as linux,
        ):
            install.codex(containers=True)
        self.assertEqual((dest / "codex.exe").read_bytes(), b"native codex")
        self.assertFalse((dest / "codex.exe").is_symlink())
        linux.assert_called_once_with(dry_run=False)

    def _archive(self, member="wb-bench-office-v1.0/tasks/task.txt"):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            item = tarfile.TarInfo(member)
            item.size = len(b"task")
            archive.addfile(item, io.BytesIO(b"task"))
        return output.getvalue()

    def _download(self, archive, checksum=None):
        digest = checksum or hashlib.sha256(archive).hexdigest()
        sums = f"{digest}  wb-bench-office-v1.0.tar.gz\n".encode()

        def response(url, **kwargs):
            return io.BytesIO(sums if url.endswith("SHA256SUMS") else archive)

        with (
            patch.object(config, "wb_repo", return_value=self.root),
            patch.object(install, "urlopen", side_effect=response),
        ):
            install.download_workbuddy(["office"])

    def test_workbuddy_download_checks_hash_and_extracts(self):
        self._download(self._archive())
        task = self.root / "datasets/wb-bench-office-v1.0/tasks/task.txt"
        self.assertEqual(task.read_bytes(), b"task")
        with (
            patch.object(config, "wb_repo", return_value=self.root),
            patch.object(
                install, "urlopen", side_effect=AssertionError("downloaded existing data")
            ),
        ):
            install.download_workbuddy(["office"])

    def test_workbuddy_checksum_failure_leaves_no_partial_dataset(self):
        with self.assertRaisesRegex(RuntimeError, "Checksum failed"):
            self._download(self._archive(), checksum="0" * 64)
        self.assertEqual(list((self.root / "datasets").iterdir()), [])

    def test_workbuddy_archive_rejects_parent_traversal(self):
        with self.assertRaises(tarfile.FilterError):
            self._download(self._archive("../../outside.txt"))
        self.assertEqual(list((self.root / "datasets").iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows extended paths")
    def test_workbuddy_download_extracts_paths_beyond_max_path(self):
        member = "wb-bench-office-v1.0/tasks/" + "/".join(["nested-folder" * 4] * 5) + "/task.txt"
        self.addCleanup(shutil.rmtree, config.long_path(self.root), ignore_errors=True)
        self._download(self._archive(member))
        self.assertEqual(config.long_path(self.root / "datasets" / member).read_bytes(), b"task")

    def test_patch_is_idempotent_with_crlf_source(self):
        repo = self.root / "opencode"
        repo.mkdir()
        subprocess.run(config.command(["git", "init", "-q", repo]), check=True)
        (repo / "sample.txt").write_bytes(b"before\n")
        assets = self.root / "assets/patches"
        assets.mkdir(parents=True)
        (assets / install.PATCHES["opencode"]).write_bytes(
            b"diff --git a/sample.txt b/sample.txt\r\n"
            b"--- a/sample.txt\r\n+++ b/sample.txt\r\n"
            b"@@ -1 +1 @@\r\n-before\r\n+after\r\n"
        )
        with (
            patch.object(config, "DEPENDENCIES", self.root),
            patch.object(config, "THIRD_PARTY", assets.parent),
        ):
            install.patch("opencode")
            install.patch("opencode")
        self.assertEqual((repo / "sample.txt").read_text(encoding="utf-8").strip(), "after")

    def test_windows_setup_plans_need_no_bash(self):
        for substrate in ("oc", "cx"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    setup.main(["wb", "--subset", "office", "--substrate", substrate, "--dry-run"]),
                    0,
                )
            self.assertNotIn("bash", output.getvalue())


if __name__ == "__main__":
    unittest.main()
