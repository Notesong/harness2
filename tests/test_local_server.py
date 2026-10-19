"""Managed lifecycle proofs: a real HTTP child stands in for llama.cpp only.

The adapter substitutes Python for the executable, retaining the launcher's real
process ownership, environment, pipe logging, HTTP readiness, and cleanup paths.
No model weights are loaded and no completion endpoint is ever called.
"""

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import local_server, processes

FAKE_SERVER = r'''
import http.server,json,os,subprocess,sys,time
from pathlib import Path
args=sys.argv[1:]
port=int(args[args.index('--port')+1])
alias=args[args.index('--alias')+1]
if os.environ.get('FAKE_GRANDCHILD'):
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
    target=Path(os.environ['FAKE_GRANDCHILD'])
    temporary=target.with_suffix('.tmp')
    temporary.write_text(str(child.pid));os.replace(temporary,target)
token=os.environ['LLAMA_API_KEY']
sys.stdout.write('credential='+token[:8]);sys.stdout.flush();time.sleep(.03)
sys.stdout.write(token[8:]+'\n');sys.stdout.flush()
if os.environ.get('FAKE_EXIT'):
    sys.exit(7)
time.sleep(float(os.environ.get('FAKE_DELAY','0')))
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get('Authorization')!='Bearer '+token:
            self.send_response(401);self.end_headers();return
        if self.path!='/v1/models':
            self.send_response(404);self.end_headers();return
        body=json.dumps({'data':[{'id':os.environ.get('FAKE_ALIAS',alias)}]}).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass
http.server.HTTPServer(('127.0.0.1',port),Handler).serve_forever()
'''


def available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def windows_kernel():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def process_alive(pid):
    if os.name == "nt":
        kernel = windows_kernel()
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class ManagedLocalServerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="owned local résumé ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.weights = self.root / "model's 日本語.gguf"
        self.weights.write_bytes(b"test fixture; never a model")
        self.environment = dict(os.environ)
        self.environment.update({
            "HARNESS2_LOCAL_RUNTIME": "managed",
            "HARNESS2_LOCAL_EXECUTABLE": sys.executable,
            "HARNESS2_LOCAL_WEIGHTS": str(self.weights),
            "HARNESS2_LOCAL_BASE_URL": f"http://127.0.0.1:{available_port()}/v1",
            "HARNESS2_LOCAL_MODEL": "fixture-model",
            "HARNESS2_LOCAL_CONTEXT": "16384",
            "HARNESS2_LOCAL_OUTPUT": "2048",
        })
        self.log = self.root / "logs" / "server.log"
        self.launches = []

    def manager(self, **kwargs):
        manager = local_server.ManagedLocalServer(
            self.environment, root=self.root, log_path=self.log, readiness_timeout=3, **kwargs)
        self.addCleanup(manager.close)
        return manager

    def real_child_adapter(self):
        original = processes.start

        def launch(args, **kwargs):
            self.launches.append((args, dict(kwargs)))
            return original([sys.executable, "-u", "-c", FAKE_SERVER, *args[1:]], **kwargs)

        return patch.object(local_server.processes, "start", side_effect=launch)

    def assert_owned_stopped(self, manager):
        """Check the retained process identity, not a fresh lookup by numeric PID.

        Keep the root check immediate: close() promises to wait for its owned
        launcher. A bounded descendant wait below covers asynchronous Job teardown,
        but must not hide a root process that is still running when close returns.
        """
        process = manager._process
        self.assertIsNotNone(process)
        code = process.poll()
        if os.name != "nt":
            self.assertIsNotNone(code)
            return
        import ctypes

        kernel = windows_kernel()
        original_wait = kernel.WaitForSingleObject(int(process._handle), 0)
        lookup = kernel.OpenProcess(0x00100000, False, process.pid)
        lookup_error = ctypes.get_last_error() if not lookup else None
        try:
            lookup_wait = kernel.WaitForSingleObject(lookup, 0) if lookup else None
        finally:
            if lookup:
                kernel.CloseHandle(lookup)
        diagnostic = (f"pid={process.pid}, returncode={code}, original_handle={int(process._handle)}, "
                      f"original_wait={original_wait}, reopened_pid_wait={lookup_wait}, "
                      f"open_error={lookup_error}")
        self.assertIsNotNone(code, diagnostic)
        self.assertEqual(original_wait, 0, diagnostic)  # WAIT_OBJECT_0, without a grace period.

    def retain_descendant(self, pid):
        """Hold the known live descendant's identity through Job Object shutdown."""
        import ctypes

        kernel = windows_kernel()
        handle = kernel.OpenProcess(0x00100000, False, pid)
        self.assertTrue(handle, f"Cannot retain descendant {pid}: {ctypes.get_last_error()}")
        self.addCleanup(kernel.CloseHandle, handle)
        self.assertEqual(kernel.WaitForSingleObject(handle, 0), 258)  # WAIT_TIMEOUT: alive.
        return handle

    def assert_descendant_stopped(self, handle, pid):
        # Windows terminates Job descendants asynchronously. Hold the original
        # handle and allow bounded completion; a leaked 60-second fixture fails.
        observed = windows_kernel().WaitForSingleObject(handle, 5000)
        self.assertEqual(observed, 0, f"Owned descendant {pid} not stopped: wait={observed}")

    def test_external_is_a_copied_noop_even_with_invalid_managed_settings(self):
        source = {"HARNESS2_LOCAL_RUNTIME": "external", "HARNESS2_LOCAL_WEIGHTS": "missing",
                  "HARNESS2_LOCAL_BASE_URL": "invalid", "HARNESS2_LOCAL_API_KEY": "original"}
        manager = local_server.ManagedLocalServer(source, root=self.root, log_path=self.log)
        self.addCleanup(manager.close)
        with patch.object(processes, "start") as launch, patch.object(socket, "socket") as socket_:
            self.assertIs(manager.start(), manager)
            self.assertIs(manager.start(), manager)
            manager.close()
            manager.close()
        launch.assert_not_called()
        socket_.assert_not_called()
        self.assertIsNot(manager.environment, source)
        self.assertEqual(manager.environment, source)
        self.assertFalse(manager.started)
        self.assertIsNone(manager.pid)
        self.assertFalse(self.log.exists())

    def test_validator_accepts_unicode_and_apostrophe_paths_without_opening_them(self):
        with patch.object(Path, "open", side_effect=AssertionError("must not read weights")), \
                patch.object(processes, "start") as launch, patch.object(socket, "socket") as socket_:
            settings = local_server.validate_managed_settings(self.environment)
        self.assertEqual(settings.weights, self.weights)
        self.assertEqual(settings.profile.context, 16384)
        self.assertEqual(settings.profile.output, 2048)
        launch.assert_not_called()
        socket_.assert_not_called()

    def test_invalid_settings_fail_before_log_or_launch(self):
        invalid = [
            ("HARNESS2_LOCAL_RUNTIME", "automatic"),
            ("HARNESS2_LOCAL_BASE_URL", "http://localhost:8087/v1"),
            ("HARNESS2_LOCAL_BASE_URL", "https://127.0.0.1:8087/v1"),
            ("HARNESS2_LOCAL_BASE_URL", "http://127.0.0.1/v1"),
            ("HARNESS2_LOCAL_BASE_URL", "http://127.0.0.1:0/v1"),
            ("HARNESS2_LOCAL_BASE_URL", "http://127.0.0.1:8087/other"),
            ("HARNESS2_LOCAL_BASE_URL", "http://secret@127.0.0.1:8087/v1"),
            ("HARNESS2_LOCAL_EXECUTABLE", "relative.exe"),
            ("HARNESS2_LOCAL_WEIGHTS", str(self.root)),
            ("HARNESS2_LOCAL_WEIGHTS", str(self.root / "missing.gguf")),
            ("HARNESS2_LOCAL_WEIGHTS", str(self.weights) + '"'),
            ("HARNESS2_LOCAL_WEIGHTS", str(self.weights) + "\n"),
            ("HARNESS2_LOCAL_CONTEXT", "2048"),
        ]
        with patch.object(processes, "start") as launch:
            for key, value in invalid:
                environment = dict(self.environment, **{key: value})
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    manager = local_server.ManagedLocalServer(
                        environment, root=self.root, log_path=self.log)
                    manager.start()
        launch.assert_not_called()
        self.assertFalse(self.log.exists())

    def test_real_readiness_secret_is_ephemeral_and_recipe_is_fixed(self):
        self.environment.update({"HARNESS2_LOCAL_API_KEY": "saved-local",
                                 "HARNESS2_JUDGE_API_KEY": "saved-judge",
                                 "LLAMA_API_KEY": "inherited-key",
                                 "LLAMA_ARG_API_KEY_FILE": "unowned-key-file",
                                 "LLAMA_ARG_HOST": "0.0.0.0"})
        before = dict(self.environment)
        process_environment = dict(os.environ)
        manager = self.manager()
        with self.real_child_adapter():
            with manager as resource:
                self.assertIs(resource, manager)
                token = manager.environment["HARNESS2_LOCAL_API_KEY"]
                self.assertEqual(manager.environment["HARNESS2_JUDGE_API_KEY"], token)
                self.assertNotIn(token, json.dumps(manager.metadata))
                self.assertGreater(len(token), 30)
                self.assertTrue(manager.started)
                self.assertTrue(process_alive(manager.pid))
                self.assertIs(manager.start(), manager)
                args, options = self.launches[0]
                self.assertNotIn(token, " ".join(args))
                self.assertEqual(options["env"]["LLAMA_API_KEY"], token)
                self.assertEqual([key for key in options["env"] if key.startswith("LLAMA_")],
                                 ["LLAMA_API_KEY"])
                for option, value in (("--ctx-size", "16384"), ("--parallel", "1"),
                                      ("--host", "127.0.0.1"), ("--cache-type-k", "q8_0"),
                                      ("--cache-type-v", "q8_0"), ("--flash-attn", "on"),
                                      ("--fit", "on"), ("--fit-target", "1536"),
                                      ("--batch-size", "512"), ("--ubatch-size", "128"),
                                      ("--reasoning", "off")):
                    self.assertEqual(args[args.index(option) + 1], value)
                self.assertTrue({"--offline", "--no-webui", "--jinja"} <= set(args))
                if os.name == "nt":
                    self.assertTrue(options["creationflags"] & subprocess.CREATE_NO_WINDOW)
            manager.close()
        self.assertEqual(len(self.launches), 1)
        self.assertFalse(manager.started)
        self.assert_owned_stopped(manager)
        self.assertEqual(self.environment, before)
        self.assertEqual(manager.environment, before)
        self.assertEqual(dict(os.environ), process_environment)
        log = self.log.read_text(encoding="utf-8")
        self.assertNotIn(token, log)
        self.assertIn("credential=[redacted]", log)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            manager.start()

    def test_occupied_endpoint_is_not_probed_reused_or_stopped(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            self.environment["HARNESS2_LOCAL_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
            manager = self.manager()
            with patch.object(processes, "start") as launch, \
                    patch.object(local_server.ManagedLocalServer, "_ready") as ready:
                with self.assertRaisesRegex(RuntimeError, "occupied or unavailable"):
                    manager.start()
                manager.close()
            launch.assert_not_called()
            ready.assert_not_called()
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                connection, _ = listener.accept()
                connection.close()
        self.assertFalse(self.log.exists())

    @unittest.skipUnless(os.name == "nt", "asserts Windows Job Object descendant termination")
    def test_real_owned_grandchild_is_terminated_on_context_exception(self):
        descendant = self.root / "grandchild.pid"
        self.environment["FAKE_GRANDCHILD"] = str(descendant)
        manager = self.manager()
        with self.real_child_adapter(), self.assertRaisesRegex(ValueError, "owner failed"):
            with manager:
                child_pid = int(descendant.read_text())
                self.assertTrue(process_alive(child_pid))
                child_handle = self.retain_descendant(child_pid)
                raise ValueError("owner failed")
        self.assert_owned_stopped(manager)
        self.assert_descendant_stopped(child_handle, child_pid)

    def test_cancelled_before_start_never_launches(self):
        manager = self.manager(cancelled=lambda: True)
        with patch.object(processes, "start") as launch:
            with self.assertRaises(local_server.LocalServerCancelled):
                manager.start()
        launch.assert_not_called()
        self.assertFalse(self.log.exists())

    def test_cancel_during_real_child_startup_closes_tree(self):
        event = threading.Event()
        self.environment["FAKE_DELAY"] = "60"
        descendant = self.root / "cancelled-grandchild.pid"
        if os.name == "nt":
            self.environment["FAKE_GRANDCHILD"] = str(descendant)
        manager = self.manager(cancelled=event.is_set)
        errors = []

        def start():
            try:
                manager.start()
            except BaseException as exc:
                errors.append(exc)

        with self.real_child_adapter():
            worker = threading.Thread(target=start)
            worker.start()
            deadline = time.monotonic() + 2
            while manager.pid is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(manager.pid)
            if os.name == "nt":
                while not descendant.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(descendant.exists())
                child_pid = int(descendant.read_text())
                self.assertTrue(process_alive(child_pid))
                child_handle = self.retain_descendant(child_pid)
            event.set()
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], local_server.LocalServerCancelled)
        self.assert_owned_stopped(manager)
        if os.name == "nt":
            self.assert_descendant_stopped(child_handle, child_pid)

    def test_concurrent_starts_share_one_child(self):
        manager = self.manager()
        results = []
        errors = []

        def start():
            try:
                results.append(manager.start())
            except BaseException as exc:
                errors.append(exc)

        with self.real_child_adapter():
            workers = [threading.Thread(target=start) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=4)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(errors, [])
            self.assertEqual(results, [manager, manager])
            self.assertEqual(len(self.launches), 1)
            manager.close()
        self.assert_owned_stopped(manager)

    def test_launch_exception_closes_log_and_preserves_original_environment(self):
        self.environment["HARNESS2_LOCAL_API_KEY"] = "saved"
        manager = self.manager()
        error = OSError("fixture launch failure")
        with patch.object(processes, "start", side_effect=error):
            with self.assertRaises(OSError) as raised:
                manager.start()
        self.assertIs(raised.exception, error)
        self.assertIsNone(manager.pid)
        self.assertEqual(manager.environment, self.environment)
        self.assertTrue(manager._log.closed)
        manager.close()

    def test_close_from_another_thread_interrupts_readiness(self):
        self.environment["FAKE_DELAY"] = "60"
        manager = self.manager()
        errors = []

        def start():
            try:
                manager.start()
            except BaseException as exc:
                errors.append(exc)

        with self.real_child_adapter():
            worker = threading.Thread(target=start)
            worker.start()
            deadline = time.monotonic() + 2
            while manager.pid is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(manager.pid)
            manager.close()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], local_server.LocalServerCancelled)
        self.assert_owned_stopped(manager)

    def test_real_child_exit_and_wrong_alias_fail_and_are_cleaned(self):
        for extra, message in (({"FAKE_EXIT": "1"}, "exit 7"),
                               ({"FAKE_ALIAS": "another-model"}, "deadline")):
            with self.subTest(extra=extra):
                environment = dict(self.environment, **extra)
                environment["HARNESS2_LOCAL_BASE_URL"] = f"http://127.0.0.1:{available_port()}/v1"
                manager = local_server.ManagedLocalServer(
                    environment, root=self.root, log_path=self.log, readiness_timeout=0.7)
                self.addCleanup(manager.close)
                with self.real_child_adapter(), self.assertRaisesRegex(
                        (RuntimeError, TimeoutError), message):
                    manager.start()
                self.assertFalse(manager.started)
                self.assert_owned_stopped(manager)
                self.assertNotIn("HARNESS2_JUDGE_API_KEY", {
                    key: value for key, value in manager.environment.items()
                    if key not in environment})

    def test_readiness_never_follows_redirects_or_accepts_bad_responses(self):
        settings = local_server.validate_managed_settings(self.environment)
        bodies = ((200, b'{"data":[{"id":"fixture-model"}]}', True),
                  (200, b'{"data":[{"id":"other"}]}', False),
                  (200, b"not json", False), (200, b"[]", False),
                  (401, b"", False), (302, b"", False))
        for status, body, expected in bodies:
            with self.subTest(status=status, body=body), \
                    patch.object(http.client, "HTTPConnection") as factory:
                connection = factory.return_value
                response = connection.getresponse.return_value
                response.status = status
                response.read.return_value = body
                self.assertIs(local_server.ManagedLocalServer._ready(settings, "secret", .1),
                              expected)
                self.assertEqual(connection.request.call_count, 1)
                connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
