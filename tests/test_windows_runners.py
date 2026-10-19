"""Native runner regressions that exercise paths, output and process cleanup."""

import csv
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2 import config, processes
from harness2.benchmarks import jb_codex_runner, jb_opencode_runner, lab, wb_native_runner


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="runner tests ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_native_timeout_stops_child_tree(self):
        child = "import time; time.sleep(30)"
        parent = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
            "print('started', flush=True); time.sleep(30)"
        )
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            processes.run([sys.executable, "-c", parent], timeout=1,
                          capture_output=True, text=True, encoding="utf-8")
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("started", caught.exception.stdout)

    def test_remove_read_only_document_tree(self):
        docs = self.root / "documents"
        docs.mkdir()
        source = docs / "reference.txt"
        source.write_text("reference", encoding="utf-8")
        source.chmod(stat.S_IREAD)
        processes.remove_tree(docs)
        self.assertFalse(docs.exists())

    def test_codex_runner_stages_paths_and_collects_artifacts(self):
        tasks = self.root / "tasks with spaces"
        task = tasks / "finance" / "task1" / "task_folder"
        task.mkdir(parents=True)
        (task / "TASK_INSTRUCTIONS.txt").write_text("Produce résumé.txt", encoding="utf-8")
        rendered = self.root / "rendered"
        rendered.mkdir()
        (rendered / "AGENTS.md").write_text("Use the source", encoding="utf-8")
        fake = self.root / "fake codex.py"
        fake.write_text(
            "import pathlib,sys\n"
            "ws=pathlib.Path(sys.argv[sys.argv.index('-C')+1])\n"
            "assert (ws/'AGENTS.md').read_text(encoding='utf-8') == 'Use the source'\n"
            "assert (ws/'task_folder'/'TASK_INSTRUCTIONS.txt').is_file()\n"
            "(ws/'output'/'résumé.txt').write_text('café', encoding='utf-8')\n"
            "print('{\"type\":\"thread.started\",\"thread_id\":\"thread-1\"}')\n",
            encoding="utf-8")

        def invoke(args, **kwargs):
            return processes.run([sys.executable, str(fake), *args[1:]], **kwargs)

        with patch.object(jb_codex_runner, "run_process", side_effect=invoke):
            ok = jb_codex_runner.run_one("finance/task1", "model", tasks_base=tasks,
                                         render_dir=rendered, label="label", executable="codex",
                                         timeout=10)
        self.assertTrue(ok)
        output = tasks / "finance/task1/model_output/label/résumé.txt"
        self.assertEqual(output.read_text(encoding="utf-8"), "café")
        index = tasks / "finance/task1/model_traj/label/attempt_index.tsv"
        with index.open(encoding="utf-8", newline="") as stream:
            records = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(records[0]["session_id"], "thread-1")
        self.assertEqual(records[0]["status"], "success")

    def test_codex_runner_rejects_empty_success(self):
        tasks = self.root / "tasks"
        source = tasks / "finance/task1/task_folder"
        source.mkdir(parents=True)
        (source / "TASK_INSTRUCTIONS.txt").write_text("work", encoding="utf-8")
        with patch.object(jb_codex_runner, "run_process",
                          return_value=subprocess.CompletedProcess([], 0)):
            self.assertFalse(jb_codex_runner.run_one(
                "finance/task1", "model", tasks_base=tasks, render_dir=None,
                label="empty", executable="codex", timeout=1))

    def test_opencode_command_uses_native_timeout(self):
        cfg = jb_opencode_runner.Settings(
            task_ids=("finance/task1",), label="test", render_dir=self.root,
            tasks_base=self.root, opencode_dir=self.root, model_id="provider/model",
            agent_name="", variant="", agent_steps="", timeout_per_task="1",
            max_retries=1, log_dir=self.root)
        command = jb_opencode_runner._command(cfg, self.root, "a prompt", "a title")
        self.assertEqual(command[0], "bun")
        self.assertNotIn("--kill-after=30s", command)
        permissions = json.loads(jb_opencode_runner._permission(self.root))
        self.assertIn(f"{self.root.as_posix()}/**", permissions["external_directory"])

    def test_lab_env_uses_native_path_separator(self):
        env = lab._lab_env()
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(config.LAB_PYTHON.parent))
        self.assertIn(str(config.PKG.parent), env["PYTHONPATH"].split(os.pathsep))
        self.assertEqual(env["PYTHONUTF8"], "1")
        with patch.dict(os.environ, {"LAB_DOC_PARSER": "sandbox"}):
            self.assertEqual(lab._lab_env()["LAB_DOC_PARSER"], "sandbox")

    def test_lab_codex_config_escapes_windows_paths(self):
        fake_external = types.ModuleType("external")
        fake_tts = types.ModuleType("external.tts_lab")
        fake_tts.config = types.SimpleNamespace()
        source = config.THIRD_PARTY / "lab_runner/adapt_runs_cx/run_codex_lab.py"
        spec = importlib.util.spec_from_file_location("lab_codex_test", source)
        module = importlib.util.module_from_spec(spec)
        old_path = list(sys.path)
        try:
            with patch.dict(sys.modules, {"external": fake_external, "external.tts_lab": fake_tts}):
                spec.loader.exec_module(module)
        finally:
            sys.path[:] = old_path
        workspace = r"C:\Users\Test User\résumé"
        text = module.CONFIG_TOML_TEMPLATE.format(
            model=json.dumps('model"quoted'), base_url=json.dumps("http://localhost:8000/v1"),
            workspace=json.dumps(workspace))
        parsed = tomllib.loads(text)
        self.assertIn(workspace, parsed["projects"])
        self.assertEqual(parsed["model"], 'model"quoted')

    def test_workbuddy_metadata_is_parsed_without_shell_execution(self):
        output = "HARNESS_DISPLAY_NAME='An agent'\nHARNESS_USES_ANTHROPIC_ENV=\n"
        parsed = wb_native_runner._shell_vars(output)
        self.assertEqual(parsed["HARNESS_DISPLAY_NAME"], "An agent")
        self.assertEqual(parsed["HARNESS_USES_ANTHROPIC_ENV"], "")

    def test_workbuddy_native_flow_uses_upstream_modules_and_cleans_proxy(self):
        job = self.root / "configs/jobs/example.yaml"
        job.parent.mkdir(parents=True)
        job.write_text(json.dumps({"model": "model", "harness": "test",
                                   "model_connection": "local_proxy",
                                   "dataset": "datasets/wb-bench-office-v1.0/tasks"}),
                       encoding="utf-8")
        model = self.root / "configs/models/model.yaml"
        model.parent.mkdir(parents=True)
        model.write_text('{"model":{"protocol":"openai"}}', encoding="utf-8")
        fake_loader = types.ModuleType("workbuddy_bench.runner.config_loaders")
        fake_loader.load_harness_config = lambda *args: types.SimpleNamespace(
            harness={"name": "test"})
        modules = {
            "yaml": types.SimpleNamespace(safe_load=json.loads),
            "workbuddy_bench": types.ModuleType("workbuddy_bench"),
            "workbuddy_bench.runner": types.ModuleType("workbuddy_bench.runner"),
            "workbuddy_bench.runner.config_loaders": fake_loader,
        }
        calls = []

        def upstream(command, **kwargs):
            self.assertEqual(kwargs["env"]["PYTHONUTF8"], "1")
            module_name = command[2]
            calls.append(module_name)
            output = ""
            if module_name.endswith("harness_adapters"):
                output = "HARNESS_PROTOCOL='openai'\nHARNESS_PROXY_URL_ENV='TEST_PROXY_URL'\n"
            elif module_name.endswith("resolve_manifest"):
                instance_dir = Path(command[command.index("--instance-dir") + 1])
                instance = command[command.index("--instance-id") + 1]
                staged = "h2abcdefgh/wb-bench-office-v1.0/tasks"
                (self.root / staged).mkdir(parents=True)
                (instance_dir / "manifest.json").write_text(
                    json.dumps({"dataset": staged, "model_route": "model-route",
                                "instance_id": instance,
                                "decisions": {"dataset_staged_to": staged,
                                              "dataset_staged_from":
                                              "datasets/wb-bench-office-v1.0/tasks"}}),
                    encoding="utf-8")
            elif module_name.endswith("bench_config"):
                output = "AGENT_USER='1000'\nVERIFIER_USER='1000'\n"
            elif module_name.endswith("sharded_eval"):
                self.assertIn("--no-force-build", command)
                self.assertEqual(kwargs["env"]["TEST_PROXY_URL"],
                                 "http://host.docker.internal:4000")
            return subprocess.CompletedProcess(command, 0, output, "")

        old_cwd = Path.cwd()
        proxy = Mock()
        try:
            os.chdir(self.root)
            with patch.dict(sys.modules, modules), \
                    patch.dict(os.environ, {"NO_FORCE_BUILD": "1", "SHARED_PROXY": "0"}), \
                    patch.object(wb_native_runner, "run", side_effect=upstream), \
                    patch.object(wb_native_runner, "start", return_value=proxy), \
                    patch.object(wb_native_runner, "terminate_tree") as stop, \
                    patch.object(wb_native_runner, "_free_port", return_value=4000), \
                    patch.object(wb_native_runner, "_wait_routes") as ready:
                self.assertEqual(wb_native_runner.execute("example"), 0)
                ready.assert_called_once_with(4000, ["model-route"], proxy)
                stop.assert_called_once_with(proxy, grace=5)
        finally:
            os.chdir(old_cwd)
        self.assertEqual(calls[-3:], ["workbuddy_bench.runner.prepare_tasks",
                                      "workbuddy_bench.runner.sharded_eval",
                                      "workbuddy_bench.runner.split_proxy_log"])
        self.assertFalse((self.root / "h2abcdefgh").exists())

    def test_workbuddy_staging_cleanup_rejects_unowned_paths(self):
        original = "datasets/wb-bench-web-v1.0/tasks"
        staged = "h2abcdefgh/wb-bench-web-v1.0/tasks"
        valid = {"instance_id": "this-run", "dataset": staged,
                 "decisions": {"dataset_staged_to": staged,
                               "dataset_staged_from": original}}
        self.assertEqual(wb_native_runner._staged_root(
            self.root, valid, instance="this-run", original_dataset=original),
            self.root / "h2abcdefgh")
        for dataset in ("../h2abcdefgh/wb-bench-web-v1.0/tasks",
                        "datasets/wb-bench-web-v1.0/tasks",
                        "h2abcd/wb-bench-web-v1.0/tasks",
                        "h2abcdefgh/other-dataset/tasks",
                        str(self.root.parent / "h2abcdefgh/wb-bench-web-v1.0/tasks")):
            with self.subTest(dataset=dataset):
                manifest = {**valid, "dataset": dataset,
                            "decisions": {**valid["decisions"], "dataset_staged_to": dataset}}
                self.assertIsNone(wb_native_runner._staged_root(
                    self.root, manifest, instance="this-run", original_dataset=original))
        self.assertIsNone(wb_native_runner._staged_root(
            self.root, valid, instance="another-run", original_dataset=original))
        self.assertIsNone(wb_native_runner._staged_root(
            self.root, valid, instance="this-run", original_dataset="datasets/other/tasks"))


if __name__ == "__main__":
    unittest.main()
