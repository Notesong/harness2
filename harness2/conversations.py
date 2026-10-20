# SPDX-License-Identifier: Apache-2.0
"""Durable visible conversations and separately journaled working continuity.

History, working decisions/checkpoints, conversational memory, and technical
profiles are separate stores. Nothing here calls a model, records hidden
reasoning, or promotes chat into a harness. Provider credentials are not inputs.
Message text is the visible text explicitly supplied by the caller.
"""

from __future__ import annotations

import copy
import re
import uuid
from pathlib import Path

from .locking import atomic_replace
from .profiles import (
    CorruptProfileError,
    ProfileStore,
    _hash,
    _id,
    _json,
    _public,
    _sequence,
    _text,
)

MAX_MESSAGES = 2_000
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_MESSAGE = 65_536
MAX_HISTORY = 20_000
MAX_WORKING = 10_000
MAX_RECOVERY_BYTES = 32 * 1024
_IDENTIFIER = re.compile(r"[a-f0-9]{32}\Z")
_EVENT = re.compile(r"([0-9]{8})-([a-f0-9]{64})\.json\Z")
_TERMINAL = frozenset({"closed", "ended", "completed", "failed", "cancelled", "stopped"})
_NATIVE_FIELDS = ("native_session_id", "native_message_id", "native_parent_id")
_NATIVE_SESSION = re.compile(r"ses_[A-Za-z0-9]{1,128}\Z")
_NATIVE_MESSAGE = re.compile(r"msg_[A-Za-z0-9]{1,128}\Z")
_TOOL_MESSAGE = re.compile(r"tool:[a-f0-9]{32}\Z")


class CorruptConversationError(CorruptProfileError):
    """A conversation journal failed its integrity or state checks."""


class _Archive(ProfileStore):
    def __init__(self, workspace_root, directory="conversations"):
        super().__init__(workspace_root)
        self.root = self.workspace_root / ".harness2" / directory


def _conversation_id(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid conversation ID.")
    return value


def _worker(value, *, empty=True):
    return _text(value, "worker ID", 240, empty=empty)


def _identifiers(value, field):
    values = _sequence(value, field)
    for identifier in values:
        _conversation_id(identifier)
    if len(set(values)) != len(values):
        raise ValueError(f"Repeated {field}.")
    return values


def _strings(value, field):
    values = _sequence(value, field)
    return [_text(item, field, 4_096, empty=False) for item in values]


def _native_metadata(data):
    """Native ancestry is explicit evidence, never inferred from visible text."""
    present = set(_NATIVE_FIELDS).intersection(data)
    if not present:
        return {}
    if present != set(_NATIVE_FIELDS):
        raise ValueError("Native message ancestry needs session, message, and parent IDs together.")
    for key, pattern in zip(_NATIVE_FIELDS, (_NATIVE_SESSION, _NATIVE_MESSAGE, _NATIVE_MESSAGE)):
        if not isinstance(data[key], str) or not pattern.fullmatch(data[key]):
            raise ValueError("Invalid native message ancestry ID.")
    if data["native_message_id"] == data["native_parent_id"]:
        raise ValueError("A native message cannot be its own parent.")
    return {key: data[key] for key in _NATIVE_FIELDS}


class ConversationStore:
    def __init__(self, workspace_root: Path | str):
        self._archive = _Archive(workspace_root)
        self.workspace_root = self._archive.workspace_root
        self.root = self._archive.root
        self._memory_archive = _Archive(workspace_root, "conversational-memory")
        self.memory_root = self._memory_archive.root
        self.profiles = ProfileStore(self.workspace_root)

    def _directory(self, conversation_id):
        path = self._archive._safe(self.root / _conversation_id(conversation_id))
        if not path.is_dir():
            raise ValueError("Conversation does not exist.")
        return path

    @staticmethod
    def _events(archive, directory, limit, *, required=False):
        event_dir = archive._safe(directory / "events")
        if not event_dir.exists():
            if required:
                raise CorruptConversationError("Conversation history is missing.")
            return []
        if not event_dir.is_dir():
            raise CorruptConversationError("Conversation event history is not a directory.")
        paths = sorted(event_dir.iterdir())
        if len(paths) > limit + 10:
            raise CorruptConversationError("Conversation event history exceeds its limit.")
        events = []
        for path in paths:
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                continue
            match = _EVENT.fullmatch(path.name)
            if not match:
                raise CorruptConversationError("Unrecognized conversation event record.")
            value = archive._read(path)
            if (int(match[1]) != len(events) + 1 or value.get("sequence") != len(events) + 1
                    or value.get("schema") != 1 or _hash(value) != match[2]
                    or value.get("previous") != (events[-1]["id"] if events else None)
                    or not isinstance(value.get("data"), dict)):
                raise CorruptConversationError("Conversation event sequence or hash is invalid.")
            events.append({"id": match[2], **value})
        return events

    @staticmethod
    def _message(view, message_id):
        message = next((item for item in view["messages"] if item["id"] == message_id), None)
        if message is None:
            raise ValueError("Message does not belong to this conversation.")
        return message

    @staticmethod
    def _known_worker(view, worker_id):
        worker = next((item for item in view["workers"] if item["id"] == worker_id), None)
        if worker is None:
            raise ValueError("Worker does not belong to this conversation.")
        return worker

    @classmethod
    def _validate_native(cls, view, message, native):
        if not native:
            return
        worker = cls._known_worker(view, message["worker_id"])
        if worker["session_id"] != native["native_session_id"]:
            raise ValueError("Native message ancestry must belong to its worker's session.")
        transport = message["session_message_id"]
        if transport.startswith("msg_") and transport != native["native_message_id"]:
            raise ValueError("Native message ancestry conflicts with its transport message ID.")

    @classmethod
    def _project_aliases(cls, view):
        """Keep both transports in history; mark only proven reply repetitions."""
        canonical = {}
        for message in view["messages"]:
            message.pop("duplicate_of", None)
            native = _native_metadata(message)
            if (message["role"] != "assistant" or not native or message.get("origin") != "reply"
                    or not message["reply_to"] or message["session_message_id"] != native["native_message_id"]):
                continue
            anchored = any(
                (source := cls._message(view, identifier))["role"] == "user"
                and source["worker_id"] == message["worker_id"]
                and source["session_message_id"] == native["native_parent_id"]
                for identifier in message["reply_to"]
            )
            if anchored:
                key = (message["worker_id"], native["native_session_id"], native["native_parent_id"], message["text"])
                canonical.setdefault(key, message["id"])
        for message in view["messages"]:
            native = _native_metadata(message)
            if (message["role"] == "assistant" and native
                    and message.get("origin") in {"legacy", "reply"}
                    and _TOOL_MESSAGE.fullmatch(message["session_message_id"])):
                key = (message["worker_id"], native["native_session_id"], native["native_parent_id"], message["text"])
                if key in canonical:
                    message["duplicate_of"] = canonical[key]

    @staticmethod
    def _expire(view, worker_id, reason, event):
        for message in view["messages"]:
            if message["role"] == "user" and (not worker_id or message["worker_id"] == worker_id):
                if message["status"] == "queued":
                    message.update(status="undelivered", reason=reason, updated_at=event["time"])
                elif message["status"] == "delivered":
                    message.update(status="failed", reason=reason, updated_at=event["time"])

    @classmethod
    def _apply_history(cls, view, event):
        action, data = event["action"], event["data"]
        stamp = {"created_at": event["time"], "event_id": event["id"]}
        if action == "created":
            if view["created_at"]:
                raise ValueError("Conversation was created twice.")
            view.update(data, **stamp)
        elif action == "worker_bound":
            if view["status"] != "open":
                raise ValueError("Conversation is closed.")
            if any(worker["id"] == data["id"] for worker in view["workers"]):
                raise ValueError("Worker IDs cannot be reused.")
            if view["active_worker_id"]:
                prior = cls._known_worker(view, view["active_worker_id"])
                prior.update(status="ended", reason="Replaced by another worker.", ended_at=event["time"])
                cls._expire(view, prior["id"], "Worker replaced before answering.", event)
            view["workers"].append({**data, **stamp})
            view["active_worker_id"] = data["id"] if data["status"] not in _TERMINAL else None
        elif action == "user_queued":
            if view["status"] != "open" or view["active_worker_id"] != data["worker_id"]:
                raise ValueError("There is no matching active worker.")
            if len(view["messages"]) >= MAX_MESSAGES or any(m["id"] == data["id"] for m in view["messages"]):
                raise ValueError("Conversation message limit or ID collision.")
            view["messages"].append({**data, **stamp, "updated_at": event["time"]})
        elif action == "message_transitioned":
            message = cls._message(view, data["message_id"])
            if message["role"] != "user":
                raise ValueError("Only queued user messages have delivery transitions.")
            old, new = message["status"], data["status"]
            if (old, new) not in {("queued", "delivered"), ("queued", "undelivered"), ("delivered", "failed")}:
                raise ValueError("Invalid message delivery transition.")
            if new in {"failed", "undelivered"} and not data["reason"].strip():
                raise ValueError("Delivery failure needs a reason.")
            message.update(status=new, reason=data["reason"], updated_at=event["time"])
            if data["session_message_id"]:
                if message.get("session_message_id") and message["session_message_id"] != data["session_message_id"]:
                    raise ValueError("A message cannot change its native session ID.")
                message["session_message_id"] = data["session_message_id"]
        elif action == "assistant_added":
            if view["status"] != "open":
                raise ValueError("Conversation is closed.")
            cls._known_worker(view, data["worker_id"])
            cls._validate_native(view, data, _native_metadata(data))
            if len(view["messages"]) >= MAX_MESSAGES or any(m["id"] == data["id"] for m in view["messages"]):
                raise ValueError("Conversation message limit or ID collision.")
            for message_id in data["reply_to"]:
                message = cls._message(view, message_id)
                if (message["role"] != "user" or message["status"] != "delivered"
                        or message["worker_id"] != data["worker_id"]):
                    raise ValueError("An answer must reference delivered messages for this worker.")
                message.update(status="answered", answered_by=data["id"], updated_at=event["time"])
            origin = data.get("origin", "reply" if data["reply_to"] else "legacy")
            if origin not in {"model_initiated", "reply", "legacy"} or origin == "model_initiated" and data["reply_to"]:
                raise ValueError("Invalid visible message origin.")
            view["messages"].append({**data, **stamp, "origin": origin,
                                     "status": "answered", "updated_at": event["time"]})
        elif action == "assistant_native_linked":
            message = cls._message(view, data["message_id"])
            if message["role"] != "assistant":
                raise ValueError("Only assistant messages can receive native ancestry links.")
            native = _native_metadata(data)
            if not native:
                raise ValueError("A native message link needs ancestry IDs.")
            cls._validate_native(view, message, native)
            prior = _native_metadata(message)
            if prior and prior != native:
                raise ValueError("Native message ancestry cannot change.")
            message.update(native)
        elif action == "worker_ended":
            worker = cls._known_worker(view, data["worker_id"])
            worker.update(status="ended", reason=data["reason"], ended_at=event["time"])
            cls._expire(view, worker["id"], data["reason"], event)
            if view["active_worker_id"] == worker["id"]:
                view["active_worker_id"] = None
        elif action == "finished":
            if data["status"] not in _TERMINAL:
                raise ValueError("A finished conversation needs a terminal status.")
            for worker in view["workers"]:
                if worker["status"] not in _TERMINAL:
                    worker.update(status="ended", reason=data["reason"], ended_at=event["time"])
            cls._expire(view, "", data["reason"], event)
            view.update(status=data["status"], reason=data["reason"], finished_at=event["time"], active_worker_id=None)
        else:
            raise ValueError("Unknown conversation history event.")
        if sum(len(item["text"].encode("utf-8")) for item in view["messages"]) > MAX_TEXT_BYTES:
            raise ValueError("Conversation visible text exceeds its storage budget.")

    def _state(self, conversation_id):
        directory = self._directory(conversation_id)
        history = self._events(self._archive, directory / "history", MAX_HISTORY, required=True)
        view = {"id": conversation_id, "profile_id": "", "configuration_id": "", "revision_id": "",
                "run_id": "", "title": "", "status": "open", "created_at": "", "workers": [],
                "active_worker_id": None, "messages": [], "decisions": [], "recovery": {}, "observations": []}
        try:
            if not history or history[0]["action"] != "created":
                raise ValueError("Conversation creation record is missing.")
            for event in history:
                self._apply_history(view, event)
            working = self._events(self._archive, directory / "working", MAX_WORKING)
            for event in working:
                action, data = event["action"], event["data"]
                stamp = {"created_at": event["time"], "event_id": event["id"]}
                if action == "decision_recorded":
                    self._validate_decision(view, data)
                    view["decisions"].append({**data, **stamp})
                elif action == "checkpoint_recorded":
                    self._validate_checkpoint(view, data)
                    view["recovery"] = {**data, **stamp}
                elif action == "observation_recorded":
                    if data["worker_id"]:
                        self._known_worker(view, data["worker_id"])
                    view["observations"].append({**data, **stamp})
                else:
                    raise ValueError("Unknown working continuity event.")
            self._project_aliases(view)
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptConversationError("Conversation history refers to invalid state.") from exc
        return view, history, working

    def _append_history(self, conversation_id, view, events, action, data):
        if len(events) >= MAX_HISTORY:
            raise ValueError("Conversation history needs archival before more additions.")
        self._apply_history(copy.deepcopy(view), {"id": "pending", "time": "pending", "action": action, "data": data})
        self._archive._append(self._directory(conversation_id) / "history", events, action, data)

    def create(self, profile_id, configuration_id, revision_id, *, run_id="", title="Conversation"):
        profile_id = _id(profile_id)
        configuration_id, revision_id = _id(configuration_id, digest=True), _id(revision_id, digest=True)
        title, run_id = _text(title, "conversation title", 240, empty=False), _text(run_id, "run ID", 240)
        profile = self.profiles.get(profile_id)
        if (not any(row["id"] == configuration_id for row in profile["configurations"])
                or not any(row["id"] == revision_id for row in profile["revisions"])):
            raise ValueError("Conversation configuration and revision must belong to its profile.")
        conversation_id = uuid.uuid4().hex
        with self._archive._locked():
            destination = self._archive._safe(self.root / conversation_id)
            if destination.exists():
                raise ValueError("A conversation with this ID already exists.")
            staging = self.root / f".create-{uuid.uuid4().hex}.tmp"
            self._archive._mkdir(staging)
            self._archive._append(staging / "history", [], "created", {
                "profile_id": profile_id, "configuration_id": configuration_id, "revision_id": revision_id,
                "run_id": run_id, "title": title,
            })
            # Destination absence is checked under the same archive lock. The
            # shared helper retries transient Windows AV/indexer sharing errors;
            # it must never replace an already published conversation.
            if self._archive._safe(destination).exists():
                raise ValueError("A conversation with this ID already exists.")
            atomic_replace(self._archive._safe(staging), destination)
            return self._state(conversation_id)[0]

    def list(self, profile_id=None):
        if profile_id is not None:
            profile_id = _id(profile_id)
        with self._archive._locked():
            directories = sorted(self.root.iterdir())
            if len(directories) > 10_000:
                raise ValueError("Conversation list exceeds its archival limit.")
            result = []
            for path in directories:
                if not _IDENTIFIER.fullmatch(path.name):
                    continue
                view = self._state(path.name)[0]
                if profile_id is None or view["profile_id"] == profile_id:
                    result.append({key: view[key] for key in (
                        "id", "profile_id", "configuration_id", "revision_id", "run_id", "title", "status",
                        "created_at", "active_worker_id")} | {"message_count": len(view["messages"])})
            return result

    def get(self, conversation_id):
        with self._archive._locked():
            return self._state(conversation_id)[0]

    def bind_worker(self, conversation_id, worker_id, *, label, session_id, status="active"):
        data = {"id": _worker(worker_id, empty=False), "label": _text(label, "worker label", 240),
                "session_id": _text(session_id, "native session ID", 512),
                "status": _text(status, "worker status", 32, empty=False)}
        with self._archive._locked():
            view, history, _ = self._state(conversation_id)
            prior = next((worker for worker in view["workers"] if worker["id"] == worker_id), None)
            if prior and all(prior[key] == value for key, value in data.items()):
                return view
            self._append_history(conversation_id, view, history, "worker_bound", data)
            return self._state(conversation_id)[0]

    def enqueue(self, conversation_id, text, *, worker_id=""):
        text = _text(text, "visible user message", MAX_MESSAGE, empty=False)
        with self._archive._locked():
            view, history, _ = self._state(conversation_id)
            worker_id = _worker(worker_id) or view["active_worker_id"]
            if not worker_id:
                raise ValueError("There is no active worker to receive this message.")
            message_id = uuid.uuid4().hex
            self._append_history(conversation_id, view, history, "user_queued", {
                "id": message_id, "role": "user", "text": text, "status": "queued",
                "worker_id": worker_id, "session_message_id": "", "reason": "",
            })
            return self._message(self._state(conversation_id)[0], message_id)

    def transition(self, conversation_id, message_id, status, *, reason="", session_message_id=""):
        data = {"message_id": _conversation_id(message_id),
                "status": _text(status, "message status", 32, empty=False),
                "reason": _text(reason, "delivery reason", 4_096),
                "session_message_id": _text(session_message_id, "native message ID", 512)}
        with self._archive._locked():
            view, history, _ = self._state(conversation_id)
            prior = self._message(view, message_id)
            if prior["status"] == status:
                if (data["session_message_id"] and prior.get("session_message_id") != data["session_message_id"]):
                    raise ValueError("A message cannot change its native session ID.")
                return view
            self._append_history(conversation_id, view, history, "message_transitioned", data)
            return self._state(conversation_id)[0]

    def assistant(self, conversation_id, text, *, worker_id, session_message_id="", reply_to=(), origin=None,
                  native_session_id=None, native_message_id=None, native_parent_id=None):
        native = _native_metadata({key: value for key, value in zip(
            _NATIVE_FIELDS, (native_session_id, native_message_id, native_parent_id)) if value is not None})
        data = {"id": uuid.uuid4().hex, "role": "assistant",
                "text": _text(text, "visible assistant message", MAX_MESSAGE, empty=False),
                "worker_id": _worker(worker_id, empty=False),
                "session_message_id": _text(session_message_id, "native message ID", 512),
                "reply_to": _identifiers(reply_to, "answer references"),
                "origin": origin or ("reply" if reply_to else "legacy"), **native}
        with self._archive._locked():
            view, history, _ = self._state(conversation_id)
            self._validate_native(view, data, native)
            if session_message_id or native:
                prior = next((message for message in view["messages"] if message["role"] == "assistant"
                              and (message["worker_id"] == worker_id or
                                   _TOOL_MESSAGE.fullmatch(session_message_id) or
                                   native and message.get("native_session_id") == native["native_session_id"])
                              and message["session_message_id"] == session_message_id
                              and (session_message_id or message.get("native_message_id") == native["native_message_id"])), None)
                if prior:
                    if (prior["text"] != data["text"]
                            or data["reply_to"] and prior["reply_to"] != data["reply_to"]
                            or native and _native_metadata(prior) and _native_metadata(prior) != native):
                        raise ValueError("A completed native assistant message cannot change.")
                    return prior
            self._append_history(conversation_id, view, history, "assistant_added", data)
            return self._message(self._state(conversation_id)[0], data["id"])

    def link_native_message(self, conversation_id, message_id, *, native_session_id, native_message_id, native_parent_id):
        """Append verified native ancestry without rewriting the original post."""
        data = {"message_id": _conversation_id(message_id), **_native_metadata({
            "native_session_id": native_session_id, "native_message_id": native_message_id,
            "native_parent_id": native_parent_id})}
        with self._archive._locked():
            view, history, _ = self._state(conversation_id)
            message = self._message(view, message_id)
            native = _native_metadata(data)
            if message["role"] != "assistant":
                raise ValueError("Only assistant messages can receive native ancestry links.")
            self._validate_native(view, message, native)
            prior = _native_metadata(message)
            if prior:
                if prior != native:
                    raise ValueError("Native message ancestry cannot change.")
                return message
            self._append_history(conversation_id, view, history, "assistant_native_linked", data)
            return self._message(self._state(conversation_id)[0], message_id)

    def end_worker(self, conversation_id, worker_id, reason):
        data = {"worker_id": _worker(worker_id, empty=False),
                "reason": _text(reason, "worker end reason", 4_096, empty=False)}
        with self._archive._locked():
            view, history, _ = self._state(conversation_id)
            prior = self._known_worker(view, worker_id)
            if prior["status"] in _TERMINAL:
                return view
            self._append_history(conversation_id, view, history, "worker_ended", data)
            return self._state(conversation_id)[0]

    def finish(self, conversation_id, reason, status="closed"):
        status = _text(status, "conversation status", 32, empty=False)
        if status not in _TERMINAL:
            raise ValueError("Choose a terminal conversation status.")
        data = {"reason": _text(reason, "conversation end reason", 4_096, empty=False), "status": status}
        with self._archive._locked():
            view, history, _ = self._state(conversation_id)
            if view["status"] in _TERMINAL:
                return view
            self._append_history(conversation_id, view, history, "finished", data)
            return self._state(conversation_id)[0]

    @classmethod
    def _validate_decision(cls, view, data):
        if "request_id" in data:
            _conversation_id(data["request_id"])
        if data["worker_id"]:
            cls._known_worker(view, data["worker_id"])
        for message_id in data["source_message_ids"]:
            cls._message(view, message_id)
        if not data["source_message_ids"]:
            raise ValueError("A consequential decision needs source message references.")
        if data["status"] not in {"proposed", "adopted", "revised", "reverted"}:
            raise ValueError("Choose proposed, adopted, revised, or reverted.")
        parent = data["supersedes_id"]
        if parent is not None and not any(decision["id"] == parent for decision in view["decisions"]):
            raise ValueError("A superseded decision must belong to this conversation.")
        if data["status"] in {"revised", "reverted"} and parent is None:
            raise ValueError("Revising or reverting needs the earlier decision ID.")

    def record_decision(self, conversation_id, *, text, why, source_message_ids, status="proposed",
                        supersedes_id=None, worker_id="", request_id=None):
        data = {"id": uuid.uuid4().hex, "text": _text(text, "decision", 8_192, empty=False),
                "why": _text(why, "decision explanation", 8_192, empty=False),
                "source_message_ids": _identifiers(source_message_ids, "source messages"),
                "status": _text(status, "decision status", 32, empty=False),
                "supersedes_id": supersedes_id, "worker_id": _worker(worker_id),
                "qualification": "Declared conversational influence; benefit is not established."}
        if request_id is not None:
            data["request_id"] = _conversation_id(request_id)
        if supersedes_id is not None:
            _conversation_id(supersedes_id)
        with self._archive._locked():
            view, _, working = self._state(conversation_id)
            self._validate_decision(view, data)
            previous = self._working_replay(working, "decision_recorded", data)
            if previous is not None:
                return previous
            self._append_working(conversation_id, working, "decision_recorded", data)
            return next(item for item in self._state(conversation_id)[0]["decisions"] if item["id"] == data["id"])

    @classmethod
    def _validate_checkpoint(cls, view, data):
        if "request_id" in data:
            _conversation_id(data["request_id"])
        if data["worker_id"]:
            cls._known_worker(view, data["worker_id"])
        known = {decision["id"] for decision in view["decisions"]}
        if any(decision_id not in known for decision_id in data["decision_ids"]):
            raise ValueError("Recovery decisions must belong to this conversation.")

    def _append_working(self, conversation_id, events, action, data):
        if len(events) >= MAX_WORKING:
            raise ValueError("Working continuity history needs archival before further additions.")
        if len(_json(data)) > MAX_RECOVERY_BYTES:
            raise ValueError("A working continuity record exceeds its context budget.")
        self._archive._append(self._directory(conversation_id) / "working", events, action, data)

    def has_request(self, conversation_id, request_id):
        _conversation_id(request_id)
        with self._archive._locked():
            _, _, working = self._state(conversation_id)
            return any(event["data"].get("request_id") == request_id for event in working)

    @staticmethod
    def _working_replay(events, action, data):
        """Recover a committed tool result when its acknowledgement was lost."""
        request_id = data.get("request_id")
        if request_id is None:
            return None
        prior = next((event for event in events if event["data"].get("request_id") == request_id), None)
        if prior is None:
            return None
        def payload(item):
            return {key: value for key, value in item.items() if key not in ("id", "worker_id")}
        if prior["action"] != action or _json(payload(prior["data"])) != _json(payload(data)):
            raise ValueError("A committed conversation tool request cannot change its payload.")
        return {**prior["data"], "created_at": prior["time"], "event_id": prior["id"]}

    def checkpoint(self, conversation_id, *, objective="", constraints=(), completed=(), planned=(),
                   open_questions=(), decision_ids=(), worker_id="", request_id=None):
        completed = _sequence(completed, "completion claims")
        claims = []
        for item in completed:
            if isinstance(item, str):
                claims.append({"text": _text(item, "completion claim", 4_096, empty=False), "references": []})
            elif isinstance(item, dict) and not set(item) - {"text", "references", "evidence"}:
                claims.append({"text": _text(item.get("text"), "completion claim", 4_096, empty=False),
                               "references": _sequence(item.get("references", item.get("evidence", ())), "completion references")})
            else:
                raise ValueError("Completion claims need text and optional evidence references.")
        data = {"id": uuid.uuid4().hex, "objective": _text(objective, "recovery objective", 8_192),
                "constraints": _strings(constraints, "recovery constraints"), "completed": claims,
                "planned": _strings(planned, "planned work"), "open_questions": _strings(open_questions, "open questions"),
                "decision_ids": _identifiers(decision_ids, "recovery decision references"), "worker_id": _worker(worker_id),
                "completion_qualification": "Completion is model-reported; supplied references are not independently verified."}
        if request_id is not None:
            data["request_id"] = _conversation_id(request_id)
        with self._archive._locked():
            view, _, working = self._state(conversation_id)
            self._validate_checkpoint(view, data)
            previous = self._working_replay(working, "checkpoint_recorded", data)
            if previous is not None:
                return previous
            self._append_working(conversation_id, working, "checkpoint_recorded", data)
            return self._state(conversation_id)[0]["recovery"]

    def record_event(self, conversation_id, kind, detail, *, worker_id=""):
        data = {"id": uuid.uuid4().hex, "kind": _text(kind, "working observation kind", 80, empty=False),
                "detail": _public(detail), "worker_id": _worker(worker_id)}
        with self._archive._locked():
            view, _, working = self._state(conversation_id)
            if worker_id:
                self._known_worker(view, worker_id)
            self._append_working(conversation_id, working, "observation_recorded", data)
            return self._state(conversation_id)[0]["observations"][-1]

    def memory(self, profile_id):
        """Reserved, separately owned conversational memory; never auto-populated."""
        profile_id = _id(profile_id)
        self.profiles.get(profile_id)
        with self._memory_archive._locked():
            directory = self._memory_archive._safe(self.memory_root / profile_id)
            return [{**event["data"], "created_at": event["time"], "event_id": event["id"]}
                    for event in self._events(self._memory_archive, directory, MAX_WORKING)]

    def add_memory(self, profile_id, *, text, why, conversation_id, source_message_ids):
        """Explicit sourced archival only; does not affect technical profiles or context."""
        profile_id = _id(profile_id)
        data = {"id": uuid.uuid4().hex, "text": _text(text, "conversational memory", 8_192, empty=False),
                "why": _text(why, "memory explanation", 8_192, empty=False),
                "conversation_id": _conversation_id(conversation_id),
                "source_message_ids": _identifiers(source_message_ids, "memory source messages"),
                "qualification": "Explicit conversational memory; not a technical lesson or demonstrated capability."}
        if not data["source_message_ids"]:
            raise ValueError("Conversational memory needs explicit source messages.")
        with self._archive._locked():
            view, _, _ = self._state(conversation_id)
            if view["profile_id"] != profile_id:
                raise ValueError("Conversational memory sources must belong to this profile.")
            for message_id in data["source_message_ids"]:
                self._message(view, message_id)
            with self._memory_archive._locked():
                directory = self._memory_archive._safe(self.memory_root / profile_id)
                events = self._events(self._memory_archive, directory, MAX_WORKING)
                if len(events) >= MAX_WORKING:
                    raise ValueError("Conversational memory needs archival before further additions.")
                self._memory_archive._append(directory, events, "memory_added", data)
                event = self._events(self._memory_archive, directory, MAX_WORKING)[-1]
                return {**data, "created_at": event["time"], "event_id": event["id"]}
