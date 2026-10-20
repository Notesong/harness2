# SPDX-License-Identifier: Apache-2.0
"""Durable UI attention, separate from conversation and model learning records."""

from __future__ import annotations

import json
import os
import re
import uuid

from ..locking import atomic_replace
from ..profiles import MAX_RECORD, ProfileStore

_ID = re.compile(r"[a-f0-9]{32}\Z")


class Attention:
    def __init__(self, workspace, conversations):
        self.conversations = conversations
        self.archive = ProfileStore(workspace.root)
        self.archive.root = workspace.state_dir / "conversation-attention"
        self.path = self.archive.root / "state.json"

    def _load(self):
        if not self.archive._safe(self.path).exists():
            return {"version": 1, "enabled": False, "read": {}, "attempted": {}}
        value = self.archive._read(self.path)
        if (set(value) != {"version", "enabled", "read", "attempted"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["enabled"]) is not bool):
            raise ValueError("Notification preferences are invalid.")
        for field in ("read", "attempted"):
            records = value[field]
            if not isinstance(records, dict) or len(records) > 10_000:
                raise ValueError("Notification attention records are invalid.")
            for key, identifiers in records.items():
                if (not _ID.fullmatch(key) or not isinstance(identifiers, list) or len(identifiers) > 2_000
                        or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in identifiers)
                        or len(set(identifiers)) != len(identifiers)):
                    raise ValueError("Notification message references are invalid.")
        return value

    def _save(self, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(raw) > MAX_RECORD:
            raise ValueError("Conversation attention storage is full.")
        temporary = self.archive._safe(self.path.parent / ("." + uuid.uuid4().hex + ".tmp"))
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            atomic_replace(temporary, self.archive._safe(self.path))
        finally:
            temporary.unlink(missing_ok=True)

    def state(self):
        with self.archive._locked():
            value = self._load()
        return {"enabled": value["enabled"]}

    def decorate(self, conversation):
        with self.archive._locked():
            value = self._load()
        read = set(value["read"].get(conversation["id"], []))
        read.update(item["duplicate_of"] for item in conversation["messages"]
                    if item.get("duplicate_of") and item["id"] in read)
        unread = [item for item in conversation["messages"]
                  if item["role"] == "assistant" and not item.get("duplicate_of") and item["id"] not in read]
        return {"unread_count": len(unread), "unread_message_ids": [item["id"] for item in unread],
                "attention_messages": [{key: item[key] for key in ("id", "role", "origin", "reply_to", "created_at")}
                                       | {"text": item["text"][:240]} for item in unread]}

    def read(self, identifier, message_ids):
        if (not isinstance(message_ids, list) or len(message_ids) > 128
                or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in message_ids)
                or len(set(message_ids)) != len(message_ids)):
            raise ValueError("Choose up to 128 distinct visible assistant message IDs.")
        # Validate exactly what the client rendered. A later model append must
        # stay unread; no cursor is inferred from random UUID lexical order.
        view = self.conversations.get(identifier)
        known = {item["id"] for item in view["messages"] if item["role"] == "assistant"}
        if any(item not in known for item in message_ids):
            raise ValueError("Read references must be assistant messages in this conversation.")
        with self.archive._locked():
            value = self._load()
            old = set(value["read"].get(identifier, []))
            combined = old | set(message_ids)
            if old != combined:
                value["read"][identifier] = sorted(combined)
                self._save(value)

    def settings(self, data):
        if not isinstance(data, dict) or set(data) != {"enabled"} or type(data["enabled"]) is not bool:
            raise ValueError("Notification preference must be an explicit enabled boolean.")
        # Take the baseline before the UI lock, consistently with get/decorate.
        # Any subsequent append is new; this never marks the backlog as read.
        baseline = {}
        if data["enabled"]:
            for summary in self.conversations.list():
                view = self.conversations.get(summary["id"])
                baseline[view["id"]] = {item["id"] for item in view["messages"]
                                        if item["role"] == "assistant" and item["origin"] == "model_initiated"}
        with self.archive._locked():
            value = self._load()
            if data["enabled"] and not value["enabled"]:
                for identifier, messages in baseline.items():
                    value["attempted"][identifier] = sorted(set(value["attempted"].get(identifier, [])) | messages)
            if value["enabled"] != data["enabled"]:
                value["enabled"] = data["enabled"]
                self._save(value)
        return {"enabled": value["enabled"]}

    def claim(self, data):
        if (not isinstance(data, dict) or set(data) != {"conversation_id", "message_id"}
                or any(not isinstance(value, str) or not _ID.fullmatch(value) for value in data.values())):
            raise ValueError("Choose a valid conversation and message identity.")
        identifier, message_id = data["conversation_id"], data["message_id"]
        view = self.conversations.get(identifier)
        message = next((item for item in view["messages"] if item["id"] == message_id), None)
        if message is None:
            raise ValueError("Notification message does not belong to this conversation.")
        if (message["role"] != "assistant" or message.get("origin") != "model_initiated"
                or message.get("duplicate_of")):
            return {"claimed": False}
        with self.archive._locked():
            value = self._load()
            attempted = set(value["attempted"].get(identifier, []))
            if (not value["enabled"] or message_id in attempted
                    or message_id in value["read"].get(identifier, [])):
                return {"claimed": False}
            attempted.add(message_id)
            value["attempted"][identifier] = sorted(attempted)
            self._save(value)
        # This records a single attempted browser notification, never OS receipt.
        return {"claimed": True}

