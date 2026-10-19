"""Full local CLI lifecycle tests with a fake model runtime and no model calls."""

import contextlib
import io
import os
import signal
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.cli import local_run

LOCAL = {
    "HARNESS2_LOCAL_BASE_URL": "http://127.0.0.1:8087/v1",
    "HARNESS2_LOCAL_MODEL": "fixture-model",
    "HARNESS2_LOCAL_RUNTIME": "managed",
}


class LocalRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="local pipeline ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.events = []
        self.calls = []
        events = self.events

        class Runtime:
            def __init__(self, environment, **kwargs):
                self.environment = {**environment, "HARNESS2_LOCAL_API_KEY": "ephemeral-fixture"}

            def start(self):
                events.append("start")
                return self

            def close(self):
                events.append("close")

        self.runtime = Runtime
        self.stack = self.enterContext(contextlib.ExitStack())
        self.stack.enter_context(patch.dict(os.environ, LOCAL, clear=True))
        self.stack.enter_context(patch.object(local_run.config, "RUNS", self.root / "runs"))
        self.stack.enter_context(patch.object(local_run, "ManagedLocalServer", Runtime))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def phase(self, command, **kwargs):
        module = command[3].rsplit(".", 1)[-1]
        self.events.append(module)
        self.calls.append((command, kwargs))
        self.assertNotIn("close", self.events)
        return subprocess.CompletedProcess(
            command, 0, "fixture report\n" if module == "report" else None
        )

    def args(self, name="experiment"):
        return [
            "--output",
            str(self.root / name),
            "--only-domains",
            "technical_writers",
            "--k",
            "2",
            "--seed",
            "7",
            "--mode",
            "parallel,sequential",
            "--max-tasks",
            "1",
            "--workers",
            "1",
        ]

    def test_runtime_spans_all_phases_and_exact_identity_is_shared(self):
        with patch.object(local_run.processes, "run", side_effect=self.phase):
            self.assertEqual(local_run.main(self.args()), 0)
        self.assertEqual(
            self.events, ["start", "check", "run_sweep", "judge", "report", "summarize", "close"]
        )
        environments = [kwargs["env"] for _, kwargs in self.calls]
        self.assertEqual(
            {Path(env["HARNESS2_RUNS_DIR"]).resolve() for env in environments},
            {(self.root / "experiment").resolve()},
        )
        self.assertTrue(
            all(env["HARNESS2_LOCAL_API_KEY"] == "ephemeral-fixture" for env in environments)
        )
        for command, _ in self.calls[1:4]:
            for flag, expected in (
                ("--k", "2"),
                ("--seed", "7"),
                ("--mode", "parallel,sequential"),
            ):
                self.assertEqual(command[command.index(flag) + 1], expected)
        self.assertEqual(
            (self.root / "experiment/report.txt").read_text(encoding="utf-8"), "fixture report\n"
        )
        self.assertNotIn("HARNESS2_RUNS_DIR", os.environ)
        self.assertEqual(self.calls[-1][1]["timeout"], 300)

    def test_optional_wrapup_failure_does_not_fail_the_graded_workflow(self):
        def fail_summary(command, **kwargs):
            result = self.phase(command, **kwargs)
            if "harness2.cli.summarize" in command:
                raise subprocess.TimeoutExpired(command, 300)
            return result

        with patch.object(local_run.processes, "run", side_effect=fail_summary):
            self.assertEqual(local_run.main(self.args()), 0)
        self.assertEqual(self.events[-1], "close")
        self.assertTrue((self.root / "experiment/run-summary.md").is_file())

    def test_each_phase_failure_stops_remaining_work_and_closes_runtime(self):
        for failed_index in range(4):
            with self.subTest(phase=failed_index):
                self.events.clear()
                self.calls.clear()

                def fail(command, **kwargs):
                    result = self.phase(command, **kwargs)
                    if len(self.calls) == failed_index + 1:
                        result.returncode = 23
                    return result

                with patch.object(local_run.processes, "run", side_effect=fail):
                    self.assertEqual(local_run.main(self.args(f"failure-{failed_index}")), 23)
                self.assertEqual(len(self.calls), failed_index + 1)
                self.assertEqual(self.events[-1], "close")

    def test_ctrl_c_during_a_phase_closes_runtime(self):
        with patch.object(local_run.processes, "run", side_effect=KeyboardInterrupt):
            self.assertEqual(local_run.main(self.args()), 130)
        self.assertEqual(self.events, ["start", "close"])

    def test_startup_failure_also_closes_runtime(self):
        with (
            patch.object(self.runtime, "start", side_effect=RuntimeError("startup failed")),
            patch.object(local_run.processes, "run") as run,
        ):
            self.assertEqual(local_run.main(self.args()), 1)
        self.assertEqual(self.events, ["close"])
        run.assert_not_called()

    def test_termination_handler_requests_stop_and_restores_previous_handler(self):
        cancelled = threading.Event()
        previous = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(KeyboardInterrupt), local_run._stop_signals(cancelled):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_nonempty_output_is_rejected_before_runtime_start(self):
        output = self.root / "experiment"
        output.mkdir()
        (output / "existing.txt").write_text("previous evidence", encoding="utf-8")
        with patch.object(local_run.processes, "run") as run:
            self.assertEqual(local_run.main(self.args()), 1)
        self.assertEqual(self.events, [])
        run.assert_not_called()
        self.assertEqual((output / "existing.txt").read_text(encoding="utf-8"), "previous evidence")

    def test_default_output_is_distinct_for_every_invocation(self):
        for _ in range(2):
            self.events.clear()
            with patch.object(local_run.processes, "run", side_effect=self.phase):
                self.assertEqual(local_run.main(["--only-domains", "technical_writers"]), 0)
        outputs = {kwargs["env"]["HARNESS2_RUNS_DIR"] for _, kwargs in self.calls}
        self.assertEqual(len(outputs), 2)
        self.assertTrue(
            all(Path(output).is_relative_to(self.root / "runs/local") for output in outputs)
        )

    def test_dry_run_needs_no_profile_or_runtime_and_creates_no_files(self):
        output = self.root / "preview-only"
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(
                local_run.processes, "run", return_value=subprocess.CompletedProcess([], 0)
            ) as run,
        ):
            self.assertEqual(local_run.main(["--dry-run", "--output", str(output)]), 0)
        self.assertEqual(self.events, [])
        self.assertFalse(output.exists())
        self.assertEqual(run.call_count, 1)
        self.assertIn("harness2.cli.run_sweep", run.call_args.args[0])
        self.assertIn("--dry-run", run.call_args.args[0])

    def test_explicit_remote_model_override_is_rejected(self):
        with (
            patch.dict(os.environ, {"HARNESS2_IMPROVER_MODEL": "google-vertex/fixture"}),
            patch.object(local_run.processes, "run") as run,
        ):
            self.assertEqual(local_run.main(self.args()), 1)
        self.assertEqual(self.events, [])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
