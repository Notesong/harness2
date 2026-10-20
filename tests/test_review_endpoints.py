"""Explicit CLI endpoints must stay public before writes or network activity."""

import json
import os
import tempfile
import tomllib
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2 import config, install
from harness2.cli import judge, run_sweep
from harness2.substrate import codex

PRIVATE = "http://user:credential-fixture@127.0.0.1:8087/v1"


class EndpointReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="endpoint review ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def args(self, substrate):
        return types.SimpleNamespace(substrate=substrate, split="all", max_tasks=1,
                                     only_domains="", seed=None)

    def assert_private_identity_rejected(self, substrate, judge_endpoint, codex_endpoint):
        namespace = self.root / "experiment"
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(config, "JB_JUDGE_API_BASE", judge_endpoint), \
                patch.object(config, "CODEX_BASE_URL", codex_endpoint), \
                patch.object(run_sweep, "_stream_for", return_value=({}, [])) as stream:
            with self.assertRaises(ValueError) as error:
                run_sweep._check_identity(Mock(), self.args(substrate), namespace)
        self.assertNotIn("credential-fixture", str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)
        self.assertFalse(namespace.exists())
        stream.assert_not_called()

    def test_explicit_judge_credentials_never_create_public_identity(self):
        self.assert_private_identity_rejected("oc", PRIVATE, "")

    def test_codex_credentials_never_create_public_identity(self):
        self.assert_private_identity_rejected("cx", "https://judge.example/v1/", PRIVATE)

    def test_valid_nonlocal_identity_preserves_endpoint_spelling_and_trailing_slash(self):
        namespace = self.root / "experiment"
        judge_endpoint = "https://Judge.Example:443/v1/"
        codex_endpoint = "https://Responses.Example:443/v1/"
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(config, "JB_JUDGE_API_BASE", judge_endpoint), \
                patch.object(config, "CODEX_BASE_URL", codex_endpoint), \
                patch.object(run_sweep, "_stream_for", return_value=({}, [])):
            run_sweep._check_identity(Mock(), self.args("cx"), namespace)
            run_sweep._check_identity(Mock(), self.args("cx"), namespace)
        identity = json.loads((namespace / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(identity["judge_endpoint"], judge_endpoint)
        self.assertEqual(identity["endpoint"], codex_endpoint)

    def test_opencode_identity_does_not_validate_unused_stale_codex_endpoint(self):
        namespace = self.root / "experiment"
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(config, "JB_JUDGE_API_BASE", "https://judge.example/v1/"), \
                patch.object(config, "CODEX_BASE_URL", PRIVATE), \
                patch.object(run_sweep, "_stream_for", return_value=({}, [])):
            run_sweep._check_identity(Mock(), self.args("oc"), namespace)
        identity = json.loads((namespace / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(identity["endpoint"], "")
        self.assertNotIn("credential-fixture", json.dumps(identity))

    def test_codex_preflight_rejects_private_or_malformed_url_before_binary_or_socket(self):
        for endpoint in (PRIVATE, "http://@127.0.0.1/v1",
                         "http://127.0.0.1:credential-fixture/v1"):
            with self.subTest(endpoint=endpoint), \
                    patch.object(config, "require"), \
                    patch.object(config, "CODEX_BASE_URL", endpoint), \
                    patch.object(config, "codex_binary") as binary, \
                    patch.object(codex.socket, "create_connection") as connect:
                with self.assertRaises(ValueError) as error:
                    codex.CodexSubstrate().preflight()
                self.assertNotIn("credential-fixture", str(error.exception))
                self.assertTrue(error.exception.__suppress_context__)
                binary.assert_not_called()
                connect.assert_not_called()

    def test_codex_container_endpoint_is_validated_before_binary_or_socket(self):
        with patch.object(config, "require"), \
                patch.object(config, "CODEX_BASE_URL", "https://responses.example/v1/"), \
                patch.object(config, "CODEX_CONTAINER_URL", PRIVATE), \
                patch.object(config, "codex_binary") as binary, \
                patch.object(codex.socket, "create_connection") as connect:
            with self.assertRaises(ValueError):
                codex.CodexSubstrate().preflight(require_bridge=True)
            binary.assert_not_called()
            connect.assert_not_called()

    def test_valid_codex_preflight_keeps_nonlocal_endpoint_and_auth_separate(self):
        endpoint = "https://Responses.Example:443/v1/"
        with patch.object(config, "require") as require, \
                patch.object(config, "CODEX_BASE_URL", endpoint), \
                patch.object(config, "codex_binary", return_value=Mock(is_file=lambda: True)), \
                patch.object(codex.socket, "create_connection") as connect:
            codex.CodexSubstrate().preflight()
            connect.assert_called_once_with(("responses.example", 443), timeout=5)
            self.assertEqual(config.CODEX_BASE_URL, endpoint)
            self.assertEqual([call.args[0] for call in require.call_args_list],
                             ["HARNESS2_CODEX_BASE_URL", "HARNESS2_CODEX_API_KEY"])

    def test_codex_setup_rejects_credentials_before_creating_config_home(self):
        home = self.root / "codex home"
        with patch.object(config, "require"), \
                patch.object(config, "CODEX_BASE_URL", PRIVATE), \
                patch.object(config, "CODEX_HOME_DIR", home):
            with self.assertRaises(ValueError) as error:
                install.configure_codex()
        self.assertNotIn("credential-fixture", str(error.exception))
        self.assertFalse(home.exists())

    def test_valid_codex_setup_keeps_exact_endpoint_string_in_toml(self):
        home = self.root / "codex home"
        endpoint = "https://Responses.Example:443/v1/"
        with patch.object(config, "require"), \
                patch.object(config, "CODEX_BASE_URL", endpoint), \
                patch.object(config, "CODEX_HOME_DIR", home), \
                patch.object(config, "SOLVER_MODEL_CX", "model"):
            install.configure_codex()
            self.assertIsNone(install.codex_config_error())
        parsed = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(parsed["model_providers"]["harness2"]["base_url"], endpoint)
        self.assertEqual(parsed["model_providers"]["harness2"]["env_key"], "VERTEX_PROXY_KEY")

    def test_stale_codex_config_diagnostic_does_not_echo_old_or_current_credentials(self):
        home = self.root / "codex home"
        home.mkdir()
        (home / "config.toml").write_text(
            'model="model"\nmodel_provider="harness2"\n[model_providers.harness2]\n'
            f'base_url={json.dumps(PRIVATE)}\n', encoding="utf-8")
        with patch.object(config, "CODEX_HOME_DIR", home), \
                patch.object(config, "SOLVER_MODEL_CX", "model"):
            for endpoint in (PRIVATE, "https://responses.example/v1/"):
                with self.subTest(endpoint=endpoint), \
                        patch.object(config, "CODEX_BASE_URL", endpoint):
                    error = install.codex_config_error()
                    self.assertIsNotNone(error)
                    self.assertNotIn("credential-fixture", error)
                    self.assertIn(install.CODEX_CONFIG_REMEDY, error)

    def test_judge_cli_rejects_explicit_private_endpoint_before_adapter_or_requests(self):
        with patch.object(config, "require"), \
                patch.object(config, "SOLVER_MODEL_OC", "provider/solver"), \
                patch.object(config, "IMPROVER_MODEL", "provider/improver"), \
                patch.object(config, "JB_JUDGE_API_BASE", PRIVATE), \
                patch.object(judge.C, "make_adapter") as adapter, \
                patch.object(judge.J, "judge_selections") as requests:
            with self.assertRaises(ValueError) as error:
                judge.main(["--bench", "jb", "--k", "1"])
        self.assertNotIn("credential-fixture", str(error.exception))
        adapter.assert_not_called()
        requests.assert_not_called()


if __name__ == "__main__":
    unittest.main()
