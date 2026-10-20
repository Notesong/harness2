"""Endpoint identity and log redaction regressions without a server or model."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2.local_models import profile_identity
from harness2.ui import jobs
from harness2.ui.workspace import Workspace

LOCAL = {"HARNESS2_LOCAL_BASE_URL": "http://127.0.0.1:8087/v1",
         "HARNESS2_LOCAL_MODEL": "qwen-local", "HARNESS2_JUDGE_API_KEY": "local"}


class SettingsReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="settings review ")
        self.addCleanup(temporary.cleanup)
        self.workspace = Workspace(Path(temporary.name))

    def test_private_or_malformed_endpoints_cannot_enter_identity_or_ui_settings(self):
        marker = "credential-fixture"
        for endpoint in (
            f"http://user:{marker}@127.0.0.1/v1",
            f"http://{marker}@127.0.0.1/v1",
            f"http://:{marker}@127.0.0.1/v1",
            "http://@127.0.0.1/v1", "http://:@127.0.0.1/v1",
            f"http://127.0.0.1:{marker}/v1", "http://127.0.0.1:65536/v1",
            f"http://[::{marker}/v1", f"http://127.0.0.1/v1?token={marker}",
            f"http://127.0.0.1/v1#{marker}", "http://127.0.0.1/v1?",
            "http://127.0.0.1/v1#", "http://bad host/v1",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError) as identity_error:
                    profile_identity({**LOCAL, "HARNESS2_LOCAL_BASE_URL": endpoint})
                with patch.object(self.workspace, "environment", return_value={}):
                    with self.assertRaises(ValueError) as ui_error:
                        self.workspace.save({"provider": "local", "local_url": endpoint})
                for error in (identity_error, ui_error):
                    self.assertNotIn(marker, str(error.exception))
                    self.assertNotIn(endpoint, str(error.exception))
                    self.assertTrue(error.exception.__suppress_context__)
                self.assertFalse((self.workspace.root / ".env").exists())

    def test_public_ipv6_endpoint_normalizes_consistently_in_identity_and_ui(self):
        endpoint = "https://[::1]:8087/v1/"
        identity = profile_identity({**LOCAL, "HARNESS2_LOCAL_BASE_URL": endpoint})
        with patch.object(self.workspace, "environment", return_value={}):
            self.workspace.save({"provider": "local", "local_url": endpoint})
        self.assertEqual(identity["base_url"], endpoint.rstrip("/"))
        self.assertIn(f'HARNESS2_LOCAL_BASE_URL="{identity["base_url"]}"',
                      (self.workspace.root / ".env").read_text(encoding="utf-8"))

    def run_log(self, environment, output):
        manager = jobs.JobManager(self.workspace)
        identifier = f"{len(manager.jobs):012x}"
        manager.jobs[identifier] = {
            "id": identifier, "output_dir": None, "error": "", "total_tasks": 0,
            "phases": [{"label": "Fixture process", "command": ["unused"], "status": "pending"}],
        }
        child = Mock(stdout=io.StringIO(output))
        child.wait.return_value = 0
        with patch.object(self.workspace, "environment", return_value=environment), \
                patch.object(jobs.processes, "start", return_value=child), \
                patch.object(jobs.processes, "terminate_tree"):
            manager._execute(identifier)
        self.assertEqual(manager.jobs[identifier]["status"], "succeeded")
        return (manager.directory / f"{identifier}.log").read_text(encoding="utf-8")

    def test_no_auth_local_judge_placeholder_does_not_mangle_log_text(self):
        text = "local/qwen-local at http://localhost:8087/v1 is local\n"
        self.assertIn(text, self.run_log(LOCAL, text))
        self.assertNotIn("[redacted]", self.run_log(LOCAL, text))

    def test_real_keys_are_redacted_even_when_short_or_equal_to_local(self):
        for field, value in (("HARNESS2_LOCAL_API_KEY", "local"),
                             ("HARNESS2_CODEX_API_KEY", "local"),
                             ("OTHER_TOKEN", "local"),
                             ("HARNESS2_LOCAL_API_KEY", "k"),
                             ("HARNESS2_JUDGE_API_KEY", "sk-1")):
            with self.subTest(field=field, value=value):
                log = self.run_log({**LOCAL, field: value}, f"credential={value}\n")
                self.assertNotIn(f"credential={value}", log)
                self.assertIn("credential=[redacted]", log)

    def test_judge_local_value_is_redacted_without_matching_local_profile(self):
        for environment in (
            {"HARNESS2_JUDGE_API_KEY": "local"},
            {**LOCAL, "HARNESS2_JUDGE_BASE_URL": "https://other.example/v1"},
            {**LOCAL, "HARNESS2_JUDGE_MODEL": "other-model"},
            {**LOCAL, "HARNESS2_LOCAL_BASE_URL": "http://invalid:port/v1"},
        ):
            with self.subTest(environment=environment):
                log = self.run_log(environment, "credential=local\n")
                self.assertNotIn("credential=local", log)
                self.assertIn("credential=[redacted]", log)


if __name__ == "__main__":
    unittest.main()
