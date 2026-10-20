"""Managed local model settings validate without launching a process."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.ui.workspace import Workspace, dotenv


class LocalServerSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="managed settings résumé ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.workspace = Workspace(self.root)
        self.executable = self.root / ("llama-server.exe" if os.name == "nt" else "llama-server")
        self.executable.write_bytes(b"test executable; never launched")
        self.executable.chmod(0o755)
        self.weights = self.root / "Qwen résumé model.gguf"
        self.weights.write_bytes(b"GGUF test file; never loaded")
        self.managed = {
            "provider": "local",
            "local_runtime": "managed",
            "local_executable": str(self.executable),
            "local_weights": str(self.weights),
            "local_url": "http://127.0.0.1:8087/v1",
            "local_model": "qwen-test",
            "local_context": "65536",
            "local_output": "8192",
        }

    def test_existing_connections_default_to_external_and_do_not_require_paths(self):
        self.assertEqual(self.workspace.settings()["local_runtime"], "external")
        saved = self.workspace.save({
            "provider": "local", "local_url": "http://localhost:9000/v1",
            "local_model": "already-running",
        })
        self.assertEqual(saved["local_runtime"], "external")
        self.assertEqual(saved["local_executable"], "")
        self.assertEqual(saved["local_weights"], "")

    def test_managed_settings_round_trip_preserves_paths_limits_and_other_values(self):
        path = self.root / ".env"
        path.write_text('UNRELATED="keep me"\n', encoding="utf-8")
        before_environment = dict(os.environ)
        saved = self.workspace.save(self.managed)
        for field, expected in self.managed.items():
            self.assertEqual(saved[field], expected)
        self.assertEqual(saved["solver"], "local/qwen-test")
        self.assertEqual(saved["improver"], saved["solver"])
        self.assertEqual(saved["judge"], "qwen-test")
        values = dotenv(path)
        self.assertEqual(values["HARNESS2_LOCAL_RUNTIME"], "managed")
        self.assertEqual(values["HARNESS2_LOCAL_EXECUTABLE"], str(self.executable))
        self.assertEqual(values["HARNESS2_LOCAL_WEIGHTS"], str(self.weights))
        self.assertEqual(values["UNRELATED"], "keep me")
        self.assertEqual(dict(os.environ), before_environment)

    def test_invalid_managed_inputs_leave_saved_settings_and_environment_untouched(self):
        self.workspace.save(self.managed)
        path = self.root / ".env"
        before = path.read_bytes()
        before_environment = dict(os.environ)
        invalid = (
            {"local_runtime": "sometimes"},
            {"local_runtime": True},
            {"local_executable": "relative/llama-server.exe"},
            {"local_executable": str(self.root / "missing-server.exe")},
            {"local_executable": str(self.root)},
            {"local_weights": "relative/model.gguf"},
            {"local_weights": str(self.root / "missing.gguf")},
            {"local_weights": str(self.executable)},
            {"local_weights": str(self.root)},
            {"local_weights": "bad\nmodel.gguf"},
            {"local_executable": None},
            {"local_url": "https://127.0.0.1:8087/v1"},
            {"local_url": "http://localhost:8087/v1"},
            {"local_url": "http://127.0.0.1/v1"},
            {"local_url": "http://127.0.0.1:8087/other"},
            {"local_url": "http://127.0.0.1:0/v1"},
            {"local_url": "http://127.0.0.1:8087/v1?secret=value"},
            {"local_url": "http://user:secret@127.0.0.1:8087/v1"},
            {"local_context": "8192", "local_output": "8192"},
        )
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.workspace.save(change)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(dict(os.environ), before_environment)
            self.assertEqual(list(self.root.glob(".settings-*")), [])

    def test_invalid_initial_managed_save_does_not_create_an_environment_file(self):
        with self.assertRaises(ValueError):
            self.workspace.save({**self.managed, "local_weights": "missing.gguf"})
        self.assertFalse((self.root / ".env").exists())
        self.assertEqual(list(self.root.glob(".settings-*")), [])

    def test_switching_provider_disables_management_and_preserves_saved_paths(self):
        self.workspace.save(self.managed)
        saved = self.workspace.save({
            "provider": "vertex", "project": "my-project", "solver": "gemini-solver",
            "improver": "gemini-improver", "judge": "gemini-judge",
        })
        self.assertEqual(saved["local_runtime"], "external")
        self.assertEqual(saved["local_executable"], str(self.executable))
        self.assertEqual(saved["local_weights"], str(self.weights))
        self.assertEqual(self.workspace.environment()["HARNESS2_LOCAL_RUNTIME"], "external")

    def test_custom_connection_ignores_unused_missing_managed_paths(self):
        saved = self.workspace.save({
            "provider": "custom", "codex_url": "http://localhost:9000/v1",
            "codex_model": "responses-model", "local_runtime": "managed",
            "local_executable": "unused-missing", "local_weights": "unused-missing",
        })
        self.assertEqual(saved["local_runtime"], "external")


if __name__ == "__main__":
    unittest.main()
