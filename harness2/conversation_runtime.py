# SPDX-License-Identifier: Apache-2.0
"""Connect an opted-in worker to its real OpenCode session and public conversation.

The model endpoint is inherited from the owning operation. This module never
loads weights, guesses an existing server's credentials, or creates a second
model. Only explicit conversation tools enter history; working continuity is a
separate bounded projection, reread by the trusted OpenCode plugin.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

from . import config, processes
from .conversations import ConversationStore
from .locking import atomic_replace, file_lock

CONVERSATION_ENV = "HARNESS2_CONVERSATION_ID"
WORKSPACE_ENV = "HARNESS2_CONVERSATION_WORKSPACE"
_ID = re.compile(r"[a-f0-9]{32}\Z")
_NATIVE_SESSION = re.compile(r"ses_[A-Za-z0-9]{1,128}\Z")
_NATIVE_MESSAGE = re.compile(r"msg_[A-Za-z0-9]{1,128}\Z")
MAX_PRIVATE = 1024 * 1024


def enabled(environment=None) -> bool:
    environment = os.environ if environment is None else environment
    return bool(environment.get(CONVERSATION_ENV))


def require_worker_containment() -> None:
    """Reject working-run participation where nested cancellation can escape."""
    if os.name != "nt":
        raise ValueError(
            "Conversation-enabled experiment runs currently require Windows for nested process containment. "
            "Standalone conversations and saved archives remain available."
        )


def runtime_root(workspace: Path, conversation_id: str) -> Path:
    if not isinstance(conversation_id, str) or not _ID.fullmatch(conversation_id):
        raise ValueError("Invalid conversation identity.")
    workspace = Path(workspace).resolve()
    target = workspace
    for name in (".harness2", "conversation-runtime", conversation_id):
        target = target / name
        if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
            raise ValueError("Conversation runtime paths must not be links.")
        if not target.resolve().is_relative_to(workspace):
            raise ValueError("Conversation runtime escaped its workspace.")
        config.long_path(target).mkdir(exist_ok=True)
    return target


def write_json(path: Path, value: dict) -> None:
    checked = config.long_path(path)
    if checked.is_symlink() or getattr(checked, "is_junction", lambda: False)():
        raise ValueError("Conversation runtime records must not be links.")
    body = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(body) > MAX_PRIVATE:
        raise ValueError("Conversation runtime record is too large.")
    checked.parent.mkdir(parents=True, exist_ok=True)
    temporary = checked.with_name(f".{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        atomic_replace(temporary, checked)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    checked = config.long_path(path)
    if checked.is_symlink() or getattr(checked, "is_junction", lambda: False)():
        raise ValueError("Conversation runtime records must not be links.")
    with checked.open("rb") as stream:
        body = stream.read(MAX_PRIVATE + 1)
    if len(body) > MAX_PRIVATE:
        raise ValueError("Conversation runtime record is too large.")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Conversation runtime record must be an object.")
    return value


def _native_ancestry(session_id, info):
    values = (session_id, info.get("id"), info.get("parentID"))
    patterns = (_NATIVE_SESSION, _NATIVE_MESSAGE, _NATIVE_MESSAGE)
    if (info.get("role") != "assistant" or info.get("sessionID", session_id) != session_id
            or any(not isinstance(value, str) or not pattern.fullmatch(value)
                   for value, pattern in zip(values, patterns))):
        return {}
    return dict(zip(("native_session_id", "native_message_id", "native_parent_id"), values))


def link_native_records(store, conversation_id, session_id, native_messages, *, outboxes=()):
    """Append verified ancestry to old posts; preserve their original message events.

    This also works on retained native records without starting a model/session.
    A tool link requires its saved request, acknowledgement, and native tool
    output to agree on the actual ledger message identity.
    """
    view = store.get(conversation_id)
    workers = {worker["id"]: worker["session_id"] for worker in view["workers"]}
    native = {row.get("info", {}).get("id"): row for row in native_messages
              if _native_ancestry(session_id, row.get("info", {}))}
    candidates = {message["session_message_id"]: message for message in view["messages"]
                  if message["role"] == "assistant" and not message.get("native_session_id")
                  and workers.get(message["worker_id"]) == session_id}
    linked = set()

    def link(message, row):
        if message["id"] in linked:
            return
        store.link_native_message(conversation_id, message["id"],
                                  **_native_ancestry(session_id, row["info"]))
        linked.add(message["id"])

    for transport, message in candidates.items():
        row = native.get(transport)
        text = "\n".join(part["text"] for part in row.get("parts", [])
                         if part.get("type") == "text" and isinstance(part.get("text"), str)).strip() if row else ""
        if (row and row["info"].get("finish") not in (None, "tool-calls", "unknown")
                and not row["info"].get("error") and not row["info"].get("has_error")
                and text == message["text"]):
            link(message, row)
    for outbox in outboxes:
        outbox = Path(outbox)
        for transport, message in candidates.items():
            request_id = transport.removeprefix("tool:")
            if not transport.startswith("tool:") or not _ID.fullmatch(request_id):
                continue
            request_path = outbox / "requests" / (request_id + ".json")
            reply_path = outbox / "replies" / request_path.name
            if not config.long_path(request_path).is_file() or not config.long_path(reply_path).is_file():
                continue
            request, reply = read_json(request_path), read_json(reply_path)
            row = native.get(request.get("native_message_id"))
            acknowledged = reply.get("record")
            if (not row or request.get("kind") != "message" or request.get("request_id") != request_id
                    or request.get("session_id") != session_id or reply.get("ok") is not True
                    or not isinstance(acknowledged, dict) or acknowledged.get("id") != message["id"]
                    or acknowledged.get("text") != message["text"]):
                continue
            for part in row.get("parts", []):
                state = part.get("state", {})
                if (part.get("type") != "tool" or part.get("tool") != "harness2_conversation"
                        or not isinstance(state, dict) or not isinstance(state.get("input"), dict)
                        or state.get("status") != "completed"
                        or state["input"].get("kind") != "message"
                        or state["input"].get("text") != request.get("text")):
                    continue
                try:
                    output = json.loads(state.get("output", ""))
                except (ValueError, TypeError):
                    continue
                if (isinstance(output, dict) and output.get("ok") is True
                        and isinstance(output.get("record"), dict)
                        and output["record"].get("id") == message["id"]):
                    link(message, row)
                    break
    return len(linked)


def working_directory(environment, label: str) -> Path | None:
    if not enabled(environment):
        return None
    root = runtime_root(Path(environment[WORKSPACE_ENV]), environment[CONVERSATION_ENV])
    ConversationStore(Path(environment[WORKSPACE_ENV])).get(environment[CONVERSATION_ENV])
    name = re.sub(r"[^a-zA-Z0-9_-]+", "-", label)[:64].strip("-") or "worker"
    target = root / "workspaces" / f"{name}-{uuid.uuid4().hex}"
    config.long_path(target).mkdir(parents=True)
    write_json(target.parent / f".label-{target.name}.json", {"label": label[:240]})
    return target


def _redact(text: str, environment: dict) -> str:
    for key, value in environment.items():
        if (re.search(r"api[_-]?key|secret|password|(?:^|_)token$|access[_-]?token", key, re.I)
                and isinstance(value, str)
                and value and value != "local"):
            text = text.replace(value, "[redacted]")
    return text


def _redact_value(value, environment):
    if isinstance(value, str):
        return _redact(value, environment)
    if isinstance(value, list):
        return [_redact_value(item, environment) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item, environment) for key, item in value.items()}
    return value


class SessionBridge:
    """One real worker, with serialized public delivery and separate continuity."""

    def __init__(self, workspace: Path, conversation_id: str, cwd: Path,
                 environment: dict, *, model: str, label: str,
                 resume_session_id: str | None = None, session_runtime: Path | None = None,
                 wait_for_initial: bool = False):
        self.workspace = Path(workspace).resolve()
        self.conversation_id = conversation_id
        self.store = ConversationStore(self.workspace)
        self.store.get(conversation_id)
        self.root = runtime_root(self.workspace, conversation_id)
        self.worker_id = uuid.uuid4().hex
        self.cwd = Path(cwd).resolve()
        self.environment = dict(environment)
        self.model, self.label = model, label
        self.runtime = session_runtime or self.root / "workers" / self.worker_id
        config.long_path(self.runtime).mkdir(parents=True, exist_ok=True)
        self.recovery_file = self.root / "recovery.json"
        self.outbox = self.runtime / "outbox"
        self.resume_session_id = resume_session_id
        self.client = None
        self.stop_event = threading.Event()
        self.thread = None
        self.failure = ""
        self._native_seen = set()
        self._compacted = set()
        self._resumed_actions = 0
        self._after_compaction = False
        self._state_lock = threading.RLock()
        self._wait_for_initial = wait_for_initial
        self._worker_started = False
        self._native_inputs = {}
        self._inputs_primed = False

    def _recovery(self):
        view = self.store.get(self.conversation_id)
        value = {"version": 1, "conversation_id": self.conversation_id,
                 "profile": {"id": view["profile_id"],
                             "name": self.store.profiles.get(view["profile_id"])["name"],
                             "configuration_id": view["configuration_id"], "revision_id": view["revision_id"]},
                 "worker": self.label, "checkpoint": view["recovery"],
                 "decisions": view["decisions"],
                 "qualification": "Progress and influence are model declarations; sources remain inspectable."}
        # Never silently truncate decisions or turn chat history into a summary.
        if len(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")) > 32768:
            raise ValueError("Working continuity exceeds the compaction recovery budget.")
        write_json(self.recovery_file, value)

    def start(self):
        from .opencode_session import OpenCodeSession

        self._recovery()
        self.client = OpenCodeSession(
            self.cwd, self.environment, runtime_dir=self.runtime, model=self.model,
            label=self.label, recovery_file=self.recovery_file, outbox=self.outbox,
            resume_session_id=self.resume_session_id,
        )
        try:
            self.client.start()
            with self._state_lock:
                if self.stop_event.is_set():
                    raise ValueError("Conversation startup was cancelled.")
                self.store.bind_worker(self.conversation_id, self.worker_id,
                                       label=self.label, session_id=self.client.session_id)
            saved = {"version": 1, "worker_id": self.worker_id, "workspace": str(self.cwd),
                     "session_runtime": str(self.runtime), "session_id": self.client.session_id,
                     "model": self.model, "label": self.label, "pid": os.getpid()}
            write_json(self.root / "latest.json", saved)
            self.thread = threading.Thread(target=self._monitor, daemon=True,
                                           name=f"conversation-{self.worker_id[:8]}")
            self.thread.start()
            return self
        except BaseException:
            self.client.close()
            raise

    def _tool_requests(self, native_messages=None):
        if native_messages is not None:
            self._remember_inputs(native_messages)
        requests = config.long_path(self.outbox / "requests")
        if not requests.is_dir():
            return
        processed = 0
        for path in sorted(requests.glob("*.json")):
            if not _ID.fullmatch(path.stem):
                continue
            reply = self.outbox / "replies" / path.name
            if config.long_path(reply).exists():
                continue
            if processed >= 64:
                break
            processed += 1
            try:
                request = _redact_value(read_json(path), self.environment)
                if (request.get("request_id") != path.stem
                        or request.get("session_id") != self.client.session_id):
                    raise ValueError("The tool request belongs to another session.")
                kind = request.get("kind")
                if kind == "message":
                    origin = "legacy"
                    native = None
                    if native_messages is not None:
                        native = next((item["info"] for item in native_messages
                                       if item.get("info", {}).get("id") == request.get("native_message_id")
                                       and item["info"].get("role") == "assistant"), None)
                        if native and native.get("parentID"):
                            view = self.store.get(self.conversation_id)
                            human = {item.get("session_message_id") for item in view["messages"]
                                     if item["role"] == "user" and item.get("session_message_id")}
                            source = self._native_inputs.get(native["parentID"])
                            origin = ("reply" if native["parentID"] in human or source == "human"
                                      else "model_initiated" if source == "formal" else "legacy")
                    result = self.store.assistant(
                        self.conversation_id, _redact(request.get("text", ""), self.environment),
                        worker_id=self.worker_id, session_message_id="tool:" + path.stem,
                        origin=origin,
                        **(_native_ancestry(self.client.session_id, native) if native else {}),
                    )
                elif kind == "decision":
                    self._check_update(request)
                    result = self.store.record_decision(
                        self.conversation_id, worker_id=self.worker_id,
                        text=_redact(request.get("text", ""), self.environment),
                        why=_redact(request.get("why", ""), self.environment),
                        source_message_ids=request.get("source_message_ids", []),
                        status=request.get("status", "proposed"),
                        supersedes_id=request.get("supersedes_id"),
                        request_id=path.stem,
                    )
                    self._recovery()
                elif kind == "checkpoint":
                    self._check_update(request)
                    keys = ("objective", "constraints", "completed", "planned", "open_questions", "decision_ids")
                    result = self.store.checkpoint(
                        self.conversation_id, worker_id=self.worker_id,
                        request_id=path.stem,
                        **{key: request[key] for key in keys if key in request},
                    )
                    self._recovery()
                elif kind == "compaction":
                    result = self.store.record_event(
                        self.conversation_id, "before_compaction",
                        {"session_id": self.client.session_id,
                         "recovery_sha256": hashlib.sha256(config.long_path(self.recovery_file).read_bytes()).hexdigest(),
                         "recovery": read_json(self.recovery_file)}, worker_id=self.worker_id,
                    )
                else:
                    raise ValueError("Unknown conversation tool request.")
                write_json(reply, {"ok": True, "record": result})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                write_json(reply, {"ok": False, "error": _redact(str(exc), self.environment)[:1000]})

    def _remember_inputs(self, native_messages):
        # A stored user ID is not guaranteed after an ambiguous send receipt.
        # Recognize the injected human wrapper as well, and retain formal input
        # identities when a long task moves beyond the bounded native page.
        if not self._inputs_primed:
            for event in self.store.get(self.conversation_id)["observations"]:
                detail = event.get("detail", {})
                if (event["kind"] == "native_input" and detail.get("session_id") == self.client.session_id
                        and detail.get("origin") in {"human", "formal"}):
                    self._native_inputs[detail["message_id"]] = detail["origin"]
            self._inputs_primed = True
        for message in native_messages:
            info = message.get("info", {})
            identifier = info.get("id")
            if info.get("role") != "user" or not identifier or identifier in self._native_inputs:
                continue
            wrapped = any(part.get("type") == "text" and isinstance(part.get("text"), str)
                          and part["text"].startswith("[Human conversation; source_message_id=")
                          for part in message.get("parts", []))
            origin = "human" if wrapped else "formal"
            self.store.record_event(self.conversation_id, "native_input", {
                "session_id": self.client.session_id, "message_id": identifier, "origin": origin,
            }, worker_id=self.worker_id)
            self._native_inputs[identifier] = origin

    def _check_update(self, request):
        if self.store.has_request(self.conversation_id, request["request_id"]):
            # Store replay validation still checks the exact original payload.
            return
        # Reject before committing if the next record could exhaust recovery.
        # Leave room for archive-generated identities/timestamps/qualification.
        value = read_json(self.recovery_file)
        if request["kind"] == "decision":
            value["decisions"].append(request)
        else:
            value["checkpoint"] = {**request, "completed": [
                {"text": item, "references": []} if isinstance(item, str) else item
                for item in request.get("completed", [])
            ]}
        if len(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")) + 2048 > 32768:
            raise ValueError("Working continuity is full; consolidate the checkpoint before adding more.")

    def _answers(self, native_messages):
        link_native_records(self.store, self.conversation_id, self.client.session_id,
                            native_messages, outboxes=(self.outbox,))
        self._worker_started |= any(message.get("info", {}).get("role") == "user"
                                    for message in native_messages)
        view = self.store.get(self.conversation_id)
        # Explicit continuation reopens the same native history under a new
        # worker. Previously recorded boundaries are historical, not fresh
        # compactions or evidence of actions resumed by this new worker.
        for observation in view["observations"]:
            detail = observation.get("detail")
            if (observation.get("kind") == "compaction_summary" and isinstance(detail, dict)
                    and detail.get("session_id") == self.client.session_id
                    and isinstance(detail.get("message_id"), str)):
                self._compacted.add(detail["message_id"])
        pending = {message.get("session_message_id"): message["id"]
                   for message in view["messages"]
                   if message["worker_id"] == self.worker_id and message["status"] == "delivered"}
        # Native messages are chronological. On subsequent polls, never count
        # tools preceding the latest compaction as resumed actions.
        boundary = max((index for index, message in enumerate(native_messages)
                        if message.get("info", {}).get("summary")), default=-1)
        for index, message in enumerate(native_messages):
            info, parts = message.get("info", {}), message.get("parts", [])
            native_id = info.get("id")
            if info.get("role") != "assistant" or not native_id:
                continue
            completed = bool((info.get("time") or {}).get("completed") or info.get("finish"))
            texts = [part.get("text", "") for part in parts if part.get("type") == "text"]
            text = _redact("\n".join(texts).strip(), self.environment)
            if info.get("summary") and completed and native_id not in self._compacted:
                self.store.record_event(self.conversation_id, "compaction_summary",
                                        {"session_id": self.client.session_id, "message_id": native_id,
                                         "text": text[:16000]}, worker_id=self.worker_id)
                self._compacted.add(native_id)
                self._after_compaction, self._resumed_actions = True, 0
            if self._after_compaction and index > boundary and not info.get("summary"):
                for part in parts:
                    if part.get("type") == "tool" and part.get("id") not in self._native_seen:
                        if self._resumed_actions < 8:
                            self.store.record_event(
                                self.conversation_id, "resumed_action",
                                {"session_id": self.client.session_id, "tool": part.get("tool", ""),
                                 "part_id": part.get("id", "")}, worker_id=self.worker_id,
                            )
                            self._resumed_actions += 1
                        self._native_seen.add(part.get("id"))
            parent = info.get("parentID")
            if completed and not info.get("summary") and parent in pending and native_id not in self._native_seen:
                if info.get("error") or info.get("has_error"):
                    self.store.transition(self.conversation_id, pending.pop(parent), "failed",
                                          reason="The working agent returned an error before answering.")
                    self._native_seen.add(native_id)
                elif info.get("finish") in (None, "tool-calls", "unknown"):
                    # OpenCode completes each assistant/tool step separately.
                    # Only a terminal step can consume the pending human turn.
                    continue
                elif text:
                    self.store.assistant(self.conversation_id, text, worker_id=self.worker_id,
                                         session_message_id=native_id, reply_to=[pending[parent]],
                                         **_native_ancestry(self.client.session_id, info))
                    self._native_seen.add(native_id)
                    pending.pop(parent)
                else:
                    self.store.transition(self.conversation_id, pending.pop(parent), "failed",
                                          reason="The working agent finished without a visible answer.")
                    self._native_seen.add(native_id)

    def _deliver(self):
        if self._wait_for_initial and not self._worker_started:
            return
        view = self.store.get(self.conversation_id)
        messages = [message for message in view["messages"] if message["worker_id"] == self.worker_id]
        if any(message["role"] == "user" and message["status"] == "delivered" for message in messages):
            return
        message = next((item for item in messages if item["role"] == "user" and item["status"] == "queued"), None)
        if message:
            try:
                native_id = self.client.send(
                    f"[Human conversation; source_message_id={message['id']}]\n{message['text']}\n"
                    "[Ordinary conversation is not automatically an instruction to change the task. "
                    "If this changes a work decision, record its source with harness2_conversation.]"
                )
            except (OSError, ValueError) as exc:
                self.store.transition(self.conversation_id, message["id"], "undelivered",
                                      reason="Delivery could not be confirmed; it may have reached the session. "
                                      "It was not automatically resent. " + _redact(str(exc), self.environment)[:500])
                self.store.record_event(self.conversation_id, "delivery_unconfirmed",
                                        {"message_id": message["id"]}, worker_id=self.worker_id)
                return
            self.store.transition(self.conversation_id, message["id"], "delivered", session_message_id=native_id)
            self.store.record_event(self.conversation_id, "human_contact",
                                    {"message_id": message["id"], "session_message_id": native_id},
                                    worker_id=self.worker_id)

    def _monitor(self):
        errors = 0
        while not self.stop_event.is_set():
            try:
                native_messages = self.client.messages()
                self._tool_requests(native_messages)
                self._answers(native_messages)
                self._deliver()
                errors = 0
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors += 1
                if errors >= 3:
                    self.failure = _redact(str(exc), self.environment)[:1000]
                    try:
                        self.store.record_event(self.conversation_id, "session_connection_failed",
                                                {"reason": self.failure}, worker_id=self.worker_id)
                        self.store.end_worker(self.conversation_id, self.worker_id,
                                              "The session connection failed: " + self.failure)
                    finally:
                        self.stop_event.set()
                    return
            self.stop_event.wait(0.35)

    def drain(self, deadline: float):
        # A CLI can observe idle just as a human message reaches prompt_async.
        # Give already delivered input its remaining worker budget; no fake ack.
        idle_since = None
        while time.monotonic() < deadline and not self.failure:
            view = self.store.get(self.conversation_id)
            pending = [message for message in view["messages"] if message["worker_id"] == self.worker_id
                       and message["role"] == "user" and message["status"] in ("queued", "delivered")]
            if not pending:
                break
            if self.client.status() == "idle":
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since > 5:
                    break
            else:
                idle_since = None
            time.sleep(0.2)

    def close(self, reason="The working session ended before this message was answered."):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=8)
        if self.client:
            self.client.close()
        if self.thread and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)
        try:
            with self._state_lock:
                view = self.store.get(self.conversation_id)
                if any(worker["id"] == self.worker_id for worker in view["workers"]):
                    self.store.end_worker(self.conversation_id, self.worker_id, reason)
        except (OSError, ValueError):
            if not self.failure:
                raise


def run(command, *, cwd, env, timeout, label="Working on this experiment", **kwargs):
    """Preserve the normal process contract, adding a bridge only when opted in."""
    if not enabled(env):
        return processes.run(command, cwd=cwd, env=env, timeout=timeout, **kwargs)
    require_worker_containment()
    workspace, identifier = Path(env[WORKSPACE_ENV]), env[CONVERSATION_ENV]
    root = runtime_root(workspace, identifier)
    # The native runner launches Bun from the pinned checkout, with --dir
    # selecting the actual task workspace. Session APIs must use that directory.
    session_cwd = Path(command[command.index("--dir") + 1]) if "--dir" in command else Path(cwd)
    if not session_cwd.resolve().is_relative_to(root):
        raise ValueError("The conversational worker must use its retained task workspace.")
    marker = session_cwd.parent / f".label-{session_cwd.name}.json"
    if marker.is_file() and marker.resolve().is_relative_to(root):
        label = read_json(marker)["label"]
    deadline = time.monotonic() + float(timeout)
    with ExitStack() as stack:
        while True:
            if time.monotonic() >= deadline:
                import subprocess

                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stack.enter_context(file_lock(root / ".worker.lock", blocking=False))
                break
            except BlockingIOError:
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        model = command[command.index("--model") + 1]
        deadline = time.monotonic() + float(timeout)
        bridge = SessionBridge(workspace, identifier, session_cwd, env, model=model, label=label,
                               wait_for_initial=True)
        try:
            bridge.start()
            if time.monotonic() >= deadline:
                import subprocess

                raise subprocess.TimeoutExpired(command, timeout)
            result = processes.run(bridge.client.attach(command), cwd=cwd,
                                   env=bridge.client.environment, timeout=max(1, deadline - time.monotonic()),
                                   **kwargs)
            bridge.drain(deadline)
            return result
        finally:
            bridge.close()


def provenance(store: ConversationStore, identifier: str) -> dict:
    view = store.get(identifier)
    delivered = [message for message in view["messages"] if message["role"] == "user"
                 and message.get("session_message_id")]
    uncertain = {event["detail"]["message_id"] for event in view["observations"]
                 if event["kind"] == "delivery_unconfirmed" and event["detail"].get("message_id")}
    return {"conversation_id": identifier, "enabled": True,
            "human_messages_delivered": len(delivered), "human_assisted": bool(delivered),
            "human_delivery_unconfirmed": len(uncertain), "human_contact_possible": bool(uncertain),
            "decision_ids": [decision["id"] for decision in view["decisions"]],
            "qualification": "Contact and declared influence are not proof of benefit."}
