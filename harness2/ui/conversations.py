# SPDX-License-Identifier: Apache-2.0
"""Studio conversation ownership; visiting an archive never starts inference."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..conversation_runtime import (
    CONVERSATION_ENV,
    WORKSPACE_ENV,
    SessionBridge,
    provenance,
    read_json,
    runtime_root,
)
from ..conversations import ConversationStore
from ..local_models import profile
from ..local_server import ManagedLocalServer
from ..locking import atomic_replace
from ..profile_runtime import (
    MAX_SNAPSHOT,
    SNAPSHOT_ENV,
    SNAPSHOT_NAME,
    load_snapshot,
    pin_profile,
    run_environment,
    verify_configuration,
)
from .jobs import ACTIVE, BusyError

IDLE_TIMEOUT = 600.0
POLL_INTERVAL = 0.2
MAX_USER_MESSAGE = 16_000
_TERMINAL = {"closed", "ended", "completed", "failed", "cancelled", "stopped"}
_SESSION = re.compile(r"ses_[A-Za-z0-9]{1,128}\Z")


@dataclass
class _Idle:
    identifier: str
    root: Path
    snapshot: dict
    workspace: Path
    resume_session_id: str | None = None
    session_runtime: Path | None = None
    label: str = "Conversation"
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    model: object | None = None
    bridge: object | None = None
    last_activity: float = field(default_factory=time.monotonic)
    stop_reason: str = "Conversation stopped before a response was completed."


class Conversations:
    def __init__(self, workspace, jobs):
        self.workspace, self.jobs = workspace, jobs
        self.store = ConversationStore(workspace.root)
        from .attention import Attention

        self.attention = Attention(workspace, self.store)
        self.lock = threading.RLock()
        self._idle: _Idle | None = None
        self._statuses = {}
        self._closers = set()
        self._closed = False
        # Native sessions are retained for explicit continuation. These are old
        # journal workers, not processes owned by this new application instance.
        for summary in self.store.list():
            view = self.store.get(summary["id"])
            for worker in view["workers"]:
                if worker["status"] not in _TERMINAL:
                    self.store.end_worker(view["id"], worker["id"],
                        "The app restarted before this worker finished; its pending messages were not replayed.")

    @contextmanager
    def _locked(self):
        # JobManager also asks busy while holding this RLock. Never reverse the
        # order in background cleanup or a model-start/experiment race can hang.
        with self.jobs.lock:
            with self.lock:
                yield

    @property
    def busy(self):
        with self._locked():
            return self._idle is not None

    def _active_job(self):
        identifier = self.jobs.active_id
        record = self.jobs.jobs.get(identifier) if identifier else None
        return record if record and record.get("status") in ACTIVE else None

    def _runtime(self, view):
        active = self._active_job()
        if active and active.get("conversation_id") == view["id"]:
            connected = bool(view["active_worker_id"])
            last = view["workers"][-1] if view["workers"] else {}
            disconnected = not connected and "connection failed" in last.get("reason", "").lower()
            if disconnected:
                return {"status": "error", "mode": "run", "connected": False, "job_id": active["id"],
                        "can_start": False, "can_stop": False, "can_message": False,
                        "detail": "The run is active, but its conversation session disconnected.",
                        "error": "Messages cannot be delivered until a new working session starts."}
            return {"status": "running" if connected else "loading", "mode": "run",
                    "connected": connected, "job_id": active["id"],
                    "can_start": False, "can_stop": False, "can_message": connected,
                    "detail": "Connected to this run's working session." if connected
                    else "Waiting for the run's next working session.", "error": ""}
        result = dict(self._statuses.get(view["id"], {
            "status": "offline", "mode": "idle", "detail": "Not connected.", "error": "",
        }))
        owned = self._idle is not None and self._idle.identifier == view["id"]
        connected = bool(owned and not self._idle.stop.is_set() and view["active_worker_id"]
                         and result["status"] == "running")
        result.update(connected=connected, can_message=connected,
                      can_stop=owned, can_start=not self._closed and active is None
                      and self._idle is None and view["status"] == "open")
        return result

    def _get(self, identifier):
        view = self.store.get(identifier)
        view["runtime"] = self._runtime(view)
        view["pending_count"] = sum(message["role"] == "user" and message["status"] in ("queued", "delivered")
                                    for message in view["messages"])
        view["conversational_memory"] = self.store.memory(view["profile_id"])
        view.update(self.attention.decorate(view))
        return view

    def get(self, identifier):
        with self._locked():
            return self._get(identifier)

    def list(self):
        with self._locked():
            result = []
            for summary in self.store.list():
                view = self.store.get(summary["id"])
                attention = self.attention.decorate(view)
                summary.update({key: attention[key] for key in ("unread_count", "attention_messages")})
                summary.update(runtime=self._runtime(view), pending_count=sum(
                    message["role"] == "user" and message["status"] in ("queued", "delivered")
                    for message in view["messages"]))
                result.append(summary)
            return result

    def create(self, data):
        if not isinstance(data, dict):
            raise ValueError("Conversation settings should be an object.")
        with self._locked():
            if self._closed:
                raise ValueError("The conversation service is closed.")
            selected = self.store.profiles.get(data.get("profile_id"))
            configuration_id = (data.get("configuration_id") or data.get("profile_configuration_id")
                                or selected.get("active_configuration_id"))
            revision_id = (data.get("revision_id") or data.get("profile_revision_id")
                           or selected.get("active_revision_id"))
            configuration = next((item for item in selected["configurations"] if item["id"] == configuration_id), None)
            if not configuration or configuration["snapshot"]["settings"].get("provider", "local") != "local":
                raise ValueError("Choose a saved local model configuration for this conversation.")
            view = self.store.create(selected["id"], configuration_id, revision_id,
                                     title=data.get("title") or "Conversation")
            root = runtime_root(self.workspace.root, view["id"])
            try:
                pin_profile(self.store.profiles, selected["id"], root,
                            configuration_id=configuration_id, revision_id=revision_id)
            except BaseException:
                self.store.finish(view["id"], "The conversation starting configuration could not be saved.", status="failed")
                raise
            return self._get(view["id"])

    def _safe_directory(self, value, root):
        if not isinstance(value, str) or not value:
            raise ValueError("The retained session workspace is missing.")
        path = Path(value)
        if not path.is_absolute() or path == root:
            raise ValueError("The retained session must remain inside its conversation runtime.")
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            raise ValueError("The retained session must remain inside its conversation runtime.") from None
        cursor = root
        for part in parts:
            if part in ("", ".", ".."):
                raise ValueError("The retained session path is invalid.")
            cursor /= part
            if cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)():
                raise ValueError("The retained session cannot traverse links.")
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_dir():
            raise ValueError("The retained session files are unavailable.")
        return path

    def _prepare(self, view):
        root = runtime_root(self.workspace.root, view["id"])
        snapshot = load_snapshot(root / SNAPSHOT_NAME)
        if any(snapshot.get(key) != view[key] for key in ("profile_id", "configuration_id", "revision_id")):
            raise ValueError("The conversation's pinned model identity changed.")
        latest = root / "latest.json"
        if latest.exists():
            saved = read_json(latest)
            session_id = saved.get("session_id")
            if not isinstance(session_id, str) or not _SESSION.fullmatch(session_id):
                raise ValueError("The retained native session identity is missing or invalid.")
            workspace = self._safe_directory(saved.get("workspace"), root)
            session_runtime = self._safe_directory(saved.get("session_runtime"), root)
            return _Idle(view["id"], root, snapshot, workspace, session_id, session_runtime,
                         label="Continue " + str(view["title"])[:220])
        if view["run_id"] or view["workers"]:
            raise ValueError("This conversation has no retained native session to continue; no replacement was created.")
        workspace = root / "workspaces" / ("chat-" + uuid.uuid4().hex)
        return _Idle(view["id"], root, snapshot, workspace, label=str(view["title"])[:240])

    def _start(self, identifier):
        with self._locked():
            view = self.store.get(identifier)
            if self._closed or view["status"] != "open":
                raise ValueError("This conversation is closed.")
            if self._active_job():
                raise BusyError("A run is active. Speak through its linked conversation, or wait for it to finish.")
            if self._idle:
                if self._idle.identifier == identifier and not self._idle.stop.is_set():
                    return self._get(identifier)
                raise BusyError("Stop the current idle conversation before starting another.")
            idle = self._prepare(view)
            self._idle = idle
            self._statuses[identifier] = {"status": "loading", "mode": "idle", "error": "",
                                           "detail": "Loading the selected model and working session."}
            thread = threading.Thread(target=self._idle_worker, args=(idle,), daemon=True,
                                      name=f"harness-chat-{identifier[:8]}")
            idle.thread = thread
            try:
                thread.start()
            except BaseException:
                self._idle = None
                self._statuses[identifier] = {"status": "error", "mode": "idle",
                    "error": "The conversation background worker could not start.", "detail": "Not connected."}
                raise
            return self._get(identifier)

    def _check_stopped(self, idle):
        if idle.stop.is_set() or self._closed:
            raise InterruptedError("Conversation startup was cancelled.")

    def _idle_worker(self, idle):
        failed = False
        try:
            from ..cli.local_run import _local_environment
            from ..substrate.opencode import OpencodeSubstrate

            environment = run_environment(self.workspace.environment(), idle.root)
            verify_configuration(idle.snapshot["configuration"], environment)
            environment = _local_environment(environment)
            local = profile(environment)
            if local is None:
                raise ValueError("The selected local model configuration is unavailable.")
            if idle.resume_session_id:
                saved = read_json(idle.root / "latest.json")
                if saved.get("model") != local.opencode_model:
                    raise ValueError("The retained session used a different model configuration.")
            else:
                from ..benchmarks.jobbench import render_opencode_tree
                from ..core import tree
                from ..substrate.opencode import OPENCODE_SURFACE

                staged = idle.root / ("chat-harness-" + uuid.uuid4().hex)
                tree.write_tree(OPENCODE_SURFACE, idle.snapshot["harness"], staged)
                if idle.workspace.exists():
                    raise ValueError("The new conversation workspace already exists.")
                render_opencode_tree(staged, idle.workspace, {
                    "env_description": "A private local conversation workspace.",
                    "domain_focus": "Conversation with the user", "date": "",
                    "working_directory": "the private conversation workspace",
                })
            self._check_stopped(idle)
            # The model sees its selected harness and working ledger. The private
            # run snapshot, experience, and archive are not advertised as context.
            environment.pop(SNAPSHOT_ENV, None)
            environment.pop("HARNESS2_RUNS_DIR", None)
            environment = OpencodeSubstrate().run_env(environment, idle.workspace,
                                                       output_token_max=local.output)
            environment.update({CONVERSATION_ENV: idle.identifier, WORKSPACE_ENV: str(self.workspace.root)})
            owner = ManagedLocalServer(environment, root=self.workspace.root,
                log_path=idle.root / "idle-model.log", cancelled=idle.stop.is_set)
            with self._locked():
                idle.model = owner
            self._check_stopped(idle)
            owner.start()
            self._check_stopped(idle)
            bridge = SessionBridge(self.workspace.root, idle.identifier, idle.workspace, owner.environment,
                model=local.opencode_model, label=idle.label, resume_session_id=idle.resume_session_id,
                session_runtime=idle.session_runtime)
            with self._locked():
                idle.bridge = bridge
            self._check_stopped(idle)
            bridge.start()
            self._check_stopped(idle)
            with self._locked():
                idle.last_activity = time.monotonic()
                self._statuses[idle.identifier] = {"status": "running", "mode": "idle", "error": "",
                                                   "detail": "Connected to the retained working session."}
            previous_count, last_probe = 0, 0.0
            while not idle.stop.wait(POLL_INTERVAL):
                if bridge.failure:
                    raise RuntimeError("The conversation session connection failed.")
                view = self.store.get(idle.identifier)
                count = len(view["messages"])
                if count != previous_count:
                    previous_count, idle.last_activity = count, time.monotonic()
                if time.monotonic() - last_probe >= 1.0:
                    last_probe = time.monotonic()
                    if bridge.client.status() == "busy":
                        idle.last_activity = time.monotonic()
                if time.monotonic() - idle.last_activity >= IDLE_TIMEOUT:
                    idle.stop_reason = "The conversation disconnected after ten minutes without activity. Its session was saved."
                    idle.stop.set()
        except BaseException as exc:
            if not idle.stop.is_set() and not self._closed:
                failed = True
                # Paths, endpoint credentials, and raw error bodies stay private.
                error = "The local conversation could not connect. Check the saved model configuration and installed OpenCode, then try again."
                with self._locked():
                    self._statuses[idle.identifier] = {"status": "error", "mode": "idle", "error": error,
                                                       "detail": "Not connected."}
                try:
                    self.store.record_event(idle.identifier, "connection_failed", {"error_type": type(exc).__name__})
                except (OSError, ValueError):
                    pass
        finally:
            cleanup_failed = self._close_owned(idle)
            with self._locked():
                if self._idle is idle and not cleanup_failed:
                    self._idle = None
                if cleanup_failed:
                    self._statuses[idle.identifier] = {"status": "error", "mode": "idle",
                        "error": "Conversation cleanup failed; close the app before starting another model.", "detail": "Not connected."}
                elif not failed:
                    self._statuses[idle.identifier] = {"status": "offline", "mode": "idle", "error": "",
                                                       "detail": idle.stop_reason}

    @staticmethod
    def _close_owned(idle):
        failed = False
        for resource in (idle.bridge, idle.model):
            if resource is not None:
                try:
                    if resource is idle.bridge:
                        resource.close(idle.stop_reason)
                    else:
                        resource.close()
                except BaseException:
                    failed = True
        return failed

    def _interrupt_idle(self, idle):
        try:
            failed = self._close_owned(idle)
            with self._locked():
                if (not failed and self._idle is idle and idle.thread
                        and not idle.thread.is_alive()):
                    self._idle = None
                    self._statuses[idle.identifier] = {"status": "offline", "mode": "idle", "error": "",
                                                       "detail": idle.stop_reason}
        finally:
            with self._locked():
                self._closers.discard(threading.current_thread())

    def _stop(self, identifier, *, closing=False):
        with self._locked():
            self.store.get(identifier)
            active = self._active_job()
            if active and active.get("conversation_id") == identifier and not closing:
                raise BusyError("This conversation belongs to the active run. Use Stop run to stop that operation.")
            idle = self._idle
            if not idle or idle.identifier != identifier:
                return self._get(identifier)
            idle.stop.set()
            idle.stop_reason = "The conversation was disconnected; unanswered messages were preserved with their delivery status."
            self._statuses[identifier] = {"status": "loading", "mode": "idle", "error": "",
                                          "detail": "Stopping the conversation and unloading its owned model."}
            # close() is explicitly safe during model/session startup. Interrupt
            # those owners outside both application locks to avoid deadlocks.
            closer = threading.Thread(target=self._interrupt_idle, args=(idle,), daemon=True,
                                      name=f"harness-chat-stop-{identifier[:8]}")
            self._closers.add(closer)
            try:
                closer.start()
            except BaseException:
                self._closers.discard(closer)
                raise
            return self._get(identifier)

    def command(self, identifier, action, data):
        if not isinstance(data, dict):
            raise ValueError("Conversation input should be an object.")
        if action == "read":
            if set(data) != {"message_ids"}:
                raise ValueError("Choose explicit visible message IDs to mark read.")
            with self._locked():
                self.attention.read(identifier, data["message_ids"])
                return self._get(identifier)
        if action == "start":
            return self._start(identifier)
        if action == "stop":
            return self._stop(identifier)
        if action != "messages":
            raise KeyError(action)
        text = data.get("text", "")
        if not isinstance(text, str) or len(text) > MAX_USER_MESSAGE:
            raise ValueError("Conversation messages must contain at most 16,000 characters.")
        with self._locked():
            if self._closed:
                raise ValueError("The conversation service is closed.")
            active = self._active_job()
            if active and active.get("conversation_id") != identifier:
                raise BusyError("Only the active run's linked conversation can receive messages during that operation.")
            view = self._get(identifier)
            if not view["runtime"]["can_message"]:
                raise ValueError("Connect this conversation to a working session before sending a message.")
            self.store.enqueue(identifier, text, worker_id=data.get("worker_id", ""))
            if self._idle and self._idle.identifier == identifier:
                self._idle.last_activity = time.monotonic()
            return self._get(identifier)

    def _save_run_snapshot(self, root, pinned):
        destination = root / SNAPSHOT_NAME
        if destination.exists():
            prior = load_snapshot(destination)
            if any(prior.get(key) != pinned.get(key) for key in ("profile_id", "configuration_id", "revision_id")):
                raise ValueError("The conversation belongs to another pinned model identity.")
            return
        body = json.dumps(pinned, ensure_ascii=False, indent=2).encode("utf-8")
        if len(body) > MAX_SNAPSHOT:
            raise ValueError("The conversation's pinned model snapshot is too large.")
        temporary = root / f".{SNAPSHOT_NAME}-{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            atomic_replace(temporary, destination)
            load_snapshot(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def begin_run(self, record, pinned):
        from ..conversation_runtime import require_worker_containment

        require_worker_containment()
        settings = pinned.get("configuration", {}).get("settings", {})
        if settings.get("provider", "local") != "local":
            raise ValueError("Conversational runs require an explicitly selected local model profile.")
        with self._locked():
            if self._closed or self._idle:
                raise BusyError("Stop the idle conversation before starting a conversational run.")
            identifier = record.get("conversation_id")
            if not identifier and record.get("resumed_from"):
                identifier = (self.jobs.jobs.get(record["resumed_from"]) or {}).get("conversation_id")
            if identifier:
                view = self.store.get(identifier)
                if view["status"] != "open" or any(view[key] != pinned[key]
                    for key in ("profile_id", "configuration_id", "revision_id")):
                    raise ValueError("The run's conversation has another pinned identity or is closed.")
            else:
                view = self.store.create(pinned["profile_id"], pinned["configuration_id"], pinned["revision_id"],
                    run_id=record["id"], title=record.get("title") or "Run conversation")
                identifier = view["id"]
            self._save_run_snapshot(runtime_root(self.workspace.root, identifier), pinned)
            record["conversation_id"] = identifier
            record["conversation"] = provenance(self.store, identifier)
            return identifier

    def run_environment(self, record, environment):
        identifier = record.get("conversation_id")
        if not identifier:
            return dict(environment)
        self.store.get(identifier)
        return {**environment, CONVERSATION_ENV: identifier, WORKSPACE_ENV: str(self.workspace.root)}

    def end_run(self, record):
        identifier = record.get("conversation_id")
        if not identifier:
            return
        with self._locked():
            view = self.store.get(identifier)
            if view["active_worker_id"]:
                self.store.end_worker(identifier, view["active_worker_id"],
                    "The run ended before this message received a completed response.")
            record["conversation"] = provenance(self.store, identifier)

    def close(self):
        with self._locked():
            self._closed = True
            idle = self._idle
            closers = list(self._closers)
            if idle:
                idle.stop.set()
                idle.stop_reason = "The app closed before this conversation completed its response."
        if idle:
            self._close_owned(idle)
            if idle.thread and idle.thread is not threading.current_thread():
                idle.thread.join(timeout=15)
        for closer in closers:
            if closer is not threading.current_thread():
                closer.join(timeout=15)
