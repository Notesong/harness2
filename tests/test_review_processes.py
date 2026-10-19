"""Hostile regressions for native process ownership and portable filesystem writes."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from harness2 import config, locking, processes
from harness2.core import context, runmark, schedule, tree
from harness2.core.spec import RunView
from harness2.demo import DemoAdapter


class FilesystemReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="review résumé ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.surface = DemoAdapter(self.root).substrate.surface()

    def test_windows_path_aliases_are_rejected_on_every_platform(self):
        for name in ("C:outside.txt", "C:/outside.txt", "\\\\server\\share\\file",
                     "scripts/x.py:stream", "scripts/NUL.py", "scripts/x.py.",
                     "scripts/x.py ", "scripts/a\x00.py", "./", "."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                tree.normalize_rel(name)

    def test_invalid_tree_does_not_delete_existing_harness(self):
        dest = self.root / "harness"
        tree.write_tree(self.surface, {"systemprompt.md": "preserve"}, dest)
        with self.assertRaises(ValueError):
            tree.write_tree(self.surface, {"../escape": "bad"}, dest)
        self.assertEqual((dest / "systemprompt.md").read_text(encoding="utf-8"), "preserve")

    def test_fingerprint_order_is_platform_independent(self):
        files = {"scripts/Z.py": b"upper", "scripts/a.py": b"lower"}
        root = self.root / "fingerprint"
        for name, body in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        expected = hashlib.sha1()
        for name in sorted(files):
            expected.update(name.encode("utf-8") + b"\0" + files[name] + b"\0")
        self.assertEqual(runmark.fingerprint(root), expected.hexdigest()[:16])

    def test_case_and_separator_aliases_cannot_silently_overwrite(self):
        for candidate in (
            {"scripts/Foo.py": "first", "scripts/foo.py": "second"},
            {"scripts/foo.py": "first", "scripts\\foo.py": "second"},
            {"scripts/foo.py": "first", "scripts/./foo.py": "second"},
        ):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                tree.write_tree(self.surface, candidate, self.root / "harness")

    def test_mark_writers_in_same_process_use_independent_temporary_files(self):
        # Synchronize replacement so every writer has finished writing its temp file.
        barrier = threading.Barrier(8)
        replacement_guard = threading.Lock()
        writer_state = threading.local()
        original = os.replace

        def replace(source, target):
            if not getattr(writer_state, "started", False):
                writer_state.started = True
                barrier.wait(timeout=10)
            with replacement_guard:
                return original(source, target)

        with patch.object(locking.os, "replace", side_effect=replace):
            with ThreadPoolExecutor(max_workers=8) as pool:
                marks = list(pool.map(
                    lambda index: runmark.mark_complete(self.root, run_id=f"run-{index}"),
                    range(8),
                ))
        self.assertEqual(len(set(marks)), 1)
        self.assertTrue(runmark.is_complete(self.root))
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_structurally_invalid_marker_and_summary_are_cache_misses(self):
        (self.root / runmark.MARK).write_text('["bad"]', encoding="utf-8")
        self.assertFalse(runmark.is_complete(self.root))
        path = self.root / "summary.json"
        path.write_text('["bad"]', encoding="utf-8")
        result = schedule.TaskResult("résumé", "domain", 1, status="partial")
        schedule._write_summary(path, result)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["task_id"], "résumé")

    def test_atomic_replace_retries_only_windows_access_and_sharing_conflicts(self):
        original = os.replace
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                source, target = self.root / "new", self.root / "old"
                source.write_text("new", encoding="utf-8")
                target.write_text("old", encoding="utf-8")
                error = PermissionError("temporary conflict")
                error.winerror = code
                calls = []

                def replace(src, dst):
                    calls.append((src, dst))
                    if len(calls) == 1:
                        raise error
                    return original(src, dst)

                with patch.object(locking.os, "name", "nt"), \
                        patch.object(locking.os, "replace", side_effect=replace), \
                        patch.object(locking.time, "sleep") as sleep:
                    locking.atomic_replace(source, target)
                self.assertEqual(len(calls), 2)
                sleep.assert_called_once()
                self.assertEqual(target.read_text(encoding="utf-8"), "new")
                self.assertFalse(source.exists())

    def test_atomic_replace_permanent_conflict_is_bounded_and_preserves_files(self):
        source, target = self.root / "new", self.root / "old"
        source.write_text("new", encoding="utf-8")
        target.write_text("old", encoding="utf-8")
        error = PermissionError("persistent conflict")
        error.winerror = 32
        with patch.object(locking.os, "name", "nt"), \
                patch.object(locking.os, "replace", side_effect=error) as replace, \
                patch.object(locking.time, "monotonic", side_effect=[0, 0, 1]), \
                patch.object(locking.time, "sleep") as sleep:
            with self.assertRaises(PermissionError) as raised:
                locking.atomic_replace(source, target)
        self.assertIs(raised.exception, error)
        self.assertEqual(replace.call_count, 2)
        sleep.assert_called_once_with(0.02)
        self.assertEqual(source.read_text(encoding="utf-8"), "new")
        self.assertEqual(target.read_text(encoding="utf-8"), "old")

    def test_atomic_replace_other_errors_and_posix_errors_are_immediate(self):
        source, target = self.root / "new", self.root / "old"
        for platform, code in (("posix", 5), ("nt", 112)):
            error = OSError("unretryable error")
            error.winerror = code
            with self.subTest(platform=platform, winerror=code), \
                    patch.object(locking.os, "name", platform), \
                    patch.object(locking.os, "replace", side_effect=error) as replace, \
                    patch.object(locking.time, "sleep") as sleep:
                with self.assertRaises(OSError) as raised:
                    locking.atomic_replace(source, target)
                self.assertIs(raised.exception, error)
                replace.assert_called_once_with(source, target)
                sleep.assert_not_called()

    def test_failed_result_replacement_preserves_previous_result_and_cleans_temp(self):
        runmark.mark_complete(self.root, run_id="old")
        summary = self.root / "summary.json"
        result = schedule.TaskResult("résumé", "domain", 1, status="partial")
        schedule._write_summary(summary, result)
        for target, write in (
            (self.root / runmark.MARK, lambda: runmark.mark_complete(self.root, run_id="new")),
            (summary, lambda: schedule._write_summary(summary, result)),
        ):
            before = target.read_bytes()
            with self.subTest(target=target.name), \
                    patch.object(locking.os, "replace", side_effect=OSError("write refused")):
                with self.assertRaisesRegex(OSError, "write refused"):
                    write()
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(list(self.root.glob("*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "requires Windows file sharing semantics")
    def test_atomic_replace_recovers_after_real_windows_reader_releases_handle(self):
        import ctypes
        from ctypes import wintypes

        source, target = self.root / "new", self.root / "old"
        source.write_text("new", encoding="utf-8")
        target.write_text("old", encoding="utf-8")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        # Permit reads/writes but deliberately omit FILE_SHARE_DELETE.
        handle = kernel.CreateFileW(str(target), 0x80000000, 1 | 2, None, 3, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        blocked = threading.Event()
        errors = []

        def release_reader():
            blocked.wait(timeout=2)
            time.sleep(0.05)
            kernel.CloseHandle(handle)

        reader = threading.Thread(target=release_reader)
        reader.start()
        original = os.replace

        def replace(src, dst):
            try:
                return original(src, dst)
            except OSError as exc:
                errors.append(exc.winerror)
                self.assertEqual(target.read_text(encoding="utf-8"), "old")
                blocked.set()
                raise

        try:
            with patch.object(locking.os, "replace", side_effect=replace):
                locking.atomic_replace(source, target)
        finally:
            blocked.set()
            reader.join(timeout=3)
        self.assertFalse(reader.is_alive())
        self.assertTrue(errors)
        self.assertTrue(set(errors) <= {5, 32, 33})
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        self.assertFalse(source.exists())

    def test_deep_unicode_paths_and_lf_materialization(self):
        dest = self.root.joinpath(*(["nested harness with spaces"] * 11), "日本語")
        self.assertGreater(len(str(dest)), 300)
        self.addCleanup(processes.remove_tree, self.root / "nested harness with spaces")
        expected = {name: "" for name in self.surface.always_on}
        expected.update({"systemprompt.md": "Résumé\n日本語\n", "scripts/check.py": "x = 1\n"})
        tree.write_tree(self.surface, expected, dest, stubs=False)
        self.assertEqual(tree.read_tree(self.surface, dest), expected)
        self.assertNotIn(b"\r\n", (config.long_path(dest) / "systemprompt.md").read_bytes())
        runmark.mark_complete(dest, run_id="résumé", harness_dir=dest)
        self.assertTrue(runmark.is_complete(dest, harness_dir=dest))
        summary = dest / "summary.json"
        schedule._write_summary(summary, schedule.TaskResult("résumé", "domain", 1))
        self.assertEqual(json.loads(config.long_path(summary).read_text(encoding="utf-8"))[
            "task_id"], "résumé")

    def test_artifact_names_cannot_escape_or_overwrite_other_evidence(self):
        dest = self.root / "context"
        (dest / "rollouts").mkdir(parents=True)
        (dest / "deliverables").mkdir()
        files = {"..\\..\\escaped": "escape", "a/b": "first", "a__b": "second",
                 "A__B": "third", "NUL": "reserved", "report:stream": "stream"}
        adapter = types.SimpleNamespace(
            trajectory=lambda *args, **kwargs: "trajectory",
            deliverable_files=lambda *args: files,
        )
        rollout = RunView("result", "result", self.root, True, True)
        self.assertEqual(context._write_rollout(dest, rollout, adapter, 1000, False), "")
        texts = list((dest / "deliverables" / "result").glob("*.txt"))
        self.assertEqual(len(texts), len(files))
        self.assertEqual({p.read_text(encoding="utf-8") for p in texts}, set(files.values()))
        self.assertFalse((dest / "escaped.txt").exists())

    def test_lock_is_released_after_owner_is_killed(self):
        from harness2.locking import file_lock

        lock = self.root / "stale.lock"
        ready = self.root / "ready"
        worker = (
            "from harness2.locking import file_lock; from pathlib import Path; import sys,time; "
            "lock=Path(sys.argv[1]); ready=Path(sys.argv[2]); "
            "ctx=file_lock(lock); ctx.__enter__(); ready.write_text('ready'); time.sleep(30)"
        )
        child = subprocess.Popen([sys.executable, "-c", worker, str(lock), str(ready)])
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists())
        finally:
            child.kill()
            child.wait(timeout=10)
        with file_lock(lock):
            self.assertTrue(lock.is_file())

    def test_summary_merge_keeps_all_independent_process_results(self):
        summary = self.root / "summary.json"
        worker = """
from pathlib import Path
import sys
from harness2.core.schedule import TaskResult, _write_summary
mode = sys.argv[2]
for step in range(4):
    result = TaskResult('résumé', 'domain', 2, status='ok',
                        modes={mode: {'ok': True, 'step': step}},
                        selections={mode: {'ok': True, 'chosen_label': mode}})
    _write_summary(Path(sys.argv[1]), result)
"""
        workers = [subprocess.Popen([sys.executable, "-c", worker, str(summary), f"mode-{n}"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                   for n in range(6)]
        try:
            for process in workers:
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, (stdout, stderr))
        finally:
            for process in workers:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
        result = json.loads(summary.read_text(encoding="utf-8"))
        self.assertEqual(set(result["modes"]), {f"mode-{n}" for n in range(6)})
        self.assertTrue(all(record["step"] == 3 for record in result["modes"].values()))
        self.assertEqual(result["status"], "ok")
        self.assertFalse(summary.with_suffix(".json.tmp").exists())


class ConcurrentTerminationReviewTests(unittest.TestCase):
    def test_owned_and_fallback_popen_cleanup_is_serial_and_idempotent(self):
        # A cancellation request and timeout/finally cleanup must never overlap
        # native Job handle closure. Exercise both initialized owned Popen locks
        # and lazy initialization for ordinary caller-supplied Popen instances.
        for owned in (True, False):
            with self.subTest(owned=owned):
                arguments = [sys.executable, "-c", "import time; time.sleep(30)"]
                process = (processes.start(arguments) if owned else subprocess.Popen(
                    arguments, **processes.popen_group_kwargs()))
                entered, release = threading.Event(), threading.Event()
                requests = threading.Barrier(3)
                calls, errors = [], []
                original = processes._terminate_tree

                def delayed_cleanup(target, *, grace):
                    calls.append(target.pid)
                    entered.set()
                    if not release.wait(timeout=3):
                        raise TimeoutError("fixture release was not signalled")
                    original(target, grace=grace)

                def stop():
                    try:
                        requests.wait(timeout=3)
                        processes.terminate_tree(process)
                    except BaseException as exc:
                        errors.append(exc)

                workers = [threading.Thread(target=stop) for _ in range(2)]
                try:
                    with patch.object(processes, "_terminate_tree", side_effect=delayed_cleanup):
                        for worker in workers:
                            worker.start()
                        requests.wait(timeout=3)
                        self.assertTrue(entered.wait(timeout=3))
                        time.sleep(0.05)
                        self.assertIsNone(process.poll())
                        self.assertEqual(calls, [process.pid])
                        release.set()
                        for worker in workers:
                            worker.join(timeout=5)
                        self.assertTrue(all(not worker.is_alive() for worker in workers))
                        self.assertEqual(errors, [])
                        processes.terminate_tree(process)
                        self.assertEqual(calls, [process.pid])
                    self.assertIsNotNone(process.poll())
                finally:
                    release.set()
                    for worker in workers:
                        if worker.ident is not None:
                            worker.join(timeout=5)
                    processes.terminate_tree(process)


@unittest.skipUnless(os.name == "nt", "Windows Job Object integration")
class WindowsProcessReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="job review ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def assert_pid_stopped(self, pid):
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if handle:
            try:
                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
            finally:
                kernel.CloseHandle(handle)

    def test_success_closes_job_and_stops_remaining_child(self):
        pid_file = self.root / "child.pid"
        parent = (
            "import subprocess,sys; from pathlib import Path; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
            "Path(sys.argv[1]).write_text(str(child.pid))"
        )
        try:
            result = processes.run([sys.executable, "-c", parent, str(pid_file)],
                                   capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assert_pid_stopped(int(pid_file.read_text()))
        finally:
            if pid_file.exists():
                subprocess.run(["taskkill.exe", "/PID", pid_file.read_text(), "/T", "/F"],
                               capture_output=True, check=False)

    def test_child_cannot_run_before_job_assignment(self):
        marker = self.root / "started"
        original = processes._windows_job
        ran_unowned = []

        def delayed_assignment(process):
            time.sleep(0.3)
            ran_unowned.append(marker.exists())
            original(process)

        with patch.object(processes, "_windows_job", side_effect=delayed_assignment):
            processes.run([sys.executable, "-c",
                           "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
                           str(marker)], timeout=10)
        self.assertEqual(ran_unowned, [False])
        self.assertTrue(marker.exists())

    def test_job_assignment_failure_cleans_up_unstarted_process(self):
        marker = self.root / "should-not-run"
        spawned = []
        original = subprocess.Popen

        def record(*args, **kwargs):
            process = original(*args, **kwargs)
            spawned.append(process)
            return process

        try:
            with patch.object(processes.subprocess, "Popen", side_effect=record), \
                    patch.object(processes, "_windows_job", side_effect=OSError("job denied")):
                with self.assertRaisesRegex(OSError, "job denied"):
                    processes.start([sys.executable, "-c",
                                     "from pathlib import Path; import sys,time; "
                                     "Path(sys.argv[1]).touch(); time.sleep(30)", str(marker)])
            self.assertIsNotNone(spawned[0].poll())
            self.assertFalse(marker.exists())
        finally:
            for process in spawned:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)

    def test_timeout_kills_grandchildren_even_after_parent_exits(self):
        pid_file = self.root / "grandchild.pid"
        grandchild = "import time; time.sleep(30)"
        child = (
            "import subprocess,sys,time; from pathlib import Path; "
            f"proc=subprocess.Popen([sys.executable,'-c',{grandchild!r}]); "
            "Path(sys.argv[1]).write_text(str(proc.pid)); time.sleep(30)"
        )
        parent = (
            "import subprocess,sys,time; from pathlib import Path; "
            f"subprocess.Popen([sys.executable,'-c',{child!r},sys.argv[1]]); "
            "p=Path(sys.argv[1]); "
            "exec('while not p.exists(): time.sleep(0.01)')"
        )
        try:
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                processes.run([sys.executable, "-c", parent, str(pid_file)],
                              capture_output=True, timeout=1)
            self.assertLess(time.monotonic() - started, 10)
            self.assertTrue(pid_file.is_file())
            self.assert_pid_stopped(int(pid_file.read_text()))
        finally:
            if pid_file.exists():
                subprocess.run(["taskkill.exe", "/PID", pid_file.read_text(), "/T", "/F"],
                               capture_output=True, check=False)


if __name__ == "__main__":
    unittest.main()
