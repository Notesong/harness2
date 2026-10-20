"""HTTP and owned-process regressions for the loopback UI, without model calls."""

import ctypes
import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from harness2.ui.jobs import JobManager
from harness2.ui.server import App, LocalServer

SECRET = "ui-test-secret-do-not-expose"


class UITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ui résumé ")
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            "HARNESS2_RUNS_DIR": str(self.root / "runs"),
            "HARNESS2_LOCAL_API_KEY": SECRET,
            "HARNESS2_JUDGE_API_KEY": SECRET,
        })
        self.environment.start()
        self.app = App(self.root)
        self.server = LocalServer(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.app.close()
        self.server.server_close()
        self.thread.join(timeout=10)
        self.environment.stop()
        self.temporary.cleanup()

    def request(self, method, path, data=None, *, headers=None, authorized=True, body=None):
        values = {"Connection": "close"}
        if authorized:
            values["Authorization"] = "Bearer " + self.app.token
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            values["Content-Type"] = "application/json"
        if headers:
            values.update(headers)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port,
                                                timeout=10)
        try:
            connection.request(method, path, body=body, headers=values)
            response = connection.getresponse()
            raw = response.read()
            content = json.loads(raw) if response.getheader("Content-Type", "").startswith(
                "application/json") else raw
            return response.status, content, dict(response.getheaders())
        finally:
            connection.close()

    def wait_job(self, identifier, *, terminal=True, predicate=None, timeout=40):
        deadline = time.monotonic() + timeout
        record = None
        while time.monotonic() < deadline:
            status, record, _ = self.request("GET", "/api/jobs/" + identifier)
            self.assertEqual(status, 200, record)
            if predicate and predicate(record):
                return record
            if terminal and record["status"] not in ("starting", "running"):
                return record
            time.sleep(0.03)
        self.fail(f"Job did not reach the expected state: {record}")

    def synthetic_record(self, *, status="succeeded"):
        identifier = "a123456789ab"
        output = self.root / "runs" / "synthetic"
        output.mkdir(parents=True, exist_ok=True)
        record = {"id": identifier, "kind": "demo", "title": "Synthetic run",
                  "status": status, "params": self.app.jobs.parameters({}),
                  "created_at": "2026-10-19T00:00:00+00:00", "started_at": None,
                  "finished_at": None, "phase": "Complete", "phase_index": 0,
                  "phases": [], "output_dir": str(output), "error": "", "exit_code": 0,
                  "total_tasks": 1, "progress": {"done": 0, "failed": 0, "total": 1},
                  "models": {}, "resumed_from": None}
        self.app.jobs.jobs[identifier] = record
        self.app.jobs._write(record)
        return record, output

    def test_authentication_host_and_origin_are_enforced_over_http(self):
        for path in ("/api/state", "/api/jobs/missing", "/api/artifacts/missing/file"):
            status, _, _ = self.request("GET", path, authorized=False)
            self.assertEqual(status, 401)
        status, _, _ = self.request("GET", "/api/state?token=" + self.app.token,
                                     authorized=False)
        self.assertEqual(status, 401)
        for headers, expected in (
            ({"Authorization": "Bearer wrong"}, 401),
            ({"Host": "attacker.example"}, 403),
            ({"Host": f"attacker.example:{self.server.server_port}"}, 403),
            ({"Origin": "https://attacker.example"}, 403),
            ({"Origin": "null"}, 403),
            ({"Origin": f"http://127.0.0.1:{self.server.server_port}"}, 200),
        ):
            with self.subTest(headers=headers):
                status, _, _ = self.request("GET", "/api/state", headers=headers)
                self.assertEqual(status, expected)

    def test_json_body_limits_and_types_are_enforced(self):
        cases = (
            (b"{}", {"Content-Type": "text/plain"}, 415),
            (b"[]", {"Content-Type": "application/json"}, 400),
            (b"null", {"Content-Type": "application/json"}, 400),
            (b"{broken", {"Content-Type": "application/json"}, 400),
            (b"{}", {"Content-Type": "application/json", "Content-Length": "-1"}, 400),
            (b" " * 65537, {"Content-Type": "application/json"}, 400),
        )
        for body, headers, expected in cases:
            with self.subTest(length=len(body), headers=headers):
                status, _, _ = self.request("POST", "/api/jobs", body=body, headers=headers)
                self.assertEqual(status, expected)
        for params in ([], {"k": True}, {"workers": 0}, {"benchmark": "unknown"},
                       {"domains": ["../escape"]}, {"mode": "shell"}):
            with self.subTest(params=params):
                status, _, _ = self.request("POST", "/api/jobs", {"kind": "demo", "params": params})
                self.assertEqual(status, 400)
        self.assertEqual(self.app.jobs.list(), [])

    def test_model_work_requires_explicit_cost_confirmation(self):
        for confirmation in (None, False, "true", 1):
            with self.subTest(confirmation=confirmation):
                status, value, _ = self.request("POST", "/api/jobs", {
                    "kind": "benchmark", "params": {}, "confirm": confirmation})
                self.assertEqual(status, 400, value)
        self.assertIsNone(self.app.jobs.active_id)

    def test_conversation_routes_share_auth_and_creation_does_not_load_a_model(self):
        from harness2.profiles import ProfileStore

        profiles = ProfileStore(self.root)
        profiles.create("Owen")
        configuration = profiles.add_configuration("owen", {
            "settings": {"provider": "local", "local_model": "synthetic"}, "identity": {"model": "synthetic"}})
        revision = profiles.add_revision("owen", {"guardrails.md": "A synthetic harness."})
        profiles.activate("owen", configuration_id=configuration["id"], revision_id=revision["id"])
        with patch("harness2.ui.conversations.ManagedLocalServer", side_effect=AssertionError("Creation must not load")):
            status, conversation, _ = self.request("POST", "/api/conversations", {"profile_id": "owen"})
        self.assertEqual(status, 201, conversation)
        identifier = conversation["id"]
        self.assertFalse(conversation["runtime"]["connected"])
        self.assertTrue(conversation["runtime"]["can_start"])
        for method, path, data in (("GET", "/api/conversations/" + identifier, None),
                                   ("POST", f"/api/conversations/{identifier}/messages", {"text": "Hello"}),
                                   ("POST", f"/api/conversations/{identifier}/start", {})):
            status, _, _ = self.request(method, path, data, authorized=False)
            self.assertEqual(status, 401)
            status, _, _ = self.request(method, path, data, headers={"Origin": "https://attacker.example"})
            self.assertEqual(status, 403)
        status, value, _ = self.request("POST", f"/api/conversations/{identifier}/messages", {"text": "Hello"})
        self.assertEqual(status, 400, value)
        status, state, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["conversations"][0]["id"], identifier)
        self.assertNotIn(SECRET, json.dumps(state))

    def test_conversational_experiment_parameters_are_explicit_and_bounded(self):
        base = {"profile_id": "owen", "benchmark": "jb", "substrate": "oc", "workers": 1}
        self.assertNotIn("conversation_enabled", self.app.jobs.parameters(base))
        self.assertTrue(self.app.jobs.parameters({**base, "conversation_enabled": True})["conversation_enabled"])
        for override in ({"profile_id": ""}, {"benchmark": "lab"}, {"substrate": "cx"}, {"workers": 2},
                         {"conversation_enabled": "true"}, {"conversation_enabled": 1}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.app.jobs.parameters({**base, "conversation_enabled": True, **override})

    def test_local_profile_checks_effective_source_experiment_before_judging(self):
        record, _ = self.synthetic_record()
        record["kind"] = "benchmark"
        record["models"] = self.app.workspace.settings()
        with patch.object(JobManager, "_plan", side_effect=AssertionError(
                "Unsupported local combinations must not launch any commands")) as plan:
            for params in ({"benchmark": "wb", "subset": "office", "substrate": "oc"},
                           {"benchmark": "jb", "substrate": "cx"}):
                with self.subTest(params=params):
                    record["params"] = self.app.jobs.parameters(params)
                    self.app.jobs._write(record)
                    status, result, _ = self.request("POST", "/api/jobs", {
                        "kind": "judge", "params": {"source_id": record["id"]}, "confirm": True})
                    self.assertEqual(status, 400, result)
                    self.assertIn("local model profile", result["error"])
            plan.assert_not_called()
        self.assertIsNone(self.app.jobs.active_id)

    def test_local_lab_pipeline_is_rejected_for_direct_and_saved_source_requests(self):
        record, _ = self.synthetic_record()
        record["kind"] = "benchmark"
        record["params"] = self.app.jobs.parameters({"benchmark": "lab", "substrate": "oc"})
        record["models"] = self.app.workspace.settings()
        self.app.jobs._write(record)
        with patch.object(JobManager, "_plan", side_effect=AssertionError(
                "An unsupported local LAB pipeline must not launch commands")) as plan:
            for endpoint, data in (
                ("/api/jobs", {"kind": "benchmark", "params": record["params"], "confirm": True}),
                ("/api/jobs", {"kind": "judge", "params": {"source_id": record["id"]},
                               "confirm": True}),
                (f"/api/jobs/{record['id']}/resume", {"confirm": True}),
            ):
                with self.subTest(endpoint=endpoint, kind=data.get("kind", "resume")):
                    status, result, _ = self.request("POST", endpoint, data)
                    self.assertEqual(status, 400, result)
                    self.assertIn("JobBench", result["error"])
                    self.assertIn("OpenCode", result["error"])
            plan.assert_not_called()
        self.assertIsNone(self.app.jobs.active_id)

    def test_settings_keep_secrets_private_and_preserve_unknown_environment(self):
        (self.root / ".env").write_text("# preserve this\nUNRELATED_VALUE=keep\n", encoding="utf-8")
        status, value, _ = self.request("POST", "/api/settings", {
            "provider": "local", "local_url": "http://127.0.0.1:1234/v1/",
            "local_model": "a-model", "api_key": SECRET})
        self.assertEqual(status, 200, value)
        self.assertTrue(value["key_configured"])
        self.assertNotIn(SECRET, json.dumps(value))
        self.assertNotIn("api_key", value)
        self.assertEqual(self.app.workspace.environment()["HARNESS2_LOCAL_API_KEY"], SECRET)
        self.assertEqual(self.app.workspace.environment()["UNRELATED_VALUE"], "keep")
        status, state, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertNotIn(SECRET, json.dumps(state))
        before = (self.root / ".env").read_bytes()
        for invalid in ({"api_key": "secret\nINJECTED=1"}, {"root": "C:/different"},
                        {"local_url": "http://user:password@localhost/v1"},
                        {"local_model": "not a model"}):
            status, _, _ = self.request("POST", "/api/settings", invalid)
            self.assertEqual(status, 400)
            self.assertEqual((self.root / ".env").read_bytes(), before)

    def test_local_token_limits_have_defaults_and_round_trip_through_api(self):
        with patch.dict(os.environ, {"HARNESS2_LOCAL_CONTEXT": "", "HARNESS2_LOCAL_OUTPUT": ""}):
            status, state, _ = self.request("GET", "/api/state")
            self.assertEqual(status, 200)
            self.assertEqual(state["settings"]["local_context"], "32768")
            self.assertEqual(state["settings"]["local_output"], "4096")
            status, saved, _ = self.request("POST", "/api/settings", {
                "provider": "local", "local_context": 65536, "local_output": 8192})
            self.assertEqual(status, 200, saved)
            self.assertEqual(saved["local_context"], "65536")
            self.assertEqual(saved["local_output"], "8192")
            status, saved, _ = self.request("POST", "/api/settings", {
                "local_context": " 065536 ", "local_output": "008192"})
            self.assertEqual(status, 200, saved)
            self.assertEqual(saved["local_context"], "65536")
            self.assertEqual(saved["local_output"], "8192")
            status, state, _ = self.request("GET", "/api/state")
            self.assertEqual(status, 200)
            self.assertEqual(state["settings"]["local_context"], "65536")
            self.assertEqual(state["settings"]["local_output"], "8192")
            environment = self.app.workspace.environment()
            self.assertEqual(environment["HARNESS2_LOCAL_CONTEXT"], "65536")
            self.assertEqual(environment["HARNESS2_LOCAL_OUTPUT"], "8192")

    def test_invalid_local_token_limits_do_not_change_saved_settings(self):
        status, saved, _ = self.request("POST", "/api/settings", {
            "provider": "local", "local_context": "32768", "local_output": "4096"})
        self.assertEqual(status, 200, saved)
        before = (self.root / ".env").read_bytes()
        invalid = ({"local_context": "0"}, {"local_context": "-1"},
                   {"local_context": "3.5"}, {"local_context": "not-a-number"},
                   {"local_context": ""}, {"local_context": True},
                   {"local_output": "0"}, {"local_output": "-1"},
                   {"local_output": "4096.5"}, {"local_output": 4096.0},
                   {"local_context": "4096", "local_output": "4096"},
                   {"local_context": "4096", "local_output": "8192"})
        for values in invalid:
            with self.subTest(values=values):
                status, result, _ = self.request("POST", "/api/settings", {
                    "local_model": "must-not-be-saved", **values})
                self.assertEqual(status, 400, result)
                self.assertEqual((self.root / ".env").read_bytes(), before)

    def test_local_budget_changes_reject_resume_before_planning_model_work(self):
        status, saved, _ = self.request("POST", "/api/settings", {
            "provider": "local", "local_context": "32768", "local_output": "4096"})
        self.assertEqual(status, 200, saved)
        record, _ = self.synthetic_record()
        record["kind"] = "benchmark"
        record["models"] = self.app.workspace.settings()
        self.app.jobs._write(record)
        with patch.object(JobManager, "_plan", return_value=[]) as plan:
            status, resumed, _ = self.request("POST", f"/api/jobs/{record['id']}/resume", {
                "confirm": True})
            self.assertEqual(status, 201, resumed)
            self.assertEqual(self.wait_job(resumed["id"])["status"], "succeeded")
            plan.assert_called_once()
        for changed in ({"local_context": "65536"}, {"local_output": "2048"}):
            with self.subTest(changed=changed):
                status, saved, _ = self.request("POST", "/api/settings", {
                    "local_context": "32768", "local_output": "4096", **changed})
                self.assertEqual(status, 200, saved)
                with patch.object(JobManager, "_plan", side_effect=AssertionError(
                        "Changed budgets must reject resume before planning any model work")) as plan:
                    status, result, _ = self.request("POST", f"/api/jobs/{record['id']}/resume", {
                        "confirm": True})
                    self.assertEqual(status, 400, result)
                    self.assertIn("connection changed", result["error"])
                    plan.assert_not_called()
                self.assertIsNone(self.app.jobs.active_id)

    def test_connection_only_requests_model_metadata(self):
        requests = []
        payload = [b'{"data":[{"id":"local-model"}]}']

        class Models(BaseHTTPRequestHandler):
            def do_GET(inner):
                requests.append((inner.command, inner.path, inner.headers.get("Authorization")))
                body = payload[0]
                inner.send_response(200)
                inner.send_header("Content-Length", str(len(body)))
                inner.end_headers()
                inner.wfile.write(body)

            def log_message(inner, *args):
                pass

        provider = ThreadingHTTPServer(("127.0.0.1", 0), Models)
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        try:
            status, result, _ = self.request("POST", "/api/connection", {
                "url": f"http://127.0.0.1:{provider.server_port}/v1", "model": "local-model",
                "api_key": SECRET})
            self.assertEqual(status, 200)
            self.assertTrue(result["ok"], result)
            self.assertNotIn(SECRET, json.dumps(result))
            self.assertEqual(requests, [("GET", "/v1/models", "Bearer " + SECRET)])
            payload[0] = b'[]'
            status, result, _ = self.request("POST", "/api/connection", {
                "url": f"http://127.0.0.1:{provider.server_port}/v1"})
            self.assertIn(status, (200, 400), result)
            if status == 200:
                self.assertFalse(result["ok"], result)
        finally:
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=10)

    def test_custom_metadata_request_uses_saved_codex_key_instead_of_local_key(self):
        custom_secret = "ui-custom-codex-only-secret"
        authorizations = []

        class Models(BaseHTTPRequestHandler):
            def do_GET(inner):
                authorizations.append(inner.headers.get("Authorization"))
                body = b'{"data":[{"id":"custom-model"}]}'
                inner.send_response(200)
                inner.send_header("Content-Length", str(len(body)))
                inner.end_headers()
                inner.wfile.write(body)

            def log_message(inner, *args):
                pass

        provider = ThreadingHTTPServer(("127.0.0.1", 0), Models)
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{provider.server_port}/v1"
        try:
            status, saved, _ = self.request("POST", "/api/settings", {
                "provider": "custom", "codex_url": url, "codex_model": "custom-model",
                "api_key": custom_secret})
            self.assertEqual(status, 200, saved)
            self.assertNotIn(custom_secret, json.dumps(saved))
            self.assertEqual(self.app.workspace.environment()["HARNESS2_CODEX_API_KEY"],
                             custom_secret)
            for selected in ("custom", "local"):
                status, result, _ = self.request("POST", "/api/connection", {
                    "provider": selected, "url": url})
                self.assertEqual(status, 200, result)
                self.assertTrue(result["ok"], result)
                self.assertNotIn(custom_secret, json.dumps(result))
                self.assertNotIn(SECRET, json.dumps(result))
            self.assertEqual(authorizations, ["Bearer " + custom_secret, "Bearer " + SECRET])
        finally:
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=10)

    def test_real_deterministic_demo_runs_and_serves_artifacts_through_http(self):
        status, started, _ = self.request("POST", "/api/jobs", {
            "kind": "demo", "params": {"k": 1, "mode": "parallel"}})
        self.assertEqual(status, 201, started)
        record = self.wait_job(started["id"])
        self.assertEqual(record["status"], "succeeded", record)
        self.assertEqual(len(record["tasks"]), 2)
        self.assertEqual(record["progress"]["done"], 2)
        self.assertTrue(all(task["status"] == "ok" for task in record["tasks"]))
        files = [artifact for task in record["tasks"] for selection in task["selections"]
                 for artifact in selection["files"]]
        self.assertTrue(files)
        artifact = next(item for item in files if item["name"].endswith("answer.txt"))
        status, body, headers = self.request(
            "GET", f"/api/artifacts/{started['id']}/{artifact['id']}")
        self.assertEqual(status, 200)
        self.assertIn(b"Total:", body)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_artifacts_are_limited_to_the_selected_owned_tree(self):
        record, output = self.synthetic_record()
        chosen = output / "chosen"
        chosen.mkdir()
        (chosen / "résumé.txt").write_text("owned evidence", encoding="utf-8")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.txt").write_text("outside content", encoding="utf-8")
        summary = output / "namespace" / "task" / "summary.json"
        summary.parent.mkdir(parents=True)
        summary.write_text(json.dumps({"task_id": "task", "status": "ok", "selections": {
            "agg": {"chosen_run_dir": str(chosen), "chosen_label": "chosen"},
            "seq": {"chosen_run_dir": str(output / ".." / ".." / "outside"),
                    "chosen_label": "../outside"}}}), encoding="utf-8")
        status, viewed, _ = self.request("GET", "/api/jobs/" + record["id"])
        self.assertEqual(status, 200, viewed)
        selections = viewed["tasks"][0]["selections"]
        self.assertEqual(len(selections[0]["files"]), 1)
        self.assertEqual(selections[1]["files"], [])
        self.assertNotIn("outside content", json.dumps(viewed))
        for artifact in ("../private.txt", "..%2fprivate.txt", "%2e%2e%5cprivate.txt", "unknown"):
            status, _, _ = self.request("GET", f"/api/artifacts/{record['id']}/{artifact}")
            self.assertEqual(status, 404)

    def test_lab_and_workbuddy_results_only_list_deliverables_without_hidden_folders(self):
        record, output = self.synthetic_record()
        record["kind"] = "benchmark"
        summary = output / "namespace" / "task" / "summary.json"
        summary.parent.mkdir(parents=True)
        cases = (
            ("lab", {"benchmark": "lab"}, {"output/answer.txt": "LAB deliverable"},
             ("input/source.txt", "scratch/runtime.txt", "output/.private/secret.txt")),
            ("office", {"benchmark": "wb", "subset": "office"},
             {"verifier/raw_artifacts/report.txt": "Office deliverable",
              "verifier/agent.patch": "Office patch"},
             ("verifier/score.json", "verifier/artifact_text/rendered.txt",
              "verifier/raw_artifacts/.private/secret.txt")),
            ("web", {"benchmark": "wb", "subset": "web"},
             {"verifier/agent.workspace.tar.gz": "Web deliverable"},
             ("verifier/agent.patch", "verifier/raw_artifacts/report.txt", "verifier/reward.txt")),
            ("code", {"benchmark": "wb", "subset": "code"},
             {"verifier/agent.patch": "Code deliverable"},
             ("verifier/raw_artifacts/report.txt", "verifier/test_output.txt", "verifier/score.json")),
        )
        for name, params, deliverables, excluded in cases:
            with self.subTest(benchmark=name):
                chosen = output / name / "chosen"
                for rel, body in deliverables.items():
                    target = chosen / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(body, encoding="utf-8")
                blocked = (*excluded, ".runtime/private.txt")
                for rel in blocked:
                    target = chosen / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("EXCLUDED PRIVATE CONTENT", encoding="utf-8")
                record["params"] = self.app.jobs.parameters(params)
                self.app.jobs._write(record)
                summary.write_text(json.dumps({"task_id": "task", "status": "ok", "selections": {
                    "agg": {"chosen_run_dir": str(chosen), "chosen_label": "selected"}}}),
                    encoding="utf-8")
                status, viewed, _ = self.request("GET", "/api/jobs/" + record["id"])
                self.assertEqual(status, 200, viewed)
                files = viewed["tasks"][0]["selections"][0]["files"]
                self.assertEqual({item["name"] for item in files}, set(deliverables))
                self.assertNotIn("EXCLUDED PRIVATE CONTENT", json.dumps(viewed))
                for artifact in files:
                    status, body, _ = self.request(
                        "GET", f"/api/artifacts/{record['id']}/{artifact['id']}")
                    self.assertEqual(status, 200)
                    self.assertEqual(body, deliverables[artifact["name"]].encode("utf-8"))
                for rel in blocked:
                    key = hashlib.sha256(str((chosen / rel).resolve()).encode("utf-8")).hexdigest()[:20]
                    status, _, _ = self.request("GET", f"/api/artifacts/{record['id']}/{key}")
                    self.assertEqual(status, 404)

    def test_cancel_stops_owned_tree_and_settings_and_second_jobs_stay_blocked(self):
        pid_file = self.root / "child.pid"
        program = (
            "import os,subprocess,sys,time; from pathlib import Path; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            "Path(sys.argv[1]).write_text(str(child.pid)); "
            "print('ready ' + os.environ['HARNESS2_LOCAL_API_KEY'], flush=True); time.sleep(30)"
        )
        phase = {"id": "synthetic", "label": "Owned process test", "status": "pending",
                 "command": [sys.executable, "-u", "-c", program, str(pid_file)]}
        with patch.object(JobManager, "_plan", return_value=[phase]):
            status, record, _ = self.request("POST", "/api/jobs", {"kind": "demo", "params": {}})
        self.assertEqual(status, 201)
        identifier = record["id"]
        try:
            running = self.wait_job(identifier, terminal=False,
                                    predicate=lambda row: "ready [redacted]" in row["log"])
            self.assertNotIn(SECRET, json.dumps(running))
            for endpoint, data in (("/api/settings", {"local_model": "changed"}),
                                   ("/api/jobs", {"kind": "demo", "params": {}})):
                status, _, _ = self.request("POST", endpoint, data)
                self.assertEqual(status, 409)
            status, _, _ = self.request("POST", f"/api/jobs/{identifier}/cancel", {})
            self.assertEqual(status, 200)
            finished = self.wait_job(identifier)
            self.assertEqual(finished["status"], "cancelled", finished)
            self.assertNotIn(SECRET, (self.app.jobs.directory / f"{identifier}.log").read_text(
                encoding="utf-8"))
            if os.name == "nt":
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel.OpenProcess.restype = wintypes.HANDLE
                kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel.OpenProcess(0x00100000, False, int(pid_file.read_text()))
                if handle:
                    try:
                        self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
                    finally:
                        kernel.CloseHandle(handle)
            self.assertIsNone(self.app.jobs.active_id)
        finally:
            if identifier in self.app.jobs.jobs:
                self.app.jobs.cancel(identifier)

    def test_restart_marks_unfinished_job_interrupted_and_preserves_results(self):
        record, output = self.synthetic_record(status="running")
        (output / "kept.txt").write_text("completed result", encoding="utf-8")
        restarted = App(self.root)
        try:
            self.assertIsNone(restarted.jobs.active_id)
            recovered = restarted.jobs.get(record["id"])
            self.assertEqual(recovered["status"], "interrupted")
            self.assertTrue(recovered["finished_at"])
            self.assertEqual((output / "kept.txt").read_text(encoding="utf-8"), "completed result")
        finally:
            restarted.close()

    @unittest.skipUnless(os.name == "nt", "Windows Job cleanup after UI process death")
    def test_server_process_death_stops_job_tree_and_restart_records_interruption(self):
        root = self.root / "separate UI workspace"
        root.mkdir()
        ready = root / "session.json"
        child_pid = root / "child.pid"
        helper = root / "server helper.py"
        helper.write_text("""
import json, subprocess, sys
from pathlib import Path
from harness2.ui.server import App, LocalServer
root = Path(sys.argv[1])
app = App(root)
program = ("import subprocess,sys,time; from pathlib import Path; "
           "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
           "Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)")
app.jobs._plan = lambda *args: [{'id':'owned', 'label':'Owned child', 'status':'pending',
                               'command':[sys.executable, '-u', '-c', program,
                                          str(root / 'child.pid')]}]
server = LocalServer(('127.0.0.1', 0), app)
(root / 'session.json').write_text(json.dumps({'port':server.server_port, 'token':app.token}))
server.serve_forever(poll_interval=0.02)
""", encoding="utf-8")
        environment = {**os.environ, "HARNESS2_RUNS_DIR": str(root / "runs")}
        worker = subprocess.Popen([sys.executable, str(helper), str(root)], env=environment,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not ready.is_file() and worker.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.is_file())
            session = json.loads(ready.read_text())
            connection = http.client.HTTPConnection("127.0.0.1", session["port"], timeout=10)
            try:
                connection.request("POST", "/api/jobs", json.dumps({"kind": "demo", "params": {}}),
                                   {"Content-Type": "application/json", "Connection": "close",
                                    "Authorization": "Bearer " + session["token"]})
                response = connection.getresponse()
                self.assertEqual(response.status, 201)
                record = json.loads(response.read())
            finally:
                connection.close()
            deadline = time.monotonic() + 10
            while not child_pid.is_file() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(child_pid.is_file())
            worker.kill()
            worker.wait(timeout=10)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, int(child_pid.read_text()))
            if handle:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
                finally:
                    kernel.CloseHandle(handle)
            restarted = App(root)
            try:
                self.assertEqual(restarted.jobs.get(record["id"])["status"], "interrupted")
                self.assertIsNone(restarted.jobs.active_id)
            finally:
                restarted.close()
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=10)
            worker.stderr.close()
            if child_pid.is_file():
                subprocess.run(["taskkill.exe", "/PID", child_pid.read_text(), "/T", "/F"],
                               capture_output=True, check=False)

    def test_short_configured_api_keys_are_redacted_from_job_logs(self):
        secret = "sk-1234"
        phase = {"id": "print", "label": "Print test credential", "status": "pending",
                 "command": [sys.executable, "-u", "-c",
                             "import os; print(os.environ['HARNESS2_LOCAL_API_KEY'])"]}
        with patch.dict(os.environ, {"HARNESS2_LOCAL_API_KEY": secret}), \
                patch.object(JobManager, "_plan", return_value=[phase]):
            status, record, _ = self.request("POST", "/api/jobs", {"kind": "demo", "params": {}})
            self.assertEqual(status, 201)
            finished = self.wait_job(record["id"])
        self.assertEqual(finished["status"], "succeeded", finished)
        self.assertNotIn(secret, finished["log"])
        self.assertIn("[redacted]", finished["log"])

    def test_malformed_result_structure_does_not_disconnect_job_view(self):
        record, output = self.synthetic_record()
        summary = output / "namespace" / "task" / "summary.json"
        summary.parent.mkdir(parents=True)
        summary.write_text(json.dumps({"task_id": "task", "selections": ["invalid"]}),
                           encoding="utf-8")
        status, viewed, _ = self.request("GET", "/api/jobs/" + record["id"])
        self.assertEqual(status, 200, viewed)
        self.assertTrue(not viewed["tasks"] or not viewed["tasks"][0]["selections"])

    def test_settings_and_start_are_serialized_so_job_models_match_saved_connection(self):
        entered, release = threading.Event(), threading.Event()
        original_save = self.app.workspace.save
        phase = {"id": "sleep", "label": "Keep job active", "status": "pending",
                 "command": [sys.executable, "-u", "-c", "import time; time.sleep(30)"]}

        def delayed_save(data):
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return original_save(data)

        identifier = None
        try:
            with patch.object(self.app.workspace, "save", side_effect=delayed_save), \
                    patch.object(JobManager, "_plan", return_value=[phase]), \
                    ThreadPoolExecutor(max_workers=2) as pool:
                settings = pool.submit(self.request, "POST", "/api/settings", {
                    "provider": "local", "local_model": "changed-during-start"})
                self.assertTrue(entered.wait(timeout=5))
                starting = pool.submit(self.request, "POST", "/api/jobs", {"kind": "demo", "params": {}})
                timer = threading.Timer(0.3, release.set)
                timer.start()
                try:
                    saved_status, saved, _ = settings.result(timeout=10)
                    start_status, record, _ = starting.result(timeout=10)
                finally:
                    release.set()
                    timer.join(timeout=5)
            self.assertEqual(start_status, 201, record)
            identifier = record["id"]
            self.assertEqual(saved_status, 200, saved)
            self.assertEqual(record["models"]["local_model"], saved["local_model"])
        finally:
            release.set()
            if identifier:
                self.app.jobs.cancel(identifier)


if __name__ == "__main__":
    unittest.main()
