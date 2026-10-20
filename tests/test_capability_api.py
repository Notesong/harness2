# SPDX-License-Identifier: Apache-2.0
"""Authenticated local catalog workflows with real archives, without model calls."""

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.capability_runtime import catalog_snapshot
from harness2.ui.server import App, LocalServer


class CapabilityAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="catalog api résumé ")
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"HARNESS2_RUNS_DIR": str(self.root / "runs")})
        self.environment.start()
        self.app = App(self.root)
        self.store = self.app.profiles.store
        for name in ("Owen", "Kestrel"):
            self.store.create(name)
        self.config = self.store.add_configuration("owen", {"settings": {"provider": "local"}, "identity": {}})
        self.source = self.store.add_revision("owen", {
            "guardrails.md": "Reconcile sources before writing.",
            "scripts/check.py": "raise RuntimeError('Catalog files must not be executed')\n",
        })
        self.target = self.store.add_revision("kestrel", {
            "guardrails.md": "Keep the local workflow.", "LongTermMEMORY.md": "Local memory.",
        })
        self.store.activate("owen", configuration_id=self.config["id"], revision_id=self.source["id"])
        self.store.activate("kestrel", revision_id=self.target["id"])
        self.server = LocalServer(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.app.close()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.environment.stop()
        self.temp.cleanup()

    def request(self, path, data=None, *, authenticated=True, origin=None, headers_only=False):
        headers = {"Connection": "close"}
        if authenticated:
            headers["Authorization"] = "Bearer " + self.app.token
        if origin:
            headers["Origin"] = origin
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = b"" if headers_only else json.dumps(data).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request("POST" if data is not None else "GET", path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def publish(self):
        status, state = self.request("/api/capabilities/publish", {
            "profile_id": "owen", "configuration_id": self.config["id"],
            "revision_id": self.source["id"], "name": "Source reconciliation",
            "paths": ["guardrails.md", "scripts/check.py"], "kind": "workflow",
            "portability": "model-sensitive", "why": "Conflicting source documents.",
        })
        return status, state

    def test_catalog_defaults_off_and_api_cannot_bypass_it(self):
        status, state = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertFalse(state["capabilities"]["enabled"])
        self.assertEqual(state["capabilities"]["entries"], [])
        self.assertEqual(self.publish()[0], 400)
        status, _ = self.request("/api/capabilities/fork", {
            "capability_id": "a" * 64, "target_profile_id": "kestrel",
        })
        self.assertEqual(status, 400)
        self.assertEqual(len(self.store.get("kestrel")["revisions"]), 1)

    def test_settings_changes_require_authentication_and_same_origin(self):
        for options in ({"authenticated": False}, {"origin": "https://another-site.invalid"}):
            # Guards reject before reading a body. Send headers only so closing
            # with unread payload does not turn the response into a Windows RST.
            status, _ = self.request("/api/capabilities/settings", {"enabled": True},
                                    headers_only=True, **options)
            self.assertIn(status, (401, 403))
        self.assertFalse(self.app.capabilities.state()["enabled"])

    def test_publish_fork_modify_and_disable_preserve_independent_profiles(self):
        self.assertEqual(self.request("/api/capabilities/settings", {"enabled": True})[0], 200)
        status, catalog = self.publish()
        self.assertEqual(status, 200, catalog)
        entry = catalog["entries"][0]
        self.assertIsNone(catalog_snapshot(self.root, "kestrel"))
        status, full = self.request("/api/capabilities/" + entry["id"])
        self.assertEqual(status, 200)
        self.assertIn("must not be executed", full["files"]["scripts/check.py"])
        body = {"capability_id": entry["id"], "target_profile_id": "kestrel"}
        self.assertEqual(self.request("/api/capabilities/fork", body)[0], 400)
        body.update(replace_paths=["guardrails.md"],
                    replace_files={"guardrails.md": "Adapted for Kestrel's workflow."})
        status, result = self.request("/api/capabilities/fork", body)
        self.assertEqual(status, 200, result)
        profile = result["profile"]
        revision = next(row for row in profile["revisions"] if row["id"] == result["revision_id"])
        self.assertEqual(profile["active_revision_id"], self.target["id"])
        self.assertEqual(revision["tree"]["LongTermMEMORY.md"], "Local memory.")
        self.assertEqual(revision["tree"]["guardrails.md"], body["replace_files"]["guardrails.md"])
        self.assertEqual(revision["evidence"][0]["local_validation"], "unvalidated")
        self.assertEqual(self.store.get("owen")["active_revision_id"], self.source["id"])
        self.assertEqual(len(result["catalog"]["entries"][0]["forks"]), 1)
        self.assertEqual(self.request("/api/capabilities/access", {"profile_id": "kestrel", "enabled": True})[0], 200)
        self.assertIsNotNone(catalog_snapshot(self.root, "kestrel"))
        self.assertEqual(self.request("/api/capabilities/settings", {"enabled": False})[0], 200)
        self.assertIsNone(catalog_snapshot(self.root, "kestrel"))
        self.assertEqual(self.request("/api/capabilities/" + entry["id"])[0], 200)
        self.assertEqual(self.request("/api/capabilities/fork", body)[0], 400)
        self.assertIsNone(self.app.jobs.active_id)

    def test_unknown_files_and_wrong_field_types_do_not_publish_or_enable(self):
        for bad in ("true", 1, None, {}):
            self.assertEqual(self.request("/api/capabilities/settings", {"enabled": bad})[0], 400)
        self.request("/api/capabilities/settings", {"enabled": True})
        status, _ = self.request("/api/capabilities/publish", {
            "profile_id": "owen", "configuration_id": self.config["id"],
            "revision_id": self.source["id"], "name": "Invalid file",
            "paths": ["../outside.py"], "kind": "script", "portability": "unknown",
        })
        self.assertEqual(status, 400)
        self.assertEqual(self.app.capabilities.state()["entries"], [])
