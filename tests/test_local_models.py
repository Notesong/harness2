"""Local provider configuration without a server, network, or model calls."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2.benchmarks.lab import _lab_env
from harness2.cli import judge, run_sweep
from harness2.local_models import opencode_env, profile, profile_defaults, profile_identity
from harness2.substrate.opencode import OpencodeSubstrate

LOCAL = {"HARNESS2_LOCAL_BASE_URL": "http://127.0.0.1:8087/v1/",
         "HARNESS2_LOCAL_MODEL": "qwen-local"}


class LocalModelTests(unittest.TestCase):
    def test_unconfigured_environment_is_unchanged_and_copied(self):
        original = {"OPENCODE_CONFIG_CONTENT": "not parsed unless opted in", "PATH": "tools"}
        self.assertEqual(opencode_env(original), original)
        self.assertIsNot(opencode_env(original), original)

    def test_explicit_nonlocal_call_keeps_its_provider_configuration(self):
        original = {**LOCAL, "HARNESS2_SOLVER_MODEL": "cloud/model",
                    "OPENCODE_CONFIG_CONTENT": '{"small_model":"cloud/small"}'}
        self.assertEqual(opencode_env(original), original)
        local_call = opencode_env(original, model="local/qwen-local")
        self.assertEqual(json.loads(local_call["OPENCODE_CONFIG_CONTENT"])["small_model"],
                         "local/qwen-local")

    def test_unused_saved_local_settings_do_not_change_a_cloud_run_identity(self):
        environment = {**LOCAL, "HARNESS2_SOLVER_MODEL": "cloud/model",
                       "HARNESS2_IMPROVER_MODEL": "cloud/model",
                       "HARNESS2_JUDGE_MODEL": "cloud-judge",
                       "HARNESS2_JUDGE_BASE_URL": "https://judge.example/v1"}
        self.assertIsNone(profile_identity(environment))
        self.assertNotIn("api_key", profile_identity(LOCAL))

    def test_profile_defaults_do_not_override_explicit_models(self):
        supplied = {**LOCAL, "HARNESS2_IMPROVER_MODEL": "other/explicit"}
        actual = profile_defaults(supplied)
        self.assertEqual(actual["HARNESS2_SOLVER_MODEL"], "local/qwen-local")
        self.assertEqual(actual["HARNESS2_IMPROVER_MODEL"], "other/explicit")
        self.assertEqual(actual["HARNESS2_JUDGE_MODEL"], "qwen-local")
        self.assertEqual(actual["HARNESS2_JUDGE_BASE_URL"], "http://127.0.0.1:8087/v1")
        self.assertNotIn("HARNESS2_SOLVER_MODEL", supplied)

    def test_authenticated_profile_uses_the_same_key_for_executor_and_judge(self):
        environment = {**LOCAL, "HARNESS2_LOCAL_API_KEY": "fixture-server-key"}
        self.assertEqual(profile_defaults(environment)["HARNESS2_JUDGE_API_KEY"], "fixture-server-key")
        content = json.loads(opencode_env(environment)["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(content["provider"]["local"]["options"]["apiKey"], "fixture-server-key")

    def test_provider_merge_preserves_harness_and_other_providers(self):
        existing = {"plugin": ["./plugin/safety.ts"], "agent": {"build": {"steps": 7}},
                    "provider": {"other": {"name": "Keep"},
                                 "local": {"options": {"timeout": 90000},
                                           "models": {"another": {"id": "keep"}}}},
                    "enabled_providers": ["other"]}
        original = {**LOCAL, "OPENCODE_CONFIG_CONTENT": json.dumps(existing)}
        actual = opencode_env(original)
        content = json.loads(actual["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(content["plugin"], existing["plugin"])
        self.assertEqual(content["agent"], existing["agent"])
        self.assertEqual(content["provider"]["other"], existing["provider"]["other"])
        self.assertEqual(content["provider"]["local"]["models"]["another"], {"id": "keep"})
        self.assertEqual(content["provider"]["local"]["options"]["timeout"], 90000)
        model = content["provider"]["local"]["models"]["qwen-local"]
        self.assertEqual(model["limit"], {"context": 32768, "output": 4096})
        self.assertTrue(model["tool_call"])
        self.assertEqual(content["enabled_providers"], ["other", "local"])
        self.assertEqual(content["small_model"], "local/qwen-local")
        self.assertEqual(actual["OPENCODE_DISABLE_MODELS_FETCH"], "1")
        self.assertNotIn("OPENCODE_PURE", actual)
        self.assertEqual(json.loads(original["OPENCODE_CONFIG_CONTENT"]), existing)
        self.assertEqual(opencode_env(actual), actual)

    def test_invalid_profile_or_configuration_fails_without_discarding_it(self):
        for settings in (
            {"HARNESS2_LOCAL_MODEL": "missing-url"},
            {**LOCAL, "HARNESS2_LOCAL_BASE_URL": "file:///model"},
            {**LOCAL, "HARNESS2_LOCAL_OUTPUT": "bad"},
            {**LOCAL, "HARNESS2_LOCAL_CONTEXT": "1024"},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                profile(settings)
        for existing in ("invalid", "[]", '{"provider":[]}', '{"disabled_providers":["local"]}'):
            with self.subTest(existing=existing), self.assertRaises(ValueError):
                opencode_env({**LOCAL, "OPENCODE_CONFIG_CONTENT": existing})

    def test_substrate_and_lab_apply_profile_after_environment_construction(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = OpencodeSubstrate().run_env(LOCAL, Path(directory))
        self.assertEqual(environment["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"], "4096")
        self.assertIn("external_directory", json.loads(environment["OPENCODE_PERMISSION"]))
        with patch.dict(os.environ, LOCAL, clear=True):
            environment = _lab_env()
        self.assertEqual(environment["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"], "4096")
        self.assertIn("PYTHONPATH", environment)
        self.assertEqual(json.loads(environment["OPENCODE_CONFIG_CONTENT"])["model"],
                         "local/qwen-local")

    def test_resume_rejects_changed_local_context_or_endpoint(self):
        args = Mock(substrate="oc", split="all", max_tasks=1, only_domains="", seed=None)
        adapter = Mock()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(run_sweep, "_stream_for", return_value=({}, [])), \
                patch.dict(os.environ, LOCAL, clear=True):
            run_sweep._check_identity(adapter, args, Path(directory))
            for changed in ({"HARNESS2_LOCAL_OUTPUT": "2048"},
                            {"HARNESS2_LOCAL_BASE_URL": "http://127.0.0.1:9000/v1"}):
                with patch.dict(os.environ, changed), self.assertRaises(ValueError):
                    run_sweep._check_identity(adapter, args, Path(directory))

    def test_judging_rejects_changed_local_endpoint_before_using_cached_scores(self):
        from harness2 import config

        with tempfile.TemporaryDirectory() as directory:
            namespace = Path(directory) / "jb-oc-agg1"
            namespace.mkdir()
            (namespace / "config.json").write_text(json.dumps({
                "judge": config.JUDGE_MODEL, "local_profile": profile_identity(LOCAL),
            }), encoding="utf-8")
            with patch.object(judge.C, "make_adapter", return_value=Mock()), \
                    patch.object(judge.C, "summary_namespace", return_value="jb-oc-agg1"), \
                    patch.object(judge.C, "runs_root_for", return_value=Path(directory)), \
                    patch.object(config, "validate_models"), \
                    patch.dict(os.environ, {**LOCAL, "HARNESS2_LOCAL_BASE_URL": "http://127.0.0.1:9000/v1"}, clear=True), \
                    patch.object(judge, "judge_bases") as bases, \
                    patch.object(judge, "collect_selections") as selections:
                for extra in ([], ["--bases"]):
                    with self.subTest(extra=extra), self.assertRaisesRegex(SystemExit, "Local model profile changed"):
                        judge.main(["--bench", "jb", "--k", "1", *extra])
            bases.assert_not_called()
            selections.assert_not_called()

    def test_judging_rejects_a_changed_explicit_judge_endpoint(self):
        from harness2 import config

        with tempfile.TemporaryDirectory() as directory:
            namespace = Path(directory) / "jb-oc-agg1"
            namespace.mkdir()
            (namespace / "config.json").write_text(json.dumps({
                "judge": config.JUDGE_MODEL, "judge_endpoint": "http://127.0.0.1:8000/v1",
                "local_profile": profile_identity(LOCAL),
            }), encoding="utf-8")
            with patch.object(judge.C, "make_adapter", return_value=Mock()), \
                    patch.object(judge.C, "summary_namespace", return_value="jb-oc-agg1"), \
                    patch.object(judge.C, "runs_root_for", return_value=Path(directory)), \
                    patch.object(config, "validate_models"), \
                    patch.object(config, "JB_JUDGE_API_BASE", "http://127.0.0.1:9000/v1"), \
                    patch.dict(os.environ, LOCAL, clear=True):
                with self.assertRaisesRegex(SystemExit, "Judge endpoint changed"):
                    judge.main(["--bench", "jb", "--k", "1"])


if __name__ == "__main__":
    unittest.main()
