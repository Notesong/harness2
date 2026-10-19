# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at https://www.apache.org/licenses/LICENSE-2.0

"""Portable process groups and timeout cleanup for benchmark launchers."""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import threading
from typing import Any

from . import config

_TERMINATION_LOCK_GUARD = threading.Lock()


def _windows_job(proc: subprocess.Popen) -> None:
    """Contain a suspended child before it can create any descendants.

    Do not silently continue without a job: taskkill cannot find descendants after
    their launcher has exited, and a timed-out run would leave them running.
    """
    import ctypes
    from ctypes import wintypes

    class Limits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                    ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]

    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("limits", Limits), ("io", Counters),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.limits.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or \
            not kernel.AssignProcessToJobObject(job, int(proc._handle)):
        error = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise ctypes.WinError(error)
    proc._harness2_job = job


def _resume_windows_process(proc: subprocess.Popen) -> None:
    """Resume the initial thread after Popen has closed its thread handle.

    Use the documented Tool Help and ResumeThread APIs. The child is suspended,
    so its initial thread cannot exit or create other threads during this scan.
    """
    import ctypes
    from ctypes import wintypes

    class ThreadEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("thread_id", wintypes.DWORD), ("owner_pid", wintypes.DWORD),
                    ("base_priority", wintypes.LONG), ("delta_priority", wintypes.LONG),
                    ("flags", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ThreadEntry()
        entry.size = ctypes.sizeof(entry)
        found = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.owner_pid == proc.pid:
                thread = kernel.OpenThread(0x0002, False, entry.thread_id)  # SUSPEND_RESUME
                if not thread:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                        raise ctypes.WinError(ctypes.get_last_error())
                finally:
                    kernel.CloseHandle(thread)
                return
            found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        raise OSError(f"could not find initial thread for suspended process {proc.pid}")
    finally:
        kernel.CloseHandle(snapshot)


def _close_job(proc: subprocess.Popen) -> bool:
    job = getattr(proc, "_harness2_job", None)
    if job is None:
        return False
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle(job)
    proc._harness2_job = None
    return True


def start(args, **kwargs) -> subprocess.Popen:
    """Start an owned process tree; close it with terminate_tree or run()."""
    options = popen_group_kwargs()
    options.update(kwargs)
    if os.name == "nt":
        # Assignment after an ordinary Popen races fast launchers, which can
        # create children that escape the job before the assignment happens.
        options["creationflags"] = (options.get("creationflags", 0)
                                    | subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004)
    proc = subprocess.Popen(args, **options)
    proc._harness2_terminate_lock = threading.RLock()
    if os.name == "nt":
        try:
            _windows_job(proc)
            _resume_windows_process(proc)
        except BaseException:
            _close_job(proc)
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
            raise
    return proc


def remove_tree(path, *, ignore_errors: bool = False) -> None:
    """Remove runner scratch trees, including Windows read-only input copies."""
    def writable(function, filename, exc_info):
        os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
        function(filename)

    target = config.long_path(path)
    try:
        shutil.rmtree(target, onerror=writable if os.name == "nt" else None)
    except FileNotFoundError:
        # A disappearing child can interrupt rmtree while stale siblings remain.
        # Broken links also remain targets, even though Path.exists() is false.
        if not ignore_errors and os.path.lexists(target):
            raise
    except OSError:
        if not ignore_errors:
            raise


def popen_group_kwargs() -> dict[str, Any]:
    """Give a child a process group that can be stopped with its descendants."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def terminate_tree(proc: subprocess.Popen, *, grace: float = 0) -> None:
    """Stop the full child tree, escalating after an optional graceful interval."""
    # UI cancellation, a deadline timer, and a worker's finally block can all
    # arrive together. Protect the entire operation, including Job handle close,
    # and make later requests a no-op after successful cleanup. The guard also
    # initializes ownership safely for callers passing an ordinary Popen.
    with _TERMINATION_LOCK_GUARD:
        lock = getattr(proc, "_harness2_terminate_lock", None)
        if lock is None:
            lock = proc._harness2_terminate_lock = threading.RLock()
    with lock:
        if getattr(proc, "_harness2_tree_terminated", False):
            return
        _terminate_tree(proc, grace=grace)
        proc._harness2_tree_terminated = True


def _terminate_tree(proc: subprocess.Popen, *, grace: float) -> None:
    if os.name == "nt":
        if grace and proc.poll() is None:
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
                proc.wait(timeout=grace)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if not _close_job(proc) and proc.poll() is None:
            # Fallback for callers supplying their own Popen or hosts forbidding
            # nested jobs. Popen.kill() alone would leave model processes alive.
            taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                    "System32", "taskkill.exe")
            subprocess.run([taskkill, "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        if grace:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=grace)
            except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
                pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def run(args, *, timeout: float | None = None, input=None, capture_output: bool = False,
        check: bool = False, **kwargs) -> subprocess.CompletedProcess:
    """Like subprocess.run, with native wall timeouts and process-tree cleanup."""
    if input is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used")
        kwargs["stdin"] = subprocess.PIPE
    if capture_output:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output")
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with start(args, **kwargs) as proc:
        try:
            stdout, stderr = proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            terminate_tree(proc)
            stdout, stderr = proc.communicate()
            exc.stdout, exc.stderr = stdout, stderr
            raise
        except BaseException:
            terminate_tree(proc)
            raise
        result = subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
        _close_job(proc)
    if check:
        result.check_returncode()
    return result
