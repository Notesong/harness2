"""Runner parity checks from the independent Windows review; no model calls."""

import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2 import config
from harness2.benchmarks import jb_codex_runner, wb_native_runner


class RunnerParityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="runner parity ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_codex_discovery_and_serial_models_with_parallel_tasks(self):
        for task in ("accounting/task1", "law/task2", "law/not-a-task"):
            (self.root / task / "task_folder").mkdir(parents=True)
        calls = []
        children = []

        def start(command, **kwargs):
            env = kwargs["env"]
            model, task = env["BENCHMARK_MODELS"], env["TASK_IDS"]
            active = [child for child in children if child.returncode is None]
            self.assertLess(len(active), 2)
            self.assertTrue(all(child.model == model for child in active))
            self.assertEqual(env["MAX_CONCURRENT_PER_MODEL"], "1")
            self.assertEqual(command, [sys.executable, "-m", "harness2.benchmarks.jb_codex_runner"])
            calls.append((model, task, env["LABEL"]))
            child = Mock(returncode=None, model=model)

            def poll():
                child.returncode = int(task == "law/task2")
                return child.returncode

            child.poll.side_effect = poll
            child.wait.side_effect = lambda: child.returncode
            children.append(child)
            return child

        env = {
            "TASKS_BASE_DIR": str(self.root),
            "BENCHMARK_MODELS": "first/model second.model",
            "HARNESS2_CODEX_BIN": "unused",
            "MAX_CONCURRENT_PER_MODEL": "2",
            "RUN_LABEL": "review run",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(jb_codex_runner, "start", side_effect=start),
            patch.object(jb_codex_runner, "terminate_tree") as stop,
        ):
            self.assertEqual(jb_codex_runner.main(), 1)
            self.assertEqual(stop.call_count, 4)
        self.assertEqual({task for _, task, _ in calls}, {"accounting/task1", "law/task2"})
        self.assertEqual(
            [model for model, _, _ in calls],
            ["first/model", "first/model", "second.model", "second.model"],
        )
        self.assertEqual(
            {label for _, _, label in calls},
            {"first_model-codexcli-review_run", "second-model-codexcli-review_run"},
        )

    def test_codex_parallel_interruption_stops_every_active_task(self):
        children = [Mock(), Mock()]
        children[0].poll.side_effect = KeyboardInterrupt
        with (
            patch.object(jb_codex_runner, "start", side_effect=children),
            patch.object(jb_codex_runner, "terminate_tree") as stop,
        ):
            with self.assertRaises(KeyboardInterrupt):
                jb_codex_runner._parallel_tasks(["a/task1", "a/task2"], "model", "label", 2)
        self.assertEqual([call.args[0] for call in stop.call_args_list], children)

    def test_codex_parallel_native_children_record_each_failed_task(self):
        for task_id in ("law/task1", "law/task2"):
            source = self.root / task_id / "task_folder"
            source.mkdir(parents=True)
            (source / "TASK_INSTRUCTIONS.txt").write_text("fixture task", encoding="utf-8")
        with patch.dict(
            os.environ,
            {
                "TASKS_BASE_DIR": str(self.root),
                "TASK_IDS": "law/task1 law/task2",
                "BENCHMARK_MODELS": "fixture",
                "LABEL": "parallel",
                "MAX_CONCURRENT_PER_MODEL": "2",
                # Python rejects the Codex arguments immediately. This exercises real
                # child launchers and their failure records without invoking a model.
                "HARNESS2_CODEX_BIN": sys.executable,
            },
        ):
            self.assertEqual(jb_codex_runner.main(), 1)
        for task_id in ("law/task1", "law/task2"):
            index = self.root / task_id / "model_traj/parallel/attempt_index.tsv"
            with index.open(encoding="utf-8", newline="") as stream:
                (row,) = csv.DictReader(stream, delimiter="\t")
            self.assertEqual(row["status"], "failed")
            self.assertNotEqual(row["exit_code"], "0")

    def test_codex_timeout_keeps_partial_output_and_failure_index(self):
        task = self.root / "law/task1/task_folder"
        task.mkdir(parents=True)
        (task / "TASK_INSTRUCTIONS.txt").write_text("Create a deliverable", encoding="utf-8")

        def timeout(command, **kwargs):
            workspace = Path(kwargs["cwd"])
            (workspace / "output/partial.txt").write_text("unfinished", encoding="utf-8")
            kwargs["stdout"].write('{"type":"thread.started","thread_id":"timed-out"}\n')
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with patch.object(jb_codex_runner, "run_process", side_effect=timeout):
            self.assertFalse(
                jb_codex_runner.run_one(
                    "law/task1",
                    "model",
                    tasks_base=self.root,
                    render_dir=None,
                    label="attempt",
                    executable="unused",
                    timeout=10,
                )
            )
        self.assertEqual(
            (self.root / "law/task1/model_output/attempt/partial.txt").read_text(encoding="utf-8"),
            "unfinished",
        )
        with (self.root / "law/task1/model_traj/attempt/attempt_index.tsv").open(
            encoding="utf-8", newline=""
        ) as stream:
            (row,) = csv.DictReader(stream, delimiter="\t")
        self.assertEqual(
            (row["status"], row["exit_code"], row["session_id"]), ("timeout", "124", "timed-out")
        )

    def test_workbuddy_direct_empty_bindings_use_model_env(self):
        job = self.root / "configs/jobs/direct.yaml"
        job.parent.mkdir(parents=True)
        job.write_text(
            json.dumps(
                {
                    "model": "model",
                    "harness": "test",
                    "model_connection": "direct",
                    "dataset": "datasets/tasks",
                }
            ),
            encoding="utf-8",
        )
        model = self.root / "configs/models/model.yaml"
        model.parent.mkdir(parents=True)
        model.write_text(
            json.dumps(
                {
                    "model": {
                        "protocol": "openai",
                        "backend_url_env": "MODEL_URL",
                        "backend_key_env": "MODEL_KEY",
                    }
                }
            ),
            encoding="utf-8",
        )
        loader = types.ModuleType("workbuddy_bench.runner.config_loaders")
        loader.load_harness_config = lambda *args: types.SimpleNamespace(harness={"name": "test"})
        modules = {
            "yaml": types.SimpleNamespace(safe_load=json.loads),
            "workbuddy_bench": types.ModuleType("workbuddy_bench"),
            "workbuddy_bench.runner": types.ModuleType("workbuddy_bench.runner"),
            "workbuddy_bench.runner.config_loaders": loader,
        }

        def upstream(command, **kwargs):
            name = command[2].rsplit(".", 1)[-1]
            output = ""
            if name == "harness_adapters":
                output = (
                    "HARNESS_PROTOCOL='openai'\n"
                    "HARNESS_BACKEND_BASE_ENV='HARNESS_URL'\n"
                    "HARNESS_BACKEND_KEY_ENV='HARNESS_KEY'\n"
                )
            elif name == "resolve_manifest":
                state = Path(command[command.index("--instance-dir") + 1])
                (state / "manifest.json").write_text(
                    json.dumps({"dataset": "datasets/tasks"}), encoding="utf-8"
                )
            elif name == "sharded_eval":
                self.assertEqual(kwargs["env"]["HARNESS_URL"], expected_url)
                self.assertEqual(kwargs["env"]["HARNESS_KEY"], expected_key)
            return subprocess.CompletedProcess(command, 0, output, "")

        previous = Path.cwd()
        try:
            os.chdir(self.root)
            for existing, expected_url, expected_key in (
                ("", "https://model.example/v1", "model-key"),
                ("explicit", "explicit", "explicit"),
            ):
                with (
                    self.subTest(existing=existing),
                    patch.dict(sys.modules, modules),
                    patch.dict(
                        os.environ,
                        {
                            "HARNESS_URL": existing,
                            "HARNESS_KEY": existing,
                            "MODEL_URL": "https://model.example/v1",
                            "MODEL_KEY": "model-key",
                        },
                    ),
                    patch.object(wb_native_runner, "run", side_effect=upstream),
                ):
                    self.assertEqual(wb_native_runner.execute("direct"), 0)
        finally:
            os.chdir(previous)

    def test_lab_codex_main_writes_valid_toml_for_non_bmp_paths(self):
        fake_external = types.ModuleType("external")
        fake_tts = types.ModuleType("external.tts_lab")
        fake_tts.config = types.SimpleNamespace(
            documents_dir=lambda task: self.root / "missing-documents",
            task_json=lambda task: {"instructions": "Complete the assignment"},
        )
        source = config.THIRD_PARTY / "lab_runner/adapt_runs_cx/run_codex_lab.py"
        spec = importlib.util.spec_from_file_location("review_lab_codex", source)
        module = importlib.util.module_from_spec(spec)
        previous_path = sys.path[:]
        try:
            with patch.dict(sys.modules, {"external": fake_external, "external.tts_lab": fake_tts}):
                spec.loader.exec_module(module)
        finally:
            sys.path[:] = previous_path
        reader = self.root / "read_doc.py"
        reader.write_text("# fixture reader", encoding="utf-8")
        module.SCRATCH_BASE = self.root / "scratch 🐈"
        module.RESULTS_ROOT = self.root / "results"
        module.CODEX_BIN = Path(sys.executable)
        module.READ_DOC = reader
        args = types.SimpleNamespace(
            reap_hours=None,
            task="task",
            run_id="run",
            model="model",
            timeout=60,
            harness_dir=None,
            doc_parser="host",
            reasoning_effort="high",
        )

        def invoke(command, **kwargs):
            workspace = Path(kwargs["cwd"])
            home = Path(kwargs["env"]["CODEX_HOME"])
            parsed = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
            self.assertIn(str(workspace), parsed["projects"])
            (workspace / "output/result.txt").write_text("complete", encoding="utf-8")
            kwargs["stdout"].write('{"type":"thread.started","thread_id":"fixture"}\n')
            return subprocess.CompletedProcess(command, 0)

        with (
            patch.object(module, "run_process", side_effect=invoke),
            patch.object(module.signal, "signal"),
        ):
            self.assertEqual(module.main(args), 0)


if __name__ == "__main__":
    unittest.main()
