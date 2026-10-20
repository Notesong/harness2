# SPDX-License-Identifier: Apache-2.0
"""Owned, authenticated OpenCode sessions for explicitly conversational runs.

The session API shares the worker's actual native session. A sent message is
confirmed only after its correlation metadata appears in that session's store;
this is distinct from the assistant having read or answered it. No SQLite writes
or second inference conversation are used. The caller owns transcript policy.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from . import config, processes
from .local_models import opencode_env, profile

MAX_RESPONSE = 4 * 1024 * 1024
# Recent native messages are polled for new public replies. Keep the page small:
# upstream rows also carry private tool outputs before our public projection.
MAX_MESSAGES = 32
MAX_TEXT = 32_768
START_TIMEOUT = 60
DELIVERY_TIMEOUT = 5
_NATIVE_ID = re.compile(r"(?:ses|msg|prt)_[A-Za-z0-9]{1,128}\Z")

PLUGIN = r'''
import { tool } from __TOOL_IMPORT__;
import { readFile, writeFile, mkdir, rename, stat } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

const recoveryFile = process.env.HARNESS2_CONVERSATION_RECOVERY;
const outbox = process.env.HARNESS2_CONVERSATION_OUTBOX;
const sessionFile = process.env.HARNESS2_CONVERSATION_SESSION_FILE;
const maxBytes = 32768;
const policy = `You are working in an explicitly conversational Harness² experiment.
Keep doing the task without requesting routine approvals. Ordinary conversation
is not automatically an instruction to change strategy or pause work. If an
exchange changes an assumption or a decision, record a decision with the real
source_message_id values shown in human conversation messages or acknowledged
conversation records. These source IDs are 32-hex ledger IDs, not OpenCode msg_
IDs; native transport identity is recorded separately. Use harness2_conversation to speak occasionally when
there is uncertainty, surprise, or a meaningful stage completion; avoid flooding
the conversation. Keep official task answers and deliverable files separate.
Reply to a human conversation message with your normal assistant response.
Do not publish that same reply through the conversation tool as well. Tool
messages are for distinct progress updates or contact during ongoing work.
Recovery is a factual ledger: completed means done, planned means not yet done.
Preserve unresolved questions and decision references through compaction.
If a recorded conversation decision actually changes a proposed harness, include
CONVERSATION_DECISION: <full 32-hex decision id> in that proposal's final decision
notes. This declares influence, not evidence that the change provides a benefit.
Only the acknowledged ledger records persist; a proposed tool call is not a
completed record. Never claim that planned work or a rejected record is done.`;

async function readBounded(file, limit = maxBytes) {
  const info = await stat(file);
  if (info.size > limit) throw new Error("Conversation recovery or reply exceeds its size limit");
  const bytes = await readFile(file);
  if (bytes.length > limit) throw new Error("Conversation recovery or reply exceeds its size limit");
  return bytes.toString("utf8");
}
async function owns(sessionID) {
  if (!sessionID || !sessionFile) return false;
  try { return JSON.parse(await readBounded(sessionFile)).session_id === sessionID; }
  catch (error) { if (error.code === "ENOENT") return false; throw error; }
}
async function recovery() {
  if (!recoveryFile) return "";
  try {
    const value = JSON.parse(await readBounded(recoveryFile));
    return "\nCURRENT DURABLE RECOVERY (completed and planned are different):\n" + JSON.stringify(value);
  } catch (error) { if (error.code === "ENOENT") return ""; throw error; }
}
async function publish(packet, signal, wait = true) {
  if (!outbox) throw new Error("The conversation outbox is unavailable");
  const request_id = randomUUID().replaceAll("-", "");
  const requests = join(outbox, "requests"), replies = join(outbox, "replies");
  await mkdir(requests, { recursive: true });
  await mkdir(replies, { recursive: true });
  const body = JSON.stringify({ version: 1, ...packet, request_id });
  if (Buffer.byteLength(body) > 65536) throw new Error("Conversation record exceeds its size limit");
  const target = join(requests, request_id + ".json"), temporary = target + ".tmp";
  await writeFile(temporary, body, { flag: "wx" });
  await rename(temporary, target);
  if (!wait) return JSON.stringify({ ok: true, request_id, pending: true });
  const configured = Number(process.env.HARNESS2_CONVERSATION_ACK_TIMEOUT_MS || 20000);
  const duration = Number.isFinite(configured) ? Math.max(100, Math.min(60000, configured)) : 20000;
  const deadline = Date.now() + duration;
  while (Date.now() < deadline) {
    if (signal?.aborted) throw new Error("Conversation record interrupted before acknowledgement");
    try {
      const reply = JSON.parse(await readBounded(join(replies, request_id + ".json"), 65536));
      if (reply.ok !== true) throw new Error("Conversation record rejected: " + String(reply.error || "Invalid acknowledgement").slice(0, 2000));
      return JSON.stringify(reply);
    } catch (error) { if (error.code !== "ENOENT") throw error; }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error("Conversation record acknowledgement timed out; the record is not confirmed");
}
export default async function () {
  const s = tool.schema;
  return {
    tool: {
      harness2_conversation: tool({
        description: "Speak in the run conversation or record a changed decision/checkpoint. These are public working notes, not private reasoning. For source_message_ids use the exact 32-hex source_message_id from human messages or acknowledged conversation record IDs, not OpenCode msg_ IDs. Wait for acknowledgement before treating a record as saved.",
        args: {
          kind: s.enum(["message", "decision", "checkpoint"]),
          text: s.string().max(8000).optional(), why: s.string().max(8000).optional(),
          source_message_ids: s.array(s.string().max(160)).max(64).optional(),
          status: s.string().max(64).optional(), supersedes_id: s.string().max(160).optional(),
          objective: s.string().max(4000).optional(),
          constraints: s.array(s.string().max(2000)).max(32).optional(),
          completed: s.array(s.string().max(2000)).max(64).optional(),
          planned: s.array(s.string().max(2000)).max(64).optional(),
          open_questions: s.array(s.string().max(2000)).max(32).optional(),
          decision_ids: s.array(s.string().max(160)).max(64).optional(),
        },
        async execute(args, context) {
          if (!await owns(context.sessionID)) throw new Error("This is not the owned conversation session");
          return publish({ ...args, session_id: context.sessionID, native_message_id: context.messageID }, context.abort);
        },
      }),
    },
    "experimental.chat.system.transform": async (input, output) => {
      if (await owns(input.sessionID)) output.system.push(policy + await recovery());
    },
    "experimental.session.compacting": async (input, output) => {
      if (!await owns(input.sessionID)) return;
      output.context.push(policy + await recovery());
      if (outbox) await publish({ kind: "compaction", event: "before_compaction", session_id: input.sessionID }, undefined, false);
    },
  };
}
'''


class SessionTransportError(OSError):
    """The owned OpenCode session could not be reached or confirmed."""


def _native_id(value: object, prefix: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix + "_") or not _NATIVE_ID.fullmatch(value):
        raise SessionTransportError("OpenCode returned an invalid native session/message identifier.")
    return value


def _regular_path(path: Path) -> Path:
    absolute = path.resolve()
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        checked = config.long_path(cursor)
        if checked.is_symlink() or getattr(checked, "is_junction", lambda: False)():
            raise ValueError("Conversation paths cannot traverse symlinks or junctions.")
    return absolute


class OpenCodeSession:
    """One process owner and exact native session, optionally reopened explicitly."""

    def __init__(self, workspace: Path, environment: dict, *, runtime_dir: Path,
                 model: str, label: str = "", recovery_file: Path | None = None,
                 outbox: Path | None = None, resume_session_id: str | None = None):
        self.workspace = _regular_path(Path(workspace).absolute())
        self.runtime_dir = _regular_path(Path(runtime_dir).absolute())
        self.environment = dict(environment)
        local = profile(self.environment)
        if local is None or model != local.opencode_model:
            raise ValueError("Conversational sessions require the explicitly configured local model.")
        self.model, self.label = model, str(label)[:240] or "Harness² conversation"
        self.session_id = _native_id(resume_session_id, "ses") if resume_session_id else None
        self._resume = resume_session_id is not None
        self.recovery_file = _regular_path(Path(recovery_file).absolute()) if recovery_file else None
        self.outbox = _regular_path(Path(outbox).absolute()) if outbox else None
        self.base_url: str | None = None
        self._port: int | None = None
        self._process: subprocess.Popen | None = None
        self._log = None
        self._password: str | None = None
        self._closed = False
        self._ready = False
        self._starting = False
        self._state_lock = threading.RLock()
        self._send_lock = threading.Lock()

    def start(self):
        with self._state_lock:
            if self._closed:
                raise SessionTransportError("The OpenCode session owner is closed.")
            if self._ready:
                return self
            if self._starting:
                raise SessionTransportError("The OpenCode session is already starting.")
            self._starting = True
            try:
                if not config.BUN.is_file() or not (config.OPENCODE_DIR / "packages/opencode/src/index.ts").is_file():
                    raise FileNotFoundError("Install the pinned OpenCode checkout and Bun before starting a conversation.")
                config.long_path(self.workspace).mkdir(parents=True, exist_ok=True)
                config.long_path(self.runtime_dir).mkdir(parents=True, exist_ok=True)
                self.environment = opencode_env(self.environment, model=self.model)
                self._password = secrets.token_urlsafe(32)
                self.environment.update({
                    "OPENCODE_SERVER_PASSWORD": self._password, "OPENCODE_SERVER_USERNAME": "opencode",
                    "XDG_DATA_HOME": str(self.runtime_dir / "data"),
                    "XDG_STATE_HOME": str(self.runtime_dir / "state"),
                })
                if self.recovery_file or self.outbox:
                    self._plugin()
                with socket.socket() as reservation:
                    if os.name == "nt":
                        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    reservation.bind(("127.0.0.1", 0))
                    self._port = reservation.getsockname()[1]
                self.base_url = f"http://127.0.0.1:{self._port}"
                self._log = config.long_path(self.runtime_dir / "server.log").open("ab")
                self._process = processes.start([
                    str(config.BUN), "run", "--cwd", str(config.OPENCODE_DIR / "packages/opencode"),
                    "--conditions=browser", "src/index.ts", "serve", "--hostname", "127.0.0.1",
                    "--port", str(self._port), "--mdns", "false",
                ], cwd=self.workspace, env=self.environment, stdin=subprocess.DEVNULL,
                    stdout=self._log, stderr=subprocess.STDOUT,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
                # Readiness and instance initialization can block. Let close()
                # stop the owned child during those requests rather than waiting
                # behind the state lock for the full startup deadline.
                self._state_lock.release()
                try:
                    deadline = time.monotonic() + START_TIMEOUT
                    while True:
                        if self._closed:
                            raise SessionTransportError("OpenCode session startup was cancelled.")
                        if self._process.poll() is not None:
                            raise SessionTransportError("The owned OpenCode server exited before readiness; inspect its local log.")
                        try:
                            health = self._request("GET", "/global/health", timeout=0.5)
                            if isinstance(health, dict) and health.get("healthy") is True:
                                break
                        except (OSError, SessionTransportError):
                            pass
                        if time.monotonic() >= deadline:
                            raise SessionTransportError("The owned OpenCode server did not become ready.")
                        time.sleep(0.1)
                    if self._resume:
                        info = self._request("GET", "/session/" + self.session_id, timeout=START_TIMEOUT)
                        if not isinstance(info, dict) or info.get("id") != self.session_id:
                            raise SessionTransportError("The retained native session is unavailable; no replacement was created.")
                    else:
                        info = self._request("POST", "/session", {"title": self.label}, timeout=START_TIMEOUT)
                        self.session_id = _native_id(info.get("id") if isinstance(info, dict) else None, "ses")
                    if not isinstance(info.get("directory"), str) or Path(info["directory"]).resolve() != self.workspace:
                        raise SessionTransportError("The native session belongs to another working directory.")
                finally:
                    self._state_lock.acquire()
                if self._closed:
                    raise SessionTransportError("OpenCode session startup was cancelled.")
                self._write_session_identity()
                self._ready = True
                return self
            except BaseException:
                self.close()
                raise
            finally:
                self._starting = False

    def _plugin(self):
        if self.environment.get("OPENCODE_PURE", "").lower() in ("1", "true"):
            raise ValueError("OpenCode pure mode disables the required conversation plugin.")
        content = json.loads(self.environment.get("OPENCODE_CONFIG_CONTENT") or "{}")
        if not isinstance(content, dict) or not isinstance(content.get("plugin", []), list):
            raise ValueError("OpenCode conversation configuration needs an object and plugin array.")
        plugin = self.runtime_dir / "conversation-plugin.ts"
        tool = config.OPENCODE_DIR / "packages/plugin/src/tool.ts"
        if not tool.is_file():
            raise FileNotFoundError("The pinned OpenCode plugin tool API is unavailable.")
        config.long_path(plugin).write_text(PLUGIN.replace("__TOOL_IMPORT__", json.dumps(tool.resolve().as_uri())), encoding="utf-8")
        plugins = content.setdefault("plugin", [])
        if plugin.as_uri() not in plugins:
            plugins.append(plugin.as_uri())
        self.environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(content, ensure_ascii=False)
        self.environment["HARNESS2_CONVERSATION_SESSION_FILE"] = str(self.runtime_dir / "session.json")
        if self.recovery_file:
            self.environment["HARNESS2_CONVERSATION_RECOVERY"] = str(self.recovery_file)
        if self.outbox:
            for name in ("requests", "replies"):
                config.long_path(self.outbox / name).mkdir(parents=True, exist_ok=True)
            self.environment["HARNESS2_CONVERSATION_OUTBOX"] = str(self.outbox)

    def _write_session_identity(self):
        path = self.runtime_dir / "session.json"
        temporary = path.with_suffix(".tmp")
        config.long_path(temporary).write_text(json.dumps({"session_id": self.session_id}) + "\n", encoding="utf-8")
        os.replace(config.long_path(temporary), config.long_path(path))

    def _request(self, method, path, body=None, *, timeout=5, limit=MAX_RESPONSE):
        if self._port is None or self._password is None or self._closed:
            raise SessionTransportError("The OpenCode session transport is not running.")
        headers = {
            "Authorization": "Basic " + base64.b64encode(("opencode:" + self._password).encode()).decode("ascii"),
            "Content-Type": "application/json", "Connection": "close",
        }
        query = urlencode({"directory": str(self.workspace)})
        path += ("&" if "?" in path else "?") + query
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=timeout)
        try:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise SessionTransportError("The OpenCode response exceeds its size limit.")
            if response.status < 200 or response.status >= 300:
                raise SessionTransportError(f"The owned OpenCode API returned HTTP {response.status}.")
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (ValueError, UnicodeError):
                raise SessionTransportError("The OpenCode API returned invalid JSON.") from None
        finally:
            connection.close()

    def attach(self, command: list[str]) -> list[str]:
        if not self._ready or self._closed:
            raise SessionTransportError("Start the exact native session before attaching a worker.")
        restricted = ("--attach", "--session", "--continue", "--fork", "-s", "-c")
        if any(arg == flag or arg.startswith(flag + "=") for arg in command for flag in restricted):
            raise ValueError("A worker must not override its owned conversation session.")
        selected = []
        for index, arg in enumerate(command):
            if arg in ("--model", "-m"):
                selected.append(command[index + 1] if index + 1 < len(command) else None)
            elif arg.startswith(("--model=", "-m=")):
                selected.append(arg.split("=", 1)[1])
        if any(value != self.model for value in selected):
            raise ValueError("The attached worker model differs from its explicit local session model.")
        return [*command, *([] if selected else ["--model", self.model]),
                "--attach", self.base_url, "--session", self.session_id]

    def send(self, text: str) -> str:
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT or "\0" in text:
            raise ValueError("Send a nonempty bounded conversation message.")
        with self._send_lock:
            if not self._ready or self._closed:
                raise SessionTransportError("The native conversation session is not running.")
            delivery_id = uuid.uuid4().hex
            provider_id, model_id = self.model.split("/", 1)
            self._request("POST", f"/session/{self.session_id}/prompt_async", {
                "model": {"providerID": provider_id, "modelID": model_id},
                "parts": [{"type": "text", "text": text, "metadata": {"harness2_delivery_id": delivery_id}}],
            })
            deadline = time.monotonic() + DELIVERY_TIMEOUT
            while True:
                for message in self._raw_messages():
                    info = message.get("info", {})
                    if info.get("role") != "user" or info.get("sessionID") != self.session_id:
                        continue
                    if any(part.get("metadata", {}).get("harness2_delivery_id") == delivery_id
                           for part in message.get("parts", []) if isinstance(part, dict)):
                        return _native_id(info.get("id"), "msg")
                if time.monotonic() >= deadline:
                    raise SessionTransportError("The message was accepted but its native storage is unconfirmed; do not resend automatically.")
                time.sleep(0.05)

    def _raw_messages(self):
        if not self.session_id:
            raise SessionTransportError("There is no selected native session.")
        result = self._request("GET", f"/session/{self.session_id}/message?limit={MAX_MESSAGES}")
        if not isinstance(result, list) or len(result) > MAX_MESSAGES:
            raise SessionTransportError("The OpenCode message list is malformed or exceeds its bound.")
        if not all(isinstance(row, dict) and isinstance(row.get("info"), dict)
                   and isinstance(row.get("parts"), list) and len(row["parts"]) <= 1024
                   and all(isinstance(part, dict) and ("metadata" not in part or isinstance(part["metadata"], dict))
                           for part in row["parts"]) for row in result):
            raise SessionTransportError("The OpenCode message list contains invalid entries.")
        return result

    def messages(self) -> list[dict]:
        """Recent native messages, oldest first, with public text/tool identity only.

        The pinned API's bounded message page reverses its database order before
        returning. Reasoning, tool arguments/results, and arbitrary metadata are
        never copied into this public projection.
        """
        result = []
        for row in self._raw_messages():
            raw = row.get("info", {})
            if not isinstance(raw, dict) or raw.get("sessionID") != self.session_id:
                raise SessionTransportError("OpenCode returned a message from another session.")
            if raw.get("role") not in ("user", "assistant"):
                continue
            info = {key: raw[key] for key in ("id", "sessionID", "role", "parentID", "time", "finish", "summary") if key in raw}
            info["has_error"] = bool(raw.get("error"))
            _native_id(info.get("id"), "msg")
            parts = []
            budget = MAX_TEXT
            for part in row.get("parts", []):
                if isinstance(part, dict) and part.get("type") == "tool":
                    parts.append({"id": _native_id(part.get("id"), "prt"), "type": "tool",
                                  "tool": str(part.get("tool", "unknown"))[:128]})
                    continue
                if (not isinstance(part, dict) or part.get("type") != "text"
                        or not isinstance(part.get("text"), str) or part.get("ignored")):
                    continue
                text = part["text"][:budget]
                budget -= len(text)
                parts.append({key: part[key] for key in ("id", "type", "synthetic", "time") if key in part} | {"text": text})
                if len(text) != len(part["text"]):
                    parts[-1]["truncated"] = True
            result.append({"info": info, "parts": parts})
        return result

    def status(self) -> str:
        if not self.session_id:
            raise SessionTransportError("There is no selected native session.")
        values = self._request("GET", "/session/status")
        if not isinstance(values, dict):
            raise SessionTransportError("The OpenCode session status is malformed.")
        current = values.get(self.session_id, {})
        state = current.get("type", "idle") if isinstance(current, dict) else None
        if state not in ("idle", "busy", "retry"):
            raise SessionTransportError("The OpenCode session status is unsupported.")
        return "busy" if state == "retry" else state

    def abort(self):
        if self.session_id and self._ready and not self._closed:
            self._request("POST", f"/session/{self.session_id}/abort")

    def close(self):
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._ready = False
            try:
                if self._process is not None:
                    processes.terminate_tree(self._process)
            finally:
                if self._log is not None:
                    self._log.close()
                self._password = None
                self.environment.pop("OPENCODE_SERVER_PASSWORD", None)

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
