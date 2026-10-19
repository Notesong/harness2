"""UI ownership orchestration with a fake model lease and real CLI children.

The model lease is simulated; local_server tests exercise the actual server
process owner. These tests check its lifetime across the complete UI pipeline.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import processes
from harness2.ui.jobs import JobManager
from harness2.ui.workspace import Workspace

RUNTIME_KEY = "ephemeral-ui-model-key-not-for-records"


class ManagedUIJobsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="managed UI résumé ")
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {
            "HARNESS2_RUNS_DIR": str(self.root / "runs"),
            "HARNESS2_UI_PROVIDER": "local",
            "HARNESS2_LOCAL_RUNTIME": "managed",
        })
        self.env.start()
        self.manager = JobManager(Workspace(self.root))
        self.lease_file = self.root / "model-loaded"
        self.events = []
        self.loading = threading.Event()
        self.hold_loading = False
        self.owner = None
        case = self

        class ModelLease:
            def __init__(self, environment, *, root, log_path, cancelled):
                self.environment = dict(environment)
                self.environment["HARNESS2_LOCAL_API_KEY"] = RUNTIME_KEY
                self.pid = 12345  # Metadata only; this fake never stops a PID.
                self.cancelled = cancelled
                case.owner = self

            def start(self):
                case.events.append("load")
                case.loading.set()
                if case.hold_loading:
                    deadline = time.monotonic() + 5
                    while not self.cancelled() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    raise RuntimeError("Model loading cancelled")
                case.lease_file.write_text("loaded", encoding="utf-8")
                return self

            def close(self):
                case.events.append("unload")
                case.assertIsNotNone(case.manager.active_id)
                case.lease_file.unlink(missing_ok=True)

        self.fake = patch("harness2.ui.jobs.ManagedLocalServer", ModelLease)
        self.fake.start()
        self.catalog = patch.object(self.manager.workspace, "benchmarks", return_value=[])
        self.catalog.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.manager.close()
        self.catalog.stop()
        self.fake.stop()
        self.env.stop()
        self.temporary.cleanup()

    def phase(self, name, *, code=0, sleep=0):
        program = (
            "import os,sys,time; from pathlib import Path; "
            "assert Path(sys.argv[1]).read_text() == 'loaded'; "
            "Path(sys.argv[2]).write_text('saved result'); "
            "print(os.environ.get('HARNESS2_LOCAL_API_KEY',''), flush=True); "
            f"time.sleep({sleep}); sys.exit({code})"
        )
        return {"id": name, "label": name, "status": "pending", "command": [
            sys.executable, "-u", "-c", program, str(self.lease_file),
            str(self.root / (name + ".result")),
        ]}

    def start(self, phases):
        with patch.object(self.manager, "_plan", return_value=phases):
            return self.manager.start("benchmark", {}, confirmed=True)["id"]

    def finished(self, identifier):
        self.manager.threads[identifier].join(timeout=10)
        self.assertFalse(self.manager.threads[identifier].is_alive())
        return self.manager.get(identifier)

    def test_lease_covers_check_refine_judge_report_and_key_is_not_persisted(self):
        names = ("check", "refine", "judge", "report")
        identifier = self.start([self.phase(name) for name in names])
        record = self.finished(identifier)
        self.assertEqual(record["status"], "succeeded", record)
        self.assertEqual(self.events, ["load", "unload"])
        self.assertEqual(record["local_server"]["status"], "unloaded")
        for name in names:
            self.assertEqual((self.root / (name + ".result")).read_text(), "saved result")
        for path in self.manager.directory.iterdir():
            self.assertNotIn(RUNTIME_KEY, path.read_text(encoding="utf-8"))
        self.assertNotIn(RUNTIME_KEY, str(record))
        self.assertIsNone(self.manager.active_id)

    def test_judge_failure_unloads_and_preserves_preceding_results(self):
        identifier = self.start([
            self.phase("refine"), self.phase("judge", code=2), self.phase("report")
        ])
        record = self.finished(identifier)
        self.assertEqual(record["status"], "failed", record)
        self.assertEqual(record["exit_code"], 2)
        self.assertEqual(self.events, ["load", "unload"])
        self.assertTrue((self.root / "refine.result").is_file())
        self.assertFalse((self.root / "report.result").exists())

    def test_cancel_during_loading_also_closes_the_lease(self):
        self.hold_loading = True
        identifier = self.start([self.phase("check")])
        self.assertTrue(self.loading.wait(timeout=5))
        self.manager.cancel(identifier)
        record = self.finished(identifier)
        self.assertEqual(record["status"], "cancelled", record)
        self.assertEqual(self.events, ["load", "unload"])
        self.assertFalse((self.root / "check.result").exists())

    def test_app_close_stops_the_phase_before_releasing_model(self):
        identifier = self.start([self.phase("refine", sleep=30)])
        deadline = time.monotonic() + 5
        while not (self.root / "refine.result").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((self.root / "refine.result").exists())
        self.manager.close()
        record = self.finished(identifier)
        self.assertEqual(record["status"], "cancelled", record)
        self.assertEqual(self.events, ["load", "unload"])
        self.assertFalse(self.manager.procs)

    def test_phase_launch_exception_still_releases_model(self):
        phase = {"id": "missing", "label": "missing", "status": "pending",
                 "command": [str(self.root / "missing-executable")]}
        record = self.finished(self.start([phase]))
        self.assertEqual(record["status"], "failed", record)
        self.assertEqual(self.events, ["load", "unload"])

    def test_wrap_up_deadline_releases_model_and_preserves_existing_outputs(self):
        prior_result = self.root / "prior.result"
        prior_result.write_text("saved before wrap-up", encoding="utf-8")
        phase = self.phase("summary", sleep=30)
        phase["timeout"] = 0.1
        record = self.finished(self.start([phase]))
        self.assertEqual(record["status"], "failed", record)
        self.assertNotEqual(record["exit_code"], 0)
        self.assertEqual(self.events, ["load", "unload"])
        self.assertEqual(prior_result.read_text(), "saved before wrap-up")

    def test_deadline_timer_start_failure_stops_the_already_started_child(self):
        children = []
        real_start = processes.start

        def remember_child(*args, **kwargs):
            child = real_start(*args, **kwargs)
            children.append(child)
            return child

        phase = self.phase("summary", sleep=30)
        phase["timeout"] = 1
        with patch("harness2.ui.jobs.processes.start", side_effect=remember_child), \
                patch("harness2.ui.jobs.threading.Timer.start", side_effect=RuntimeError("timer unavailable")):
            record = self.finished(self.start([phase]))
        self.assertEqual(record["status"], "failed", record)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertFalse(self.manager.procs)
        self.assertIsNone(self.manager.active_id)
        self.assertEqual(self.events, ["load", "unload"])

    def test_preflight_environment_failure_releases_the_active_job(self):
        original_environment = self.manager.workspace.environment

        def fail_in_worker():
            if threading.current_thread().name.startswith("harness-ui-"):
                raise PermissionError("Environment is unavailable")
            return original_environment()

        with patch.object(self.manager.workspace, "environment", side_effect=fail_in_worker):
            record = self.finished(self.start([self.phase("check")]))
        self.assertEqual(record["status"], "failed", record)
        self.assertIn("Environment is unavailable", record["error"])
        self.assertIsNone(self.manager.active_id)
        self.assertFalse(self.manager.procs)
        self.assertEqual(self.events, [])

    def test_external_server_and_demo_do_not_acquire_a_model_lease(self):
        phase = {"id": "plain", "label": "plain", "status": "pending",
                 "command": [sys.executable, "-c", "pass"]}
        with patch.dict(os.environ, {"HARNESS2_LOCAL_RUNTIME": "external"}):
            self.assertEqual(self.finished(self.start([phase]))["status"], "succeeded")
        with patch.object(self.manager, "_plan", return_value=[dict(phase)]):
            identifier = self.manager.start("demo", {})["id"]
        self.assertEqual(self.finished(identifier)["status"], "succeeded")
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
