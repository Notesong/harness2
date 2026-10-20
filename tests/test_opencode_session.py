# SPDX-License-Identifier: Apache-2.0
"""Real HTTP child ownership and API boundaries; no inference or model weights.

Python's HTTP server substitutes for the OpenCode executable in these unit
integration tests. The owned launch, authenticated HTTP, native-ID correlation,
bounded public projection, and process cleanup are real. A separate local proof
exercises the pinned Bun/OpenCode checkout against a scripted model endpoint.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import opencode_session as transport
from harness2 import processes

FAKE = r'''
import base64,http.server,json,os,sys,time
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
args=sys.argv[1:];port=int(args[args.index('--port')+1])
if os.environ.get('FAKE_EXIT'):sys.exit(7)
secret=base64.b64encode(('opencode:'+os.environ['OPENCODE_SERVER_PASSWORD']).encode()).decode()
session='ses_fixture00000001';rows=[];busy=False
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):self.handle_request()
    def do_POST(self):self.handle_request()
    def handle_request(self):
        global busy
        uri=urlsplit(self.path);query=parse_qs(uri.query);body={}
        if self.command=='POST':body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
        with open(os.environ['FAKE_REQUEST_LOG'],'a',encoding='utf-8') as log:
            log.write(json.dumps({'method':self.command,'path':uri.path,'query':query,'body':body})+'\n')
        if self.headers.get('Authorization')!='Basic '+secret:self.send_response(401);self.end_headers();return
        code=200;result={}
        if uri.path=='/global/health':result={'healthy':not os.environ.get('FAKE_UNHEALTHY')}
        elif self.command=='POST' and uri.path=='/session':result={'id':session,'directory':query['directory'][0]}
        elif self.command=='GET' and uri.path=='/session/'+session:result={'id':session,'directory':query['directory'][0]}
        elif uri.path=='/session/status':result={session:{'type':'busy' if busy else 'idle'}}
        elif uri.path.endswith('/abort'):busy=False;result=True
        elif uri.path.endswith('/prompt_async'):
            if not os.environ.get('FAKE_DROP_MESSAGE'):
                rows.append({'info':{'id':'msg_native'+str(len(rows)+1),'sessionID':session,'role':'user'},'parts':body['parts']})
            busy=True;code=204;result=None
        elif uri.path.endswith('/message'):
            if os.environ.get('FAKE_REDIRECT'):code=302;result={}
            elif os.environ.get('FAKE_BAD_JSON'):self.send_response(200);self.end_headers();self.wfile.write(b'not JSON');return
            elif os.environ.get('FAKE_LARGE'):self.send_response(200);self.end_headers();self.wfile.write(b'x'*1024);return
            elif os.environ.get('FAKE_ROWS'):result=json.loads(os.environ['FAKE_ROWS'])
            else:result=rows
        else:code=404;result={'error':'not found'}
        self.send_response(code);self.send_header('Content-Type','application/json');self.end_headers()
        if result is not None:self.wfile.write(json.dumps(result).encode())
    def log_message(self,*args):pass
http.server.HTTPServer(('127.0.0.1',port),Handler).serve_forever()
'''


class OpenCodeSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native conversation résumé ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / "opencode"
        for name in ("packages/opencode/src/index.ts", "packages/plugin/src/tool.ts"):
            path = self.checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("// Fake executable fixture, never imported.\n", encoding="utf-8")
        self.enterContext(patch.object(transport.config, "BUN", Path(sys.executable)))
        self.enterContext(patch.object(transport.config, "OPENCODE_DIR", self.checkout))
        self.environment = {
            **os.environ, "HARNESS2_LOCAL_BASE_URL": "http://127.0.0.1:8087/v1",
            "HARNESS2_LOCAL_MODEL": "fixture-model", "FAKE_REQUEST_LOG": str(self.root / "requests.jsonl"),
        }
        self.launches = []

    def session(self, **kwargs):
        instance = transport.OpenCodeSession(self.root / "workspace", self.environment,
            runtime_dir=self.root / "runtime", model="local/fixture-model", **kwargs)
        self.addCleanup(instance.close)
        return instance

    def fake_executable(self):
        original = processes.start

        def launch(args, **kwargs):
            self.launches.append((list(args), {**kwargs, "env": dict(kwargs["env"])}))
            return original([sys.executable, "-u", "-c", FAKE, *args[1:]], **kwargs)

        return patch.object(transport.processes, "start", side_effect=launch)

    def requests(self):
        return [json.loads(line) for line in (self.root / "requests.jsonl").read_text(encoding="utf-8").splitlines()]

    def assert_stopped(self, instance):
        self.assertIsNotNone(instance._process.poll())
        if os.name == "nt":
            import _winapi

            self.assertEqual(_winapi.WaitForSingleObject(instance._process._handle, 0), _winapi.WAIT_OBJECT_0)

    def test_real_http_start_attach_authentication_and_owned_cleanup(self):
        before = dict(self.environment)
        instance = self.session(label="Specific conversation")
        with self.fake_executable():
            self.assertIs(instance.start(), instance)
            self.assertIs(instance.start(), instance)
            self.assertEqual(instance.session_id, "ses_fixture00000001")
            self.assertEqual(instance.status(), "idle")
            original = ["bun", "run", "src/index.ts", "run", "Actual task", "--model", "local/fixture-model"]
            attached = instance.attach(original)
            self.assertEqual(attached[:len(original)], original)
            self.assertEqual(attached[-4:], ["--attach", instance.base_url, "--session", instance.session_id])
            self.assertNotIn(instance.environment["OPENCODE_SERVER_PASSWORD"], " ".join(attached))
            self.assertEqual(self.launches[0][0][-6:], ["--hostname", "127.0.0.1", "--port", str(instance._port), "--mdns", "false"])
            self.assertEqual(len(self.launches), 1)
            self.assertTrue(all(row["query"]["directory"] == [str(instance.workspace)] for row in self.requests()))
            self.assertEqual(self.requests()[1]["body"]["title"], "Specific conversation")
            instance.close()
        self.assert_stopped(instance)
        self.assertNotIn("OPENCODE_SERVER_PASSWORD", instance.environment)
        self.assertEqual(self.environment, before)
        with self.assertRaises(transport.SessionTransportError):
            instance.start()

    def test_native_delivery_is_confirmed_by_private_correlation_metadata(self):
        instance = self.session()
        with self.fake_executable(), instance:
            first = instance.send("hello")
            second = instance.send("hello")
            self.assertEqual((first, second), ("msg_native1", "msg_native2"))
            self.assertEqual(instance.status(), "busy")
            calls = [row for row in self.requests() if row["path"].endswith("prompt_async")]
            self.assertEqual(len(calls), 2)
            self.assertNotIn("messageID", calls[0]["body"])
            self.assertEqual(calls[0]["body"]["model"], {"providerID": "local", "modelID": "fixture-model"})
            self.assertNotEqual(calls[0]["body"]["parts"][0]["metadata"], calls[1]["body"]["parts"][0]["metadata"])
            messages = instance.messages()
            self.assertEqual(messages[0]["parts"][0]["text"], "hello")
            self.assertNotIn("metadata", str(messages))
            instance.abort()
            self.assertEqual(instance.status(), "idle")

    def test_public_projection_keeps_tool_identity_without_private_payloads(self):
        self.environment["FAKE_ROWS"] = json.dumps([{
            "info": {"id": "msg_public1", "sessionID": "ses_fixture00000001", "role": "assistant",
                     "parentID": "msg_parent1", "apiKey": "PRIVATE_KEY", "error": {"message": "PRIVATE_ERROR"}},
            "parts": [{"id": "prt_public1", "type": "text", "text": "Public reply", "metadata": {"secret": "PRIVATE_META"}},
                      {"type": "reasoning", "text": "PRIVATE_REASONING"},
                      {"id": "prt_tool1", "type": "tool", "tool": "read", "state": {"input": "PRIVATE_ARGS", "output": "PRIVATE_TOOL_OUTPUT"}},
                      {"type": "text", "text": "PRIVATE_IGNORED", "ignored": True}],
        }])
        instance = self.session()
        with self.fake_executable(), instance:
            messages = instance.messages()
        self.assertEqual(messages[0]["parts"][0]["text"], "Public reply")
        self.assertEqual(messages[0]["info"]["parentID"], "msg_parent1")
        self.assertTrue(messages[0]["info"]["has_error"])
        self.assertEqual(messages[0]["parts"][1], {"id": "prt_tool1", "type": "tool", "tool": "read"})
        self.assertNotIn("PRIVATE_", json.dumps(messages))

    def test_resume_requires_exact_retained_session_and_never_creates_replacement(self):
        instance = self.session(resume_session_id="ses_fixture00000001")
        with self.fake_executable(), instance:
            self.assertEqual(instance.session_id, "ses_fixture00000001")
        self.assertFalse(any(row["method"] == "POST" and row["path"] == "/session" for row in self.requests()))
        missing = self.session(resume_session_id="ses_missing")
        with self.fake_executable(), self.assertRaises(transport.SessionTransportError):
            missing.start()
        self.assert_stopped(missing)

    def test_plugin_preserves_existing_configuration_and_uses_expected_protocol_paths(self):
        self.environment["OPENCODE_CONFIG_CONTENT"] = json.dumps({"plugin": ["file:///existing.ts"],
            "tools": {"webfetch": False}, "agent": {"custom": {"description": "retained"}}})
        instance = self.session(recovery_file=self.root / "recovery.json", outbox=self.root / "outbox")
        with self.fake_executable(), instance:
            configuration = json.loads(instance.environment["OPENCODE_CONFIG_CONTENT"])
            self.assertEqual(configuration["plugin"][0], "file:///existing.ts")
            self.assertFalse(configuration["tools"]["webfetch"])
            self.assertEqual(configuration["agent"]["custom"]["description"], "retained")
            self.assertEqual(len(configuration["plugin"]), 2)
            plugin = (instance.runtime_dir / "conversation-plugin.ts").read_text(encoding="utf-8")
            self.assertIn('"experimental.chat.system.transform"', plugin)
            self.assertIn('"experimental.session.compacting"', plugin)
            self.assertIn('s.enum(["message", "decision", "checkpoint"])', plugin)
            self.assertIn('join(outbox, "requests")', plugin)
            self.assertIn('join(outbox, "replies")', plugin)
            self.assertTrue((self.root / "outbox/requests").is_dir())
            self.assertEqual(json.loads((instance.runtime_dir / "session.json").read_text())["session_id"], instance.session_id)

    def test_unconfirmed_acceptance_is_not_retried_or_reported_as_delivered(self):
        self.environment["FAKE_DROP_MESSAGE"] = "1"
        instance = self.session()
        with self.fake_executable(), instance, patch.object(transport, "DELIVERY_TIMEOUT", 0.15):
            with self.assertRaisesRegex(transport.SessionTransportError, "unconfirmed"):
                instance.send("A uniquely submitted message")
        self.assertEqual(sum(row["path"].endswith("prompt_async") for row in self.requests()), 1)

    def test_deep_private_runtime_files_use_native_windows_long_paths(self):
        deep = self.root / ("private-" + "x" * 100) / ("worker-" + "y" * 100)
        self.assertTrue(deep.is_relative_to(self.root))
        self.addCleanup(lambda: shutil.rmtree(transport.config.long_path(deep.parent), ignore_errors=True))
        instance = transport.OpenCodeSession(self.root / "workspace", self.environment,
            runtime_dir=deep, model="local/fixture-model", outbox=deep / "outbox")
        self.addCleanup(instance.close)
        with self.fake_executable(), instance:
            self.assertEqual(instance.send("Deep runtime test"), "msg_native1")
            self.assertEqual(json.loads(transport.config.long_path(deep / "session.json").read_text())["session_id"], instance.session_id)
            self.assertTrue(transport.config.long_path(deep / "conversation-plugin.ts").is_file())
            self.assertTrue(transport.config.long_path(deep / "outbox/replies").is_dir())
            self.assertNotIn("\\\\?\\", instance.environment["HARNESS2_CONVERSATION_OUTBOX"])
        self.assert_stopped(instance)

    def test_startup_failure_and_close_during_readiness_stop_real_child(self):
        self.environment["FAKE_EXIT"] = "1"
        exited = self.session()
        with self.fake_executable(), self.assertRaisesRegex(transport.SessionTransportError, "exited"):
            exited.start()
        self.assert_stopped(exited)
        del self.environment["FAKE_EXIT"]
        self.environment["FAKE_UNHEALTHY"] = "1"
        instance = self.session()
        errors = []

        def start():
            try:
                instance.start()
            except BaseException as exc:
                errors.append(exc)

        with self.fake_executable():
            worker = threading.Thread(target=start)
            worker.start()
            deadline = time.monotonic() + 3
            while instance._process is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(instance._process)
            instance.close()
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assert_stopped(instance)

    def test_redirect_and_oversized_or_invalid_json_are_rejected(self):
        for flag, message in (("FAKE_REDIRECT", "HTTP 302"), ("FAKE_BAD_JSON", "invalid JSON"),
                              ("FAKE_LARGE", "size limit")):
            with self.subTest(flag=flag):
                self.environment[flag] = "1"
                instance = self.session()
                with self.fake_executable(), instance:
                    with patch.object(transport, "MAX_RESPONSE", 128):
                        with self.assertRaisesRegex(transport.SessionTransportError, message):
                            instance._request("GET", f"/session/{instance.session_id}/message", limit=128)
                del self.environment[flag]

    def test_other_session_messages_are_rejected(self):
        self.environment["FAKE_ROWS"] = json.dumps([{"info": {"id": "msg_foreign", "sessionID": "ses_other", "role": "user"}, "parts": []}])
        instance = self.session()
        with self.fake_executable(), instance, self.assertRaisesRegex(transport.SessionTransportError, "another session"):
            instance.messages()

    def test_invalid_model_and_session_override_fail_before_launch(self):
        with self.assertRaises(ValueError):
            transport.OpenCodeSession(self.root / "workspace", self.environment,
                runtime_dir=self.root / "runtime", model="google-vertex/cloud-model")
        instance = self.session()
        with self.fake_executable(), instance:
            for command in (["run", "--session", "ses_other"], ["run", "--attach", "http://elsewhere/"], ["run", "--continue"],
                            ["run", "--session=ses_other"], ["run", "--fork"], ["run", "-s", "ses_other"],
                            ["run", "--model", "google-vertex/cloud"], ["run", "--model=local/other"]):
                with self.subTest(command=command), self.assertRaises(ValueError):
                    instance.attach(command)


if __name__ == "__main__":
    unittest.main()
