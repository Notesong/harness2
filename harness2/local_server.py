# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at https://www.apache.org/licenses/LICENSE-2.0

"""An explicitly owned, temporary llama.cpp server for one local experiment."""

from __future__ import annotations

import http.client
import json
import math
import os
import secrets
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import config, processes
from .local_models import LocalProfile, profile


@dataclass(frozen=True)
class ManagedSettings:
    profile: LocalProfile
    executable: Path
    weights: Path
    port: int


def validate_managed_settings(environment: Mapping[str, str]) -> ManagedSettings | None:
    """Validate saved settings without opening a socket, log, executable, or weights."""
    runtime = environment.get("HARNESS2_LOCAL_RUNTIME", "") or "external"
    if runtime not in ("external", "managed"):
        raise ValueError("HARNESS2_LOCAL_RUNTIME must be external or managed")
    if runtime == "external":
        return None
    local = profile(environment)
    if local is None:
        raise ValueError("Managed local runtime requires a local model profile")
    parsed = urlsplit(local.base_url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.port is None or parsed.port == 0 or parsed.path != "/v1"):
        raise ValueError("Managed local runtime requires http://127.0.0.1:PORT/v1")
    paths = []
    for key, suffix in (("HARNESS2_LOCAL_EXECUTABLE", ".exe" if os.name == "nt" else ""),
                        ("HARNESS2_LOCAL_WEIGHTS", ".gguf")):
        value = environment.get(key, "")
        if not isinstance(value, str) or not value or any(
                char == '"' or ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f"{key} must be an absolute existing file path without quotes or controls")
        path = Path(value)
        if not path.is_absolute() or not config.long_path(path).is_file():
            raise ValueError(f"{key} must be an absolute existing file path")
        if suffix and path.suffix.lower() != suffix:
            raise ValueError(f"{key} must name a {suffix} file")
        if key == "HARNESS2_LOCAL_EXECUTABLE" and os.name != "nt" and not os.access(path, os.X_OK):
            raise ValueError("HARNESS2_LOCAL_EXECUTABLE must be executable")
        paths.append(path)
    return ManagedSettings(local, *paths, parsed.port)


class LocalServerCancelled(RuntimeError):
    """The owner cancelled while its local server was starting."""


class ManagedLocalServer:
    """Start at most one server and close its owned process tree deterministically.

    Construction has no effects. External mode does no probing or launching.
    A closed resource cannot be restarted; create a new instance for another run.
    ``environment`` is a private copy for children, with temporary authentication
    installed only while the managed resource is alive. ``pid`` retains the last
    owned PID after close, and ``started`` describes current authenticated readiness.
    """

    def __init__(self, environment: Mapping[str, str], *, root: Path, log_path: Path,
                 cancelled: Callable[[], bool] = lambda: False,
                 readiness_timeout: float = 180):
        self.environment = dict(environment)
        self._original_environment = dict(environment)
        self.root = Path(root)
        self.log_path = Path(log_path)
        self.cancelled = cancelled
        self.readiness_timeout = readiness_timeout
        self.pid: int | None = None
        self.started = False
        self._settings: ManagedSettings | None = None
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._log = None
        self._log_error = False
        self._token: str | None = None
        self._closed = False
        self._starting = False
        self._closing = False
        self._condition = threading.Condition()

    @property
    def metadata(self) -> dict:
        """Public lifecycle information, excluding authentication and command lines."""
        local = self._settings.profile if self._settings else None
        return {"runtime": self.environment.get("HARNESS2_LOCAL_RUNTIME") or "external",
                "pid": self.pid, "started": self.started,
                "owned": self._process is not None and not self._closed,
                "model": local.model if local else None,
                "base_url": local.base_url if local else None,
                "context": local.context if local else None}

    def __enter__(self) -> ManagedLocalServer:
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def start(self) -> ManagedLocalServer:
        with self._condition:
            while self._starting and not self._closed:
                self._condition.wait()
            if self._closed:
                raise RuntimeError("Local server resource is closed")
            if self.started:
                return self
            self._starting = True
        try:
            settings = validate_managed_settings(self.environment)
            if settings is None:
                return self
            self._settings = settings
            if not math.isfinite(self.readiness_timeout) or self.readiness_timeout <= 0:
                raise ValueError("Local server readiness timeout must be positive and finite")
            self._check_cancelled()
            # Refuse another owner's socket. Never discover or terminate its PID.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
                if os.name == "nt":
                    reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    reservation.bind(("127.0.0.1", settings.port))
                except OSError:
                    raise RuntimeError("Managed local endpoint is occupied or unavailable; "
                                       "stop its owner or choose another port") from None
            self._check_cancelled()
            token = secrets.token_urlsafe(32)
            # llama.cpp environment defaults must not override this owned recipe,
            # especially inherited authentication files or remote/offline settings.
            child_environment = {key: value for key, value in self.environment.items()
                                 if not key.upper().startswith("LLAMA_")}
            child_environment["LLAMA_API_KEY"] = token
            args = [str(settings.executable), "--model", str(settings.weights),
                    "--host", "127.0.0.1", "--port", str(settings.port),
                    "--alias", settings.profile.model,
                    "--ctx-size", str(settings.profile.context), "--parallel", "1",
                    "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
                    "--flash-attn", "on", "--fit", "on", "--fit-target", "1536",
                    "--batch-size", "512", "--ubatch-size", "128",
                    "--jinja", "--no-webui", "--offline", "--reasoning", "off"]
            with self._condition:
                self._check_cancelled()
                config.long_path(self.log_path.parent).mkdir(parents=True, exist_ok=True)
                self._log = config.long_path(self.log_path).open("wb")
                self._token = token
                options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
                self._process = processes.start(
                    args, cwd=self.root, env=child_environment, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **options)
                self.pid = self._process.pid
                for key in ("HARNESS2_LOCAL_API_KEY", "HARNESS2_JUDGE_API_KEY"):
                    self.environment[key] = token
                self._reader = threading.Thread(target=self._copy_log, args=(token,), daemon=True,
                                                name="harness2-local-server-log")
                self._reader.start()
            deadline = time.monotonic() + self.readiness_timeout
            while True:
                self._check_cancelled()
                if self._process.poll() is not None:
                    raise RuntimeError(f"Owned local server exited before readiness "
                                       f"(exit {self._process.returncode}); inspect its log")
                if self._log_error:
                    raise RuntimeError("Could not write the owned local server log")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Owned local server did not become ready before the deadline")
                if self._ready(settings, token, min(1.0, remaining)):
                    with self._condition:
                        self._check_cancelled()
                        if self._process.poll() is None:
                            self.started = True
                            return self
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        except BaseException:
            self.close()
            raise
        finally:
            with self._condition:
                self._starting = False
                self._condition.notify_all()

    def _check_cancelled(self) -> None:
        if self._closed or self.cancelled():
            raise LocalServerCancelled("Owned local server startup was cancelled")

    @staticmethod
    def _ready(settings: ManagedSettings, token: str, timeout: float) -> bool:
        # Direct HTTP avoids proxy environment settings and never follows redirects
        # carrying the temporary Authorization header to a different endpoint.
        connection = http.client.HTTPConnection("127.0.0.1", settings.port, timeout=timeout)
        try:
            connection.request("GET", "/v1/models", headers={
                "Authorization": "Bearer " + token, "Connection": "close"})
            response = connection.getresponse()
            if response.status != 200:
                return False
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                return False
            value = json.loads(body)
            return (isinstance(value, dict) and isinstance(value.get("data"), list)
                    and any(isinstance(item, dict) and item.get("id") == settings.profile.model
                            for item in value["data"]))
        except (OSError, ValueError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    def _copy_log(self, token: str) -> None:
        # Preserve an overlap so tokens split across pipe reads cannot leak. Work
        # in bytes so malformed UTF-8 and a server's very long lines are bounded.
        secret = token.encode("utf-8")
        pending = b""
        try:
            while chunk := self._process.stdout.read1(65536):
                pending = (pending + chunk).replace(secret, b"[redacted]")
                count = max(0, len(pending) - len(secret) + 1)
                self._log.write(pending[:count])
                self._log.flush()
                pending = pending[count:]
            self._log.write(pending.replace(secret, b"[redacted]"))
            self._log.flush()
        except (OSError, ValueError):
            self._log_error = True

    def close(self) -> None:
        with self._condition:
            while self._closing:
                self._condition.wait()
            if self._closed:
                return
            self._closed = True
            self._closing = True
            self.started = False
            self._condition.notify_all()
        try:
            if self._process is not None:
                processes.terminate_tree(self._process)
            if self._reader is not None:
                self._reader.join(timeout=5)
            if self._process is not None and self._process.stdout is not None:
                self._process.stdout.close()
            if self._log is not None:
                self._log.close()
        finally:
            self._token = None
            for key in ("HARNESS2_LOCAL_API_KEY", "HARNESS2_JUDGE_API_KEY"):
                if key in self._original_environment:
                    self.environment[key] = self._original_environment[key]
                else:
                    self.environment.pop(key, None)
            with self._condition:
                self._closing = False
                self._condition.notify_all()
